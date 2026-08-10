from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flexdisplay_bridge.app import create_app
from flexdisplay_bridge.config import BridgeConfig, HomeAssistantConfig
from flexdisplay_bridge.voice_assistant import (
    MIN_AUDIO_BYTES,
    HomeAssistantVoiceClient,
    VoiceAssistantError,
    VoiceAssistantResult,
    decode_voice_response,
    display_text,
    encode_voice_response,
)


class FakeSocket:
    def __init__(self, messages: list[dict[str, Any]]):
        self.messages = [json.dumps(message) for message in messages]
        self.sent: list[str | bytes] = []

    def __enter__(self) -> "FakeSocket":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def recv(self, timeout: float | None = None) -> str:
        del timeout
        return self.messages.pop(0)

    def send(self, payload: str | bytes) -> None:
        self.sent.append(payload)


def assist_messages() -> list[dict[str, Any]]:
    return [
        {"type": "auth_required"},
        {"type": "auth_ok"},
        {
            "id": 1,
            "type": "result",
            "success": True,
            "result": {"runner_data": {"stt_binary_handler_id": 7}},
        },
        {
            "type": "event",
            "event": {"type": "stt-end", "data": {"stt_output": {"text": "turn off garage lights"}}},
        },
        {
            "type": "event",
            "event": {
                "type": "intent-end",
                "data": {
                    "intent_output": {
                        "conversation_id": "conversation-1",
                        "continue_conversation": True,
                        "response": {"speech": {"plain": {"speech": "Garage lights turned off"}}}
                    }
                },
            },
        },
        {
            "type": "event",
            "event": {
                "type": "tts-end",
                "data": {"tts_output": {"url": "/api/tts_proxy/reply.wav", "mime_type": "audio/wav"}},
            },
        },
        {"type": "event", "event": {"type": "run-end", "data": {}}},
    ]


def test_assist_streams_pcm_and_returns_spoken_response() -> None:
    socket = FakeSocket(assist_messages())
    client = HomeAssistantVoiceClient(
        HomeAssistantConfig(base_url="http://supervisor/core", token="token", verify_tls=False),
        websocket_connect=lambda *args, **kwargs: socket,
    )
    client._download_and_convert_tts = lambda url, mime: b"\x01\x02" * 400  # type: ignore[method-assign]
    audio = b"\x10\x00" * (MIN_AUDIO_BYTES // 2)

    result = client.run(audio, "N4-226290")

    assert result.transcript == "turn off garage lights"
    assert result.response_text == "Garage lights turned off"
    assert result.audio_pcm == b"\x01\x02" * 400
    assert result.conversation_id == "conversation-1"
    assert result.continue_conversation is True
    assert client._websocket_url() == "ws://supervisor/core/api/websocket"
    binary = [payload for payload in socket.sent if isinstance(payload, bytes)]
    assert binary[0][0] == 7
    assert b"".join(chunk[1:] for chunk in binary[:-1]) == audio
    assert binary[-1] == b"\x07"


def test_voice_response_wire_format_and_ascii_display_text() -> None:
    encoded = encode_voice_response(
        VoiceAssistantResult(
            transcript="Turn off the garage lights",
            response_text="Garage lights — turned off…",
            audio_pcm=b"\x00\x01\x02\x03",
        )
    )
    decoded = decode_voice_response(encoded)

    assert decoded.transcript == "Turn off the garage lights"
    assert decoded.response_text == "Garage lights - turned off..."
    assert decoded.audio_pcm == b"\x00\x01\x02\x03"
    assert decoded.sample_rate == 16_000
    assert display_text("It’s done") == "It's done"


def test_assist_reuses_and_resets_device_conversation() -> None:
    first_socket = FakeSocket(assist_messages())
    second_socket = FakeSocket(assist_messages())
    third_socket = FakeSocket(assist_messages())
    sockets = [first_socket, second_socket, third_socket]
    client = HomeAssistantVoiceClient(
        HomeAssistantConfig(base_url="http://supervisor/core", token="token", verify_tls=False),
        websocket_connect=lambda *args, **kwargs: sockets.pop(0),
    )
    client._download_and_convert_tts = lambda url, mime: b"\x00\x00"  # type: ignore[method-assign]
    audio = b"\x10\x00" * (MIN_AUDIO_BYTES // 2)

    client.run(audio, "N4-226290")
    client.run(audio, "N4-226290")
    client.reset_conversation("N4-226290")
    client.run(audio, "N4-226290")

    second_command = next(
        json.loads(payload)
        for payload in second_socket.sent
        if isinstance(payload, str) and json.loads(payload).get("type") == "assist_pipeline/run"
    )
    third_command = next(
        json.loads(payload)
        for payload in third_socket.sent
        if isinstance(payload, str) and json.loads(payload).get("type") == "assist_pipeline/run"
    )
    assert second_command["conversation_id"] == "conversation-1"
    assert "conversation_id" not in third_command


def test_assist_rejects_a_tap_and_surfaces_pipeline_errors() -> None:
    client = HomeAssistantVoiceClient(HomeAssistantConfig(token="token"))
    with pytest.raises(VoiceAssistantError, match="little longer"):
        client.run(b"\x00\x00" * 20)

    socket = FakeSocket(
        [
            {"type": "auth_required"},
            {"type": "auth_ok"},
            {
                "type": "event",
                "event": {"type": "error", "data": {"message": "No speech-to-text provider"}},
            },
        ]
    )
    failing = HomeAssistantVoiceClient(
        HomeAssistantConfig(token="token"),
        websocket_connect=lambda *args, **kwargs: socket,
    )
    with pytest.raises(VoiceAssistantError, match="No speech-to-text provider"):
        failing.run(b"\x00\x00" * (MIN_AUDIO_BYTES // 2))


def test_device_assist_endpoint_returns_framed_pcm(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token="token"),
    )
    app = create_app(config)
    app.state.voice_assistant.run = lambda audio, device_id: VoiceAssistantResult(  # type: ignore[method-assign]
        transcript="turn on the hall",
        response_text="Hall light turned on",
        audio_pcm=b"\x34\x12" * 100,
    )
    reset_devices: list[str] = []
    app.state.voice_assistant.reset_conversation = reset_devices.append  # type: ignore[method-assign]

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/devices/N4-226290/assist",
            content=b"\x00\x00" * (MIN_AUDIO_BYTES // 2),
            headers={
                "Content-Type": "application/octet-stream",
                "X-FlexDisplay-New-Conversation": "true",
            },
        )

    assert response.status_code == 200
    assert response.headers["X-FlexDisplay-Audio-Format"] == "pcm-s16le-16000-mono"
    decoded = decode_voice_response(response.content)
    assert decoded.response_text == "Hall light turned on"
    assert decoded.audio_pcm == b"\x34\x12" * 100
    assert reset_devices == ["N4-226290"]

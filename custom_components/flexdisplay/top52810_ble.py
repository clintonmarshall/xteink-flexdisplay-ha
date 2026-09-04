"""Home Assistant-owned BLE window transport for TOP52810 stock firmware."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bleak import BleakClient
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothCallbackReplay,
    BluetoothScanningMode,
)
from homeassistant.core import HomeAssistant, callback

from .api import FlexDisplayApiClient, FlexDisplayApiError
from .top52810_transport import (
    Top52810TransportError,
    execute_claimed_job,
    validate_advertisement,
)


LOGGER = logging.getLogger(__name__)
MANUFACTURER_ID = 0x1A28
SERVICE_UUID = "00000200-1212-efde-1523-785fef13d123"


class Top52810BleManager:
    """Claim a job only when its exact tag is observed through HA Bluetooth."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: FlexDisplayApiClient,
        executor_id: str,
    ) -> None:
        self._hass = hass
        self._api = api
        self._executor_id = executor_id
        self._unregister: Any = None
        self._active_addresses: set[str] = set()

    def start(self) -> None:
        """Register one passive callback; do not create a competing scanner."""
        self._unregister = bluetooth.async_register_callback(
            self._hass,
            self._advertisement,
            BluetoothCallbackMatcher(
                manufacturer_id=MANUFACTURER_ID,
                service_uuid=SERVICE_UUID,
                connectable=True,
            ),
            BluetoothScanningMode.ACTIVE,
            replay=BluetoothCallbackReplay.NEWEST_FIRST,
        )

    def stop(self) -> None:
        if self._unregister is not None:
            self._unregister()
            self._unregister = None

    @callback
    def _advertisement(self, service_info: Any, _change: Any) -> None:
        address = str(getattr(service_info, "address", "") or "").upper()
        if not address or address in self._active_addresses:
            return
        self._active_addresses.add(address)
        self._hass.async_create_task(
            self._handle_window(address, service_info),
            f"flexdisplay_top52810_{address.replace(':', '').lower()}",
        )

    async def _handle_window(self, address: str, service_info: Any) -> None:
        job: dict[str, Any] | None = None
        claimed: dict[str, Any] | None = None
        refresh_ack_received = False
        try:
            job = await self._api.pending_top52810_job(address)
            if not job:
                return
            validate_advertisement(job, service_info)
            claimed = await self._api.claim_top52810_job(
                str(job["job_id"]), self._executor_id
            )
            validate_advertisement(claimed, service_info)
            ble_device = bluetooth.async_ble_device_from_address(
                self._hass, address, connectable=True
            )
            if ble_device is None:
                raise Top52810TransportError("target was no longer connectable")
            client = await establish_connection(
                BleakClient,
                ble_device,
                str(claimed["expected_name"]),
                max_attempts=1,
            )
            try:
                if int(getattr(client, "mtu_size", 0) or 0) < 247:
                    raise Top52810TransportError("negotiated ATT MTU is below 247 bytes")
                service = client.services.get_service(str(claimed["service_uuid"]))
                if service is None:
                    raise Top52810TransportError("required GATT service is absent")
                write_characteristic = service.get_characteristic(
                    str(claimed["write_uuid"])
                )
                notify_characteristic = service.get_characteristic(
                    str(claimed["notify_uuid"])
                )
                if write_characteristic is None or "write" not in write_characteristic.properties:
                    raise Top52810TransportError("write-with-response characteristic is absent")
                if notify_characteristic is None or "notify" not in notify_characteristic.properties:
                    raise Top52810TransportError("notification characteristic is absent")
                await execute_claimed_job(client, claimed)
                refresh_ack_received = True
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    LOGGER.warning("TOP52810 disconnect cleanup failed for %s", address)
            await self._api.report_top52810_job(
                str(claimed["job_id"]),
                lease=str(claimed["lease"]),
                status="refresh_started",
                detail="exact refresh acknowledgement received",
            )
            await self._api.report_top52810_job(
                str(claimed["job_id"]),
                lease=str(claimed["lease"]),
                status="physically_unverified",
                detail="refresh acknowledgement received; visual verification required",
            )
        except Exception as err:
            # The broad final catch keeps an integration task failure from becoming
            # an implicit retry. It reports only when this executor owns a lease.
            LOGGER.warning("TOP52810 canary window failed for %s: %s", address, err)
            if claimed and claimed.get("lease"):
                try:
                    await self._api.report_top52810_job(
                        str(claimed["job_id"]),
                        lease=str(claimed["lease"]),
                        status=(
                            "physically_unverified"
                            if refresh_ack_received
                            else "failed"
                        ),
                        detail=(
                            "refresh acknowledgement received; status reporting was interrupted"
                            if refresh_ack_received
                            else str(err)
                        ),
                    )
                except FlexDisplayApiError:
                    LOGGER.exception("Could not report TOP52810 terminal failure")
        finally:
            self._active_addresses.discard(address)

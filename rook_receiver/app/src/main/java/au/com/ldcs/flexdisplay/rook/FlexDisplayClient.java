package au.com.ldcs.flexdisplay.rook;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraManager;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.BatteryManager;
import android.os.Build;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

final class FlexDisplayClient {
    static final String FIRMWARE_VERSION = "android-0.5.0";
    static final int MAX_CAMERA_SNAPSHOT_BYTES = 2 * 1024 * 1024;

    static final class Interaction {
        final String id;
        final String label;
        final int left;
        final int top;
        final int right;
        final int bottom;
        final boolean confirmation;
        final String confirmationText;

        Interaction(JSONObject value) throws JSONException {
            id = value.getString("id");
            label = value.optString("label", "Action");
            JSONObject bounds = value.getJSONObject("bounds");
            left = bounds.getInt("left");
            top = bounds.getInt("top");
            right = bounds.getInt("right");
            bottom = bounds.getInt("bottom");
            confirmation = value.optBoolean("confirmation", false);
            confirmationText = value.optString("confirmation_text", "Confirm this action?");
        }

        boolean contains(float x, float y) {
            return x >= left && x <= right && y >= top && y <= bottom;
        }
    }

    static final class NotificationAction {
        final String id;
        final String label;
        final boolean confirmation;
        final String confirmationText;

        NotificationAction(JSONObject value) throws JSONException {
            id = value.getString("id");
            label = value.optString("label", "Action");
            confirmation = value.optBoolean("confirmation", false);
            confirmationText = value.optString("confirmation_text", "Confirm this action?");
        }
    }

    static final class Notification {
        final String id;
        final String title;
        final String message;
        final String chime;
        final int duration;
        final boolean hasImage;
        final List<NotificationAction> actions;

        Notification(JSONObject value) throws JSONException {
            id = value.getString("id");
            title = value.optString("title", "Notification");
            message = value.optString("message", "");
            chime = value.optString("chime", "default");
            duration = Math.max(5, Math.min(300, value.optInt("duration", 20)));
            hasImage = value.optBoolean("has_image", false);
            JSONArray rawActions = value.optJSONArray("actions");
            List<NotificationAction> parsed = new ArrayList<>();
            if (rawActions != null) {
                for (int index = 0; index < rawActions.length(); index++) {
                    parsed.add(new NotificationAction(rawActions.getJSONObject(index)));
                }
            }
            actions = Collections.unmodifiableList(parsed);
        }
    }

    static final class NotificationEvent {
        final long sequence;
        final Notification notification;
        final String event;
        final boolean refresh;
        final String reason;

        NotificationEvent(
                long sequence,
                Notification notification,
                String event,
                boolean refresh,
                String reason) {
            this.sequence = sequence;
            this.notification = notification;
            this.event = event;
            this.refresh = refresh;
            this.reason = reason;
        }
    }

    static final class Result {
        final Bitmap bitmap;
        final Map<String, String> headers;
        final List<Interaction> interactions;

        Result(Bitmap bitmap, Map<String, String> headers, List<Interaction> interactions) {
            this.bitmap = bitmap;
            this.headers = headers;
            this.interactions = interactions;
        }

        String header(String name) {
            for (Map.Entry<String, String> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name)) return entry.getValue();
            }
            return "";
        }
    }

    static final class VoiceAssistantResponse {
        final int sampleRate;
        final String transcript;
        final String response;
        final byte[] audio;

        VoiceAssistantResponse(int sampleRate, String transcript, String response, byte[] audio) {
            this.sampleRate = sampleRate;
            this.transcript = transcript;
            this.response = response;
            this.audio = audio;
        }

        String summary() {
            String suffix = audio.length == 0 ? "\nNo Assist audio returned" : "";
            if (!response.isEmpty() && !transcript.isEmpty()) return transcript + "\n" + response + suffix;
            if (!response.isEmpty()) return response;
            if (!transcript.isEmpty()) return transcript;
            return audio.length == 0 ? "Assist completed" : "Assist response";
        }
    }

    private final Context context;
    private final ReceiverProfile profile;
    private volatile boolean foregroundAllowed = !BuildConfig.COMPANION;
    private volatile HttpURLConnection activeScreenConnection;
    private volatile HttpURLConnection activeNotificationConnection;

    FlexDisplayClient(Context context) {
        this.context = context.getApplicationContext();
        this.profile = ReceiverProfile.detect();
    }

    void setForegroundAllowed(boolean allowed) {
        foregroundAllowed = allowed || !BuildConfig.COMPANION;
        if (!foregroundAllowed) cancelForegroundRequests();
    }

    void cancelForegroundRequests() {
        HttpURLConnection screen = activeScreenConnection;
        if (screen != null) screen.disconnect();
        HttpURLConnection notification = activeNotificationConnection;
        if (notification != null) notification.disconnect();
    }

    Result fetch(
            ReceiverConfig config,
            String imageSha256,
            boolean imageCached,
            String commandResult,
            String commandId,
            String quickAction) throws IOException {
        requireForeground();
        HttpURLConnection connection = open(config, "/api/v1/screen", "GET", 25_000);
        activeScreenConnection = connection;
        connection.setRequestProperty("Accept", "image/png");
        connection.setRequestProperty("X-FlexDisplay-ID", config.deviceId);
        connection.setRequestProperty("X-FlexDisplay-Width", Integer.toString(profile.width));
        connection.setRequestProperty("X-FlexDisplay-Height", Integer.toString(profile.height));
        connection.setRequestProperty("X-FlexDisplay-Model", profile.model);
        connection.setRequestProperty("X-FlexDisplay-Firmware", FIRMWARE_VERSION);
        connection.setRequestProperty("X-FlexDisplay-Capabilities", deviceCapabilities());
        connection.setRequestProperty("X-FlexDisplay-Hardware-Manufacturer", safeHeader(Build.MANUFACTURER));
        connection.setRequestProperty("X-FlexDisplay-Hardware-Model", safeHeader(Build.MODEL));
        connection.setRequestProperty("X-FlexDisplay-Camera-Available", Boolean.toString(cameraAvailable()));
        connection.setRequestProperty("X-FlexDisplay-Camera-Permission", Boolean.toString(permissionGranted(android.Manifest.permission.CAMERA)));
        connection.setRequestProperty("X-FlexDisplay-Microphone-Available", Boolean.toString(microphoneAvailable()));
        connection.setRequestProperty("X-FlexDisplay-Microphone-Permission", Boolean.toString(permissionGranted(android.Manifest.permission.RECORD_AUDIO)));
        connection.setRequestProperty("X-FlexDisplay-Speaker-Available", Boolean.toString(speakerAvailable()));
        connection.setRequestProperty("X-FlexDisplay-Speaker-Permission", "true");
        connection.setRequestProperty("X-FlexDisplay-Audio-Available", "true");
        connection.setRequestProperty("X-FlexDisplay-Touch-Available", "true");
        connection.setRequestProperty("X-FlexDisplay-Always-On", Boolean.toString(!BuildConfig.COMPANION));
        connection.setRequestProperty("X-FlexDisplay-Device-Class", profile.deviceClass);
        AudioState audioState = audioState();
        connection.setRequestProperty("X-FlexDisplay-Volume", Integer.toString(audioState.volume));
        connection.setRequestProperty("X-FlexDisplay-Muted", Boolean.toString(audioState.muted));
        connection.setRequestProperty("X-FlexDisplay-Brightness", Integer.toString(screenBrightness()));
        connection.setRequestProperty(
                "X-FlexDisplay-Uptime-Seconds",
                Long.toString(SystemClock.elapsedRealtime() / 1000));
        connection.setRequestProperty(
                "X-FlexDisplay-Free-Heap",
                Long.toString(Runtime.getRuntime().freeMemory()));
        connection.setRequestProperty("X-FlexDisplay-Mode", "home_assistant");
        connection.setRequestProperty("X-FlexDisplay-SD-Ready", "false");
        connection.setRequestProperty("X-FlexDisplay-Wake-Reason", "android_kiosk");
        connection.setRequestProperty("X-FlexDisplay-Reset-Reason", "android_boot");
        connection.setRequestProperty("X-FlexDisplay-Boot-ID", Build.FINGERPRINT);
        connection.setRequestProperty("X-FlexDisplay-Battery-Percent", Integer.toString(batteryPercent()));
        connection.setRequestProperty("X-FlexDisplay-USB-Connected", Boolean.toString(isPluggedIn()));
        int rssi = wifiRssi();
        if (rssi != Integer.MIN_VALUE) connection.setRequestProperty("X-FlexDisplay-RSSI", Integer.toString(rssi));
        if (!imageSha256.isEmpty()) connection.setRequestProperty("X-FlexDisplay-Image-SHA256", imageSha256);
        connection.setRequestProperty("X-FlexDisplay-Image-Cached", Boolean.toString(imageCached));
        if (!commandResult.isEmpty()) connection.setRequestProperty("X-FlexDisplay-Command-Result", commandResult);
        if (!commandId.isEmpty()) connection.setRequestProperty("X-FlexDisplay-Command-ID", commandId);
        if (!quickAction.isEmpty()) connection.setRequestProperty("X-FlexDisplay-Quick-Action", quickAction);

        Map<String, String> headers;
        byte[] body;
        try {
            requireSuccess(connection);
            headers = responseHeaders(connection);
            body = readBytes(connection.getInputStream());
        } finally {
            connection.disconnect();
            if (activeScreenConnection == connection) activeScreenConnection = null;
        }
        Bitmap bitmap = body.length == 0 ? null : BitmapFactory.decodeByteArray(body, 0, body.length);
        if (body.length > 0 && bitmap == null) throw new IOException("Bridge response was not a supported image");
        requireForeground();
        return new Result(bitmap, Collections.unmodifiableMap(headers), fetchInteractions(config));
    }

    List<Interaction> fetchInteractions(ReceiverConfig config) throws IOException {
        requireForeground();
        HttpURLConnection connection = open(
                config,
                "/api/v1/devices/" + config.deviceId + "/interactions",
                "GET",
                10_000);
        activeScreenConnection = connection;
        requireSuccess(connection);
        try {
            JSONObject payload = new JSONObject(readText(connection.getInputStream()));
            JSONArray values = payload.optJSONArray("interactions");
            List<Interaction> result = new ArrayList<>();
            if (values != null) {
                for (int index = 0; index < values.length(); index++) {
                    result.add(new Interaction(values.getJSONObject(index)));
                }
            }
            return Collections.unmodifiableList(result);
        } catch (JSONException error) {
            throw new IOException("Bridge returned invalid interaction data", error);
        } finally {
            connection.disconnect();
            if (activeScreenConnection == connection) activeScreenConnection = null;
        }
    }

    String performInteraction(ReceiverConfig config, String actionId, boolean confirmed) throws IOException {
        return postJson(
                config,
                "/api/v1/devices/" + config.deviceId + "/interactions/" + actionId,
                confirmationPayload(confirmed));
    }

    NotificationEvent waitForNotification(ReceiverConfig config, long after) throws IOException {
        requireForeground();
        HttpURLConnection connection = open(
                config,
                "/api/v1/devices/" + config.deviceId + "/notifications/next?after=" + after + "&timeout=25",
                "GET",
                35_000);
        activeNotificationConnection = connection;
        try {
            requireSuccess(connection);
            JSONObject payload = new JSONObject(readText(connection.getInputStream()));
            long sequence = payload.optLong("sequence", after);
            JSONObject value = payload.optJSONObject("notification");
            return new NotificationEvent(
                    sequence,
                    value == null ? null : new Notification(value),
                    payload.optString("event", ""),
                    payload.optBoolean("refresh", false),
                    payload.optString("reason", ""));
        } catch (JSONException error) {
            throw new IOException("Bridge returned invalid notification data", error);
        } finally {
            connection.disconnect();
            if (activeNotificationConnection == connection) activeNotificationConnection = null;
        }
    }

    void uploadCameraSnapshot(
            ReceiverConfig config,
            byte[] jpeg,
            String facing,
            String commandId) throws IOException {
        requireForeground();
        if (commandId == null || commandId.trim().isEmpty()) {
            throw new IOException("Camera snapshot command ID is missing");
        }
        if (jpeg == null || jpeg.length < 2 || jpeg.length > MAX_CAMERA_SNAPSHOT_BYTES) {
            throw new IOException("Camera snapshot is empty or exceeds 2 MiB");
        }
        if ((jpeg[0] & 0xff) != 0xff || (jpeg[1] & 0xff) != 0xd8) {
            throw new IOException("Camera snapshot is not JPEG data");
        }
        HttpURLConnection connection = open(
                config,
                "/api/v1/devices/" + config.deviceId + "/camera/snapshot",
                "PUT",
                30_000);
        activeScreenConnection = connection;
        connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/octet-stream");
        connection.setRequestProperty("X-FlexDisplay-Camera-Facing", safeHeader(facing));
        connection.setRequestProperty("X-FlexDisplay-Command-ID", safeHeader(commandId));
        connection.setFixedLengthStreamingMode(jpeg.length);
        try {
            try (OutputStream output = connection.getOutputStream()) {
                output.write(jpeg);
            }
            requireSuccess(connection);
            readBytes(connection.getInputStream());
        } finally {
            connection.disconnect();
            if (activeScreenConnection == connection) activeScreenConnection = null;
        }
    }

    Bitmap fetchNotificationImage(ReceiverConfig config, String notificationId) throws IOException {
        HttpURLConnection connection = open(
                config,
                "/api/v1/devices/" + config.deviceId + "/notifications/" + notificationId + "/image",
                "GET",
                20_000);
        requireSuccess(connection);
        byte[] body = readBytes(connection.getInputStream());
        connection.disconnect();
        Bitmap bitmap = BitmapFactory.decodeByteArray(body, 0, body.length);
        if (bitmap == null) throw new IOException("Notification image was unreadable");
        return bitmap;
    }

    void dismissNotification(ReceiverConfig config, String notificationId) throws IOException {
        postJson(
                config,
                "/api/v1/devices/" + config.deviceId + "/notifications/" + notificationId + "/dismiss",
                new JSONObject());
    }

    String performNotificationAction(
            ReceiverConfig config,
            String notificationId,
            String actionId,
            boolean confirmed) throws IOException {
        return postJson(
                config,
                "/api/v1/devices/" + config.deviceId + "/notifications/" + notificationId
                        + "/actions/" + actionId,
                confirmationPayload(confirmed));
    }

    VoiceAssistantResponse runAssist(
            ReceiverConfig config,
            byte[] audio,
            boolean newConversation) throws IOException {
        HttpURLConnection connection = open(
                config,
                "/api/v1/devices/" + config.deviceId + "/assist",
                "POST",
                60_000);
        connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/octet-stream");
        connection.setRequestProperty("Accept", "application/octet-stream");
        connection.setRequestProperty(
                "X-FlexDisplay-New-Conversation",
                Boolean.toString(newConversation));
        connection.setFixedLengthStreamingMode(audio.length);
        try (OutputStream output = connection.getOutputStream()) {
            output.write(audio);
        }
        requireSuccess(connection);
        try {
            return decodeVoiceResponse(readBytes(connection.getInputStream()));
        } finally {
            connection.disconnect();
        }
    }

    private HttpURLConnection open(
            ReceiverConfig config, String path, String method, int readTimeout) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(config.bridgeUrl + path).openConnection();
        connection.setConnectTimeout(10_000);
        connection.setReadTimeout(readTimeout);
        connection.setRequestMethod(method);
        connection.setRequestProperty("X-FlexDisplay-Receiver-Token", config.receiverToken);
        return connection;
    }

    private void requireForeground() throws IOException {
        if (BuildConfig.COMPANION && !foregroundAllowed) {
            throw new IOException("Companion receiver is in the background");
        }
    }

    private static JSONObject confirmationPayload(boolean confirmed) throws IOException {
        try {
            return new JSONObject().put("confirmed", confirmed);
        } catch (JSONException error) {
            throw new IOException("Could not encode action confirmation", error);
        }
    }

    private String postJson(ReceiverConfig config, String path, JSONObject payload) throws IOException {
        HttpURLConnection connection = open(config, path, "POST", 20_000);
        connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/json");
        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        connection.setFixedLengthStreamingMode(body.length);
        try (OutputStream output = connection.getOutputStream()) {
            output.write(body);
        }
        requireSuccess(connection);
        try {
            JSONObject response = new JSONObject(readText(connection.getInputStream()));
            return response.optString("detail", "Done");
        } catch (JSONException error) {
            return "Done";
        } finally {
            connection.disconnect();
        }
    }

    private static void requireSuccess(HttpURLConnection connection) throws IOException {
        int status = connection.getResponseCode();
        if (status >= 200 && status < 300) return;
        String detail = readText(connection.getErrorStream());
        connection.disconnect();
        throw new IOException("Bridge returned " + status + (detail.isEmpty() ? "" : ": " + detail));
    }

    private static Map<String, String> responseHeaders(HttpURLConnection connection) {
        Map<String, String> headers = new LinkedHashMap<>();
        for (Map.Entry<String, List<String>> entry : connection.getHeaderFields().entrySet()) {
            if (entry.getKey() != null && entry.getValue() != null && !entry.getValue().isEmpty()) {
                headers.put(entry.getKey(), entry.getValue().get(0));
            }
        }
        return headers;
    }

    private int batteryPercent() {
        BatteryManager manager = (BatteryManager) context.getSystemService(Context.BATTERY_SERVICE);
        int value = manager == null ? -1 : manager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
        return Math.max(0, Math.min(100, value));
    }

    private boolean isPluggedIn() {
        Intent battery = context.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        return battery != null && battery.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0;
    }

    @SuppressWarnings("deprecation")
    private int wifiRssi() {
        WifiManager manager = (WifiManager) context.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        WifiInfo info = manager == null ? null : manager.getConnectionInfo();
        return info == null ? Integer.MIN_VALUE : info.getRssi();
    }

    private AudioState audioState() {
        android.media.AudioManager manager =
                (android.media.AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        if (manager == null) return new AudioState(0, false);
        int max = Math.max(1, manager.getStreamMaxVolume(android.media.AudioManager.STREAM_MUSIC));
        int current = manager.getStreamVolume(android.media.AudioManager.STREAM_MUSIC);
        return new AudioState(
                Math.max(0, Math.min(100, Math.round(current * 100f / max))),
                current == 0);
    }

    private int screenBrightness() {
        try {
            int raw = android.provider.Settings.System.getInt(
                    context.getContentResolver(),
                    android.provider.Settings.System.SCREEN_BRIGHTNESS);
            return Math.max(0, Math.min(100, raw * 100 / 255));
        } catch (android.provider.Settings.SettingNotFoundException error) {
            return 100;
        }
    }

    private String deviceCapabilities() {
        String result = profile.capabilities();
        if (!BuildConfig.COMPANION) return result;
        if (cameraAvailable()) result += ",camera";
        if (microphoneAvailable()) result += ",microphone";
        if (speakerAvailable()) result += ",speaker";
        return result;
    }

    private boolean cameraAvailable() {
        if (!context.getPackageManager().hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)) return false;
        CameraManager manager = (CameraManager) context.getSystemService(Context.CAMERA_SERVICE);
        try {
            return manager != null && manager.getCameraIdList().length > 0;
        } catch (CameraAccessException | SecurityException error) {
            return false;
        }
    }

    private boolean microphoneAvailable() {
        return context.getPackageManager().hasSystemFeature(PackageManager.FEATURE_MICROPHONE);
    }

    private boolean speakerAvailable() {
        return context.getPackageManager().hasSystemFeature("android.hardware.audio.output");
    }

    private boolean permissionGranted(String permission) {
        return context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    private static String safeHeader(String value) {
        return value == null ? "" : value.replace("\r", " ").replace("\n", " ").trim();
    }

    private static final class AudioState {
        final int volume;
        final boolean muted;

        AudioState(int volume, boolean muted) {
            this.volume = volume;
            this.muted = muted;
        }
    }

    private static byte[] readBytes(InputStream input) throws IOException {
        if (input == null) return new byte[0];
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = stream.read(buffer)) >= 0) output.write(buffer, 0, read);
            return output.toByteArray();
        }
    }

    private static String readText(InputStream input) throws IOException {
        if (input == null) return "";
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(input, StandardCharsets.UTF_8))) {
            StringBuilder result = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) result.append(line);
            return result.toString();
        }
    }

    private static VoiceAssistantResponse decodeVoiceResponse(byte[] body) throws IOException {
        if (body.length < 4) throw new IOException("Bridge returned an empty Assist response");
        String magic = new String(body, 0, 4, StandardCharsets.US_ASCII);
        ByteBuffer header = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN);
        if ("FVA2".equals(magic)) {
            if (body.length < 20) throw new IOException("Bridge returned a truncated Assist response");
            header.position(4);
            int sampleRate = header.getInt();
            int transcriptLength = header.getInt();
            int responseLength = header.getInt();
            int audioLength = header.getInt();
            int offset = 20;
            requirePayloadLength(body.length, offset, transcriptLength, responseLength, audioLength);
            String transcript = new String(body, offset, transcriptLength, StandardCharsets.UTF_8);
            offset += transcriptLength;
            String response = new String(body, offset, responseLength, StandardCharsets.UTF_8);
            offset += responseLength;
            byte[] audio = new byte[audioLength];
            System.arraycopy(body, offset, audio, 0, audioLength);
            return new VoiceAssistantResponse(sampleRate, transcript, response, audio);
        }
        if ("FVA1".equals(magic)) {
            if (body.length < 16) throw new IOException("Bridge returned a truncated Assist response");
            header.position(4);
            int sampleRate = header.getInt();
            int textLength = header.getInt();
            int audioLength = header.getInt();
            int offset = 16;
            requirePayloadLength(body.length, offset, textLength, audioLength);
            String response = new String(body, offset, textLength, StandardCharsets.UTF_8);
            offset += textLength;
            byte[] audio = new byte[audioLength];
            System.arraycopy(body, offset, audio, 0, audioLength);
            return new VoiceAssistantResponse(sampleRate, "", response, audio);
        }
        throw new IOException("Bridge returned an unknown Assist payload");
    }

    private static void requirePayloadLength(int bodyLength, int offset, int... lengths) throws IOException {
        long required = offset;
        for (int length : lengths) {
            if (length < 0) throw new IOException("Bridge returned an invalid Assist payload");
            required += length;
        }
        if (required > bodyLength) throw new IOException("Bridge returned a truncated Assist payload");
    }
}

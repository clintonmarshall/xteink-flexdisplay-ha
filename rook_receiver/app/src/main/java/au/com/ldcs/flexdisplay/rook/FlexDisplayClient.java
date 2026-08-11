package au.com.ldcs.flexdisplay.rook;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
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
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

final class FlexDisplayClient {
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

        NotificationEvent(long sequence, Notification notification) {
            this.sequence = sequence;
            this.notification = notification;
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

    private final Context context;

    FlexDisplayClient(Context context) {
        this.context = context.getApplicationContext();
    }

    Result fetch(
            ReceiverConfig config,
            String imageSha256,
            boolean imageCached,
            String commandResult,
            String commandId,
            String quickAction) throws IOException {
        HttpURLConnection connection = open(config, "/api/v1/screen", "GET", 25_000);
        connection.setRequestProperty("Accept", "image/png");
        connection.setRequestProperty("X-FlexDisplay-ID", config.deviceId);
        connection.setRequestProperty("X-FlexDisplay-Width", "480");
        connection.setRequestProperty("X-FlexDisplay-Height", "480");
        connection.setRequestProperty("X-FlexDisplay-Model", "ROOK");
        connection.setRequestProperty("X-FlexDisplay-Firmware", "android-0.2.0");
        connection.setRequestProperty(
                "X-FlexDisplay-Capabilities",
                "android,color,touch,round-display,png,empty-unchanged,kiosk,interactions,notifications,audio");
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

        requireSuccess(connection);
        Map<String, String> headers = responseHeaders(connection);
        byte[] body = readBytes(connection.getInputStream());
        connection.disconnect();
        Bitmap bitmap = body.length == 0 ? null : BitmapFactory.decodeByteArray(body, 0, body.length);
        if (body.length > 0 && bitmap == null) throw new IOException("Bridge response was not a supported image");
        return new Result(bitmap, Collections.unmodifiableMap(headers), fetchInteractions(config));
    }

    List<Interaction> fetchInteractions(ReceiverConfig config) throws IOException {
        HttpURLConnection connection = open(
                config,
                "/api/v1/devices/" + config.deviceId + "/interactions",
                "GET",
                10_000);
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
        }
    }

    String performInteraction(ReceiverConfig config, String actionId, boolean confirmed) throws IOException {
        return postJson(
                config,
                "/api/v1/devices/" + config.deviceId + "/interactions/" + actionId,
                confirmationPayload(confirmed));
    }

    NotificationEvent waitForNotification(ReceiverConfig config, long after) throws IOException {
        HttpURLConnection connection = open(
                config,
                "/api/v1/devices/" + config.deviceId + "/notifications/next?after=" + after + "&timeout=25",
                "GET",
                35_000);
        requireSuccess(connection);
        try {
            JSONObject payload = new JSONObject(readText(connection.getInputStream()));
            long sequence = payload.optLong("sequence", after);
            JSONObject value = payload.optJSONObject("notification");
            return new NotificationEvent(sequence, value == null ? null : new Notification(value));
        } catch (JSONException error) {
            throw new IOException("Bridge returned invalid notification data", error);
        } finally {
            connection.disconnect();
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

    private HttpURLConnection open(
            ReceiverConfig config, String path, String method, int readTimeout) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(config.bridgeUrl + path).openConnection();
        connection.setConnectTimeout(10_000);
        connection.setReadTimeout(readTimeout);
        connection.setRequestMethod(method);
        connection.setRequestProperty("X-FlexDisplay-Receiver-Token", config.receiverToken);
        return connection;
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
}

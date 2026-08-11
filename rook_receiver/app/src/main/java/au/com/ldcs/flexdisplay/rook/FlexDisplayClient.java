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

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

final class FlexDisplayClient {
    static final class Result {
        final Bitmap bitmap;
        final Map<String, String> headers;

        Result(Bitmap bitmap, Map<String, String> headers) {
            this.bitmap = bitmap;
            this.headers = headers;
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
        HttpURLConnection connection = (HttpURLConnection) new URL(
                config.bridgeUrl + "/api/v1/screen").openConnection();
        connection.setConnectTimeout(10_000);
        connection.setReadTimeout(25_000);
        connection.setRequestMethod("GET");
        connection.setRequestProperty("Accept", "image/png");
        connection.setRequestProperty("X-FlexDisplay-ID", config.deviceId);
        connection.setRequestProperty("X-FlexDisplay-Width", "480");
        connection.setRequestProperty("X-FlexDisplay-Height", "480");
        connection.setRequestProperty("X-FlexDisplay-Model", "ROOK");
        connection.setRequestProperty("X-FlexDisplay-Firmware", "android-0.1.0");
        connection.setRequestProperty(
                "X-FlexDisplay-Capabilities",
                "android,color,touch,round-display,png,empty-unchanged,kiosk");
        connection.setRequestProperty(
                "X-FlexDisplay-Uptime-Seconds",
                Long.toString(SystemClock.elapsedRealtime() / 1000));
        connection.setRequestProperty(
                "X-FlexDisplay-Free-Heap",
                Long.toString(Runtime.getRuntime().freeMemory()));
        connection.setRequestProperty("X-FlexDisplay-Mode", "home_assistant");
        // Rook has no FlexDisplay firmware/SD-card update path. Reporting this
        // truthfully also makes older Bridges fail closed instead of offering
        // the X3/X4 firmware image before model-aware OTA gating is deployed.
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

        int status = connection.getResponseCode();
        if (status < 200 || status >= 300) {
            String detail = readText(connection.getErrorStream());
            connection.disconnect();
            throw new IOException("Bridge returned " + status + (detail.isEmpty() ? "" : ": " + detail));
        }

        Map<String, String> headers = new LinkedHashMap<>();
        for (Map.Entry<String, List<String>> entry : connection.getHeaderFields().entrySet()) {
            if (entry.getKey() != null && entry.getValue() != null && !entry.getValue().isEmpty()) {
                headers.put(entry.getKey(), entry.getValue().get(0));
            }
        }
        byte[] body = readBytes(connection.getInputStream());
        connection.disconnect();
        Bitmap bitmap = body.length == 0 ? null : BitmapFactory.decodeByteArray(body, 0, body.length);
        if (body.length > 0 && bitmap == null) throw new IOException("Bridge response was not a supported image");
        return new Result(bitmap, Collections.unmodifiableMap(headers));
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
        return new String(readBytes(input));
    }
}

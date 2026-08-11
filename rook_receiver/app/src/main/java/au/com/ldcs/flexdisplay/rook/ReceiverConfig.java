package au.com.ldcs.flexdisplay.rook;

import android.content.Context;
import android.content.SharedPreferences;
import android.provider.Settings;

import java.util.Locale;

final class ReceiverConfig {
    private static final String PREFS = "flexdisplay_rook";
    final String bridgeUrl;
    final String deviceId;

    ReceiverConfig(String bridgeUrl, String deviceId) {
        this.bridgeUrl = normalizeUrl(bridgeUrl);
        this.deviceId = deviceId == null ? "" : deviceId.trim().toUpperCase(Locale.ROOT);
    }

    static ReceiverConfig load(Context context) {
        SharedPreferences preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String androidId = Settings.Secure.getString(
                context.getContentResolver(), Settings.Secure.ANDROID_ID);
        if (androidId == null || androidId.length() < 8) androidId = "00000000";
        String generatedId = "ROOK-" + androidId.substring(androidId.length() - 8).toUpperCase(Locale.ROOT);
        return new ReceiverConfig(
                preferences.getString("bridge_url", ""),
                preferences.getString("device_id", generatedId));
    }

    void save(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit()
                .putString("bridge_url", bridgeUrl)
                .putString("device_id", deviceId)
                .apply();
    }

    boolean isReady() {
        return !bridgeUrl.isEmpty() && deviceId.matches("[A-Z0-9][A-Z0-9_-]{2,63}");
    }

    private static String normalizeUrl(String value) {
        String selected = value == null ? "" : value.trim();
        while (selected.endsWith("/")) selected = selected.substring(0, selected.length() - 1);
        return selected;
    }
}

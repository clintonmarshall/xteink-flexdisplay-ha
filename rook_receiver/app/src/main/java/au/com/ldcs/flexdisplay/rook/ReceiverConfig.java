package au.com.ldcs.flexdisplay.rook;

import android.content.Context;
import android.content.SharedPreferences;
import android.provider.Settings;

import java.util.Locale;
import java.util.UUID;

final class ReceiverConfig {
    private static final String PREFS = "flexdisplay_rook";
    final String bridgeUrl;
    final String deviceId;
    final String receiverToken;
    final boolean microphoneEnabled;

    ReceiverConfig(String bridgeUrl, String deviceId) {
        this(bridgeUrl, deviceId, UUID.randomUUID().toString(), !BuildConfig.COMPANION);
    }

    ReceiverConfig(String bridgeUrl, String deviceId, String receiverToken) {
        this(bridgeUrl, deviceId, receiverToken, !BuildConfig.COMPANION);
    }

    ReceiverConfig(String bridgeUrl, String deviceId, String receiverToken, boolean microphoneEnabled) {
        this.bridgeUrl = normalizeUrl(bridgeUrl);
        this.deviceId = deviceId == null ? "" : deviceId.trim().toUpperCase(Locale.ROOT);
        this.receiverToken = receiverToken == null || receiverToken.length() < 20
                ? UUID.randomUUID().toString()
                : receiverToken;
        this.microphoneEnabled = microphoneEnabled;
    }

    static ReceiverConfig load(Context context) {
        ReceiverProfile profile = ReceiverProfile.detect();
        SharedPreferences preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String androidId = Settings.Secure.getString(
                context.getContentResolver(), Settings.Secure.ANDROID_ID);
        if (androidId == null || androidId.length() < 8) androidId = "00000000";
        String generatedId = profile.idPrefix + "-" + androidId.substring(androidId.length() - 8).toUpperCase(Locale.ROOT);
        ReceiverConfig selected = new ReceiverConfig(
                preferences.getString("bridge_url", ""),
                preferences.getString("device_id", generatedId),
                preferences.getString("receiver_token", ""),
                preferences.getBoolean("microphone_enabled", !BuildConfig.COMPANION));
        selected.save(context);
        return selected;
    }

    void save(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit()
                .putString("bridge_url", bridgeUrl)
                .putString("device_id", deviceId)
                .putString("receiver_token", receiverToken)
                .putBoolean("microphone_enabled", microphoneEnabled)
                .apply();
    }

    ReceiverConfig withMicrophoneEnabled(boolean enabled) {
        return new ReceiverConfig(bridgeUrl, deviceId, receiverToken, enabled);
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

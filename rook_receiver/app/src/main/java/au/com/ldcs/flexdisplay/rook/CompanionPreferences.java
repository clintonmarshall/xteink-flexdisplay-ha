package au.com.ldcs.flexdisplay.rook;

import android.content.Context;
import android.content.SharedPreferences;

final class CompanionPreferences {
    static final String CAMERA_POLICY_OFF = "off";
    static final String CAMERA_POLICY_ALLOW_WHILE_OPEN = "allow_while_open";
    private static final String PREFS = "flexdisplay_companion_privacy";
    private static final String CAMERA_POLICY = "camera_policy";
    private static final String DOCK_MODE = "dock_mode";
    private static final String LAST_CAMERA_REQUEST_AT = "last_camera_request_at";
    private static final String LAST_CAMERA_OUTCOME = "last_camera_outcome";
    private static final String LAST_CAMERA_OUTCOME_AT = "last_camera_outcome_at";
    private static final String LAST_SYNC_AT = "last_sync_at";
    private static final String LAST_ERROR_AT = "last_error_at";
    private static final String LAST_ERROR = "last_error";

    private CompanionPreferences() { }

    static String cameraPolicy(Context context) {
        String value = preferences(context).getString(CAMERA_POLICY, CAMERA_POLICY_OFF);
        return CAMERA_POLICY_ALLOW_WHILE_OPEN.equals(value)
                ? CAMERA_POLICY_ALLOW_WHILE_OPEN
                : CAMERA_POLICY_OFF;
    }

    static void setCameraPolicy(Context context, String policy) {
        preferences(context).edit().putString(
                CAMERA_POLICY,
                CAMERA_POLICY_ALLOW_WHILE_OPEN.equals(policy)
                        ? CAMERA_POLICY_ALLOW_WHILE_OPEN
                        : CAMERA_POLICY_OFF).apply();
    }

    static boolean dockMode(Context context) {
        return preferences(context).getBoolean(DOCK_MODE, false);
    }

    static void setDockMode(Context context, boolean enabled) {
        preferences(context).edit().putBoolean(DOCK_MODE, enabled).apply();
    }

    static void recordCameraRequest(Context context) {
        preferences(context).edit()
                .putLong(LAST_CAMERA_REQUEST_AT, System.currentTimeMillis())
                .putString(LAST_CAMERA_OUTCOME, "Pending")
                .remove(LAST_CAMERA_OUTCOME_AT)
                .apply();
    }

    static void recordCameraOutcome(Context context, String outcome) {
        preferences(context).edit()
                .putString(LAST_CAMERA_OUTCOME, bounded(outcome))
                .putLong(LAST_CAMERA_OUTCOME_AT, System.currentTimeMillis())
                .apply();
    }

    static void recordSync(Context context) {
        preferences(context).edit()
                .putLong(LAST_SYNC_AT, System.currentTimeMillis())
                .remove(LAST_ERROR)
                .remove(LAST_ERROR_AT)
                .apply();
    }

    static void recordError(Context context, String error) {
        preferences(context).edit()
                .putLong(LAST_ERROR_AT, System.currentTimeMillis())
                .putString(LAST_ERROR, bounded(error))
                .apply();
    }

    static long lastCameraRequestAt(Context context) {
        return preferences(context).getLong(LAST_CAMERA_REQUEST_AT, 0L);
    }

    static String lastCameraOutcome(Context context) {
        return preferences(context).getString(LAST_CAMERA_OUTCOME, "No camera request recorded");
    }

    static long lastCameraOutcomeAt(Context context) {
        return preferences(context).getLong(LAST_CAMERA_OUTCOME_AT, 0L);
    }

    static long lastSyncAt(Context context) {
        return preferences(context).getLong(LAST_SYNC_AT, 0L);
    }

    static long lastErrorAt(Context context) {
        return preferences(context).getLong(LAST_ERROR_AT, 0L);
    }

    static String lastError(Context context) {
        return preferences(context).getString(LAST_ERROR, "");
    }

    private static SharedPreferences preferences(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static String bounded(String value) {
        String selected = value == null ? "" : value.replace('\n', ' ').replace('\r', ' ').trim();
        return selected.length() > 160 ? selected.substring(0, 160) : selected;
    }
}

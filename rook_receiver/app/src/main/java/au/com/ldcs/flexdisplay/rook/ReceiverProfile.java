package au.com.ldcs.flexdisplay.rook;

import android.os.Build;

import java.util.Locale;

final class ReceiverProfile {
    final String model;
    final String label;
    final String idPrefix;
    final int width;
    final int height;
    final boolean round;
    final String deviceClass;

    private ReceiverProfile(
            String model,
            String label,
            String idPrefix,
            int width,
            int height,
            boolean round,
            String deviceClass) {
        this.model = model;
        this.label = label;
        this.idPrefix = idPrefix;
        this.width = width;
        this.height = height;
        this.round = round;
        this.deviceClass = deviceClass;
    }

    static ReceiverProfile detect() {
        String device = value(Build.DEVICE);
        String product = value(Build.PRODUCT);
        String model = value(Build.MODEL);
        String fingerprint = value(Build.FINGERPRINT);
        String identity = device + " " + product + " " + model + " " + fingerprint;
        if (identity.contains("checkers")) {
            return new ReceiverProfile(
                    "CHECKERS",
                    "Echo Show 5",
                    "CHECKERS",
                    960,
                    480,
                    false,
                    "echo_show_5");
        }
        if (identity.contains("rook") || !BuildConfig.COMPANION) {
            return new ReceiverProfile(
                    "ROOK",
                    "Echo Spot",
                    "ROOK",
                    480,
                    480,
                    true,
                    "echo_spot");
        }
        return new ReceiverProfile(
                "ANDROID",
                "Android phone",
                "PHONE",
                1200,
                675,
                false,
                "android_phone");
    }

    String capabilities() {
        if (BuildConfig.COMPANION) {
            return "android,color,touch,png,empty-unchanged,interactions,notifications,audio,assist,long-poll-refresh,companion,battery,usb";
        }
        String base = "android,color,touch,png,empty-unchanged,kiosk,interactions,notifications,audio,assist,always-on-color,long-poll-refresh";
        return round ? base + ",round-display" : base;
    }

    private static String value(String selected) {
        return (selected == null ? "" : selected).toLowerCase(Locale.ROOT);
    }
}

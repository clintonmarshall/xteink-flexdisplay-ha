package au.com.ldcs.flexdisplay.rook;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.BatteryManager;

import java.util.Locale;

final class BatteryTelemetry {
    final boolean present;
    final int percent;
    final boolean charging;
    final String status;
    final String health;
    final Float temperatureC;
    final Integer voltageMv;
    final String plugType;
    final Float currentMa;

    private BatteryTelemetry(
            boolean present,
            int percent,
            boolean charging,
            String status,
            String health,
            Float temperatureC,
            Integer voltageMv,
            String plugType,
            Float currentMa) {
        this.present = present;
        this.percent = percent;
        this.charging = charging;
        this.status = status;
        this.health = health;
        this.temperatureC = temperatureC;
        this.voltageMv = voltageMv;
        this.plugType = plugType;
        this.currentMa = currentMa;
    }

    static BatteryTelemetry read(Context context) {
        Intent battery = context.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (battery == null) return missing();
        BatteryManager manager = (BatteryManager) context.getSystemService(Context.BATTERY_SERVICE);
        int currentMicroamps = manager == null
                ? Integer.MIN_VALUE
                : manager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW);
        return from(
                battery.getIntExtra(BatteryManager.EXTRA_LEVEL, -1),
                battery.getIntExtra(BatteryManager.EXTRA_SCALE, -1),
                battery.getIntExtra(BatteryManager.EXTRA_STATUS, BatteryManager.BATTERY_STATUS_UNKNOWN),
                battery.getIntExtra(BatteryManager.EXTRA_HEALTH, BatteryManager.BATTERY_HEALTH_UNKNOWN),
                battery.hasExtra(BatteryManager.EXTRA_TEMPERATURE)
                        ? battery.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) : null,
                battery.hasExtra(BatteryManager.EXTRA_VOLTAGE)
                        ? battery.getIntExtra(BatteryManager.EXTRA_VOLTAGE, 0) : null,
                battery.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0),
                currentMicroamps);
    }

    static BatteryTelemetry missing() {
        return new BatteryTelemetry(false, -1, false, "unknown", "unknown", null, null, "unknown", null);
    }

    static BatteryTelemetry from(
            int level,
            int scale,
            int rawStatus,
            int rawHealth,
            Integer temperatureTenthsC,
            Integer voltageMv,
            int plugged,
            int currentMicroamps) {
        int percent = level < 0 || scale <= 0
                ? -1
                : Math.max(0, Math.min(100, Math.round(level * 100f / scale)));
        String status = statusName(rawStatus);
        String health = healthName(rawHealth);
        String plugType = plugName(plugged);
        boolean charging = rawStatus == BatteryManager.BATTERY_STATUS_CHARGING
                || rawStatus == BatteryManager.BATTERY_STATUS_FULL;
        Float currentMa = currentMicroamps == Integer.MIN_VALUE
                ? null
                : currentMicroamps / 1000f;
        return new BatteryTelemetry(
                true,
                percent,
                charging,
                status,
                health,
                temperatureTenthsC == null ? null : temperatureTenthsC / 10f,
                voltageMv == null || voltageMv <= 0 ? null : voltageMv,
                plugType,
                currentMa);
    }

    boolean usbConnected() {
        return "usb".equals(plugType);
    }

    boolean dockPowered() {
        return present && !"none".equals(plugType) && !"unknown".equals(plugType);
    }

    String temperatureHeader() {
        return temperatureC == null ? "" : String.format(Locale.US, "%.1f", temperatureC);
    }

    String currentHeader() {
        return currentMa == null ? "" : String.format(Locale.US, "%.1f", currentMa);
    }

    private static String statusName(int status) {
        switch (status) {
            case BatteryManager.BATTERY_STATUS_CHARGING:
                return "charging";
            case BatteryManager.BATTERY_STATUS_DISCHARGING:
                return "discharging";
            case BatteryManager.BATTERY_STATUS_FULL:
                return "full";
            case BatteryManager.BATTERY_STATUS_NOT_CHARGING:
                return "not_charging";
            default:
                return "unknown";
        }
    }

    private static String healthName(int health) {
        switch (health) {
            case BatteryManager.BATTERY_HEALTH_GOOD:
                return "good";
            case BatteryManager.BATTERY_HEALTH_OVERHEAT:
                return "overheat";
            case BatteryManager.BATTERY_HEALTH_DEAD:
                return "dead";
            case BatteryManager.BATTERY_HEALTH_OVER_VOLTAGE:
                return "over_voltage";
            case BatteryManager.BATTERY_HEALTH_UNSPECIFIED_FAILURE:
                return "unspecified_failure";
            case BatteryManager.BATTERY_HEALTH_COLD:
                return "cold";
            default:
                return "unknown";
        }
    }

    private static String plugName(int plugged) {
        if ((plugged & BatteryManager.BATTERY_PLUGGED_USB) != 0) return "usb";
        if ((plugged & BatteryManager.BATTERY_PLUGGED_AC) != 0) return "ac";
        if ((plugged & BatteryManager.BATTERY_PLUGGED_WIRELESS) != 0) return "wireless";
        if ((plugged & BatteryManager.BATTERY_PLUGGED_DOCK) != 0) return "dock";
        return plugged == 0 ? "none" : "unknown";
    }
}

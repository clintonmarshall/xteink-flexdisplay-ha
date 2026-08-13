package au.com.ldcs.flexdisplay.rook;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import android.os.BatteryManager;

import org.junit.Test;

public final class BatteryTelemetryTest {
    @Test
    public void missingStickyIntentOmitsEntireFamily() {
        BatteryTelemetry value = BatteryTelemetry.missing();

        assertFalse(value.present);
        assertEquals(-1, value.percent);
        assertEquals("unknown", value.plugType);
        assertEquals("", value.temperatureHeader());
        assertEquals("", value.currentHeader());
        assertNull(value.voltageMv);
        assertFalse(value.dockPowered());
    }

    @Test
    public void invalidScaleKeepsPercentUnknown() {
        BatteryTelemetry value = telemetry(45, 0, BatteryManager.BATTERY_STATUS_DISCHARGING, 0, 0);

        assertTrue(value.present);
        assertEquals(-1, value.percent);
    }

    @Test
    public void usbNotChargingIsPoweredButNotCharging() {
        BatteryTelemetry value = telemetry(
                80,
                100,
                BatteryManager.BATTERY_STATUS_NOT_CHARGING,
                BatteryManager.BATTERY_PLUGGED_USB,
                -320_000);

        assertEquals("usb", value.plugType);
        assertTrue(value.usbConnected());
        assertTrue(value.dockPowered());
        assertFalse(value.charging);
        assertEquals("not_charging", value.status);
        assertEquals("-320.0", value.currentHeader());
    }

    @Test
    public void acNotChargingIsPoweredWithoutClaimingUsb() {
        BatteryTelemetry value = telemetry(
                80,
                100,
                BatteryManager.BATTERY_STATUS_NOT_CHARGING,
                BatteryManager.BATTERY_PLUGGED_AC,
                Integer.MIN_VALUE);

        assertEquals("ac", value.plugType);
        assertFalse(value.usbConnected());
        assertTrue(value.dockPowered());
        assertFalse(value.charging);
        assertEquals("", value.currentHeader());
    }

    @Test
    public void wirelessAndDockHaveDistinctPlugTypes() {
        BatteryTelemetry wireless = telemetry(
                80, 100, BatteryManager.BATTERY_STATUS_CHARGING,
                BatteryManager.BATTERY_PLUGGED_WIRELESS, 120_000);
        BatteryTelemetry dock = telemetry(
                80, 100, BatteryManager.BATTERY_STATUS_FULL,
                BatteryManager.BATTERY_PLUGGED_DOCK, 0);

        assertEquals("wireless", wireless.plugType);
        assertTrue(wireless.charging);
        assertEquals("dock", dock.plugType);
        assertTrue(dock.dockPowered());
    }

    @Test
    public void staleChargingStatusWithoutPlugIsNotPowered() {
        BatteryTelemetry value = telemetry(
                80, 100, BatteryManager.BATTERY_STATUS_CHARGING, 0, 100_000);

        assertTrue(value.charging);
        assertEquals("none", value.plugType);
        assertFalse(value.dockPowered());
        assertFalse(value.usbConnected());
    }

    @Test
    public void unknownPlugDoesNotActivateDock() {
        BatteryTelemetry value = telemetry(
                80, 100, BatteryManager.BATTERY_STATUS_CHARGING, 1 << 8, 100_000);

        assertEquals("unknown", value.plugType);
        assertFalse(value.dockPowered());
    }

    @Test
    public void absentVoltageAndTemperatureAreOmitted() {
        BatteryTelemetry value = BatteryTelemetry.from(
                80,
                100,
                BatteryManager.BATTERY_STATUS_DISCHARGING,
                BatteryManager.BATTERY_HEALTH_GOOD,
                null,
                null,
                0,
                Integer.MIN_VALUE);

        assertEquals("", value.temperatureHeader());
        assertNull(value.voltageMv);
    }

    private static BatteryTelemetry telemetry(
            int level,
            int scale,
            int status,
            int plugged,
            int currentMicroamps) {
        return BatteryTelemetry.from(
                level,
                scale,
                status,
                BatteryManager.BATTERY_HEALTH_GOOD,
                265,
                4283,
                plugged,
                currentMicroamps);
    }
}

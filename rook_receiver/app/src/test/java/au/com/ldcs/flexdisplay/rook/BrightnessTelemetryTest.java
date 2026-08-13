package au.com.ldcs.flexdisplay.rook;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class BrightnessTelemetryTest {
    @Test
    public void activityWindowBrightnessUsesFiveToOneHundredPercent() {
        assertEquals(5, BrightnessTelemetry.clampWindowPercent(0));
        assertEquals(5, BrightnessTelemetry.clampWindowPercent(5));
        assertEquals(63, BrightnessTelemetry.clampWindowPercent(63));
        assertEquals(100, BrightnessTelemetry.clampWindowPercent(120));
    }

    @Test
    public void explicitWindowValueWinsAndUnsetValueUsesFallback() {
        assertEquals(5, BrightnessTelemetry.fromWindowValue(0.05f, 72));
        assertEquals(48, BrightnessTelemetry.fromWindowValue(0.476f, 72));
        assertEquals(72, BrightnessTelemetry.fromWindowValue(-1f, 72));
        assertEquals(72, BrightnessTelemetry.fromWindowValue(Float.NaN, 72));
    }

    @Test
    public void initialSystemFallbackPreservesLegacyScale() {
        assertEquals(0, BrightnessTelemetry.fromSystemRaw(0));
        assertEquals(49, BrightnessTelemetry.fromSystemRaw(127));
        assertEquals(100, BrightnessTelemetry.fromSystemRaw(255));
        assertEquals(100, BrightnessTelemetry.fromSystemRaw(300));
    }
}

package au.com.ldcs.flexdisplay.rook;

final class BrightnessTelemetry {
    private BrightnessTelemetry() { }

    static int clampPercent(int percent) {
        return Math.max(0, Math.min(100, percent));
    }

    static int clampWindowPercent(int percent) {
        return Math.max(5, Math.min(100, percent));
    }

    static int fromSystemRaw(int raw) {
        int selected = Math.max(0, Math.min(255, raw));
        return selected * 100 / 255;
    }

    static int fromWindowValue(float value, int fallbackPercent) {
        if (value < 0f || Float.isNaN(value) || Float.isInfinite(value)) {
            return clampPercent(fallbackPercent);
        }
        return clampPercent(Math.round(value * 100f));
    }
}

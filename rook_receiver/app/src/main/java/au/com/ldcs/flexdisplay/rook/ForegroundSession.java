package au.com.ldcs.flexdisplay.rook;

import java.util.UUID;

final class ForegroundSession {
    private ForegroundSession() { }

    static String create() {
        return UUID.randomUUID().toString().replace("-", "").substring(0, 16);
    }

    static boolean matchesCameraCommand(String current, String bound) {
        return current != null
                && bound != null
                && current.matches("[A-Za-z0-9_-]{8,64}")
                && current.equals(bound);
    }
}

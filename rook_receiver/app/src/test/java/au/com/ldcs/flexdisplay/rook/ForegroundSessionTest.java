package au.com.ldcs.flexdisplay.rook;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class ForegroundSessionTest {
    @Test
    public void createdSessionIsBoundedAndUrlSafe() {
        String value = ForegroundSession.create();

        assertTrue(value.matches("[A-Za-z0-9_-]{8,64}"));
    }

    @Test
    public void cameraCommandMustMatchCurrentResumeSessionExactly() {
        assertTrue(ForegroundSession.matchesCameraCommand("0123456789abcdef", "0123456789abcdef"));
        assertFalse(ForegroundSession.matchesCameraCommand("0123456789abcdef", "fedcba9876543210"));
        assertFalse(ForegroundSession.matchesCameraCommand("", ""));
        assertFalse(ForegroundSession.matchesCameraCommand(null, "0123456789abcdef"));
    }
}

package au.com.ldcs.flexdisplay.rook;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class InteractiveForegroundPolicyTest {
    @Test
    public void companionRequiresForegroundAndUnlocked() {
        assertTrue(InteractiveForegroundPolicy.allows(true, true, true));
        assertFalse(InteractiveForegroundPolicy.allows(true, false, true));
        assertFalse(InteractiveForegroundPolicy.allows(true, true, false));
        assertFalse(InteractiveForegroundPolicy.allows(true, false, false));
    }

    @Test
    public void kioskBehaviorIsUnchanged() {
        assertTrue(InteractiveForegroundPolicy.allows(false, true, true));
        assertTrue(InteractiveForegroundPolicy.allows(false, false, false));
    }
}

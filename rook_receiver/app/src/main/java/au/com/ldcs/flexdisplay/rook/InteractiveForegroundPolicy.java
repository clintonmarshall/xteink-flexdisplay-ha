package au.com.ldcs.flexdisplay.rook;

final class InteractiveForegroundPolicy {
    private InteractiveForegroundPolicy() { }

    static boolean allows(boolean companion, boolean foregroundActive, boolean deviceUnlocked) {
        return !companion || (foregroundActive && deviceUnlocked);
    }
}

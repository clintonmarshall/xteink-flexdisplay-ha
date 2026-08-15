package au.com.ldcs.flexdisplay.rook;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertThrows;

import java.io.ByteArrayInputStream;
import java.io.IOException;

import org.junit.Test;

public final class NotificationImagePolicyTest {
    @Test
    public void boundedReaderAcceptsMaximumBody() throws Exception {
        byte[] body = new byte[NotificationImagePolicy.MAX_BYTES];
        body[0] = 1;
        body[body.length - 1] = 2;

        assertArrayEquals(
                body,
                NotificationImagePolicy.readBounded(new ByteArrayInputStream(body)));
    }

    @Test
    public void boundedReaderRejectsOversizedAndEmptyBodies() {
        assertThrows(
                IOException.class,
                () -> NotificationImagePolicy.readBounded(new ByteArrayInputStream(
                        new byte[NotificationImagePolicy.MAX_BYTES + 1])));
        assertThrows(
                IOException.class,
                () -> NotificationImagePolicy.readBounded(new ByteArrayInputStream(new byte[0])));
        assertThrows(IOException.class, () -> NotificationImagePolicy.readBounded(null));
    }

    @Test
    public void contentLengthRejectsKnownOversizeButAllowsUnknown() throws Exception {
        NotificationImagePolicy.requireContentLength(-1L);
        NotificationImagePolicy.requireContentLength(NotificationImagePolicy.MAX_BYTES);
        assertThrows(
                IOException.class,
                () -> NotificationImagePolicy.requireContentLength(
                        NotificationImagePolicy.MAX_BYTES + 1L));
    }

    @Test
    public void dimensionsAcceptSafeImageAndRejectBombShapes() throws Exception {
        NotificationImagePolicy.requireDimensions(1920, 1080);
        assertThrows(IOException.class, () -> NotificationImagePolicy.requireDimensions(0, 1080));
        assertThrows(
                IOException.class,
                () -> NotificationImagePolicy.requireDimensions(
                        NotificationImagePolicy.MAX_DIMENSION + 1,
                        1));
        assertThrows(IOException.class, () -> NotificationImagePolicy.requireDimensions(4000, 3000));
        assertThrows(
                IOException.class,
                () -> NotificationImagePolicy.requireDimensions(Integer.MAX_VALUE, Integer.MAX_VALUE));
    }
}

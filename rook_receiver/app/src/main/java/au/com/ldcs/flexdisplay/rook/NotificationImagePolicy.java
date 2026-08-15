package au.com.ldcs.flexdisplay.rook;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;

final class NotificationImagePolicy {
    static final int MAX_BYTES = 4 * 1024 * 1024;
    static final int MAX_DIMENSION = 4096;
    static final long MAX_PIXELS = 8_000_000L;

    private NotificationImagePolicy() { }

    static void requireContentLength(long contentLength) throws IOException {
        if (contentLength > MAX_BYTES) {
            throw new IOException("Notification image exceeds 4 MiB");
        }
    }

    static byte[] readBounded(InputStream input) throws IOException {
        if (input == null) throw new IOException("Notification image response was empty");
        try (InputStream stream = input;
             ByteArrayOutputStream output = new ByteArrayOutputStream(Math.min(8192, MAX_BYTES))) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = stream.read(buffer)) >= 0) {
                if (read == 0) continue;
                if (output.size() > MAX_BYTES - read) {
                    throw new IOException("Notification image exceeds 4 MiB");
                }
                output.write(buffer, 0, read);
            }
            if (output.size() == 0) {
                throw new IOException("Notification image response was empty");
            }
            return output.toByteArray();
        }
    }

    static void requireDimensions(int width, int height) throws IOException {
        if (width <= 0 || height <= 0) {
            throw new IOException("Notification image dimensions are invalid");
        }
        if (width > MAX_DIMENSION
                || height > MAX_DIMENSION
                || (long) width * height > MAX_PIXELS) {
            throw new IOException("Notification image dimensions exceed the safe limit");
        }
    }
}

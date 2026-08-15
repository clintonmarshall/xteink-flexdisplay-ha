package au.com.ldcs.flexdisplay.rook;

import android.annotation.SuppressLint;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.ImageFormat;
import android.graphics.Matrix;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.media.ExifInterface;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Size;
import android.view.Surface;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.Arrays;

final class CameraSnapshotter {
    interface Callback {
        void onCaptured(byte[] jpeg, String facing);
        void onError(String detail);
    }

    private static final int MAX_CAPTURE_PIXELS = 1920 * 1080;
    private static final int MAX_OUTPUT_WIDTH = 1600;
    private static final int MAX_OUTPUT_HEIGHT = 1200;
    private static final long CAPTURE_TIMEOUT_MILLIS = 15_000L;

    private final Context context;
    private final Object lock = new Object();
    private HandlerThread thread;
    private Handler handler;
    private ImageReader imageReader;
    private CameraDevice cameraDevice;
    private CameraCaptureSession captureSession;
    private Callback callback;
    private Runnable timeout;
    private boolean finished = true;

    CameraSnapshotter(Context context) {
        this.context = context.getApplicationContext();
    }

    @SuppressLint("MissingPermission")
    void capture(int displayRotation, Callback selectedCallback) {
        synchronized (lock) {
            if (!finished) {
                selectedCallback.onError("camera-busy");
                return;
            }
            callback = selectedCallback;
            finished = false;
            thread = new HandlerThread("FlexDisplayCameraSnapshot");
            thread.start();
            handler = new Handler(thread.getLooper());
            timeout = () -> finishError("camera-timeout");
            handler.postDelayed(timeout, CAPTURE_TIMEOUT_MILLIS);
        }

        try {
            CameraManager manager = (CameraManager) context.getSystemService(Context.CAMERA_SERVICE);
            if (manager == null) throw new CameraAccessException(CameraAccessException.CAMERA_ERROR);
            CameraSelection selection = selectCamera(manager);
            if (selection == null) {
                finishError("camera-unavailable");
                return;
            }
            imageReader = ImageReader.newInstance(
                    selection.size.getWidth(),
                    selection.size.getHeight(),
                    ImageFormat.JPEG,
                    2);
            imageReader.setOnImageAvailableListener(
                    reader -> consumeImage(reader, selection, displayRotation),
                    handler);
            manager.openCamera(selection.cameraId, new CameraDevice.StateCallback() {
                @Override
                public void onOpened(CameraDevice camera) {
                    if (isFinished()) {
                        camera.close();
                        return;
                    }
                    cameraDevice = camera;
                    createCaptureSession(selection, displayRotation);
                }

                @Override
                public void onDisconnected(CameraDevice camera) {
                    camera.close();
                    finishError("camera-disconnected");
                }

                @Override
                public void onError(CameraDevice camera, int error) {
                    camera.close();
                    finishError("camera-open-error-" + error);
                }
            }, handler);
        } catch (CameraAccessException | SecurityException error) {
            finishError("camera-open-failed");
        }
    }

    void cancel() {
        finishError("camera-backgrounded");
    }

    private void createCaptureSession(CameraSelection selection, int displayRotation) {
        try {
            cameraDevice.createCaptureSession(
                    Arrays.asList(imageReader.getSurface()),
                    new CameraCaptureSession.StateCallback() {
                        @Override
                        public void onConfigured(CameraCaptureSession session) {
                            if (isFinished()) {
                                session.close();
                                return;
                            }
                            captureSession = session;
                            captureStill(selection, displayRotation);
                        }

                        @Override
                        public void onConfigureFailed(CameraCaptureSession session) {
                            finishError("camera-session-failed");
                        }
                    },
                    handler);
        } catch (CameraAccessException | IllegalStateException error) {
            finishError("camera-session-failed");
        }
    }

    private void captureStill(CameraSelection selection, int displayRotation) {
        try {
            CaptureRequest.Builder request = cameraDevice.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE);
            request.addTarget(imageReader.getSurface());
            request.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO);
            request.set(CaptureRequest.JPEG_QUALITY, (byte) 88);
            request.set(
                    CaptureRequest.JPEG_ORIENTATION,
                    jpegOrientation(selection.sensorOrientation, selection.front, displayRotation));
            captureSession.capture(request.build(), new CameraCaptureSession.CaptureCallback() { }, handler);
        } catch (CameraAccessException | IllegalStateException error) {
            finishError("camera-capture-failed");
        }
    }

    private void consumeImage(ImageReader reader, CameraSelection selection, int displayRotation) {
        Image image = null;
        try {
            image = reader.acquireLatestImage();
            if (image == null || isFinished()) return;
            ByteBuffer buffer = image.getPlanes()[0].getBuffer();
            byte[] rawJpeg = new byte[buffer.remaining()];
            buffer.get(rawJpeg);
            byte[] safeJpeg = sanitizeJpeg(rawJpeg);
            finishSuccess(safeJpeg, selection.front ? "front" : "rear");
        } catch (Exception error) {
            finishError("camera-image-failed");
        } finally {
            if (image != null) image.close();
        }
    }

    private CameraSelection selectCamera(CameraManager manager) throws CameraAccessException {
        CameraSelection rear = null;
        for (String cameraId : manager.getCameraIdList()) {
            CameraCharacteristics characteristics = manager.getCameraCharacteristics(cameraId);
            Integer lensFacing = characteristics.get(CameraCharacteristics.LENS_FACING);
            boolean front = lensFacing != null && lensFacing == CameraCharacteristics.LENS_FACING_FRONT;
            boolean back = lensFacing != null && lensFacing == CameraCharacteristics.LENS_FACING_BACK;
            if (!front && !back) continue;
            android.hardware.camera2.params.StreamConfigurationMap map =
                    characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
            Size[] sizes = map == null ? null : map.getOutputSizes(ImageFormat.JPEG);
            Size size = selectSize(sizes);
            if (size == null) continue;
            Integer sensorOrientation = characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION);
            CameraSelection selection = new CameraSelection(
                    cameraId,
                    front,
                    sensorOrientation == null ? 0 : sensorOrientation,
                    size);
            if (front) return selection;
            if (rear == null) rear = selection;
        }
        return rear;
    }

    private static Size selectSize(Size[] sizes) {
        if (sizes == null || sizes.length == 0) return null;
        Size bestWide = null;
        Size bestBounded = null;
        Size smallest = sizes[0];
        for (Size size : sizes) {
            int pixels = size.getWidth() * size.getHeight();
            int smallestPixels = smallest.getWidth() * smallest.getHeight();
            if (pixels < smallestPixels) smallest = size;
            if (pixels > MAX_CAPTURE_PIXELS) continue;
            if (bestBounded == null
                    || pixels > bestBounded.getWidth() * bestBounded.getHeight()) {
                bestBounded = size;
            }
            float ratio = (float) size.getWidth() / Math.max(1, size.getHeight());
            if (Math.abs(ratio - (16f / 9f)) < 0.08f
                    && (bestWide == null
                    || pixels > bestWide.getWidth() * bestWide.getHeight())) {
                bestWide = size;
            }
        }
        return bestWide != null ? bestWide : (bestBounded != null ? bestBounded : smallest);
    }

    private static int jpegOrientation(int sensorOrientation, boolean front, int displayRotation) {
        int deviceDegrees;
        switch (displayRotation) {
            case Surface.ROTATION_90:
                deviceDegrees = 90;
                break;
            case Surface.ROTATION_180:
                deviceDegrees = 180;
                break;
            case Surface.ROTATION_270:
                deviceDegrees = 270;
                break;
            default:
                deviceDegrees = 0;
        }
        if (front) return (360 - ((sensorOrientation + deviceDegrees) % 360)) % 360;
        return (sensorOrientation - deviceDegrees + 360) % 360;
    }

    private static byte[] sanitizeJpeg(byte[] rawJpeg) throws Exception {
        ExifInterface exif = new ExifInterface(new ByteArrayInputStream(rawJpeg));
        int orientation = exif.getAttributeInt(
                ExifInterface.TAG_ORIENTATION,
                ExifInterface.ORIENTATION_NORMAL);
        Bitmap decoded = BitmapFactory.decodeByteArray(rawJpeg, 0, rawJpeg.length);
        if (decoded == null) throw new IllegalArgumentException("JPEG decode failed");
        Bitmap selected = decoded;
        Matrix matrix = exifTransform(orientation);
        if (!matrix.isIdentity()) {
            selected = Bitmap.createBitmap(decoded, 0, 0, decoded.getWidth(), decoded.getHeight(), matrix, true);
            if (selected != decoded) decoded.recycle();
        }
        float scale = Math.min(
                1f,
                Math.min(
                        (float) MAX_OUTPUT_WIDTH / selected.getWidth(),
                        (float) MAX_OUTPUT_HEIGHT / selected.getHeight()));
        if (scale < 1f) {
            Bitmap resized = Bitmap.createScaledBitmap(
                    selected,
                    Math.max(1, Math.round(selected.getWidth() * scale)),
                    Math.max(1, Math.round(selected.getHeight() * scale)),
                    true);
            if (resized != selected) selected.recycle();
            selected = resized;
        }

        ByteArrayOutputStream output = new ByteArrayOutputStream();
        int quality = 88;
        while (true) {
            output.reset();
            if (!selected.compress(Bitmap.CompressFormat.JPEG, quality, output)) {
                selected.recycle();
                throw new IllegalStateException("JPEG encode failed");
            }
            if (output.size() <= FlexDisplayClient.MAX_CAMERA_SNAPSHOT_BYTES) break;
            if (quality > 52) {
                quality -= 9;
            } else {
                Bitmap smaller = Bitmap.createScaledBitmap(
                        selected,
                        Math.max(1, Math.round(selected.getWidth() * 0.8f)),
                        Math.max(1, Math.round(selected.getHeight() * 0.8f)),
                        true);
                if (smaller != selected) selected.recycle();
                selected = smaller;
            }
        }
        selected.recycle();
        return output.toByteArray();
    }

    private static Matrix exifTransform(int orientation) {
        Matrix matrix = new Matrix();
        switch (orientation) {
            case ExifInterface.ORIENTATION_FLIP_HORIZONTAL:
                matrix.setScale(-1f, 1f);
                break;
            case ExifInterface.ORIENTATION_ROTATE_180:
                matrix.setRotate(180f);
                break;
            case ExifInterface.ORIENTATION_FLIP_VERTICAL:
                matrix.setScale(1f, -1f);
                break;
            case ExifInterface.ORIENTATION_TRANSPOSE:
                matrix.setRotate(90f);
                matrix.postScale(-1f, 1f);
                break;
            case ExifInterface.ORIENTATION_ROTATE_90:
                matrix.setRotate(90f);
                break;
            case ExifInterface.ORIENTATION_TRANSVERSE:
                matrix.setRotate(-90f);
                matrix.postScale(-1f, 1f);
                break;
            case ExifInterface.ORIENTATION_ROTATE_270:
                matrix.setRotate(-90f);
                break;
            default:
                break;
        }
        return matrix;
    }

    private boolean isFinished() {
        synchronized (lock) {
            return finished;
        }
    }

    private void finishSuccess(byte[] jpeg, String facing) {
        Callback selected = finish();
        if (selected != null) selected.onCaptured(jpeg, facing);
    }

    private void finishError(String detail) {
        Callback selected = finish();
        if (selected != null) selected.onError(detail);
    }

    private Callback finish() {
        synchronized (lock) {
            if (finished) return null;
            finished = true;
            if (captureSession != null) captureSession.close();
            captureSession = null;
            if (cameraDevice != null) cameraDevice.close();
            cameraDevice = null;
            if (imageReader != null) imageReader.close();
            imageReader = null;
            if (handler != null && timeout != null) handler.removeCallbacks(timeout);
            timeout = null;
            if (thread != null) thread.quitSafely();
            thread = null;
            handler = null;
            Callback selected = callback;
            callback = null;
            return selected;
        }
    }

    private static final class CameraSelection {
        final String cameraId;
        final boolean front;
        final int sensorOrientation;
        final Size size;

        CameraSelection(String cameraId, boolean front, int sensorOrientation, Size size) {
            this.cameraId = cameraId;
            this.front = front;
            this.sensorOrientation = sensorOrientation;
            this.size = size;
        }
    }
}

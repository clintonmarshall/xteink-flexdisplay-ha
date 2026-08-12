package au.com.ldcs.flexdisplay.rook;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.KeyguardManager;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.media.ToneGenerator;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.ByteArrayOutputStream;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int VOICE_PERMISSION_REQUEST = 4103;
    private static final int CAMERA_PERMISSION_REQUEST = 4104;
    private static final int VOICE_SAMPLE_RATE = 16_000;
    private static final int VOICE_MAX_BYTES = VOICE_SAMPLE_RATE * 2 * 8;
    private static final int VOICE_MIN_BYTES = VOICE_SAMPLE_RATE;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private final ExecutorService notificationNetwork = Executors.newSingleThreadExecutor();
    private final ExecutorService voiceNetwork = Executors.newSingleThreadExecutor();
    private final Runnable scheduledRefresh = () -> refresh(false);
    private final ReceiverProfile profile = ReceiverProfile.detect();
    private CameraSnapshotter cameraSnapshotter;
    private ReceiverConfig config;
    private FlexDisplayClient client;
    private FrameLayout root;
    private ImageView imageView;
    private TextView statusView;
    private Button assistButton;
    private Bitmap currentBitmap;
    private String imageSha256 = "";
    private String pendingCommandResult = "";
    private String pendingCommandId = "";
    private String pendingQuickAction = "";
    private boolean fetching;
    private boolean commandInProgress;
    private boolean refreshPending;
    private boolean destroyed;
    private volatile boolean foregroundActive = !BuildConfig.COMPANION;
    private volatile boolean notificationLoopStarted;
    private volatile int notificationGeneration;
    private boolean cameraPermissionPrompted;
    private volatile boolean microphoneEnabled = true;
    private int lastAudibleMusicVolumePercent = 45;
    private long notificationSequence;
    private List<FlexDisplayClient.Interaction> interactions = Collections.emptyList();
    private FrameLayout notificationOverlay;
    private FlexDisplayClient.Notification activeNotification;
    private Runnable notificationDismissal;
    private float touchStartX;
    private float touchStartY;
    private long touchStartedAt;
    private volatile boolean voiceRecording;
    private volatile boolean voiceBusy;
    private volatile boolean discardVoiceCapture;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        cameraSnapshotter = new CameraSnapshotter(getApplicationContext());
        int windowFlags = WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON;
        if (!BuildConfig.COMPANION) {
            windowFlags |= WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                    | WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED;
        }
        getWindow().addFlags(windowFlags);
        client = new FlexDisplayClient(this);
        client.setForegroundAllowed(foregroundActive);
        config = ReceiverConfig.load(this);
        microphoneEnabled = config.microphoneEnabled;
        buildUi();
        enterReceiverMode();
        if (!config.isReady()) {
            showSettings();
        } else if (!BuildConfig.COMPANION) {
            refresh(false);
        }
    }

    @Override
    protected void onNewIntent(android.content.Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        refresh(true);
    }

    @Override
    protected void onResume() {
        super.onResume();
        foregroundActive = true;
        client.setForegroundAllowed(true);
        enterReceiverMode();
        if (BuildConfig.COMPANION && config.isReady()) {
            requestCompanionCameraPermission();
            refresh(true);
        }
    }

    @Override
    protected void onPause() {
        if (BuildConfig.COMPANION) {
            foregroundActive = false;
            discardVoiceCapture = true;
            voiceRecording = false;
            cameraSnapshotter.cancel();
            handler.removeCallbacks(scheduledRefresh);
            refreshPending = true;
            notificationGeneration++;
            notificationLoopStarted = false;
            client.setForegroundAllowed(false);
        }
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        cameraSnapshotter.cancel();
        client.cancelForegroundRequests();
        handler.removeCallbacksAndMessages(null);
        network.shutdownNow();
        notificationNetwork.shutdownNow();
        voiceNetwork.shutdownNow();
        super.onDestroy();
    }

    private void buildUi() {
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(4, 10, 17));
        imageView = new ImageView(this);
        imageView.setScaleType(ImageView.ScaleType.FIT_XY);
        root.addView(imageView, new FrameLayout.LayoutParams(-1, -1));

        statusView = new TextView(this);
        statusView.setTextColor(Color.WHITE);
        statusView.setBackgroundColor(Color.argb(220, 4, 10, 17));
        statusView.setTextSize(15);
        statusView.setGravity(Gravity.CENTER);
        statusView.setPadding(42, 14, 42, 14);
        FrameLayout.LayoutParams statusParams = new FrameLayout.LayoutParams(-1, -2, Gravity.CENTER);
        statusParams.leftMargin = 42;
        statusParams.rightMargin = 42;
        root.addView(statusView, statusParams);

        assistButton = new Button(this);
        assistButton.setText("Assist");
        assistButton.setTextSize(11);
        assistButton.setTextColor(Color.WHITE);
        assistButton.setBackgroundColor(Color.argb(190, 14, 82, 130));
        assistButton.setOnTouchListener(this::onAssistTouch);
        FrameLayout.LayoutParams assistParams = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.RIGHT);
        assistParams.rightMargin = profile.round ? 78 : 18;
        assistParams.bottomMargin = profile.round ? 46 : 18;
        root.addView(assistButton, assistParams);

        root.setClickable(true);
        root.setOnTouchListener(this::onTouch);
        setContentView(root);
        showStatus("Connecting to FlexDisplay…", true);
    }

    private boolean onAssistTouch(View view, MotionEvent event) {
        switch (event.getAction()) {
            case MotionEvent.ACTION_DOWN:
                view.performClick();
                startVoiceCapture();
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                stopVoiceCapture();
                return true;
            default:
                return true;
        }
    }

    private boolean onTouch(View view, MotionEvent event) {
        if (notificationOverlay != null) return false;
        if (event.getAction() == MotionEvent.ACTION_DOWN) {
            touchStartX = event.getX();
            touchStartY = event.getY();
            touchStartedAt = System.currentTimeMillis();
            return true;
        }
        if (event.getAction() != MotionEvent.ACTION_UP) return true;
        float dx = event.getX() - touchStartX;
        float dy = event.getY() - touchStartY;
        long duration = System.currentTimeMillis() - touchStartedAt;
        FlexDisplayClient.Interaction interaction = interactionAt(touchStartX, touchStartY);
        if (duration > 850 && Math.abs(dx) < 45 && Math.abs(dy) < 45) {
            if (interaction != null && interaction.confirmation) {
                confirmInteraction(interaction);
            } else {
                showSettings();
            }
        } else if (Math.abs(dx) > 85 && Math.abs(dx) > Math.abs(dy)) {
            pendingQuickAction = dx < 0 ? "next" : "previous";
            refresh(true);
        } else if (interaction != null) {
            view.performClick();
            if (interaction.confirmation) {
                showTransientStatus("Hold " + interaction.label + " to confirm");
            } else {
                performInteraction(interaction, false);
            }
        } else {
            view.performClick();
            pendingQuickAction = "refresh";
            refresh(true);
        }
        return true;
    }

    private FlexDisplayClient.Interaction interactionAt(float viewX, float viewY) {
        if (root.getWidth() <= 0 || root.getHeight() <= 0) return null;
        float x = viewX * profile.width / root.getWidth();
        float y = viewY * profile.height / root.getHeight();
        for (FlexDisplayClient.Interaction interaction : interactions) {
            if (interaction.contains(x, y)) return interaction;
        }
        return null;
    }

    private void confirmInteraction(FlexDisplayClient.Interaction interaction) {
        new AlertDialog.Builder(this)
                .setTitle(interaction.label)
                .setMessage(interaction.confirmationText)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Confirm", (dialog, which) -> performInteraction(interaction, true))
                .show();
    }

    private void performInteraction(FlexDisplayClient.Interaction interaction, boolean confirmed) {
        showStatus("Running " + interaction.label + "…", true);
        network.execute(() -> {
            try {
                String detail = client.performInteraction(config, interaction.id, confirmed);
                handler.post(() -> {
                    showTransientStatus(detail);
                    handler.postDelayed(() -> refresh(true), 500L);
                });
            } catch (Exception error) {
                handler.post(() -> showTransientStatus("Action failed\n" + error.getMessage()));
            }
        });
    }

    private void refresh(boolean immediate) {
        if (!config.isReady()) return;
        if (BuildConfig.COMPANION && !foregroundActive) {
            if (immediate) refreshPending = true;
            return;
        }
        if (commandInProgress) {
            if (immediate) refreshPending = true;
            return;
        }
        if (fetching) {
            if (immediate) refreshPending = true;
            return;
        }
        handler.removeCallbacks(scheduledRefresh);
        fetching = true;
        if (currentBitmap == null) showStatus("Connecting to FlexDisplay…", true);
        String commandResult = pendingCommandResult;
        String commandId = pendingCommandId;
        String quickAction = pendingQuickAction;
        pendingCommandResult = "";
        pendingCommandId = "";
        pendingQuickAction = "";
        network.execute(() -> {
            try {
                FlexDisplayClient.Result result = client.fetch(
                        config,
                        imageSha256,
                        currentBitmap != null,
                        commandResult,
                        commandId,
                        quickAction);
                handler.post(() -> applyResult(result));
            } catch (Exception error) {
                handler.post(() -> applyError(error));
            }
        });
    }

    private void applyResult(FlexDisplayClient.Result result) {
        fetching = false;
        if (BuildConfig.COMPANION && !foregroundActive) {
            refreshPending = true;
            return;
        }
        if (result.bitmap != null) {
            currentBitmap = result.bitmap;
            imageView.setImageBitmap(result.bitmap);
        }
        String digest = result.header("X-FlexDisplay-Image-SHA256");
        if (!digest.isEmpty()) imageSha256 = digest;
        interactions = result.interactions;
        applyDesiredState(result);
        showStatus("", false);
        startNotificationLoop();

        String commands = result.header("X-FlexDisplay-Commands");
        String commandId = result.header("X-FlexDisplay-Command-ID");
        boolean commandRefreshScheduled = !commands.isEmpty();
        if (commandRefreshScheduled) executeCommands(commands, commandId);

        long refreshSeconds = parseLong(result.header("X-FlexDisplay-Refresh-Interval"), 60L);
        refreshSeconds = Math.max(15L, Math.min(3600L, refreshSeconds));
        if (commandRefreshScheduled) {
            // executeCommands schedules the acknowledgement fetch; the wake
            // event that caused this fetch has already done its job.
            refreshPending = false;
        } else if (refreshPending) {
            refreshPending = false;
            handler.post(() -> refresh(true));
        } else {
            handler.postDelayed(scheduledRefresh, refreshSeconds * 1000L);
        }
    }

    private void startNotificationLoop() {
        if (notificationLoopStarted || !config.isReady()
                || (BuildConfig.COMPANION && !foregroundActive)) return;
        notificationLoopStarted = true;
        int generation = ++notificationGeneration;
        notificationNetwork.execute(() -> {
            try {
                while (!destroyed
                        && generation == notificationGeneration
                        && (!BuildConfig.COMPANION || foregroundActive)
                        && !Thread.currentThread().isInterrupted()) {
                    try {
                    ReceiverConfig selectedConfig = config;
                    long previousSequence = notificationSequence;
                    FlexDisplayClient.NotificationEvent event =
                            client.waitForNotification(selectedConfig, notificationSequence);
                    notificationSequence = Math.max(notificationSequence, event.sequence);
                    if (generation != notificationGeneration
                            || (BuildConfig.COMPANION && !foregroundActive)) break;
                    if (event.notification != null
                            && (event.event.isEmpty() || "notification".equals(event.event))) {
                        Bitmap image = event.notification.hasImage
                                ? client.fetchNotificationImage(selectedConfig, event.notification.id)
                                : null;
                        handler.post(() -> showNotification(event.notification, image));
                    } else if (event.sequence > previousSequence && !event.refresh) {
                        handler.post(() -> dismissNotification(false));
                    }
                        if (event.refresh) handler.post(() -> refresh(true));
                    } catch (Exception error) {
                        if (destroyed
                                || generation != notificationGeneration
                                || (BuildConfig.COMPANION && !foregroundActive)) break;
                        SystemClock.sleep(2_000L);
                    }
                }
            } finally {
                handler.post(() -> {
                    if (generation == notificationGeneration) notificationLoopStarted = false;
                });
            }
        });
    }

    private void showNotification(FlexDisplayClient.Notification notification, Bitmap image) {
        dismissNotification(false);
        activeNotification = notification;
        notificationOverlay = new FrameLayout(this);
        notificationOverlay.setBackgroundColor(Color.rgb(4, 10, 17));
        notificationOverlay.setClickable(true);
        notificationOverlay.setOnClickListener(view -> dismissNotification(true));

        if (image != null) {
            ImageView camera = new ImageView(this);
            camera.setScaleType(ImageView.ScaleType.CENTER_CROP);
            camera.setImageBitmap(image);
            notificationOverlay.addView(camera, new FrameLayout.LayoutParams(-1, -1));
        }

        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER);
        panel.setPadding(16, 12, 16, 12);
        panel.setBackgroundColor(Color.argb(230, 4, 10, 17));
        panel.setOnClickListener(view -> { });

        TextView title = new TextView(this);
        title.setText(notification.title);
        title.setTextColor(Color.rgb(54, 191, 255));
        title.setTextSize(24);
        title.setGravity(Gravity.CENTER);
        panel.addView(title, new LinearLayout.LayoutParams(-1, -2));

        if (!notification.message.isEmpty()) {
            TextView message = new TextView(this);
            message.setText(notification.message);
            message.setTextColor(Color.WHITE);
            message.setTextSize(15);
            message.setGravity(Gravity.CENTER);
            message.setMaxLines(3);
            panel.addView(message, new LinearLayout.LayoutParams(-1, -2));
        }

        if (!notification.actions.isEmpty()) {
            LinearLayout actions = new LinearLayout(this);
            actions.setOrientation(LinearLayout.HORIZONTAL);
            actions.setGravity(Gravity.CENTER);
            for (FlexDisplayClient.NotificationAction action : notification.actions) {
                Button button = new Button(this);
                button.setText(action.label);
                button.setTextSize(11);
                button.setOnClickListener(view -> performNotificationAction(notification, action));
                actions.addView(button, new LinearLayout.LayoutParams(0, -2, 1f));
            }
            panel.addView(actions, new LinearLayout.LayoutParams(-1, -2));
        }

        Button dismiss = new Button(this);
        dismiss.setText("Dismiss");
        dismiss.setTextSize(11);
        dismiss.setOnClickListener(view -> dismissNotification(true));
        panel.addView(dismiss, new LinearLayout.LayoutParams(-1, -2));

        FrameLayout.LayoutParams panelParams = new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM);
        panelParams.leftMargin = 58;
        panelParams.rightMargin = 58;
        panelParams.bottomMargin = 46;
        notificationOverlay.addView(panel, panelParams);
        root.addView(notificationOverlay, new FrameLayout.LayoutParams(-1, -1));
        playChime(notification.chime);
        notificationDismissal = () -> dismissNotification(true);
        handler.postDelayed(notificationDismissal, notification.duration * 1000L);
        enterReceiverMode();
    }

    private void performNotificationAction(
            FlexDisplayClient.Notification notification,
            FlexDisplayClient.NotificationAction action) {
        if (action.confirmation) {
            new AlertDialog.Builder(this)
                    .setTitle(action.label)
                    .setMessage(action.confirmationText)
                    .setNegativeButton("Cancel", null)
                    .setPositiveButton(
                            "Confirm",
                            (dialog, which) -> runNotificationAction(notification, action, true))
                    .show();
        } else {
            runNotificationAction(notification, action, false);
        }
    }

    private void runNotificationAction(
            FlexDisplayClient.Notification notification,
            FlexDisplayClient.NotificationAction action,
            boolean confirmed) {
        network.execute(() -> {
            try {
                String detail = client.performNotificationAction(
                        config, notification.id, action.id, confirmed);
                handler.post(() -> {
                    dismissNotification(true);
                    showTransientStatus(detail);
                    handler.postDelayed(() -> refresh(true), 500L);
                });
            } catch (Exception error) {
                handler.post(() -> showTransientStatus("Action failed\n" + error.getMessage()));
            }
        });
    }

    private void dismissNotification(boolean notifyBridge) {
        if (notificationDismissal != null) handler.removeCallbacks(notificationDismissal);
        notificationDismissal = null;
        FlexDisplayClient.Notification dismissed = activeNotification;
        activeNotification = null;
        if (notificationOverlay != null) root.removeView(notificationOverlay);
        notificationOverlay = null;
        if (notifyBridge && dismissed != null) {
            network.execute(() -> {
                try {
                    client.dismissNotification(config, dismissed.id);
                } catch (Exception ignored) {
                    // The alert still expires on the Bridge if acknowledgement is lost.
                }
            });
        }
    }

    private void playChime(String chime) {
        if ("none".equals(chime)) return;
        if (currentMusicVolumePercent() == 0) return;
        ToneGenerator tone = new ToneGenerator(AudioManager.STREAM_MUSIC, 90);
        int selected = "alert".equals(chime)
                ? ToneGenerator.TONE_PROP_ACK
                : ToneGenerator.TONE_PROP_BEEP2;
        tone.startTone(selected, 280);
        if ("doorbell".equals(chime)) {
            handler.postDelayed(() -> tone.startTone(ToneGenerator.TONE_PROP_BEEP2, 420), 430L);
        }
        handler.postDelayed(tone::release, 1_200L);
    }

    private void applyDesiredState(FlexDisplayClient.Result result) {
        String volume = result.header("X-FlexDisplay-Desired-Volume");
        if (!volume.isEmpty()) {
            setMusicVolume(parseInt(volume, 45));
        }
        String muted = result.header("X-FlexDisplay-Desired-Muted").toLowerCase(Locale.ROOT);
        if ("true".equals(muted)) {
            setMusicVolume(0);
        } else if ("false".equals(muted) && currentMusicVolumePercent() == 0) {
            setMusicVolume(volume.isEmpty() ? lastAudibleMusicVolumePercent : parseInt(volume, 45));
        }
        String brightness = result.header("X-FlexDisplay-Desired-Brightness");
        if (!brightness.isEmpty()) {
            setWindowBrightness(parseInt(brightness, currentWindowBrightnessPercent()));
        }
        String microphone = result.header("X-FlexDisplay-Desired-Microphone-Enabled")
                .toLowerCase(Locale.ROOT);
        if ("true".equals(microphone) || "false".equals(microphone)) {
            microphoneEnabled = "true".equals(microphone);
            config = config.withMicrophoneEnabled(microphoneEnabled);
            config.save(this);
            if (!microphoneEnabled) {
                discardVoiceCapture = true;
                voiceRecording = false;
                setAssistActive(false, "Microphone disabled by Home Assistant");
            } else if (!voiceBusy) {
                assistButton.setEnabled(true);
                assistButton.setText("Assist");
            }
        }
    }

    private void setMusicVolume(int percent) {
        AudioManager manager = (AudioManager) getSystemService(AUDIO_SERVICE);
        if (manager == null) return;
        int max = Math.max(1, manager.getStreamMaxVolume(AudioManager.STREAM_MUSIC));
        int selected = Math.max(0, Math.min(100, percent));
        if (selected > 0) lastAudibleMusicVolumePercent = selected;
        int streamVolume = Math.round(selected * max / 100f);
        manager.setStreamVolume(AudioManager.STREAM_MUSIC, Math.max(0, Math.min(max, streamVolume)), 0);
    }

    private int currentMusicVolumePercent() {
        AudioManager manager = (AudioManager) getSystemService(AUDIO_SERVICE);
        if (manager == null) return 0;
        int max = Math.max(1, manager.getStreamMaxVolume(AudioManager.STREAM_MUSIC));
        return Math.max(0, Math.min(
                100,
                Math.round(manager.getStreamVolume(AudioManager.STREAM_MUSIC) * 100f / max)));
    }

    private void adjustMusicVolume(int delta) {
        setMusicVolume(currentMusicVolumePercent() + delta);
    }

    private void setWindowBrightness(int percent) {
        WindowManager.LayoutParams params = getWindow().getAttributes();
        params.screenBrightness = Math.max(0.05f, Math.min(1.0f, percent / 100f));
        getWindow().setAttributes(params);
    }

    private int currentWindowBrightnessPercent() {
        float selected = getWindow().getAttributes().screenBrightness;
        if (selected < 0f) return 100;
        return Math.max(5, Math.min(100, Math.round(selected * 100f)));
    }

    private void adjustBrightness(int delta) {
        setWindowBrightness(currentWindowBrightnessPercent() + delta);
    }

    private void restartApp() {
        handler.postDelayed(() -> {
            android.content.Intent intent = getIntent();
            finish();
            startActivity(intent);
        }, 650L);
    }

    private void startVoiceCapture() {
        if (!config.isReady()) {
            showSettings();
            return;
        }
        if (voiceRecording || voiceBusy) return;
        if (BuildConfig.COMPANION && !foregroundActive) return;
        if (!microphoneEnabled) {
            showTransientStatus("Microphone disabled by Home Assistant");
            return;
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] { Manifest.permission.RECORD_AUDIO }, VOICE_PERMISSION_REQUEST);
            showTransientStatus("Allow microphone, then hold Assist again");
            return;
        }
        int minBuffer = AudioRecord.getMinBufferSize(
                VOICE_SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT);
        if (minBuffer <= 0) {
            showTransientStatus("Microphone is unavailable");
            return;
        }
        int bufferSize = Math.max(minBuffer, 4096);
        try {
            AudioRecord recorder = new AudioRecord(
                    MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    VOICE_SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    bufferSize * 2);
            if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
                recorder.release();
                showTransientStatus("Microphone did not initialise");
                return;
            }
            voiceBusy = true;
            discardVoiceCapture = false;
            voiceRecording = true;
            setAssistActive(true, "Listening… release to send");
            voiceNetwork.execute(() -> recordAndSendVoice(recorder, bufferSize));
        } catch (SecurityException error) {
            voiceBusy = false;
            showTransientStatus("Microphone permission denied");
        } catch (IllegalArgumentException error) {
            voiceBusy = false;
            showTransientStatus("Microphone is unavailable");
        }
    }

    private void stopVoiceCapture() {
        if (!voiceRecording) return;
        voiceRecording = false;
        setAssistActive(false, "Processing Assist…");
    }

    private void recordAndSendVoice(AudioRecord recorder, int bufferSize) {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[bufferSize];
        try {
            recorder.startRecording();
            while (voiceRecording && output.size() < VOICE_MAX_BYTES && !Thread.currentThread().isInterrupted()) {
                int read = recorder.read(buffer, 0, buffer.length);
                if (read > 0) output.write(buffer, 0, read);
            }
        } catch (Exception error) {
            voiceBusy = false;
            handler.post(() -> setAssistActive(false, "Assist recording failed\n" + error.getMessage()));
            return;
        } finally {
            voiceRecording = false;
            try {
                recorder.stop();
            } catch (IllegalStateException ignored) {
                // Already stopped by the audio stack.
            }
            recorder.release();
        }
        byte[] audio = output.toByteArray();
        if (discardVoiceCapture
                || !microphoneEnabled
                || (BuildConfig.COMPANION && !foregroundActive)) {
            voiceBusy = false;
            handler.post(() -> setAssistActive(false, "Assist recording cancelled"));
            return;
        }
        if (audio.length < VOICE_MIN_BYTES) {
            voiceBusy = false;
            handler.post(() -> setAssistActive(false, "Hold Assist a little longer"));
            return;
        }
        try {
            FlexDisplayClient.VoiceAssistantResponse response = client.runAssist(config, audio, false);
            voiceBusy = false;
            handler.post(() -> setAssistActive(false, response.summary()));
            playAssistAudio(response);
            handler.postDelayed(() -> refresh(true), 900L);
        } catch (Exception error) {
            voiceBusy = false;
            handler.post(() -> setAssistActive(false, "Assist failed\n" + error.getMessage()));
        }
    }

    private void playAssistAudio(FlexDisplayClient.VoiceAssistantResponse response) {
        if (response.audio.length == 0) return;
        if (currentMusicVolumePercent() == 0) return;
        int sampleRate = response.sampleRate <= 0 ? VOICE_SAMPLE_RATE : response.sampleRate;
        int minBuffer = AudioTrack.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT);
        int bufferSize = Math.max(minBuffer, response.audio.length);
        AudioTrack track = new AudioTrack(
                AudioManager.STREAM_MUSIC,
                sampleRate,
                AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferSize,
                AudioTrack.MODE_STREAM);
        try {
            track.play();
            track.write(response.audio, 0, response.audio.length);
            int durationMs = Math.max(250, (response.audio.length / 2) * 1000 / sampleRate);
            SystemClock.sleep(durationMs + 150L);
            track.stop();
        } finally {
            track.release();
        }
    }

    private void setAssistActive(boolean active, String message) {
        assistButton.setEnabled(microphoneEnabled && !active && !voiceBusy);
        assistButton.setText(active ? "Listening" : (voiceBusy ? "Assist…" : "Assist"));
        showStatus(message, true);
        if (!active) {
            handler.postDelayed(() -> {
                if (notificationOverlay == null && !voiceRecording) showStatus("", false);
            }, 3_500L);
        }
    }

    private void executeCommands(String commands, String commandId) {
        boolean success = true;
        String detail = "ok";
        boolean cameraSnapshot = false;
        for (String command : commands.split(",")) {
            String selected = command.trim().toLowerCase(Locale.ROOT);
            switch (selected) {
                case "clear":
                case "sleep":
                case "power-off":
                    imageView.setImageDrawable(null);
                    currentBitmap = null;
                    showStatus("Tap to wake", true);
                    break;
                case "restart":
                    imageSha256 = "";
                    break;
                case "restart-app":
                    imageSha256 = "";
                    restartApp();
                    break;
                case "test-chime":
                    playChime("doorbell");
                    break;
                case "volume-up":
                    adjustMusicVolume(10);
                    break;
                case "volume-down":
                    adjustMusicVolume(-10);
                    break;
                case "mute":
                    setMusicVolume(0);
                    break;
                case "unmute":
                    setMusicVolume(lastAudibleMusicVolumePercent);
                    break;
                case "brightness-up":
                    adjustBrightness(15);
                    break;
                case "brightness-down":
                    adjustBrightness(-15);
                    break;
                case "camera-snapshot":
                    if (BuildConfig.COMPANION) {
                        cameraSnapshot = true;
                    } else {
                        success = false;
                        detail = "camera-companion-only";
                    }
                    break;
                case "refresh":
                case "full-refresh":
                case "next":
                case "previous":
                case "overview":
                    break;
                case "install":
                    success = false;
                    detail = "unsupported-on-android";
                    break;
                default:
                    success = false;
                    detail = "unsupported-" + selected;
            }
        }
        if (cameraSnapshot && success) {
            captureAndUploadSnapshot(commands, commandId);
        } else {
            completeCommand(commands, commandId, success, detail);
        }
    }

    private void captureAndUploadSnapshot(String commands, String commandId) {
        if (!foregroundActive) {
            completeCommand(commands, commandId, false, "camera-backgrounded");
            return;
        }
        KeyguardManager keyguard = (KeyguardManager) getSystemService(KEYGUARD_SERVICE);
        if (keyguard != null && keyguard.isDeviceLocked()) {
            completeCommand(commands, commandId, false, "camera-device-locked");
            return;
        }
        if (commandId == null || commandId.trim().isEmpty()) {
            completeCommand(commands, commandId, false, "camera-command-id-missing");
            return;
        }
        if (!getPackageManager().hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)) {
            completeCommand(commands, commandId, false, "camera-unavailable");
            return;
        }
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            showTransientStatus("Camera snapshot denied: enable Camera permission in Android settings");
            completeCommand(commands, commandId, false, "camera-permission-denied");
            return;
        }
        commandInProgress = true;
        showStatus("Taking camera snapshot…", true);
        int rotation = getWindowManager().getDefaultDisplay().getRotation();
        cameraSnapshotter.capture(rotation, new CameraSnapshotter.Callback() {
            @Override
            public void onCaptured(byte[] jpeg, String facing) {
                handler.post(() -> showStatus("Uploading camera snapshot…", true));
                network.execute(() -> {
                    try {
                        client.uploadCameraSnapshot(config, jpeg, facing, commandId);
                        handler.post(MainActivity.this::completeCameraUpload);
                    } catch (Exception error) {
                        handler.post(() -> completeCommand(
                                commands,
                                commandId,
                                false,
                                safeCommandDetail("camera-upload-failed", error)));
                    }
                });
            }

            @Override
            public void onError(String detail) {
                handler.post(() -> completeCommand(commands, commandId, false, detail));
            }
        });
    }

    private void completeCameraUpload() {
        commandInProgress = false;
        pendingCommandResult = "";
        pendingCommandId = "";
        if (foregroundActive) showTransientStatus("Camera snapshot uploaded");
        handler.postDelayed(() -> refresh(true), 250L);
    }

    private void completeCommand(String commands, String commandId, boolean success, String detail) {
        commandInProgress = false;
        pendingCommandResult = commands + ":" + (success ? "ok" : detail);
        pendingCommandId = commandId == null ? "" : commandId;
        if (foregroundActive) {
            showTransientStatus(success && commands.contains("camera-snapshot")
                    ? "Camera snapshot uploaded"
                    : success ? "" : "Command failed: " + detail);
        }
        handler.postDelayed(() -> refresh(true), 250L);
    }

    private static String safeCommandDetail(String prefix, Exception error) {
        String message = error.getMessage();
        if (message == null || message.trim().isEmpty()) return prefix;
        String safe = message.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9_-]+", "-");
        if (safe.length() > 48) safe = safe.substring(0, 48);
        return prefix + "-" + safe;
    }

    private void applyError(Exception error) {
        fetching = false;
        if (BuildConfig.COMPANION && !foregroundActive) {
            refreshPending = true;
            return;
        }
        refreshPending = false;
        showStatus("FlexDisplay offline\n" + error.getMessage() + "\n\nTap to retry · hold for settings", true);
        handler.postDelayed(scheduledRefresh, 15_000L);
    }

    private void showSettings() {
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        int padding = Math.round(24 * getResources().getDisplayMetrics().density);
        form.setPadding(padding, padding / 2, padding, 0);
        EditText url = new EditText(this);
        url.setHint("http://bridge-host:port");
        url.setText(config.bridgeUrl);
        url.setSingleLine(true);
        EditText deviceId = new EditText(this);
        deviceId.setHint(profile.idPrefix + "-LIVINGROOM");
        deviceId.setText(config.deviceId);
        deviceId.setSingleLine(true);
        form.addView(url);
        form.addView(deviceId);
        new AlertDialog.Builder(this)
                .setTitle("FlexDisplay " + profile.label)
                .setMessage(
                        "Enter the trusted LAN or VPN address of FlexDisplay Bridge. Changing the Bridge address or device ID creates a new receiver token; delete the old Bridge record before pairing that ID again. Hold outside an interactive tile to return here.")
                .setView(form)
                .setCancelable(config.isReady())
                .setNegativeButton(config.isReady() ? "Cancel" : null, null)
                .setPositiveButton("Connect", (dialog, which) -> {
                    ReceiverConfig entered = new ReceiverConfig(
                            url.getText().toString(),
                            deviceId.getText().toString(),
                            config.receiverToken,
                            config.microphoneEnabled);
                    boolean identityChanged = !entered.bridgeUrl.equals(config.bridgeUrl)
                            || !entered.deviceId.equals(config.deviceId);
                    config = identityChanged
                            ? new ReceiverConfig(entered.bridgeUrl, entered.deviceId)
                            : entered;
                    microphoneEnabled = config.microphoneEnabled;
                    config.save(this);
                    imageSha256 = "";
                    requestCompanionCameraPermission();
                    refresh(true);
                })
                .show();
    }

    private void showTransientStatus(String text) {
        showStatus(text, true);
        handler.postDelayed(() -> {
            if (notificationOverlay == null) showStatus("", false);
        }, 1_600L);
    }

    private void showStatus(String text, boolean visible) {
        statusView.setText(text);
        statusView.setVisibility(visible ? View.VISIBLE : View.GONE);
    }

    private void enterReceiverMode() {
        if (BuildConfig.COMPANION) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
            return;
        }
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        boolean granted = grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED;
        if (requestCode == VOICE_PERMISSION_REQUEST) {
            showTransientStatus(granted ? "Microphone ready" : "Microphone permission denied");
            refresh(true);
        } else if (requestCode == CAMERA_PERMISSION_REQUEST) {
            showTransientStatus(granted
                    ? "Camera snapshots enabled"
                    : "Camera snapshots remain disabled");
            refresh(true);
        }
    }

    private void requestCompanionCameraPermission() {
        if (!BuildConfig.COMPANION
                || cameraPermissionPrompted
                || !foregroundActive
                || checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
                || !getPackageManager().hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)) return;
        cameraPermissionPrompted = true;
        new AlertDialog.Builder(this)
                .setTitle("Enable camera snapshots?")
                .setMessage(
                        "FlexDisplay only opens the camera while this app is visible and after you request a snapshot from Home Assistant. Images are re-encoded without camera metadata and sent to your configured Bridge.")
                .setNegativeButton("Not now", (dialog, which) ->
                        showTransientStatus("Camera snapshots remain disabled"))
                .setPositiveButton("Continue", (dialog, which) -> requestPermissions(
                        new String[] { Manifest.permission.CAMERA },
                        CAMERA_PERMISSION_REQUEST))
                .show();
    }

    private static long parseLong(String value, long fallback) {
        try {
            return Long.parseLong(value);
        } catch (NumberFormatException error) {
            return fallback;
        }
    }

    private static int parseInt(String value, int fallback) {
        try {
            return Integer.parseInt(value);
        } catch (NumberFormatException error) {
            return fallback;
        }
    }
}

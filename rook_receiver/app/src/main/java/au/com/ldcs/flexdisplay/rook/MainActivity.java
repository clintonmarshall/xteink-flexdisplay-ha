package au.com.ldcs.flexdisplay.rook;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.KeyguardManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
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
import android.provider.Settings;
import android.service.quicksettings.TileService;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.text.DateFormat;
import java.util.Date;
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
    private static final long DOCK_EXIT_DELAY_MILLIS = 60_000L;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private final ExecutorService notificationNetwork = Executors.newSingleThreadExecutor();
    private final ExecutorService voiceNetwork = Executors.newSingleThreadExecutor();
    private final Runnable scheduledRefresh = () -> refresh(false);
    private final Runnable dockExit = this::exitDockIfUnpowered;
    private final BroadcastReceiver batteryReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            updateDockState();
        }
    };
    private final ReceiverProfile profile = ReceiverProfile.detect();
    private CameraSnapshotter cameraSnapshotter;
    private ReceiverConfig config;
    private FlexDisplayClient client;
    private FrameLayout root;
    private ImageView imageView;
    private TextView statusView;
    private Button assistButton;
    private Button privacyButton;
    private Button dockButton;
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
    private volatile boolean microphoneEnabled = true;
    private boolean batteryReceiverRegistered;
    private boolean dockEnabled;
    private boolean dockActive;
    private boolean dockDimmed;
    private int dockRestoreBrightnessPercent = 100;
    private String activeCameraCommands = "";
    private String activeCameraCommandId = "";
    private volatile String foregroundSession = "";
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
    private final Object audioPlaybackLock = new Object();
    private AudioTrack activeAssistTrack;
    private ToneGenerator activeChime;
    private Runnable delayedChimeTone;
    private Runnable delayedChimeRelease;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        cameraSnapshotter = new CameraSnapshotter(getApplicationContext());
        int windowFlags = 0;
        if (!BuildConfig.COMPANION) {
            windowFlags |= WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                    | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                    | WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED;
        } else {
            windowFlags |= WindowManager.LayoutParams.FLAG_SECURE;
        }
        if (windowFlags != 0) getWindow().addFlags(windowFlags);
        client = new FlexDisplayClient(this);
        client.setForegroundAllowed(foregroundActive);
        config = ReceiverConfig.load(this);
        microphoneEnabled = config.microphoneEnabled;
        dockEnabled = BuildConfig.COMPANION && CompanionPreferences.dockMode(this);
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
        showRequestedCompanionCentre();
    }

    @Override
    protected void onResume() {
        super.onResume();
        foregroundActive = true;
        foregroundSession = ForegroundSession.create();
        client.setForegroundSession(foregroundSession);
        client.setForegroundAllowed(true);
        registerBatteryReceiver();
        dockEnabled = BuildConfig.COMPANION && CompanionPreferences.dockMode(this);
        updateDockState();
        enterReceiverMode();
        if (BuildConfig.COMPANION && config.isReady()) {
            refresh(true);
        }
        showRequestedCompanionCentre();
    }

    @Override
    protected void onPause() {
        if (BuildConfig.COMPANION) {
            dismissNotification(null);
            foregroundSession = "";
            client.setForegroundSession("");
            foregroundActive = false;
            discardVoiceCapture = true;
            voiceRecording = false;
            stopActiveAudio();
            cameraSnapshotter.cancel();
            client.cancelCameraSnapshotUpload();
            handler.removeCallbacks(scheduledRefresh);
            handler.removeCallbacks(dockExit);
            refreshPending = true;
            notificationGeneration++;
            notificationLoopStarted = false;
            client.setForegroundAllowed(false);
            setDockActive(false);
            unregisterBatteryReceiver();
        }
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        cameraSnapshotter.cancel();
        stopActiveAudio();
        client.cancelForegroundRequests();
        client.cancelCameraSnapshotUpload();
        unregisterBatteryReceiver();
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
        FrameLayout.LayoutParams assistParams = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.END);
        assistParams.rightMargin = profile.round ? 78 : 18;
        assistParams.bottomMargin = profile.round ? 46 : 18;
        root.addView(assistButton, assistParams);

        if (BuildConfig.COMPANION) {
            privacyButton = smallOverlayButton("Privacy");
            privacyButton.setOnClickListener(view -> showPrivacyCentre());
            FrameLayout.LayoutParams privacyParams = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.END);
            privacyParams.topMargin = 14;
            privacyParams.rightMargin = 14;
            root.addView(privacyButton, privacyParams);

            dockButton = smallOverlayButton("Dock off");
            dockButton.setOnClickListener(view -> confirmToggleDockMode());
            FrameLayout.LayoutParams dockParams = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.START);
            dockParams.leftMargin = 14;
            dockParams.bottomMargin = 14;
            root.addView(dockButton, dockParams);
            updateDockButton();
        }

        root.setClickable(true);
        root.setOnTouchListener(this::onTouch);
        setContentView(root);
        showStatus("Connecting to FlexDisplay…", true);
    }

    private Button smallOverlayButton(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextSize(10);
        button.setTextColor(Color.WHITE);
        button.setBackgroundColor(Color.argb(190, 24, 63, 82));
        return button;
    }

    private void exitDockIfUnpowered() {
        if (BuildConfig.COMPANION
                && dockEnabled
                && !BatteryTelemetry.read(this).dockPowered()) {
            setDockActive(false);
            finishAndRemoveTask();
        }
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
        if (BuildConfig.COMPANION) CompanionPreferences.recordSync(this);
        applyDesiredState(result);
        showStatus("", false);
        startNotificationLoop();

        String commands = result.header("X-FlexDisplay-Commands");
        String commandId = result.header("X-FlexDisplay-Command-ID");
        String commandForegroundSession =
                result.header("X-FlexDisplay-Command-Foreground-Session");
        boolean commandRefreshScheduled = !commands.isEmpty();
        if (commandRefreshScheduled) {
            executeCommands(commands, commandId, commandForegroundSession);
        }

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
                        if (generation != notificationGeneration || !isInteractiveForeground()) {
                            if (image != null) image.recycle();
                            break;
                        }
                        handler.post(() -> {
                            if (generation != notificationGeneration || !isInteractiveForeground()) {
                                if (image != null) image.recycle();
                                return;
                            }
                            showNotification(event.notification, image);
                        });
                    } else if (event.sequence > previousSequence && !event.refresh) {
                        handler.post(() -> dismissNotification(null));
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
        dismissNotification(null);
        activeNotification = notification;
        notificationOverlay = new FrameLayout(this);
        notificationOverlay.setBackgroundColor(Color.rgb(4, 10, 17));
        notificationOverlay.setClickable(true);
        notificationOverlay.setOnClickListener(view -> dismissNotification("dismissed"));

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
        dismiss.setOnClickListener(view -> dismissNotification("dismissed"));
        panel.addView(dismiss, new LinearLayout.LayoutParams(-1, -2));

        FrameLayout.LayoutParams panelParams = new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM);
        panelParams.leftMargin = 58;
        panelParams.rightMargin = 58;
        panelParams.bottomMargin = 46;
        notificationOverlay.addView(panel, panelParams);
        root.addView(notificationOverlay, new FrameLayout.LayoutParams(-1, -1));
        playChime(notification.chime);
        notificationDismissal = () -> dismissNotification("expired");
        handler.postDelayed(notificationDismissal, notification.duration * 1000L);
        enterReceiverMode();
    }

    private boolean isInteractiveForeground() {
        KeyguardManager keyguard = (KeyguardManager) getSystemService(KEYGUARD_SERVICE);
        boolean unlocked = keyguard == null || !keyguard.isDeviceLocked();
        return InteractiveForegroundPolicy.allows(
                BuildConfig.COMPANION,
                foregroundActive,
                unlocked);
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
                String detail = client.reportNotificationResponse(
                        config, notification.id, "action", action.id, confirmed);
                handler.post(() -> {
                    showTransientStatus(detail);
                    handler.postDelayed(() -> refresh(true), 500L);
                });
            } catch (Exception error) {
                handler.post(() -> {
                    if (foregroundActive) {
                        showTransientStatus("Action response failed\n" + error.getMessage());
                    }
                });
            }
        });
        dismissNotification(null);
    }

    private void dismissNotification(String outcome) {
        if (notificationDismissal != null) handler.removeCallbacks(notificationDismissal);
        notificationDismissal = null;
        FlexDisplayClient.Notification dismissed = activeNotification;
        activeNotification = null;
        if (notificationOverlay != null) root.removeView(notificationOverlay);
        notificationOverlay = null;
        if (outcome != null && dismissed != null) {
            network.execute(() -> {
                try {
                    client.reportNotificationResponse(
                            config, dismissed.id, outcome, null, false);
                } catch (Exception ignored) {
                    // The alert still expires on the Bridge if acknowledgement is lost.
                }
            });
        }
    }

    private void playChime(String chime) {
        if ("none".equals(chime)) return;
        if (currentMusicVolumePercent() == 0) return;
        stopActiveChime();
        ToneGenerator tone = new ToneGenerator(AudioManager.STREAM_MUSIC, 90);
        synchronized (audioPlaybackLock) {
            activeChime = tone;
        }
        int selected = "alert".equals(chime)
                ? ToneGenerator.TONE_PROP_ACK
                : ToneGenerator.TONE_PROP_BEEP2;
        tone.startTone(selected, 280);
        if ("doorbell".equals(chime)) {
            delayedChimeTone = () -> {
                synchronized (audioPlaybackLock) {
                    if (activeChime != tone || (BuildConfig.COMPANION && !foregroundActive)) return;
                    tone.startTone(ToneGenerator.TONE_PROP_BEEP2, 420);
                }
            };
            handler.postDelayed(delayedChimeTone, 430L);
        }
        delayedChimeRelease = () -> releaseChime(tone);
        handler.postDelayed(delayedChimeRelease, 1_200L);
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
        int selected = BrightnessTelemetry.clampWindowPercent(percent);
        WindowManager.LayoutParams params = getWindow().getAttributes();
        params.screenBrightness = selected / 100f;
        getWindow().setAttributes(params);
        client.setBrightnessPercent(selected);
    }

    private int currentWindowBrightnessPercent() {
        float selected = getWindow().getAttributes().screenBrightness;
        int fallback = client == null ? 100 : client.brightnessPercent();
        return BrightnessTelemetry.fromWindowValue(selected, fallback);
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
            if (!isInteractiveForeground()) return;
            handler.post(() -> {
                if (isInteractiveForeground()) {
                    setAssistActive(false, response.summary());
                }
            });
            if (!isInteractiveForeground()) return;
            playAssistAudio(response);
            handler.postDelayed(() -> refresh(true), 900L);
        } catch (Exception error) {
            voiceBusy = false;
            handler.post(() -> {
                if (isInteractiveForeground()) {
                    setAssistActive(false, "Assist failed\n" + error.getMessage());
                }
            });
        }
    }

    private void playAssistAudio(FlexDisplayClient.VoiceAssistantResponse response) {
        if (!isInteractiveForeground()) return;
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
        synchronized (audioPlaybackLock) {
            if (!isInteractiveForeground()) {
                track.release();
                return;
            }
            activeAssistTrack = track;
        }
        try {
            track.play();
            track.write(response.audio, 0, response.audio.length);
            int durationMs = Math.max(250, (response.audio.length / 2) * 1000 / sampleRate);
            SystemClock.sleep(durationMs + 150L);
            track.stop();
        } catch (IllegalStateException ignored) {
            // Activity pause may stop and release the track concurrently.
        } finally {
            synchronized (audioPlaybackLock) {
                if (activeAssistTrack == track) activeAssistTrack = null;
            }
            try {
                track.release();
            } catch (IllegalStateException ignored) {
                // Already released during Activity pause.
            }
        }
    }

    private void stopActiveAudio() {
        stopActiveChime();
        synchronized (audioPlaybackLock) {
            AudioTrack track = activeAssistTrack;
            activeAssistTrack = null;
            if (track != null) {
                try {
                    track.pause();
                    track.flush();
                    track.stop();
                } catch (IllegalStateException ignored) {
                    // Playback may not have reached the playing state yet.
                }
                try {
                    track.release();
                } catch (IllegalStateException ignored) {
                    // Already released concurrently by the playback thread.
                }
            }
        }
    }

    private void stopActiveChime() {
        if (delayedChimeTone != null) handler.removeCallbacks(delayedChimeTone);
        if (delayedChimeRelease != null) handler.removeCallbacks(delayedChimeRelease);
        delayedChimeTone = null;
        delayedChimeRelease = null;
        synchronized (audioPlaybackLock) {
            ToneGenerator tone = activeChime;
            activeChime = null;
            if (tone != null) {
                tone.stopTone();
                tone.release();
            }
        }
    }

    private void releaseChime(ToneGenerator tone) {
        synchronized (audioPlaybackLock) {
            if (activeChime != tone) return;
            activeChime = null;
            tone.stopTone();
            tone.release();
        }
        delayedChimeTone = null;
        delayedChimeRelease = null;
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

    private void executeCommands(
            String commands, String commandId, String commandForegroundSession) {
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
                        if (!ForegroundSession.matchesCameraCommand(
                                foregroundSession, commandForegroundSession)) {
                            success = false;
                            detail = "camera-snapshot:session-mismatch";
                        } else {
                            cameraSnapshot = true;
                        }
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
        if (BuildConfig.COMPANION) CompanionPreferences.recordCameraRequest(this);
        if (!snapshotsLocallyAllowed()) {
            completeCommand(commands, commandId, false, "camera-local-policy-off");
            return;
        }
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
        activeCameraCommands = commands;
        activeCameraCommandId = commandId;
        showStatus("Taking camera snapshot…", true);
        int rotation = getWindowManager().getDefaultDisplay().getRotation();
        cameraSnapshotter.capture(rotation, new CameraSnapshotter.Callback() {
            @Override
            public void onCaptured(byte[] jpeg, String facing) {
                handler.post(() -> showStatus("Uploading camera snapshot…", true));
                network.execute(() -> {
                    try {
                        if (!snapshotsLocallyAllowed()) {
                            throw new IOException("Local snapshot policy is off");
                        }
                        KeyguardManager currentKeyguard =
                                (KeyguardManager) getSystemService(KEYGUARD_SERVICE);
                        if (!foregroundActive
                                || (currentKeyguard != null && currentKeyguard.isDeviceLocked())) {
                            throw new IOException("Phone is no longer visible and unlocked");
                        }
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
        activeCameraCommands = "";
        activeCameraCommandId = "";
        pendingCommandResult = "";
        pendingCommandId = "";
        if (BuildConfig.COMPANION) CompanionPreferences.recordCameraOutcome(this, "Uploaded");
        if (foregroundActive) showTransientStatus("Camera snapshot uploaded");
        handler.postDelayed(() -> refresh(true), 250L);
    }

    private void completeCommand(String commands, String commandId, boolean success, String detail) {
        commandInProgress = false;
        activeCameraCommands = "";
        activeCameraCommandId = "";
        if (BuildConfig.COMPANION && commands.contains("camera-snapshot")) {
            CompanionPreferences.recordCameraOutcome(this, success ? "Uploaded" : detail);
        }
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
        if (BuildConfig.COMPANION) CompanionPreferences.recordError(this, error.getMessage());
        showStatus("FlexDisplay offline\n" + error.getMessage() + "\n\nTap to retry · hold for settings", true);
        handler.postDelayed(scheduledRefresh, 15_000L);
    }

    private void registerBatteryReceiver() {
        if (!BuildConfig.COMPANION || batteryReceiverRegistered) return;
        registerReceiver(batteryReceiver, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        batteryReceiverRegistered = true;
    }

    private void unregisterBatteryReceiver() {
        if (!batteryReceiverRegistered) return;
        try {
            unregisterReceiver(batteryReceiver);
        } catch (IllegalArgumentException ignored) {
            // Already unregistered by Activity teardown.
        }
        batteryReceiverRegistered = false;
    }

    private void updateDockState() {
        if (!BuildConfig.COMPANION || client == null) return;
        boolean previousEnabled = dockEnabled;
        boolean previousActive = dockActive;
        dockEnabled = CompanionPreferences.dockMode(this);
        BatteryTelemetry battery = BatteryTelemetry.read(this);
        KeyguardManager keyguard = (KeyguardManager) getSystemService(KEYGUARD_SERVICE);
        boolean unlocked = keyguard == null || !keyguard.isDeviceLocked();
        boolean powered = battery.dockPowered();
        boolean active = dockEnabled && powered && foregroundActive && unlocked;
        setDockActive(active);

        handler.removeCallbacks(dockExit);
        if (dockEnabled && foregroundActive && battery.present && !powered) {
            if (!dockDimmed) {
                dockRestoreBrightnessPercent = currentWindowBrightnessPercent();
                dockDimmed = true;
            }
            setWindowBrightness(5);
            handler.postDelayed(dockExit, DOCK_EXIT_DELAY_MILLIS);
        } else if ((active || !dockEnabled) && dockDimmed) {
            setWindowBrightness(dockRestoreBrightnessPercent);
            dockDimmed = false;
        }
        updateDockButton();
        if (config != null
                && config.isReady()
                && foregroundActive
                && (previousEnabled != dockEnabled || previousActive != dockActive)) {
            handler.removeCallbacks(scheduledRefresh);
            handler.postDelayed(() -> refresh(true), 150L);
        }
    }

    private void setDockActive(boolean active) {
        if (!BuildConfig.COMPANION) return;
        dockActive = active && foregroundActive && dockEnabled;
        if (dockActive) {
            getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        } else {
            getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        }
        if (client != null) client.setDockState(dockEnabled, dockActive);
    }

    private void updateDockButton() {
        if (dockButton == null) return;
        dockButton.setText(dockActive ? "Dock active" : dockEnabled ? "Dock waiting" : "Dock off");
    }

    private void confirmToggleDockMode() {
        if (!BuildConfig.COMPANION) return;
        if (CompanionPreferences.dockMode(this)) {
            setDockModeLocally(false);
            showTransientStatus("Dock Mode disabled");
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("Enable Dock Mode?")
                .setMessage(
                        "Dock Mode keeps FlexDisplay awake only while this app is visible, the phone is unlocked, and a known power source is connected. If power is removed it dims, stops after 60 seconds, and never enables background camera, microphone, or media.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Enable", (dialog, which) -> {
                    setDockModeLocally(true);
                    showTransientStatus(dockActive
                            ? "Dock Mode active"
                            : "Dock Mode waiting for power");
                })
                .show();
    }

    private void setDockModeLocally(boolean enabled) {
        CompanionPreferences.setDockMode(this, enabled);
        dockEnabled = enabled;
        if (!enabled) {
            handler.removeCallbacks(dockExit);
            setDockActive(false);
            if (dockDimmed) {
                setWindowBrightness(dockRestoreBrightnessPercent);
                dockDimmed = false;
            }
        }
        updateDockState();
        TileService.requestListeningState(
                this,
                new android.content.ComponentName(this, CompanionDockTileService.class));
        refresh(true);
    }

    private void showRequestedCompanionCentre() {
        if (!BuildConfig.COMPANION || getIntent() == null) return;
        if (CompanionDockTileService.ACTION_SHOW_DOCK_CENTRE.equals(getIntent().getAction())) {
            getIntent().setAction(null);
            handler.post(this::showPrivacyCentre);
        }
    }

    private void showPrivacyCentre() {
        if (!BuildConfig.COMPANION || isFinishing()) return;
        BatteryTelemetry battery = BatteryTelemetry.read(this);
        boolean cameraAllowed = snapshotsLocallyAllowed();
        boolean cameraPermission = checkSelfPermission(Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED;
        boolean microphonePermission = checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;

        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        int padding = Math.round(20 * getResources().getDisplayMetrics().density);
        content.setPadding(padding, padding / 2, padding, padding);

        TextView status = new TextView(this);
        status.setTextColor(Color.WHITE);
        status.setTextSize(14);
        status.setText(
                "Bridge: " + displayValue(config.bridgeUrl, "Not configured")
                        + "\nDevice: " + config.deviceId
                        + "\nLast sync: " + displayTime(CompanionPreferences.lastSyncAt(this))
                        + "\nLast error: " + displayError()
                        + "\n\nCamera policy: " + (cameraAllowed ? "Allow while open" : "Off")
                        + "\nAndroid camera permission: " + grantedText(cameraPermission)
                        + "\nLast camera request: " + displayTime(CompanionPreferences.lastCameraRequestAt(this))
                        + "\nLast camera outcome: " + CompanionPreferences.lastCameraOutcome(this)
                        + " at " + displayTime(CompanionPreferences.lastCameraOutcomeAt(this))
                        + "\n\nMicrophone policy: " + (microphoneEnabled ? "Local push-to-talk allowed" : "Disabled by Home Assistant")
                        + "\nAndroid microphone permission: " + grantedText(microphonePermission)
                        + "\n\nDock Mode: " + (dockEnabled ? "Enabled" : "Off")
                        + "\nDock active: " + (dockActive ? "Yes" : "No")
                        + "\nPower: " + (battery.present ? battery.plugType + " / " + battery.status : "Unknown"));
        content.addView(status, new LinearLayout.LayoutParams(-1, -2));

        Button camera = new Button(this);
        camera.setText(cameraAllowed ? "Turn HA snapshots off" : "Allow HA snapshots while open…");
        camera.setOnClickListener(view -> {
            if (snapshotsLocallyAllowed()) {
                disableSnapshotsLocally();
                camera.setText("Allow HA snapshots while open…");
                showTransientStatus("HA snapshots disabled");
            } else {
                confirmEnableSnapshots();
            }
        });
        content.addView(camera, new LinearLayout.LayoutParams(-1, -2));

        Button dock = new Button(this);
        dock.setText(dockEnabled ? "Turn Dock Mode off" : "Enable Dock Mode…");
        dock.setOnClickListener(view -> confirmToggleDockMode());
        content.addView(dock, new LinearLayout.LayoutParams(-1, -2));

        Button appSettings = new Button(this);
        appSettings.setText("Open Android app settings");
        appSettings.setOnClickListener(view -> startActivity(new Intent(
                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + getPackageName()))));
        content.addView(appSettings, new LinearLayout.LayoutParams(-1, -2));

        Button connection = new Button(this);
        connection.setText("Bridge connection settings");
        connection.setOnClickListener(view -> showSettings());
        content.addView(connection, new LinearLayout.LayoutParams(-1, -2));

        ScrollView scroll = new ScrollView(this);
        scroll.addView(content);
        new AlertDialog.Builder(this)
                .setTitle("FlexDisplay Privacy Centre")
                .setMessage(
                        "Camera and microphone access remains foreground-only. Home Assistant administrators and automations can request one photo only when local snapshot policy allows it, this app is visible, and the phone is unlocked.")
                .setView(scroll)
                .setNegativeButton("Close", null)
                .show();
    }

    private void confirmEnableSnapshots() {
        new AlertDialog.Builder(this)
                .setTitle("Allow HA snapshots while open?")
                .setMessage(
                        "A Home Assistant administrator or automation may request a single photo while FlexDisplay is visible and this phone is unlocked. Every request shows an on-screen capture/upload indicator. FlexDisplay never captures in the background or starts the camera to request Android permission.")
                .setNegativeButton("Keep off", null)
                .setPositiveButton("Allow", (dialog, which) -> {
                    CompanionPreferences.setCameraPolicy(
                            this, CompanionPreferences.CAMERA_POLICY_ALLOW_WHILE_OPEN);
                    if (checkSelfPermission(Manifest.permission.CAMERA)
                            != PackageManager.PERMISSION_GRANTED) {
                        requestPermissions(
                                new String[] { Manifest.permission.CAMERA },
                                CAMERA_PERMISSION_REQUEST);
                    } else {
                        showTransientStatus("HA snapshots allowed while open");
                        refresh(true);
                    }
                })
                .show();
    }

    private void disableSnapshotsLocally() {
        CompanionPreferences.setCameraPolicy(this, CompanionPreferences.CAMERA_POLICY_OFF);
        String commandId = activeCameraCommandId;
        cameraSnapshotter.cancel();
        client.cancelCameraSnapshotUpload();
        if (!commandId.isEmpty()) {
            handler.postDelayed(() -> {
                if (commandInProgress && commandId.equals(activeCameraCommandId)) {
                    completeCommand(
                            activeCameraCommands,
                            activeCameraCommandId,
                            false,
                            "camera-local-policy-off");
                }
            }, 2_000L);
        }
        refresh(true);
    }

    private boolean snapshotsLocallyAllowed() {
        return !BuildConfig.COMPANION || CompanionPreferences.CAMERA_POLICY_ALLOW_WHILE_OPEN.equals(
                CompanionPreferences.cameraPolicy(this));
    }

    private String displayError() {
        String error = CompanionPreferences.lastError(this);
        return error.isEmpty()
                ? "None"
                : error + " at " + displayTime(CompanionPreferences.lastErrorAt(this));
    }

    private static String displayTime(long millis) {
        return millis <= 0
                ? "Never"
                : DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.MEDIUM)
                        .format(new Date(millis));
    }

    private static String displayValue(String value, String fallback) {
        return value == null || value.trim().isEmpty() ? fallback : value;
    }

    private static String grantedText(boolean granted) {
        return granted ? "Granted" : "Not granted";
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
                    if (identityChanged && BuildConfig.COMPANION) {
                        disableSnapshotsLocally();
                    }
                    microphoneEnabled = config.microphoneEnabled;
                    config.save(this);
                    imageSha256 = "";
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
            if (!granted) {
                CompanionPreferences.setCameraPolicy(
                        this, CompanionPreferences.CAMERA_POLICY_OFF);
            }
            showTransientStatus(granted
                    ? "Camera snapshots enabled"
                    : "Camera snapshots remain disabled");
            refresh(true);
        }
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

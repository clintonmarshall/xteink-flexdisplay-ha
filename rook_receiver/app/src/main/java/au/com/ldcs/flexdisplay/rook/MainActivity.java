package au.com.ldcs.flexdisplay.rook;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.media.AudioManager;
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

import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private final ExecutorService notificationNetwork = Executors.newSingleThreadExecutor();
    private final Runnable scheduledRefresh = () -> refresh(false);
    private final ReceiverProfile profile = ReceiverProfile.detect();
    private ReceiverConfig config;
    private FlexDisplayClient client;
    private FrameLayout root;
    private ImageView imageView;
    private TextView statusView;
    private Bitmap currentBitmap;
    private String imageSha256 = "";
    private String pendingCommandResult = "";
    private String pendingCommandId = "";
    private String pendingQuickAction = "";
    private boolean fetching;
    private boolean refreshPending;
    private boolean destroyed;
    private boolean notificationLoopStarted;
    private long notificationSequence;
    private List<FlexDisplayClient.Interaction> interactions = Collections.emptyList();
    private FrameLayout notificationOverlay;
    private FlexDisplayClient.Notification activeNotification;
    private Runnable notificationDismissal;
    private float touchStartX;
    private float touchStartY;
    private long touchStartedAt;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                        | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                        | WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED);
        client = new FlexDisplayClient(this);
        config = ReceiverConfig.load(this);
        applyIntentConfiguration();
        buildUi();
        enterKioskMode();
        if (config.isReady()) refresh(false); else showSettings();
    }

    @Override
    protected void onNewIntent(android.content.Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        applyIntentConfiguration();
        refresh(true);
    }

    @Override
    protected void onResume() {
        super.onResume();
        enterKioskMode();
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        handler.removeCallbacksAndMessages(null);
        network.shutdownNow();
        notificationNetwork.shutdownNow();
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

        root.setClickable(true);
        root.setOnTouchListener(this::onTouch);
        setContentView(root);
        showStatus("Connecting to FlexDisplay…", true);
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
        if (result.bitmap != null) {
            currentBitmap = result.bitmap;
            imageView.setImageBitmap(result.bitmap);
        }
        String digest = result.header("X-FlexDisplay-Image-SHA256");
        if (!digest.isEmpty()) imageSha256 = digest;
        interactions = result.interactions;
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
        if (notificationLoopStarted || !config.isReady()) return;
        notificationLoopStarted = true;
        notificationNetwork.execute(() -> {
            while (!destroyed && !Thread.currentThread().isInterrupted()) {
                try {
                    ReceiverConfig selectedConfig = config;
                    long previousSequence = notificationSequence;
                    FlexDisplayClient.NotificationEvent event =
                            client.waitForNotification(selectedConfig, notificationSequence);
                    notificationSequence = Math.max(notificationSequence, event.sequence);
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
                    if (!destroyed) SystemClock.sleep(2_000L);
                }
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
        enterKioskMode();
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
        ToneGenerator tone = new ToneGenerator(AudioManager.STREAM_NOTIFICATION, 90);
        int selected = "alert".equals(chime)
                ? ToneGenerator.TONE_PROP_ACK
                : ToneGenerator.TONE_PROP_BEEP2;
        tone.startTone(selected, 280);
        if ("doorbell".equals(chime)) {
            handler.postDelayed(() -> tone.startTone(ToneGenerator.TONE_PROP_BEEP2, 420), 430L);
        }
        handler.postDelayed(tone::release, 1_200L);
    }

    private void executeCommands(String commands, String commandId) {
        boolean success = true;
        String detail = "ok";
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
        pendingCommandResult = commands + ":" + (success ? "ok" : detail);
        pendingCommandId = commandId;
        handler.postDelayed(() -> refresh(true), 250L);
    }

    private void applyError(Exception error) {
        fetching = false;
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
                .setMessage("Enter the LAN address of FlexDisplay Bridge. Hold outside an interactive tile to return here.")
                .setView(form)
                .setCancelable(config.isReady())
                .setNegativeButton(config.isReady() ? "Cancel" : null, null)
                .setPositiveButton("Connect", (dialog, which) -> {
                    config = new ReceiverConfig(
                            url.getText().toString(),
                            deviceId.getText().toString(),
                            config.receiverToken);
                    config.save(this);
                    imageSha256 = "";
                    refresh(true);
                })
                .show();
    }

    private void applyIntentConfiguration() {
        String url = getIntent().getStringExtra("bridge_url");
        String id = getIntent().getStringExtra("device_id");
        if (url != null || id != null) {
            config = new ReceiverConfig(
                    url == null ? config.bridgeUrl : url,
                    id == null ? config.deviceId : id,
                    config.receiverToken);
            config.save(this);
        }
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

    private void enterKioskMode() {
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }

    private static long parseLong(String value, long fallback) {
        try {
            return Long.parseLong(value);
        } catch (NumberFormatException error) {
            return fallback;
        }
    }
}

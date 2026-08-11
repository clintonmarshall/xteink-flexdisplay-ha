package au.com.ldcs.flexdisplay.rook;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final long DEFAULT_REFRESH_MILLIS = 60_000L;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private final Runnable scheduledRefresh = () -> refresh(false);
    private ReceiverConfig config;
    private FlexDisplayClient client;
    private ImageView imageView;
    private TextView statusView;
    private Bitmap currentBitmap;
    private String imageSha256 = "";
    private String pendingCommandResult = "";
    private String pendingCommandId = "";
    private String pendingQuickAction = "";
    private boolean fetching;
    private float touchStartX;
    private float touchStartY;
    private long touchStartedAt;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
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
        handler.removeCallbacksAndMessages(null);
        network.shutdownNow();
        super.onDestroy();
    }

    private void buildUi() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(4, 10, 17));
        imageView = new ImageView(this);
        imageView.setScaleType(ImageView.ScaleType.FIT_XY);
        root.addView(imageView, new FrameLayout.LayoutParams(-1, -1));

        statusView = new TextView(this);
        statusView.setTextColor(Color.WHITE);
        statusView.setBackgroundColor(Color.argb(190, 4, 10, 17));
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
        if (duration > 850 && Math.abs(dx) < 45 && Math.abs(dy) < 45) {
            showSettings();
        } else if (Math.abs(dx) > 85 && Math.abs(dx) > Math.abs(dy)) {
            pendingQuickAction = dx < 0 ? "next" : "previous";
            refresh(true);
        } else {
            view.performClick();
            pendingQuickAction = "refresh";
            refresh(true);
        }
        return true;
    }

    private void refresh(boolean immediate) {
        if (!config.isReady() || fetching) return;
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
        showStatus("", false);

        String commands = result.header("X-FlexDisplay-Commands");
        String commandId = result.header("X-FlexDisplay-Command-ID");
        if (!commands.isEmpty()) executeCommands(commands, commandId);

        long refreshSeconds = parseLong(result.header("X-FlexDisplay-Refresh-Interval"), 60L);
        refreshSeconds = Math.max(15L, Math.min(3600L, refreshSeconds));
        handler.postDelayed(scheduledRefresh, refreshSeconds * 1000L);
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
        deviceId.setHint("ROOK-LIVINGROOM");
        deviceId.setText(config.deviceId);
        deviceId.setSingleLine(true);
        form.addView(url);
        form.addView(deviceId);
        new AlertDialog.Builder(this)
                .setTitle("FlexDisplay Spot")
                .setMessage("Enter the LAN address of FlexDisplay Bridge. Hold the display at any time to return here.")
                .setView(form)
                .setCancelable(config.isReady())
                .setNegativeButton(config.isReady() ? "Cancel" : null, null)
                .setPositiveButton("Connect", (dialog, which) -> {
                    config = new ReceiverConfig(url.getText().toString(), deviceId.getText().toString());
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
            config = new ReceiverConfig(url == null ? config.bridgeUrl : url, id == null ? config.deviceId : id);
            config.save(this);
        }
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

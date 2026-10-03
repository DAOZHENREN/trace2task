package dev.trace2task.mobile;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.app.KeyguardManager;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Point;
import android.graphics.Rect;
import android.hardware.HardwareBuffer;
import android.hardware.display.DisplayManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Base64;
import android.view.Display;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.lang.ref.WeakReference;
import java.util.ArrayDeque;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

/** User-started autonomous loop; never runs merely because accessibility is enabled. */
public final class MobileAgentService extends AccessibilityService {
    private static WeakReference<MobileAgentService> instance = new WeakReference<>(null);
    static MobileAgentService current() { return instance.get(); }
    static String lastStatus = "未开始任务";
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final RunGuard guard = new RunGuard();
    private final ArrayDeque<String> history = new ArrayDeque<>();
    private ModelClient client;
    private String targetPackage;
    private String task;
    private LinearLayout overlay;
    private TextView overlayText;
    private long runToken;
    private int staleCount;
    private String lastAction = "";
    private int repeatedActions;
    private long phase;
    private Demonstration recording;
    private boolean recordingReady, recordCaptureBusy, recordCaptureQueued;
    private long lastRecordCapture;

    @Override protected void onServiceConnected() { instance = new WeakReference<>(this); }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) {
        // Enabling the service does not collect anything. Agent-generated actions are never recorded.
        if (recording == null || !recordingReady || !guard.active()) return;
        if (!targetPackage.equals(String.valueOf(event.getPackageName()))) return;
        int type = event.getEventType();
        if (type != AccessibilityEvent.TYPE_VIEW_CLICKED && type != AccessibilityEvent.TYPE_VIEW_LONG_CLICKED
                && type != AccessibilityEvent.TYPE_VIEW_SCROLLED && type != AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED) return;
        AccessibilityNodeInfo source = event.getSource();
        try {
            if (event.isPassword() || (source != null && source.isPassword())) {
                stopRun("密码事件，录制已停止；未保存该事件"); return;
            }
            AccessibilityNodeInfo root = checkedRoot();
            root.recycle();
            JSONObject evidence = new JSONObject().put("type", AccessibilityEvent.eventTypeToString(type))
                    .put("event_time_ms", event.getEventTime()).put("origin", "accessibility_event_not_verified_human_input")
                    .put("text", bounded(event.getText().toString(), 500))
                    .put("class", bounded(String.valueOf(event.getClassName()), 200));
            if (source != null) {
                Rect bounds = new Rect(); source.getBoundsInScreen(bounds);
                evidence.put("node_bounds_not_touch_point", bounds.flattenToString())
                        .put("view_id", bounded(source.getViewIdResourceName(), 200))
                        .put("description", bounded(String.valueOf(source.getContentDescription()), 200));
            }
            addRecordedEvent(evidence);
        } catch (Exception error) { fail(runToken, error); }
        finally { if (source != null) source.recycle(); }
    }
    @Override public void onInterrupt() { stopRun("服务被中断，任务已停止"); }
    @Override public void onDestroy() {
        stopRun("服务已关闭");
        worker.shutdownNow();
        if (current() == this) instance.clear();
        super.onDestroy();
    }
    @Override protected boolean onKeyEvent(KeyEvent event) {
        if (guard.active() && event.getKeyCode() == KeyEvent.KEYCODE_VOLUME_DOWN
                && event.getAction() == KeyEvent.ACTION_DOWN) stopRun("音量下键急停");
        return false; // Preserve the user's volume key behavior.
    }

    boolean running() { return guard.active(); }

    void startRecording(String target, String task) throws Exception {
        if (running()) throw new IllegalStateException("已有任务或录制在运行，请先停止");
        if (target == null || target.equals(getPackageName()) || task.trim().isEmpty() || task.length() > 4000)
            throw new IllegalArgumentException("请选择应用并填写不超过 4000 字的示范目标");
        this.targetPackage = target;
        this.task = task;
        recording = new Demonstration(new ExperienceStore(new File(getFilesDir(), "experience-library")), target, task);
        recordingReady = recordCaptureBusy = recordCaptureQueued = false;
        lastRecordCapture = 0;
        runToken = guard.start(100);
        showOverlay();
        status("即将录制 · 请人工操作目标应用");
        long token = runToken;
        main.postDelayed(() -> {
            if (!guard.current(token)) return;
            recordingReady = true;
            queueRecordedCapture(token);
            monitorRecording(token);
        }, 1800);
        main.postDelayed(() -> { if (guard.current(token)) stopRun("达到 5 分钟录制上限"); }, 300000);
    }

    private static String bounded(String value, int length) {
        return value == null ? "" : value.substring(0, Math.min(value.length(), length));
    }

    private void monitorRecording(long token) {
        if (!guard.current(token) || recording == null) return;
        try {
            AccessibilityNodeInfo root = checkedRoot(); root.recycle();
            main.postDelayed(() -> monitorRecording(token), 1000);
        } catch (Exception error) { fail(token, error); }
    }

    private void addRecordedEvent(JSONObject event) throws Exception {
        if (recording == null) return;
        if (!recording.event(event)) { stopRun("达到 80 条事件上限"); return; }
        status("录制中 · " + recording.count() + " 条事件 · 请人工操作");
        queueRecordedCapture(runToken);
    }

    private void queueRecordedCapture(long token) {
        if (!guard.current(token) || recording == null || recordCaptureQueued || !recording.canCapture()) return;
        recordCaptureQueued = true;
        long delay = Math.max(700, 1000 - (SystemClock.elapsedRealtime() - lastRecordCapture));
        main.postDelayed(() -> {
            if (!guard.current(token) || recording == null) return;
            recordCaptureQueued = false;
            if (recordCaptureBusy) { queueRecordedCapture(token); return; }
            Demonstration currentRecording = recording;
            int throughEvent = currentRecording.count();
            recordCaptureBusy = true;
            lastRecordCapture = SystemClock.elapsedRealtime();
            capture(token, frame -> worker.execute(() -> {
                try {
                    if (guard.current(token)) {
                        String png = encode(frame.bitmap);
                        if (guard.current(token)) currentRecording.frame(png, throughEvent, frame.width, frame.height);
                    }
                } catch (Exception error) { main.post(() -> fail(token, error)); }
                finally {
                    frame.bitmap.recycle();
                    main.post(() -> { if (guard.current(token)) recordCaptureBusy = false; });
                }
            }));
        }, delay);
    }

    void startRun(String target, String task, ModelClient client, int limit, String experienceVersion) {
        if (running()) throw new IllegalStateException("已有任务在运行，请先停止");
        if (target == null || target.equals(getPackageName()) || task.trim().isEmpty() || task.length() > 4000)
            throw new IllegalArgumentException("请选择目标应用并填写不超过 4000 字的任务");
        this.targetPackage = target;
        this.task = task;
        this.client = client;
        history.clear();
        staleCount = repeatedActions = 0;
        lastAction = "";
        runToken = guard.start(limit);
        // This private file contains only bounded, redacted dispatch metadata, never screenshots or keys.
        try (FileOutputStream out = new FileOutputStream(new File(getFilesDir(), "last-run.jsonl"))) {
            out.write("".getBytes(StandardCharsets.UTF_8));
        } catch (Exception error) { stopRun("无法创建本地运行记录"); throw new IllegalStateException(lastStatus); }
        if (!experienceVersion.isEmpty()) audit("experience_version", experienceVersion);
        showOverlay();
        status("即将开始 · 音量下键可急停");
        long token = runToken;
        main.postDelayed(() -> observe(token), 1800);
    }

    void stopRun(String message) {
        guard.stop();
        Demonstration previousRecording = recording;
        recording = null;
        recordingReady = false;
        if (previousRecording != null) {
            try {
                previousRecording.finish(message);
                message += " · 示范已保存到经验库（未验证完成；结束前未完成的截图可能缺失）";
            } catch (Exception error) { message += " · 保存结束状态失败；已有草稿仍保留在经验库"; }
        }
        // Invalidating generation is synchronous: no delayed callback can dispatch after this point.
        ModelClient previous = client;
        client = null;
        if (previous != null && !worker.isShutdown()) workerCancel(previous);
        removeOverlay();
        status(message);
        history.clear();
        task = null;
        targetPackage = null;
    }

    private void workerCancel(ModelClient previous) {
        // Do not queue behind the in-flight request, and never block the UI/stop control on I/O.
        Thread cancel = new Thread(previous::cancel, "trace2task-cancel");
        cancel.setDaemon(true);
        cancel.start();
    }

    private void status(String value) {
        lastStatus = value;
        if (overlayText != null) overlayText.setText(getString(R.string.overlay_status, guard.steps(), value));
    }

    private void watchdog(long token, long timeout, String message) {
        long currentPhase = ++phase;
        main.postDelayed(() -> {
            if (guard.current(token) && phase == currentPhase) stopRun(message);
        }, timeout);
    }

    private void showOverlay() {
        overlay = new LinearLayout(this);
        overlay.setOrientation(LinearLayout.VERTICAL);
        overlay.setPadding(16, 10, 16, 10);
        overlay.setBackgroundColor(Color.rgb(20, 56, 49));
        overlayText = new TextView(this);
        overlayText.setTextColor(Color.WHITE);
        overlayText.setTextSize(12);
        overlayText.setMaxLines(3);
        overlay.addView(overlayText);
        Button stop = new Button(this);
        stop.setText(recording == null ? "停止任务" : "结束并保存示范");
        stop.setOnClickListener(v -> stopRun(recording == null ? "用户停止；已投递的动作不能撤销" : "用户结束录制"));
        overlay.addView(stop);
        if (recording != null) {
            Button mark = new Button(this);
            mark.setText("标记当前画面");
            mark.setOnClickListener(v -> {
                try {
                    if (recordingReady) addRecordedEvent(new JSONObject().put("type", "manual_checkpoint")
                            .put("note", "User requested a sampled screen checkpoint, not an observed touch action"));
                } catch (Exception error) { fail(runToken, error); }
            });
            overlay.addView(mark);
        }
        WindowManager.LayoutParams params = new WindowManager.LayoutParams(
                (int) (220 * getResources().getDisplayMetrics().density), WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT);
        params.gravity = Gravity.TOP | Gravity.END;
        params.y = (int) (40 * getResources().getDisplayMetrics().density);
        try { getSystemService(WindowManager.class).addView(overlay, params); }
        catch (Exception error) { stopRun("无法显示急停悬浮控件；未开始任务"); throw new IllegalStateException(lastStatus); }
    }

    private void removeOverlay() {
        if (overlay != null) {
            try { getSystemService(WindowManager.class).removeView(overlay); }
            catch (IllegalArgumentException ignored) { /* Already removed by system. */ }
        }
        overlay = null;
        overlayText = null;
    }

    private AccessibilityNodeInfo checkedRoot() {
        if (getSystemService(KeyguardManager.class).isKeyguardLocked()
                || !getSystemService(PowerManager.class).isInteractive())
            throw new IllegalStateException("手机已锁定或熄屏");
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) throw new IllegalStateException("无法确认当前应用，未截图或操作");
        if (!targetPackage.equals(String.valueOf(root.getPackageName()))) {
            root.recycle();
            throw new IllegalStateException("已离开选定应用；任务停止，不操作其他应用或系统页面");
        }
        AccessibilityNodeInfo focus = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        boolean password = focus != null && focus.isPassword();
        if (focus != null) focus.recycle();
        if (password) { root.recycle(); throw new IllegalStateException("密码输入框处于焦点，已停止"); }
        return root;
    }

    private int rotation() {
        Display display = getSystemService(DisplayManager.class).getDisplay(Display.DEFAULT_DISPLAY);
        if (display == null) throw new IllegalStateException("主屏幕不可用");
        return display.getRotation();
    }

    private static final class Frame {
        final Bitmap bitmap;
        final int windowId, rotation, width, height;
        final int[] signature;
        final long capturedAt = SystemClock.elapsedRealtime();
        Frame(Bitmap bitmap, int windowId, int rotation) {
            this.bitmap = bitmap; this.windowId = windowId; this.rotation = rotation;
            width = bitmap.getWidth(); height = bitmap.getHeight();
            Bitmap small = Bitmap.createScaledBitmap(bitmap, 32, 32, true);
            signature = new int[32 * 32];
            small.getPixels(signature, 0, 32, 0, 0, 32, 32);
            if (small != bitmap) small.recycle();
        }
    }

    private void capture(long token, Consumer<Frame> callback) {
        if (!guard.current(token)) return;
        watchdog(token, 10000, "截图超时，任务停止；未重试");
        try {
            AccessibilityNodeInfo root = checkedRoot();
            int windowId = root.getWindowId();
            root.recycle();
            int screenRotation = rotation();
            if (overlay != null) overlay.setVisibility(View.INVISIBLE);
            main.postDelayed(() -> {
                if (!guard.current(token)) return;
                try { takeScreenshot(Display.DEFAULT_DISPLAY, getMainExecutor(), new TakeScreenshotCallback() {
                    @Override public void onSuccess(ScreenshotResult result) {
                        HardwareBuffer hardware = result.getHardwareBuffer();
                        Bitmap wrapped = null;
                        Bitmap copy = null;
                        try {
                            if (!guard.current(token)) return;
                            phase++;
                            wrapped = Bitmap.wrapHardwareBuffer(hardware, result.getColorSpace());
                            if (wrapped == null) throw new IllegalStateException("无法读取截图");
                            copy = wrapped.copy(Bitmap.Config.ARGB_8888, false);
                            AccessibilityNodeInfo current = checkedRoot();
                            int currentId = current.getWindowId();
                            current.recycle();
                            if (windowId != currentId || screenRotation != rotation())
                                throw new IllegalStateException("截图期间窗口或方向变化，请重新开始");
                            if (overlay != null) overlay.setVisibility(View.VISIBLE);
                            Frame frame = new Frame(copy, windowId, screenRotation);
                            copy = null; // Ownership transferred to callback.
                            callback.accept(frame);
                        } catch (Exception error) { fail(token, error); }
                        finally {
                            if (copy != null) copy.recycle();
                            if (wrapped != null) wrapped.recycle();
                            hardware.close();
                        }
                    }
                    @Override public void onFailure(int code) {
                        if (guard.current(token)) stopRun("系统拒绝截图（错误 " + code + "）；不绕过安全窗口");
                    }
                }); } catch (Exception error) { fail(token, error); }
            }, 120);
        } catch (Exception error) { fail(token, error); }
    }

    private void observe(long token) {
        if (!guard.current(token)) return;
        if (!guard.next(token)) { stopRun("已达到轮数上限；任务是否完成需自行检查"); return; }
        status("正在观察选定应用");
        capture(token, frame -> {
            ModelClient planningClient = client;
            String currentTask = task;
            String recentHistory = String.join("\n", history);
            status("云端模型规划中 · 可随时停止");
            watchdog(token, 90000, "模型请求超时；任务停止，不会执行迟到回复");
            worker.execute(() -> {
                try {
                    if (!guard.current(token)) { frame.bitmap.recycle(); return; }
                    String image;
                    try { image = encode(frame.bitmap); }
                    finally { frame.bitmap.recycle(); }
                    if (!guard.current(token)) return;
                    ActionPlan plan = planningClient.predict(currentTask, image, recentHistory);
                    main.post(() -> {
                        if (!guard.current(token)) return;
                        if (plan.done) {
                            audit("model_done", "not_independently_verified");
                            stopRun("模型已结束（未独立验收）：" + plan.reason);
                            return;
                        }
                        verifyThenExecute(token, frame, plan);
                    });
                } catch (Exception error) {
                    frame.bitmap.recycle();
                    main.post(() -> fail(token, error));
                }
            });
        });
    }

    private String encode(Bitmap bitmap) {
        double scale = Math.min(1, 1280.0 / Math.max(bitmap.getWidth(), bitmap.getHeight()));
        Bitmap resized = Bitmap.createScaledBitmap(bitmap, Math.max(1, (int) (bitmap.getWidth() * scale)),
                Math.max(1, (int) (bitmap.getHeight() * scale)), true);
        try (ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
            resized.compress(Bitmap.CompressFormat.PNG, 100, bytes);
            return Base64.encodeToString(bytes.toByteArray(), Base64.NO_WRAP);
        } catch (java.io.IOException error) { throw new IllegalStateException("截图编码失败"); }
        finally { if (resized != bitmap) resized.recycle(); }
    }

    private void verifyThenExecute(long token, Frame original, ActionPlan plan) {
        // Fresh local screenshot is not uploaded. Reject changed dimensions/window or stale visual state.
        capture(token, fresh -> {
            boolean changed;
            try {
                changed = original.windowId != fresh.windowId || original.rotation != fresh.rotation
                        || original.width != fresh.width || original.height != fresh.height
                        || SystemClock.elapsedRealtime() - original.capturedAt > 90000
                        || visuallyChanged(original.signature, fresh.signature);
            } finally { fresh.bitmap.recycle(); }
            if (changed) {
                if (++staleCount >= 3) { stopRun("画面连续变化，旧计划未执行；请在稳定页面重试"); return; }
                status("画面变化，丢弃旧计划并重新观察");
                main.postDelayed(() -> observe(token), 800);
                return;
            }
            staleCount = 0;
            execute(token, plan, fresh);
        });
    }

    private static boolean visuallyChanged(int[] before, int[] after) {
        int changed = 0;
        // Ignore the top 5% status bar (clock/network). This is a conservative heuristic, not proof.
        for (int y = 2; y < 32; y++) for (int x = 0; x < 32; x++) {
            int p = before[y * 32 + x], q = after[y * 32 + x];
            int delta = Math.abs(Color.red(p) - Color.red(q)) + Math.abs(Color.green(p) - Color.green(q))
                    + Math.abs(Color.blue(p) - Color.blue(q));
            if (delta > 90) changed++;
        }
        return changed > 76;
    }

    private void execute(long token, ActionPlan plan, Frame frame) {
        if (!guard.current(token)) return;
        String signature = plan.skill + plan.args;
        repeatedActions = signature.equals(lastAction) ? repeatedActions + 1 : 1;
        lastAction = signature;
        if (repeatedActions >= 4) { stopRun("相同动作连续重复，已停止以避免无进展循环"); return; }
        status("执行：" + plan.skill);
        if (overlay != null) overlay.setVisibility(View.INVISIBLE);
        main.postDelayed(() -> {
            if (!guard.current(token)) return;
            try {
                AccessibilityNodeInfo root = checkedRoot();
                try {
                    if (root.getWindowId() != frame.windowId || rotation() != frame.rotation)
                        throw new IllegalStateException("执行前窗口变化，旧动作未发送");
                    Point displaySize = new Point();
                    getSystemService(DisplayManager.class).getDisplay(Display.DEFAULT_DISPLAY).getRealSize(displaySize);
                    if (displaySize.x != frame.width || displaySize.y != frame.height)
                        throw new IllegalStateException("执行前屏幕尺寸变化，旧坐标未发送");
                    switch (plan.skill) {
                        case "wait":
                            if (overlay != null) overlay.setVisibility(View.VISIBLE);
                            main.postDelayed(() -> completed(token, plan, "等待完成"), plan.args.getInt("duration_ms"));
                            return;
                        case "press_key":
                            if (!performGlobalAction(GLOBAL_ACTION_BACK)) throw new IllegalStateException("系统拒绝返回操作");
                            completed(token, plan, "返回已投递，效果待观察");
                            return;
                        case "type_text":
                            AccessibilityNodeInfo field = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
                            try {
                                if (field == null || !field.isEditable() || !field.isEnabled()
                                        || !field.isVisibleToUser() || field.isPassword()
                                        || !targetPackage.equals(String.valueOf(field.getPackageName())))
                                    throw new IllegalStateException("没有安全的已聚焦文本框；未输入");
                                Bundle args = new Bundle();
                                args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, plan.args.getString("text"));
                                if (!field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args))
                                    throw new IllegalStateException("目标应用拒绝文本输入；未重试");
                            } finally { if (field != null) field.recycle(); }
                            completed(token, plan, "文本替换已投递，效果待观察");
                            return;
                        default: dispatchTouch(token, plan, frame);
                    }
                } finally { root.recycle(); }
            } catch (Exception error) { fail(token, error); }
        }, 100);
    }

    private void dispatchTouch(long token, ActionPlan plan, Frame frame) throws Exception {
        watchdog(token, 10000, "手势回执超时，效果未知；不重复发送");
        int width = frame.width, height = frame.height;
        Path path = new Path();
        JSONObject args = plan.args;
        boolean drag = plan.skill.equals("drag");
        float x = pixel(args.getDouble(drag ? "start_x" : "x"), width);
        float y = pixel(args.getDouble(drag ? "start_y" : "y"), height);
        path.moveTo(x, y);
        if (drag) path.lineTo(pixel(args.getDouble("end_x"), width), pixel(args.getDouble("end_y"), height));
        long duration = plan.skill.equals("click") ? 80 : args.getInt("duration_ms");
        GestureDescription gesture = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0, duration)).build();
        boolean accepted = dispatchGesture(gesture, new GestureResultCallback() {
            @Override public void onCompleted(GestureDescription description) { completed(token, plan, "手势已投递，效果待观察"); }
            @Override public void onCancelled(GestureDescription description) {
                if (guard.current(token)) stopRun("手势被取消，效果未知；不重复发送");
            }
        }, main);
        if (!accepted) throw new IllegalStateException("系统拒绝手势；未重试");
    }

    static float pixel(double normalized, int size) { return (float) Math.min(size - 1, Math.floor(normalized * size)); }

    private void completed(long token, ActionPlan plan, String message) {
        if (!guard.current(token)) return;
        phase++;
        if (overlay != null) overlay.setVisibility(View.VISIBLE);
        String summary = plan.auditSummary() + " → " + message;
        history.addLast(summary);
        while (history.size() > 8) history.removeFirst();
        audit("dispatch", summary);
        status(message);
        main.postDelayed(() -> observe(token), 900);
    }

    private void audit(String event, String detail) {
        try (FileOutputStream out = new FileOutputStream(new File(getFilesDir(), "last-run.jsonl"), true)) {
            String line = new JSONObject().put("time_ms", System.currentTimeMillis()).put("round", guard.steps())
                    .put("event", event).put("detail", detail).toString() + "\n";
            out.write(line.getBytes(StandardCharsets.UTF_8));
        } catch (Exception ignored) { /* Audit failure must not repeat an input. */ }
    }

    private void fail(long token, Exception error) {
        if (!guard.current(token)) return;
        String message = error instanceof IllegalStateException ? error.getMessage()
                : error instanceof org.json.JSONException ? "模型回复不符合动作协议；未发送新动作"
                : "请求或系统操作失败（" + error.getClass().getSimpleName() + "）；未重试";
        stopRun(message);
    }
}

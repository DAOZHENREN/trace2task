package dev.trace2task.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.ResolveInfo;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.view.WindowManager;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.io.File;
import org.json.JSONObject;

public final class MainActivity extends Activity {
    private final Handler handler = new Handler();
    private final ArrayList<String> packages = new ArrayList<>();
    private SettingsStore settings;
    private EditText endpoint, model, apiKey, task, limit;
    private Spinner app;
    private CheckBox consent, recordConsent;
    private TextView status, experienceStatus;
    private Button start, record;
    private final Runnable refresh = new Runnable() {
        @Override public void run() {
            MobileAgentService service = MobileAgentService.current();
            String availability = service == null ? "无障碍服务：未启用" : "无障碍服务：已就绪";
            status.setText(getString(R.string.service_status, availability, MobileAgentService.lastStatus));
            start.setEnabled(service != null && !service.running() && !packages.isEmpty());
            record.setEnabled(service != null && !service.running() && !packages.isEmpty());
            handler.postDelayed(this, 1000);
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        // The settings screen can contain the user's API key. Never allow screenshots/backups of it.
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        settings = new SettingsStore(this);
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(24), dp(24), dp(24), dp(40));
        scroll.addView(body);
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets bars = insets.getInsets(android.view.WindowInsets.Type.systemBars());
            scroll.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return insets;
        });
        setContentView(scroll);

        TextView badge = text("ANDROID PREVIEW · 云端模型", 12);
        badge.setTextColor(Color.rgb(23, 107, 91));
        body.addView(badge);
        TextView title = text("Trace2Task", 34);
        title.setTypeface(null, Typeface.BOLD);
        body.addView(title);
        body.addView(text("说出任务，让手机自己完成。\n模型直接执行，你随时可以停止。", 16));
        section(body, "01  连接模型");
        endpoint = field(body, "API 完整地址", "https://你的服务/v1/chat/completions", false);
        endpoint.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        endpoint.setText(settings.endpoint());
        model = field(body, "支持图片输入的模型 ID", "填写服务商提供的模型 ID", false);
        model.setText(settings.model());
        apiKey = field(body, "API Key · 仅保存在本机 Keystore 加密存储", "粘贴你的 API Key", false);
        apiKey.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        apiKey.setSaveEnabled(false);
        apiKey.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        try { apiKey.setText(settings.apiKey()); }
        catch (Exception error) { message("密钥无法解密，请重新填写；旧密钥不会上传"); }
        button(body, "保存模型配置", () -> {
            try { save(); message("已保存。尚未上传截图或调用模型。"); }
            catch (Exception error) { message(configError(error)); }
        });
        button(body, "清除保存的配置和密钥", () -> new AlertDialog.Builder(this)
                .setMessage("清除本机保存的模型配置和 API Key？正在运行的任务也会停止。")
                .setNegativeButton("取消", null).setPositiveButton("清除", (dialog, which) -> {
                    MobileAgentService service = MobileAgentService.current();
                    if (service != null) service.stopRun("配置已清除");
                    settings.clear(); apiKey.setText(""); endpoint.setText(""); model.setText("");
                }).show());

        section(body, "02  选择任务范围");
        body.addView(text("一次只操作或录制一个选定应用；切到其他应用、锁屏或权限页面会停止。", 13));
        app = new Spinner(this);
        body.addView(app);
        loadApps();
        task = field(body, "你想完成什么？", "例如：在这个应用里搜索明天的天气", true);
        limit = field(body, "最多规划轮数（1–100）", "20", false);
        limit.setInputType(InputType.TYPE_CLASS_NUMBER);
        limit.setText(String.valueOf(settings.limit()));

        section(body, "03  人工示范与经验");
        recordConsent = new CheckBox(this);
        recordConsent.setText(R.string.record_consent);
        body.addView(recordConsent);
        record = button(body, "录制人工示范（不调用模型）", this::startRecording);
        button(body, "打开示范与经验库 · 编译 / 反馈 / 复用", () -> {
            MobileAgentService service = MobileAgentService.current();
            if (service != null && service.running()) { message("请先结束当前录制或任务"); return; }
            startActivity(new Intent(this, LibraryActivity.class));
        });
        experienceStatus = text("未选择经验", 14); body.addView(experienceStatus);

        section(body, "04  授权与自主执行");
        button(body, "启用无障碍服务", () -> new AlertDialog.Builder(this)
                .setTitle("允许读取屏幕与执行操作")
                .setMessage(getString(dev.trace2task.mobile.R.string.accessibility_description))
                .setNegativeButton("取消", null)
                .setPositiveButton("前往系统设置", (dialog, which) -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)))
                .show());
        consent = new CheckBox(this);
        consent.setText(R.string.run_consent);
        consent.setTextSize(13);
        body.addView(consent);
        start = button(body, "启动任务 →", this::startTask);
        start.setTextColor(Color.WHITE);
        start.setBackgroundTintList(android.content.res.ColorStateList.valueOf(Color.rgb(23, 107, 91)));
        button(body, "立即停止", () -> {
            MobileAgentService service = MobileAgentService.current();
            if (service != null) service.stopRun("用户停止；已发送动作不可撤销");
        });
        status = text("正在检查服务…", 14);
        body.addView(status);
        body.addView(text("急停：悬浮按钮或音量下键。\n当前手势可能已发送，停止只保证不再发送后续动作。\n执行截图仅在内存处理；主动录制的示范会保存到本机私有库，不进相册。\n模型说“完成”不等于已独立验证完成。", 12));
    }

    @Override protected void onResume() {
        super.onResume(); handler.post(refresh);
        try {
            JSONObject experience = selectedExperience();
            experienceStatus.setText(experience == null ? "下次执行：不使用经验" : "下次执行经验："
                    + experience.getJSONObject("content").getString("title") + " · " + experience.getString("id").substring(0, 8)
                    + "\n绑定应用：" + experience.getString("target_package") + "\n每轮结合当前画面重新规划，无需逐步确认。");
        } catch (Exception error) { experienceStatus.setText("所选经验无法读取，请到经验库重新选择或取消选择。"); }
    }
    @Override protected void onPause() { handler.removeCallbacks(refresh); super.onPause(); }

    private void loadApps() {
        Intent launcher = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        ArrayList<ResolveInfo> entries = new ArrayList<>(getPackageManager().queryIntentActivities(launcher, 0));
        entries.sort(Comparator.comparing(item -> item.loadLabel(getPackageManager()).toString()));
        ArrayList<String> labels = new ArrayList<>();
        HashSet<String> seen = new HashSet<>();
        for (ResolveInfo item : entries) {
            String name = item.activityInfo.packageName;
            if (name.equals(getPackageName()) || name.equals("com.android.settings") || !seen.add(name)) continue;
            packages.add(name);
            labels.add(item.loadLabel(getPackageManager()) + " · " + name);
        }
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, labels);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        app.setAdapter(adapter);
    }

    private int rounds() {
        try {
            int value = Integer.parseInt(limit.getText().toString());
            if (value < 1 || value > 100) throw new NumberFormatException();
            return value;
        } catch (NumberFormatException error) { throw new IllegalArgumentException("轮数必须为 1–100"); }
    }

    private void save() throws Exception {
        settings.save(endpoint.getText().toString(), model.getText().toString(), apiKey.getText().toString(), rounds());
    }

    private void startTask() {
        MobileAgentService service = MobileAgentService.current();
        if (service == null) { message("请先手动启用 Trace2Task 无障碍服务"); return; }
        if (!consent.isChecked()) { message("请先同意本次截图上传与自动操作"); return; }
        try {
            save();
            int index = app.getSelectedItemPosition();
            if (index < 0 || index >= packages.size()) throw new IllegalArgumentException("请选择应用");
            String target = packages.get(index);
            Intent launch = getPackageManager().getLaunchIntentForPackage(target);
            if (launch == null) throw new IllegalArgumentException("目标应用无法启动");
            JSONObject experience = selectedExperience();
            String guidance = experience == null ? "" : ExperienceStore.runtimeGuidance(experience, target);
            service.startRun(target, task.getText().toString(), new CloudModelClient(
                    endpoint.getText().toString(), model.getText().toString(), apiKey.getText().toString(), guidance), rounds(),
                    experience == null ? "" : experience.getString("id"));
            try { startActivity(launch); }
            catch (Exception error) { service.stopRun("无法打开目标应用"); throw error; }
            consent.setChecked(false);
        } catch (Exception error) { message(configError(error)); }
    }

    private JSONObject selectedExperience() throws Exception {
        String selected = getSharedPreferences("library", MODE_PRIVATE).getString("selected_version", "");
        return selected.isEmpty() ? null : new ExperienceStore(new File(getFilesDir(), "experience-library")).read(selected);
    }

    private void startRecording() {
        MobileAgentService service = MobileAgentService.current();
        if (service == null) { message("请先手动启用无障碍服务"); return; }
        if (!recordConsent.isChecked()) { message("请先同意本次本地录制"); return; }
        try {
            int index = app.getSelectedItemPosition();
            if (index < 0 || index >= packages.size()) throw new IllegalArgumentException("请选择应用");
            String target = packages.get(index);
            Intent launch = getPackageManager().getLaunchIntentForPackage(target);
            if (launch == null) throw new IllegalArgumentException("目标应用无法启动");
            service.startRecording(target, task.getText().toString());
            try { startActivity(launch); } catch (Exception error) { service.stopRun("无法启动目标应用"); throw error; }
            recordConsent.setChecked(false);
        } catch (Exception error) { message(configError(error)); }
    }

    private String configError(Exception error) {
        return error instanceof IllegalArgumentException || error instanceof IllegalStateException
                ? error.getMessage() : "配置保存失败，请重新输入密钥（" + error.getClass().getSimpleName() + "）";
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private TextView text(String value, int size) {
        TextView view = new TextView(this);
        view.setText(value); view.setTextSize(size); view.setTextColor(Color.rgb(26, 40, 35));
        view.setPadding(0, dp(6), 0, dp(8));
        return view;
    }
    private void section(LinearLayout parent, String title) {
        TextView view = text(title, 18); view.setTypeface(null, Typeface.BOLD); view.setPadding(0, dp(26), 0, dp(8));
        parent.addView(view);
    }
    private EditText field(LinearLayout parent, String label, String hint, boolean multiline) {
        parent.addView(text(label, 13));
        EditText view = new EditText(this); view.setTextSize(15); view.setHint(hint);
        view.setSingleLine(!multiline); if (multiline) view.setMinLines(3);
        view.setPadding(dp(12), dp(10), dp(12), dp(10));
        parent.addView(view, new LinearLayout.LayoutParams(-1, -2)); return view;
    }
    private Button button(LinearLayout parent, String label, Runnable action) {
        Button button = new Button(this); button.setText(label); button.setAllCaps(false);
        button.setOnClickListener(v -> action.run());
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2); params.topMargin = dp(8);
        parent.addView(button, params); return button;
    }
    private void message(String value) { new AlertDialog.Builder(this).setMessage(value).setPositiveButton("知道了", null).show(); }
}

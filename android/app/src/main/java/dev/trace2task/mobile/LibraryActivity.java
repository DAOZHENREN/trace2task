package dev.trace2task.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.util.ArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Local evidence review and append-only compile/revise workflow. No execution from this screen. */
public final class LibraryActivity extends Activity {
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private ExperienceStore store;
    private ArrayList<JSONObject> traces = new ArrayList<>(), versions = new ArrayList<>();
    private Spinner traceChoice, versionChoice;
    private EditText feedback;
    private CheckBox upload;
    private TextView status;
    private Button compile, revise;
    private CloudModelClient client;
    private long generation;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        store = new ExperienceStore(new File(getFilesDir(), "experience-library"));
        ScrollView scroll = new ScrollView(this);
        LinearLayout body = new LinearLayout(this); body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(32, 32, 32, 48); scroll.addView(body); setContentView(scroll);
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets bars = insets.getInsets(android.view.WindowInsets.Type.systemBars());
            scroll.setPadding(bars.left, bars.top, bars.right, bars.bottom); return insets;
        });
        label(body, "示范与经验库", 26);
        label(body, "录制在本机保存；只有点击编译或反馈修订时才上传。旧版本不会被覆盖。\n"
                + "事件不是完整触摸录像，截图最多 12 张；请先预览。中断草稿可编译，但必须按不完整证据处理。", 14);
        label(body, "1 · 人工示范", 20);
        traceChoice = new Spinner(this); body.addView(traceChoice);
        button(body, "预览示范事件和截图", () -> {
            try { previewTrace(selected(traces, traceChoice)); } catch (Exception error) { error(error); }
        });
        upload = new CheckBox(this);
        upload.setText("我同意本次将所选示范的全部事件、输入文字、抽样截图、任务及（修订时）原经验和反馈发送给配置的云端模型。可能包含隐私并产生费用；留存由服务商决定。");
        body.addView(upload);
        compile = button(body, "编译可复用经验 · Trace Compile", () -> begin(false));
        label(body, "2 · 经验版本与复用", 20);
        versionChoice = new Spinner(this); body.addView(versionChoice);
        button(body, "查看经验、来源与版本关系", () -> {
            try { showText("经验版本", selected(versions, versionChoice).toString(2)); } catch (Exception error) { error(error); }
        });
        button(body, "设为下次执行的经验版本", () -> {
            try {
                JSONObject version = selected(versions, versionChoice);
                getSharedPreferences("library", MODE_PRIVATE).edit().putString("selected_version", version.getString("id")).apply();
                status.setText(getString(R.string.experience_selected, version.getJSONObject("content").getString("title")));
            } catch (Exception error) { error(error); }
        });
        button(body, "取消经验选择（下次仅按任务执行）", () -> {
            getSharedPreferences("library", MODE_PRIVATE).edit().remove("selected_version").apply();
            status.setText("已取消经验选择；不会改变正在执行的任务。");
        });
        label(body, "3 · 人工反馈后的修订", 20);
        label(body, "针对上方选中的经验版本填写反馈。修订使用该版本的原始示范，不使用示范下拉框中的其他记录。", 14);
        feedback = new EditText(this); feedback.setHint("哪里失败？应怎样操作？怎样判断成功？（最多 4000 字）");
        feedback.setMinLines(3); body.addView(feedback);
        revise = button(body, "保存反馈并生成新版本 · Feedback Revision", () -> begin(true));
        button(body, "查看已保存的反馈（含修订失败的反馈）", () -> {
            try { showText("人工反馈", new JSONArray(store.list("feedback")).toString(2)); }
            catch (Exception error) { error(error); }
        });
        button(body, "取消云端编译 / 修订", () -> cancel("已取消；迟到结果不会保存。已保存的反馈保留，可能已经产生的 API 费用无法撤回。"));
        button(body, "刷新本机库", this::reload);
        button(body, "返回任务主页", this::finish);
        status = label(body, "尚未调用模型", 14);
        reload();
    }

    private void reload() {
        try {
            traces = store.list("trace"); versions = store.list("experience");
            ArrayList<String> traceLabels = new ArrayList<>(), versionLabels = new ArrayList<>();
            for (JSONObject item : traces) traceLabels.add(item.getString("task") + " · " + item.getString("status")
                    + " · " + item.getString("id").substring(0, 8));
            for (JSONObject item : versions) versionLabels.add(item.getJSONObject("content").getString("title")
                    + " · " + item.getString("revision_type") + " · " + item.getString("id").substring(0, 8));
            choices(traceChoice, traceLabels); choices(versionChoice, versionLabels);
            compile.setEnabled(client == null && !traces.isEmpty()); revise.setEnabled(client == null && !versions.isEmpty());
        } catch (Exception error) { error(error); }
    }

    private void begin(boolean revision) {
        if (client != null) return;
        MobileAgentService service = MobileAgentService.current();
        if (service != null && service.running()) { status.setText("请先结束录制或运行，再编译经验。"); return; }
        if (!upload.isChecked()) { status.setText("请先预览来源，并同意本次上传。"); return; }
        try {
            JSONObject parent = revision ? selected(versions, versionChoice) : null;
            JSONObject trace = revision ? store.read(parent.getString("source_trace_id")) : selected(traces, traceChoice);
            if (trace.getJSONArray("events").length() == 0 && trace.getJSONArray("frames").length() == 0)
                throw new IllegalArgumentException("示范没有事件或截图，无法编译");
            SettingsStore settings = new SettingsStore(this);
            CloudModelClient pending = new CloudModelClient(settings.endpoint(), settings.model(), settings.apiKey());
            String feedbackText = feedback.getText().toString();
            if (revision && (feedbackText.trim().isEmpty() || feedbackText.length() > 4000))
                throw new IllegalArgumentException("反馈须为 1–4000 字");
            new AlertDialog.Builder(this).setTitle(revision ? "反馈修订来源确认" : "编译来源确认")
                    .setMessage("示范：" + trace.getString("task") + "\n应用：" + trace.getString("target_package")
                            + "\n事件：" + trace.getJSONArray("events").length() + " · 截图：" + trace.getJSONArray("frames").length()
                            + "\n来源 ID：" + trace.getString("id") + "\n发送至：" + settings.endpoint()
                            + "\n模型：" + settings.model() + "\n反馈修订也会发送原始示范全部证据。")
                    .setNegativeButton("取消", null)
                    .setPositiveButton("上传并生成", (dialog, which) -> runCompile(pending, trace, parent, feedbackText)).show();
        } catch (Exception error) { error(error); }
    }

    private void runCompile(CloudModelClient pending, JSONObject trace, JSONObject parent, String value) {
        if (client != null) return;
        try {
            // Persist human feedback first; network/validation failure must not discard it or replace a version.
            JSONObject correction = parent == null ? null : store.feedback(parent, value);
            client = pending;
            long token = ++generation;
            upload.setChecked(false); compile.setEnabled(false); revise.setEnabled(false);
            status.setText(R.string.experience_generating);
            main.postDelayed(() -> { if (generation == token && client != null) cancel("生成超时，迟到结果不会保存；反馈已保留。"); }, 90000);
            worker.execute(() -> {
                try {
                    JSONObject content = pending.compile(store, trace, parent, correction);
                    main.post(() -> {
                        if (generation != token || isFinishing() || isDestroyed()) return;
                        try {
                            JSONObject result = store.publish(trace, content, parent, correction);
                            client = null; reload();
                            status.setText(getString(R.string.experience_saved, result.getString("id").substring(0, 8)));
                        } catch (Exception error) { client = null; reload(); error(error); }
                    });
                } catch (Exception error) {
                    main.post(() -> {
                        if (generation != token) return;
                        client = null; reload(); error(error);
                    });
                }
            });
        } catch (Exception error) { error(error); }
    }

    private void cancel(String message) {
        generation++;
        CloudModelClient previous = client; client = null;
        if (previous != null) {
            Thread cancel = new Thread(previous::cancel, "library-cancel"); cancel.setDaemon(true); cancel.start();
        }
        if (status != null) { status.setText(message); reload(); }
    }
    @Override protected void onStop() {
        if (client != null) cancel("已离开经验库，生成已取消；反馈和旧版本保留。");
        super.onStop();
    }
    @Override protected void onDestroy() { generation++; worker.shutdownNow(); super.onDestroy(); }

    private void previewTrace(JSONObject trace) throws Exception {
        ScrollView scroll = new ScrollView(this);
        LinearLayout body = new LinearLayout(this); body.setOrientation(LinearLayout.VERTICAL); scroll.addView(body);
        label(body, trace.toString(2), 12);
        ArrayList<Bitmap> images = new ArrayList<>();
        JSONArray frames = trace.getJSONArray("frames");
        for (int i = 0; i < frames.length(); i++) {
            JSONObject frame = frames.getJSONObject(i);
            label(body, "截图 " + frame.getInt("frame_id") + " · 请求时最近事件 " + frame.getInt("requested_after_event_id")
                    + "（可能夹有其他动作）", 14);
            try {
                byte[] bytes = android.util.Base64.decode(store.image(trace.getString("id"), frame.getInt("frame_id")), android.util.Base64.DEFAULT);
                BitmapFactory.Options options = new BitmapFactory.Options(); options.inSampleSize = 2;
                Bitmap bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length, options);
                if (bitmap == null) throw new IllegalStateException("截图无法解码");
                images.add(bitmap);
                ImageView image = new ImageView(this); image.setAdjustViewBounds(true); image.setImageBitmap(bitmap); body.addView(image);
            } catch (Exception missing) { label(body, "该截图无法读取；不会静默省略后上传", 14); }
        }
        new AlertDialog.Builder(this).setTitle("本机示范预览").setView(scroll).setPositiveButton("关闭", null)
                .setOnDismissListener(dialog -> { for (Bitmap image : images) image.recycle(); }).show();
    }
    private static JSONObject selected(ArrayList<JSONObject> records, Spinner choice) {
        int index = choice.getSelectedItemPosition();
        if (index < 0 || index >= records.size()) throw new IllegalArgumentException("尚无记录，请先录制示范或编译经验");
        return records.get(index);
    }
    private void choices(Spinner spinner, ArrayList<String> labels) {
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, labels);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item); spinner.setAdapter(adapter);
    }
    private void showText(String title, String text) {
        ScrollView scroll = new ScrollView(this); TextView content = new TextView(this);
        content.setText(text); content.setTextIsSelectable(true); content.setPadding(24, 24, 24, 24); scroll.addView(content);
        new AlertDialog.Builder(this).setTitle(title).setView(scroll).setPositiveButton("关闭", null).show();
    }
    private void error(Exception error) {
        status.setText((error instanceof IllegalArgumentException || error instanceof IllegalStateException)
                ? error.getMessage() : "操作失败（" + error.getClass().getSimpleName() + "）；旧版本与已保存的示范/反馈保持不变。");
    }
    private TextView label(LinearLayout parent, String value, int size) {
        TextView view = new TextView(this); view.setText(value); view.setTextSize(size); view.setPadding(0, 16, 0, 12);
        parent.addView(view); return view;
    }
    private Button button(LinearLayout parent, String label, Runnable action) {
        Button button = new Button(this); button.setText(label); button.setAllCaps(false);
        button.setOnClickListener(view -> action.run()); parent.addView(button); return button;
    }
}

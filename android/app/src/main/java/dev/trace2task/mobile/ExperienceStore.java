package dev.trace2task.mobile;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;
import java.util.Base64;

/** Private on-device library. Published traces/versions/feedback are append-only. */
public final class ExperienceStore {
    private final File directory;
    public ExperienceStore(File directory) {
        this.directory = directory;
        if (!directory.isDirectory() && !directory.mkdirs()) throw new IllegalStateException("无法创建经验库");
    }
    static String id() { return UUID.randomUUID().toString(); }
    private File file(String id) {
        if (id == null || !id.matches("[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}"))
            throw new IllegalArgumentException("无效的经验库 ID");
        return new File(directory, id + ".json");
    }
    public synchronized JSONObject read(String id) throws Exception {
        File file = file(id);
        if (file.length() > 32 * 1024 * 1024) throw new IllegalStateException("经验库记录过大");
        return new JSONObject(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
    }
    private File imageFile(String traceId, int frameId) {
        file(traceId); // Validate the identifier before constructing a sibling path.
        if (frameId < 1 || frameId > Demonstration.MAX_FRAMES) throw new IllegalArgumentException("无效截图 ID");
        return new File(directory, traceId + "-" + frameId + ".png");
    }
    public synchronized void saveImage(String traceId, int frameId, String png) throws Exception {
        File destination = imageFile(traceId, frameId);
        if (destination.exists()) throw new IllegalStateException("原始截图不可覆盖");
        Files.write(destination.toPath(), Base64.getDecoder().decode(png), java.nio.file.StandardOpenOption.CREATE_NEW);
    }
    public synchronized String image(String traceId, int frameId) throws Exception {
        File source = imageFile(traceId, frameId);
        if (source.length() > 4 * 1024 * 1024) throw new IllegalStateException("截图过大");
        return Base64.getEncoder().encodeToString(Files.readAllBytes(source.toPath()));
    }
    public synchronized void save(JSONObject record, boolean draft) throws Exception {
        File destination = file(record.getString("id"));
        if (destination.exists()) {
            JSONObject old = read(record.getString("id"));
            if (!draft || !"trace".equals(old.optString("kind")) || !"recording".equals(old.optString("status")))
                throw new IllegalStateException("已发布的记录不可覆盖");
        }
        File temporary = File.createTempFile("library-", ".tmp", directory);
        try {
            Files.write(temporary.toPath(), record.toString().getBytes(StandardCharsets.UTF_8));
            Files.move(temporary.toPath(), destination.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } finally { Files.deleteIfExists(temporary.toPath()); }
    }
    public synchronized ArrayList<JSONObject> list(String kind) throws Exception {
        ArrayList<JSONObject> result = new ArrayList<>();
        File[] files = directory.listFiles((dir, name) -> name.endsWith(".json"));
        if (files != null) for (File file : files) {
            try {
                JSONObject item = read(file.getName().replace(".json", ""));
                if (kind.equals(item.optString("kind"))) result.add(item);
            } catch (Exception ignored) { /* One damaged record must not hide the remaining library. */ }
        }
        result.sort(Comparator.comparingLong((JSONObject item) -> item.optLong("created_ms")).reversed());
        return result;
    }
    public JSONObject feedback(JSONObject parent, String value) throws Exception {
        if (!"experience".equals(parent.getString("kind"))) throw new IllegalArgumentException("请选择经验版本");
        if (value == null || value.trim().isEmpty() || value.length() > 4000)
            throw new IllegalArgumentException("反馈须为 1–4000 字，说明哪里失败、正确做法或验收条件");
        JSONObject record = new JSONObject().put("id", id()).put("kind", "feedback")
                .put("created_ms", System.currentTimeMillis()).put("parent_id", parent.getString("id"))
                .put("source_trace_id", parent.getString("source_trace_id")).put("text", value.trim());
        save(record, false);
        return record;
    }
    public JSONObject publish(JSONObject trace, JSONObject content, JSONObject parent, JSONObject feedback) throws Exception {
        if (!"trace".equals(trace.getString("kind"))) throw new IllegalArgumentException("不是示范记录");
        validateContent(content, trace);
        String version = id();
        JSONObject record = new JSONObject().put("id", version).put("kind", "experience")
                .put("schema_version", 1).put("created_ms", System.currentTimeMillis())
                .put("source_trace_id", trace.getString("id")).put("target_package", trace.getString("target_package"))
                .put("task", trace.getString("task")).put("content", content)
                .put("evidence_limitations", trace.getString("limitations"))
                .put("source_status", trace.getString("status"));
        if (parent == null) {
            if (feedback != null) throw new IllegalArgumentException("修订必须关联父版本");
            record.put("family_id", version).put("revision_type", "Trace Compile");
        } else {
            if (feedback == null || !parent.getString("source_trace_id").equals(trace.getString("id"))
                    || !parent.getString("target_package").equals(trace.getString("target_package"))
                    || !feedback.getString("source_trace_id").equals(trace.getString("id"))
                    || !feedback.getString("parent_id").equals(parent.getString("id")))
                throw new IllegalArgumentException("反馈、原始示范与父版本不匹配");
            record.put("family_id", parent.getString("family_id")).put("parent_id", parent.getString("id"))
                    .put("feedback_id", feedback.getString("id")).put("revision_type", "Feedback Revision");
        }
        save(record, false);
        return record;
    }
    public static String runtimeGuidance(JSONObject experience, String target) throws Exception {
        if (!"experience".equals(experience.getString("kind")) || !target.equals(experience.getString("target_package")))
            throw new IllegalArgumentException("经验绑定的应用与本次选择不一致，请切换应用或取消经验");
        return new JSONObject().put("version_id", experience.getString("id"))
                .put("content", experience.getJSONObject("content"))
                .put("evidence_limitations", experience.getString("evidence_limitations"))
                .put("source_status", experience.getString("source_status")).toString();
    }
    public static void validateContent(JSONObject value, JSONObject trace) throws Exception {
        keys(value, Set.of("title", "objective", "preconditions", "steps", "success_criteria", "warnings"));
        string(value, "title", 120); string(value, "objective", 2000);
        strings(value.getJSONArray("preconditions"), 12); strings(value.getJSONArray("success_criteria"), 12);
        strings(value.getJSONArray("warnings"), 16);
        if (value.getJSONArray("success_criteria").length() == 0) throw new IllegalArgumentException("经验缺少验收条件");
        Set<Integer> evidence = new HashSet<>();
        JSONArray events = trace.getJSONArray("events");
        for (int i = 0; i < events.length(); i++) evidence.add(events.getJSONObject(i).getInt("event_id"));
        JSONArray steps = value.getJSONArray("steps");
        if (steps.length() < 1 || steps.length() > 30 || value.toString().length() > 24000)
            throw new IllegalArgumentException("经验步骤或内容长度超限");
        for (int i = 0; i < steps.length(); i++) {
            JSONObject step = steps.getJSONObject(i);
            keys(step, Set.of("when", "instruction", "expected", "evidence_event_ids"));
            string(step, "when", 1000); string(step, "instruction", 1000); string(step, "expected", 1000);
            JSONArray ids = step.getJSONArray("evidence_event_ids");
            if (ids.length() > 80) throw new IllegalArgumentException("证据引用超限");
            for (int j = 0; j < ids.length(); j++) {
                Object raw = ids.get(j);
                if (!(raw instanceof Number) || ((Number) raw).doubleValue() != ((Number) raw).intValue()
                        || !evidence.contains(((Number) raw).intValue())) throw new IllegalArgumentException("经验引用了不存在的示范事件");
            }
        }
    }
    private static void keys(JSONObject value, Set<String> allowed) {
        Set<String> actual = new HashSet<>();
        value.keys().forEachRemaining(actual::add);
        if (!actual.equals(allowed)) throw new IllegalArgumentException("经验字段不符合协议");
    }
    private static void string(JSONObject value, String key, int limit) throws Exception {
        Object raw = value.get(key);
        if (!(raw instanceof String) || ((String) raw).trim().isEmpty() || ((String) raw).length() > limit)
            throw new IllegalArgumentException("经验文本字段无效：" + key);
    }
    private static void strings(JSONArray values, int limit) throws Exception {
        if (values.length() > limit) throw new IllegalArgumentException("经验条目过多");
        for (int i = 0; i < values.length(); i++) {
            Object value = values.get(i);
            if (!(value instanceof String) || ((String) value).trim().isEmpty() || ((String) value).length() > 1000)
                throw new IllegalArgumentException("经验列表文本无效");
        }
    }
}

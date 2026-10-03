package dev.trace2task.mobile;

import org.json.JSONArray;
import org.json.JSONObject;

/** Accessibility evidence, not a raw touch log. Snapshots are explicitly sampled after events. */
public final class Demonstration {
    static final int MAX_EVENTS = 80, MAX_FRAMES = 12;
    private final ExperienceStore store;
    private final JSONObject trace;
    private boolean finished;
    private int imageCharacters;

    public Demonstration(ExperienceStore store, String target, String task) throws Exception {
        this.store = store;
        trace = new JSONObject().put("id", ExperienceStore.id()).put("kind", "trace").put("schema_version", 1)
                .put("created_ms", System.currentTimeMillis()).put("target_package", target).put("task", task)
                .put("status", "recording").put("events", new JSONArray()).put("frames", new JSONArray())
                .put("limitations", "Accessibility events can be missing, coalesced, or app-generated, not proven human input. "
                        + "Screenshots are sampled after events, not exact before/after pairs. Bounds are node bounds, not touch coordinates. "
                        + "Games/custom views, back gestures and keyboard interactions may be absent. Never infer verified task success.");
        store.save(trace, true);
    }
    public synchronized int count() { return trace.optJSONArray("events").length(); }
    public synchronized boolean canCapture() {
        return !finished && trace.optJSONArray("frames").length() < MAX_FRAMES && imageCharacters < 18 * 1024 * 1024;
    }
    public synchronized boolean event(JSONObject event) throws Exception {
        if (finished || count() >= MAX_EVENTS) return false;
        event.put("event_id", count() + 1).put("recorded_ms", System.currentTimeMillis());
        trace.getJSONArray("events").put(event);
        store.save(trace, true);
        return true;
    }
    public synchronized void frame(String png, int throughEvent, int width, int height) throws Exception {
        if (!canCapture()) return;
        if (png.length() > 4 * 1024 * 1024 || imageCharacters + png.length() > 22 * 1024 * 1024) {
            trace.put("image_budget_reached", true);
            store.save(trace, true);
            return;
        }
        imageCharacters += png.length();
        JSONArray frames = trace.getJSONArray("frames");
        store.saveImage(trace.getString("id"), frames.length() + 1, png);
        frames.put(new JSONObject().put("frame_id", frames.length() + 1).put("captured_ms", System.currentTimeMillis())
                .put("requested_after_event_id", throughEvent).put("events_at_save", count())
                .put("timing", "sampled_after_events; intervening_actions_possible")
                .put("original_width", width).put("original_height", height));
        store.save(trace, true);
    }
    public synchronized String finish(String reason) throws Exception {
        if (!finished) {
            trace.put("status", "stopped_unverified").put("stop_reason", reason).put("ended_ms", System.currentTimeMillis())
                    .put("image_limit_reached", trace.getJSONArray("frames").length() >= MAX_FRAMES);
            store.save(trace, true);
            finished = true;
        }
        return trace.getString("id");
    }
}

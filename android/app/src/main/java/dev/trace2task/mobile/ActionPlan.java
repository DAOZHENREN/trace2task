package dev.trace2task.mobile;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.json.JSONTokener;

import java.util.HashSet;
import java.util.Set;

/** Strict, single-action subset of Trace2Task's observation-normalized v1 protocol. */
public final class ActionPlan {
    public final String reason;
    public final String skill;
    public final JSONObject args;
    public final boolean done;

    private ActionPlan(String reason, String skill, JSONObject args, boolean done) {
        this.reason = reason;
        this.skill = skill;
        this.args = args;
        this.done = done;
    }

    public static ActionPlan parse(String raw) throws JSONException {
        if (raw == null || raw.length() > 16384) throw new JSONException("模型回复为空或过长");
        JSONTokener parser = new JSONTokener(raw.trim());
        Object parsed = parser.nextValue();
        if (!(parsed instanceof JSONObject) || parser.nextClean() != 0)
            throw new JSONException("只接受一个 JSON 对象，不能包含代码块或尾随文字");
        JSONObject response = (JSONObject) parsed;
        keys(response, "reason", "plan");
        String reason = string(response, "reason", 800);
        JSONObject plan = response.getJSONObject("plan");
        keys(plan, "protocol_version", "coordinate_space", "actions");
        if (!"1".equals(plan.get("protocol_version"))
                || !"observation_normalized_0_1".equals(plan.get("coordinate_space")))
            throw new JSONException("动作协议或坐标空间不受支持");
        JSONArray actions = plan.getJSONArray("actions");
        if (actions.length() != 1) throw new JSONException("每轮只能一个动作，之后必须重新观察");
        JSONObject action = actions.getJSONObject(0);
        if (action.has("done")) {
            keys(action, "done");
            if (!Boolean.TRUE.equals(action.get("done"))) throw new JSONException("done 必须为 true");
            return new ActionPlan(reason, "done", new JSONObject(), true);
        }
        keys(action, "skill", "args");
        String skill = string(action, "skill", 32);
        JSONObject args = action.getJSONObject("args");
        switch (skill) {
            case "click":
                keys(args, "x", "y", "button");
                if (!"left".equals(args.get("button"))) throw new JSONException("手机只支持主点击");
                point(args, "x", "y");
                break;
            case "long_press":
                keys(args, "x", "y", "duration_ms");
                point(args, "x", "y");
                integer(args, "duration_ms", 500, 1500);
                break;
            case "drag":
                keys(args, "start_x", "start_y", "end_x", "end_y", "duration_ms");
                point(args, "start_x", "start_y");
                point(args, "end_x", "end_y");
                integer(args, "duration_ms", 200, 1500);
                break;
            case "type_text":
                keys(args, "text");
                string(args, "text", 500);
                break;
            case "press_key":
                keys(args, "key");
                if (!"back".equals(args.get("key"))) throw new JSONException("只支持 back");
                break;
            case "wait":
                keys(args, "duration_ms");
                integer(args, "duration_ms", 200, 5000);
                break;
            default: throw new JSONException("不受支持的动作：" + skill);
        }
        return new ActionPlan(reason, skill, args, false);
    }

    private static void keys(JSONObject value, String... expected) throws JSONException {
        Set<String> names = new HashSet<>();
        value.keys().forEachRemaining(names::add);
        if (!names.equals(Set.of(expected))) throw new JSONException("JSON 字段不符合协议");
    }

    private static String string(JSONObject value, String name, int max) throws JSONException {
        Object raw = value.get(name);
        if (!(raw instanceof String) || ((String) raw).trim().isEmpty() || ((String) raw).length() > max)
            throw new JSONException(name + " 必须是非空字符串且长度不超过 " + max);
        return (String) raw;
    }

    private static void point(JSONObject args, String x, String y) throws JSONException {
        number(args, x, 0, 1);
        number(args, y, 0, 1);
    }

    private static double number(JSONObject args, String key, double min, double max) throws JSONException {
        Object raw = args.get(key);
        if (!(raw instanceof Number)) throw new JSONException(key + " 必须是数值");
        double value = ((Number) raw).doubleValue();
        if (!Double.isFinite(value) || value < min || value > max)
            throw new JSONException(key + " 超出范围");
        return value;
    }

    private static void integer(JSONObject args, String key, int min, int max) throws JSONException {
        double value = number(args, key, min, max);
        if (value != Math.rint(value)) throw new JSONException(key + " 必须是整数");
    }

    public String auditSummary() {
        if (skill.equals("type_text")) return "type_text（" + args.optString("text").length() + " 字符，正文不记录）";
        return skill + " " + args;
    }
}

package dev.trace2task.mobile;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class ActionPlanTest {
    static String reply(String action) {
        return "{\"reason\":\"下一步\",\"plan\":{\"protocol_version\":\"1\","
                + "\"coordinate_space\":\"observation_normalized_0_1\",\"actions\":[" + action + "]}}";
    }
    static String click(String x, String y) {
        return "{\"skill\":\"click\",\"args\":{\"x\":" + x + ",\"y\":" + y + ",\"button\":\"left\"}}";
    }
    private void rejects(String value) {
        assertThrows(Exception.class, () -> ActionPlan.parse(value));
    }
    @Test public void acceptsNormalizedEdges() throws Exception {
        ActionPlan plan = ActionPlan.parse(reply(click("0", "1")));
        assertEquals("click", plan.skill);
        assertFalse(plan.done);
    }
    @Test public void rejectsOutOfRangeAndNonNumericCoordinates() {
        for (String x : new String[]{"-0.1", "1.001", "null", "true", "\"0.5\"", "1e999"})
            rejects(reply(click(x, "0.5")));
    }
    @Test public void rejectsWrongEnvelopeAndExtraFields() {
        rejects(reply(click("0.5", "0.5")).replace("\"1\"", "1"));
        rejects(reply(click("0.5", "0.5")).replace("observation_normalized_0_1", "desktop_pixels"));
        rejects(reply(click("0.5", "0.5")).replace("\"reason\":", "\"execute\":true,\"reason\":"));
        rejects(reply(click("0.5", "0.5")).replace("\"button\":\"left\"", "\"button\":\"right\""));
    }
    @Test public void rejectsBatchesMarkdownAndTrailingContent() {
        rejects(reply(click("0.5", "0.5") + "," + click("0.5", "0.5")));
        rejects("```json\n" + reply("{\"done\":true}") + "\n```");
        rejects(reply("{\"done\":true}") + " trailing");
        rejects(reply(""));
    }
    @Test public void requiresExactDoneMarker() throws Exception {
        assertTrue(ActionPlan.parse(reply("{\"done\":true}")).done);
        rejects(reply("{\"done\":\"true\"}"));
        rejects(reply("{\"done\":false}"));
        rejects(reply("{\"done\":true,\"skill\":\"click\"}"));
    }
    @Test public void validatesDragAndDuration() throws Exception {
        String drag = "{\"skill\":\"drag\",\"args\":{\"start_x\":0.5,\"start_y\":0.8,\"end_x\":0.5,\"end_y\":0.2,\"duration_ms\":500}}";
        assertEquals("drag", ActionPlan.parse(reply(drag)).skill);
        rejects(reply(drag.replace(":500", ":500.1")));
        rejects(reply(drag.replace(":500", ":2000")));
        rejects(reply(drag.replace("\"end_y\":0.2", "\"end_y\":-1")));
    }
    @Test public void rejectsUnknownAndDangerousCapabilities() {
        for (String skill : new String[]{"shell", "launch_app", "home", "grant_permission", "download", "eval"})
            rejects(reply("{\"skill\":\"" + skill + "\",\"args\":{}}"));
        rejects(reply("{\"skill\":\"press_key\",\"args\":{\"key\":\"enter\"}}"));
    }
    @Test public void textIsBoundedAndRedactedFromAudit() throws Exception {
        JSONObject args = new JSONObject().put("text", "private-message");
        JSONObject action = new JSONObject().put("skill", "type_text").put("args", args);
        ActionPlan plan = ActionPlan.parse(reply(action.toString()));
        assertFalse(plan.auditSummary().contains("private-message"));
        args.put("text", "x".repeat(501));
        rejects(reply(action.toString()));
        args.put("text", "");
        rejects(reply(action.toString()));
    }
    @Test public void longPressWaitAndBackAreSupported() throws Exception {
        assertEquals("long_press", ActionPlan.parse(reply("{\"skill\":\"long_press\",\"args\":{\"x\":0.1,\"y\":0.2,\"duration_ms\":600}}")).skill);
        assertEquals("wait", ActionPlan.parse(reply("{\"skill\":\"wait\",\"args\":{\"duration_ms\":5000}}")).skill);
        rejects(reply("{\"skill\":\"wait\",\"args\":{\"duration_ms\":5001}}"));
        assertEquals("press_key", ActionPlan.parse(reply("{\"skill\":\"press_key\",\"args\":{\"key\":\"back\"}}")).skill);
    }
}

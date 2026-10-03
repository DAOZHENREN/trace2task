package dev.trace2task.mobile;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import static org.junit.Assert.*;

public class ExperienceWorkflowTest {
    @Rule public TemporaryFolder temp = new TemporaryFolder();
    private ExperienceStore store() throws Exception { return new ExperienceStore(temp.newFolder()); }
    private JSONObject trace(ExperienceStore store) throws Exception {
        Demonstration recorder = new Demonstration(store, "com.example.test", "查天气");
        recorder.event(new JSONObject().put("type", "TYPE_VIEW_CLICKED").put("text", "搜索"));
        recorder.frame("aW1hZ2U=", 1, 1080, 2400);
        return store.read(recorder.finish("用户结束"));
    }
    private JSONObject content() throws Exception {
        return new JSONObject().put("title", "搜索天气").put("objective", "按本次任务查询天气")
                .put("preconditions", new JSONArray().put("应用主页可见"))
                .put("steps", new JSONArray().put(new JSONObject().put("when", "搜索框可见")
                        .put("instruction", "输入本次任务的地点").put("expected", "地点结果出现")
                        .put("evidence_event_ids", new JSONArray().put(1))))
                .put("success_criteria", new JSONArray().put("结果对应本次任务地点"))
                .put("warnings", new JSONArray().put("抽样证据不证明操作成功"));
    }
    @Test public void recordsOfflineEvidenceAndStopsLateWrites() throws Exception {
        ExperienceStore store = store();
        Demonstration recorder = new Demonstration(store, "com.example.test", "demo");
        assertTrue(recorder.event(new JSONObject().put("type", "manual_checkpoint")));
        recorder.frame("aW1hZ2U=", 1, 100, 200);
        String id = recorder.finish("stopped");
        assertFalse(recorder.event(new JSONObject().put("type", "late")));
        recorder.frame("bGF0ZQ==", 1, 100, 200);
        JSONObject saved = store.read(id);
        assertEquals("stopped_unverified", saved.getString("status"));
        assertEquals(1, saved.getJSONArray("events").length());
        assertEquals(1, saved.getJSONArray("frames").length());
        assertEquals("aW1hZ2U=", store.image(id, 1));
        assertFalse(saved.toString().contains("aW1hZ2U="));
        assertTrue(saved.getString("limitations").contains("not proven human input"));
    }
    @Test public void recordingLimitsAreBounded() throws Exception {
        ExperienceStore store = store();
        Demonstration recorder = new Demonstration(store, "com.example.test", "demo");
        for (int i = 0; i < 80; i++) assertTrue(recorder.event(new JSONObject().put("type", "click")));
        assertFalse(recorder.event(new JSONObject().put("type", "extra")));
        for (int i = 0; i < 14; i++) recorder.frame("aW1hZ2U=", 80, 100, 100);
        assertFalse(recorder.canCapture());
        JSONObject saved = store.read(recorder.finish("limit"));
        assertEquals(80, saved.getJSONArray("events").length());
        assertEquals(12, saved.getJSONArray("frames").length());
        assertTrue(saved.getBoolean("image_limit_reached"));
    }
    @Test public void interruptedDraftIsRecoverableAndNotDeclaredComplete() throws Exception {
        ExperienceStore store = store();
        Demonstration recorder = new Demonstration(store, "com.example.test", "demo");
        recorder.event(new JSONObject().put("type", "click"));
        JSONObject draft = store.list("trace").get(0);
        assertEquals("recording", draft.getString("status"));
        assertEquals(1, draft.getJSONArray("events").length());
        assertTrue(ExperienceCompiler.request("test", store, draft, null, null).toString().contains("recording"));
    }
    @Test public void compileAndRevisionKeepProvenanceAndOriginals() throws Exception {
        ExperienceStore store = store(); JSONObject trace = trace(store);
        String original = store.read(trace.getString("id")).toString();
        JSONObject first = store.publish(trace, content(), null, null);
        JSONObject feedback = store.feedback(first, "先清空已有查询词，检查地点一致");
        JSONObject revised = content().put("title", "先清空再搜索");
        revised.getJSONArray("steps").getJSONObject(0).put("evidence_event_ids", new JSONArray());
        JSONObject second = store.publish(trace, revised, first, feedback);
        assertEquals("Trace Compile", first.getString("revision_type"));
        assertEquals("Feedback Revision", second.getString("revision_type"));
        assertEquals(first.getString("id"), second.getString("parent_id"));
        assertEquals(first.getString("family_id"), second.getString("family_id"));
        assertEquals(feedback.getString("id"), second.getString("feedback_id"));
        assertEquals(original, store.read(trace.getString("id")).toString());
        assertEquals("搜索天气", store.read(first.getString("id")).getJSONObject("content").getString("title"));
        assertEquals(2, store.list("experience").size());
    }
    @Test public void publishedRecordsAndImagesCannotBeOverwritten() throws Exception {
        ExperienceStore store = store(); JSONObject trace = trace(store);
        assertThrows(IllegalStateException.class, () -> store.save(trace, true));
        assertThrows(IllegalStateException.class, () -> store.save(trace, false));
        assertThrows(IllegalStateException.class, () -> store.saveImage(trace.getString("id"), 1, "bGF0ZQ=="));
        JSONObject version = store.publish(trace, content(), null, null);
        assertThrows(IllegalStateException.class, () -> store.save(version, true));
    }
    @Test public void malformedRevisionPreservesFeedbackAndParent() throws Exception {
        ExperienceStore store = store(); JSONObject trace = trace(store);
        JSONObject parent = store.publish(trace, content(), null, null);
        JSONObject feedback = store.feedback(parent, "纠正搜索范围");
        JSONObject malformed = content().put("steps", new JSONArray());
        assertThrows(Exception.class, () -> store.publish(trace, malformed, parent, feedback));
        assertEquals(1, store.list("experience").size());
        assertEquals("纠正搜索范围", store.read(feedback.getString("id")).getString("text"));
    }
    @Test public void evidenceMustExistAndHaveIntegerIds() throws Exception {
        ExperienceStore store = store(); JSONObject trace = trace(store);
        for (Object id : new Object[]{2, "1", 1.5, true}) {
            JSONObject bad = content();
            bad.getJSONArray("steps").getJSONObject(0).put("evidence_event_ids", new JSONArray().put(id));
            assertThrows(Exception.class, () -> ExperienceStore.validateContent(bad, trace));
        }
    }
    @Test public void schemaRejectsCodeFieldsAndOversizedOutput() throws Exception {
        ExperienceStore store = store(); JSONObject trace = trace(store);
        assertThrows(Exception.class, () -> ExperienceStore.validateContent(content().put("shell", "run"), trace));
        assertThrows(Exception.class, () -> ExperienceStore.validateContent(content().put("title", "x".repeat(121)), trace));
        assertThrows(Exception.class, () -> ExperienceStore.validateContent(content().put("objective", 1), trace));
        assertThrows(Exception.class, () -> ExperienceStore.validateContent(content().put("success_criteria", new JSONArray()), trace));
        assertThrows(Exception.class, () -> ExperienceCompiler.parse(content() + " trailing", trace));
        assertThrows(Exception.class, () -> ExperienceCompiler.parse("```json\n" + content() + "\n```", trace));
        assertEquals("搜索天气", ExperienceCompiler.parse(content().toString(), trace).getString("title"));
    }
    @Test public void sourceAndParentCannotBeRebound() throws Exception {
        ExperienceStore store = store(); JSONObject firstTrace = trace(store), secondTrace = trace(store);
        JSONObject parent = store.publish(firstTrace, content(), null, null);
        JSONObject feedback = store.feedback(parent, "test");
        assertThrows(Exception.class, () -> store.publish(secondTrace, content(), parent, feedback));
        feedback.put("parent_id", ExperienceStore.id());
        assertThrows(Exception.class, () -> store.publish(firstTrace, content(), parent, feedback));
    }
    @Test public void pathsCannotEscapeLibrary() throws Exception {
        ExperienceStore store = store();
        for (String id : new String[]{"../secret", "", "C:\\Windows\\file", "../../x", "foo.json"}) {
            assertThrows(IllegalArgumentException.class, () -> store.read(id));
            assertThrows(IllegalArgumentException.class, () -> store.image(id, 1));
        }
        assertThrows(IllegalArgumentException.class, () -> store.image(ExperienceStore.id(), 13));
    }
    @Test public void compilerIncludesActualEvidenceParentFeedbackAndImages() throws Exception {
        ExperienceStore store = store(); JSONObject trace = trace(store);
        JSONObject parent = store.publish(trace, content(), null, null), feedback = store.feedback(parent, "先清空");
        JSONObject request = ExperienceCompiler.request("vision", store, trace, parent, feedback);
        assertEquals(6000, request.getInt("max_completion_tokens"));
        JSONArray content = request.getJSONArray("messages").getJSONObject(1).getJSONArray("content");
        JSONObject evidence = new JSONObject(content.getJSONObject(0).getString("text"));
        assertEquals("先清空", evidence.getString("human_feedback"));
        assertEquals(trace.getString("id"), evidence.getJSONObject("source_trace").getString("id"));
        assertTrue(evidence.has("parent_experience"));
        assertEquals("data:image/png;base64,aW1hZ2U=", content.getJSONObject(2).getJSONObject("image_url").getString("url"));
        assertFalse(request.has("api_key"));
    }
    @Test public void emptyTracesDoNotTriggerCompilation() throws Exception {
        ExperienceStore store = store(); Demonstration demo = new Demonstration(store, "com.example.test", "empty");
        JSONObject empty = store.read(demo.finish("empty"));
        assertThrows(Exception.class, () -> ExperienceCompiler.request("vision", store, empty, null, null));
    }
    @Test public void selectedExperienceIsPinnedAndSentEveryRoundWithCurrentTaskAndScreen() throws Exception {
        ExperienceStore store = store(); JSONObject version = store.publish(trace(store), content(), null, null);
        String pinned = ExperienceStore.runtimeGuidance(version, "com.example.test");
        assertThrows(IllegalArgumentException.class, () -> ExperienceStore.runtimeGuidance(version, "different.app"));
        for (String screenshot : new String[]{"first-screen", "changed-screen"}) {
            JSONObject request = CloudModelClient.request("vision", "另一个地点", screenshot, "delivered", pinned);
            JSONArray body = request.getJSONArray("messages").getJSONObject(1).getJSONArray("content");
            assertTrue(body.getJSONObject(0).getString("text").contains(version.getString("id")));
            assertTrue(body.getJSONObject(1).getString("text").contains("另一个地点"));
            assertTrue(body.getJSONObject(2).getJSONObject("image_url").getString("url").endsWith(screenshot));
            assertTrue(request.getJSONArray("messages").getJSONObject(0).getString("content").contains("never replay"));
        }
    }
}

package dev.trace2task.mobile;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class CloudModelClientTest {
    @Test public void requiresExplicitSecureEndpoint() {
        assertEquals("https://example.com/v1/chat/completions", CloudModelClient.validateEndpoint("https://example.com/v1/chat/completions"));
        for (String endpoint : new String[]{"http://example.com/v1/chat/completions", "https://user:secret@example.com/v1/chat/completions",
                "https://example.com/v1/chat/completions?key=secret", "https://example.com/v1/chat/completions#secret",
                "https://example.com/v1", "file:///chat/completions", "", "https:///chat/completions"})
            assertThrows(IllegalArgumentException.class, () -> CloudModelClient.validateEndpoint(endpoint));
    }
    @Test public void rejectsHeaderInjectionAndMissingModel() {
        assertThrows(IllegalArgumentException.class, () -> new CloudModelClient("https://example.com/v1/chat/completions", "model", "key\r\nInjected: x"));
        assertThrows(IllegalArgumentException.class, () -> new CloudModelClient("https://example.com/v1/chat/completions", "", "key"));
    }
    @Test public void requestIncludesTaskImageAndBoundedHistoryWithoutCredentials() throws Exception {
        JSONObject request = CloudModelClient.request("vision-model", "查天气", "PNG_BYTES", "click → delivered");
        assertEquals("vision-model", request.getString("model"));
        assertFalse(request.getBoolean("stream"));
        JSONArray content = request.getJSONArray("messages").getJSONObject(1).getJSONArray("content");
        assertTrue(content.getJSONObject(1).getString("text").contains("查天气"));
        assertTrue(content.getJSONObject(1).getString("text").contains("click → delivered"));
        assertEquals("data:image/png;base64,PNG_BYTES", content.getJSONObject(2).getJSONObject("image_url").getString("url"));
        assertFalse(request.has("api_key"));
    }
    @Test public void truncatedOrToolCallOutputsNeverBecomeActions() throws Exception {
        for (String reason : new String[]{"length", "content_filter", "tool_calls", "", "cancelled"}) {
            JSONObject response = response(reason, ActionPlanTest.reply("{\"done\":true}"));
            assertThrows(Exception.class, () -> CloudModelClient.parseResponse(response.toString()));
        }
        assertTrue(CloudModelClient.parseResponse(response("stop", ActionPlanTest.reply("{\"done\":true}")).toString()).done);
    }
    @Test public void invalidPayloadAndEmptyReplyFailClosed() throws Exception {
        assertThrows(Exception.class, () -> CloudModelClient.parseResponse("{}"));
        assertThrows(Exception.class, () -> CloudModelClient.parseResponse(response("stop", "").toString()));
        assertThrows(Exception.class, () -> CloudModelClient.parseResponse(response("stop", "click now").toString()));
    }
    @Test public void cancellationBeforeRequestDoesNotTouchNetwork() {
        CloudModelClient client = new CloudModelClient("https://example.invalid/v1/chat/completions", "test", "test-only");
        client.cancel();
        assertThrows(java.util.concurrent.CancellationException.class, () -> client.predict("task", "image", ""));
    }
    private static JSONObject response(String finish, String content) throws Exception {
        return new JSONObject().put("choices", new JSONArray().put(new JSONObject().put("finish_reason", finish)
                .put("message", new JSONObject().put("content", content))));
    }
}

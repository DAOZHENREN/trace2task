package org.trace2task.input;
import java.util.concurrent.atomic.AtomicInteger;
/** Substitute only the Android window-service transport, not protocol/gesture logic. */
public class TestMain {
    public static void main(String[] args) {
        AtomicInteger calls = new AtomicInteger();
        BoundedTouch.run(args, new BoundedTouch.WindowScope() {
            public String read() {
                int index = calls.incrementAndGet();
                String focus = System.getProperty("test.windowFocus", "com.example.game/.Main");
                if (index >= Integer.getInteger("test.focusChangesAt", Integer.MAX_VALUE)) {
                    focus = "com.android.systemui/NotificationShade";
                }
                return "Display: mCurrentFocus=Window{123 u0 " + focus + "}\n";
            }
            public String boot() { return System.getProperty("test.boot", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"); }
        });
    }
}

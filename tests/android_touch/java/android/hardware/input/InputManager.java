package android.hardware.input;
import android.view.InputEvent;
import android.view.MotionEvent;
public class InputManager {
    public static InputManager getInstance() { return new InputManager(); }
    public boolean injectInputEvent(InputEvent value, int mode) {
        MotionEvent e = (MotionEvent) value;
        if (e.source != 0x1002 || e.display != 0 || mode != 0) {
            throw new IllegalStateException("invalid injection metadata");
        }
        System.out.printf("INJECT %d %.1f %.1f %.1f %d %d%n",
                e.action, e.x, e.y, e.pressure, e.down, e.when);
        System.out.flush();
        if (e.action == 2 && Boolean.getBoolean("test.failMove")) {
            throw new IllegalStateException("simulated uncertain move");
        }
        return !(e.action == 1 && Boolean.getBoolean("test.rejectUp"));
    }
}

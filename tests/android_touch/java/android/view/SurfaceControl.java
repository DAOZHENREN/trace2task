package android.view;
import android.graphics.Bitmap;
import android.graphics.Rect;
public class SurfaceControl {
    public static Bitmap screenshot(Rect region, int width, int height, int rotation) throws Exception {
        Thread.sleep(Integer.getInteger("test.captureDelayMs", 0));
        if (Boolean.getBoolean("test.failCapture")) throw new IllegalStateException("capture failed");
        return new Bitmap(width, height, 0, 0);
    }
}

package android.graphics;
public class Bitmap {
    public enum Config { ARGB_8888 }
    private final int width, height, left, top;
    public Bitmap(int width, int height, int left, int top) {
        this.width = width; this.height = height; this.left = left; this.top = top;
    }
    public int getWidth() { return width; }
    public int getHeight() { return height; }
    public static Bitmap createBitmap(Bitmap source, int x, int y, int width, int height) {
        return new Bitmap(width, height, source.left + x, source.top + y);
    }
    public Bitmap copy(Config config, boolean mutable) {
        return new Bitmap(width, height, left, top);
    }
    public void getPixels(int[] colors, int offset, int stride, int x, int y, int w, int h) {
        for (int row = 0; row < h; row++) {
            for (int col = 0; col < w; col++) {
                int r = (left + x + col) & 255, g = (top + y + row) & 255;
                colors[offset + row * stride + col] = 0xff000000 | (r << 16) | (g << 8) | 100;
            }
        }
    }
    public void recycle() { }
}

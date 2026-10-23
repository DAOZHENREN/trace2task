package android.view;
public class MotionEvent extends InputEvent {
    public long down, when;
    public int action, source, display;
    public float x, y, pressure;
    public static MotionEvent obtain(long down, long when, int action, float x, float y,
            float pressure, float size, int meta, float xp, float yp, int device, int edges) {
        MotionEvent event = new MotionEvent();
        event.down=down; event.when=when; event.action=action; event.x=x; event.y=y;
        event.pressure=pressure;
        return event;
    }
    public void setSource(int source) { this.source=source; }
    public void setDisplayId(int display) { this.display=display; }
    public void recycle() { }
}

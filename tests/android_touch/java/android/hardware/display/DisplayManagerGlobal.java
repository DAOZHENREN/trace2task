package android.hardware.display;
public class DisplayManagerGlobal {
    public static DisplayManagerGlobal getInstance() { return new DisplayManagerGlobal(); }
    public android.view.Display getRealDisplay(int id) { return new android.view.Display(); }
}

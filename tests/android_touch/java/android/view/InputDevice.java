package android.view;
public class InputDevice {
    public static int[] getDeviceIds() { return new int[] {1}; }
    public static InputDevice getDevice(int id) { return new InputDevice(); }
    public boolean supportsSource(int source) { return source == 0x1002; }
}

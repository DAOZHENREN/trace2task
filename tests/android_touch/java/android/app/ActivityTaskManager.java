package android.app;
public class ActivityTaskManager {
    public static IActivityTaskManager getService() { return new Service(); }
    public static class Service implements IActivityTaskManager {
        public StackInfo getFocusedStackInfo() { return new StackInfo(); }
    }
}

package dev.trace2task.mobile;

/** Invalidates delayed network/screenshot/gesture callbacks when a run stops or is replaced. */
public final class RunGuard {
    private long generation;
    private boolean active;
    private int steps;
    private int limit;

    public synchronized long start(int limit) {
        if (limit < 1 || limit > 100) throw new IllegalArgumentException("步数必须为 1–100");
        generation++;
        active = true;
        steps = 0;
        this.limit = limit;
        return generation;
    }

    public synchronized void stop() { active = false; generation++; }
    public synchronized boolean current(long token) { return active && token == generation; }
    public synchronized boolean active() { return active; }
    public synchronized int steps() { return steps; }
    public synchronized boolean next(long token) {
        if (!current(token) || steps >= limit) return false;
        steps++;
        return true;
    }
}

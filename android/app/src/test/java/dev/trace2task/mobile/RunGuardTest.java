package dev.trace2task.mobile;

import org.junit.Test;
import static org.junit.Assert.*;

public class RunGuardTest {
    @Test public void serviceConnectionDoesNotStartTask() { assertFalse(new RunGuard().active()); }
    @Test public void stopInvalidatesNetworkScreenshotAndGestureCallbacks() {
        RunGuard guard = new RunGuard();
        long token = guard.start(20);
        assertTrue(guard.current(token));
        guard.stop();
        assertFalse(guard.current(token));
        assertFalse(guard.next(token));
    }
    @Test public void newRunCannotAcceptOldResponses() {
        RunGuard guard = new RunGuard();
        long first = guard.start(20);
        long second = guard.start(10);
        assertFalse(guard.current(first));
        assertTrue(guard.current(second));
    }
    @Test public void enforcesRoundLimitWithoutExtraRequest() {
        RunGuard guard = new RunGuard();
        long token = guard.start(2);
        assertTrue(guard.next(token));
        assertTrue(guard.next(token));
        assertFalse(guard.next(token));
        assertEquals(2, guard.steps());
    }
    @Test public void rejectsUnboundedLimits() {
        for (int value : new int[]{0, -1, 101, Integer.MAX_VALUE})
            assertThrows(IllegalArgumentException.class, () -> new RunGuard().start(value));
    }
}

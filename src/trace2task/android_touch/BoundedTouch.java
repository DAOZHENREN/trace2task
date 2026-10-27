package org.trace2task.input;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.FileReader;
import java.io.InputStreamReader;
import java.lang.reflect.Method;
import java.util.Base64;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.DeflaterOutputStream;

/** One single-contact gesture per process. No sockets or reconnects.
 * The only child command is fixed `dumpsys window displays` for focus metadata;
 * no model-selected command, UI hierarchy or game state is exposed.
 * Compiled with ordinary javac + D8; Android framework calls use reflection so
 * building this small helper does not require a full Android SDK installation.
 */
public final class BoundedTouch {
    private static final int HEARTBEAT_TIMEOUT_MS = 1500;

    interface WindowScope {
        String read() throws Exception;
        String boot() throws Exception;
    }

    private static String windowScope() throws Exception {
        Process process = new ProcessBuilder("/system/bin/dumpsys", "window", "displays").start();
        try {
            ByteArrayOutputStream data = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int count;
            while ((count = process.getInputStream().read(buffer)) != -1) {
                if (data.size() + count > 512 * 1024) throw new IllegalStateException("scope too large");
                data.write(buffer, 0, count);
            }
            if (process.waitFor() != 0) throw new IllegalStateException("scope unavailable");
            return data.toString("UTF-8");
        } finally { process.destroy(); }
    }

    interface Input {
        void checkScope() throws Exception;
        void send(int action, long down, float x, float y) throws Exception;
        long uptime();
    }

    static final class Gesture {
        private final Input input;
        private final int width, height;
        private boolean started, pressed, finished;
        private final long idleDeadline;
        private long down, deadline, heartbeatDeadline;
        private float x, y;
        private int moves;

        Gesture(Input input, int width, int height) {
            this.input = input;
            this.width = width;
            this.height = height;
            idleDeadline = input.uptime() + 5000;
        }

        private void point(float px, float py) {
            if (!Float.isFinite(px) || !Float.isFinite(py)
                    || px < 0 || px >= width || py < 0 || py >= height) {
                throw new IllegalArgumentException("point outside bound display");
            }
        }

        void begin(long duration, float cx, float cy, float ex, float ey) throws Exception {
            point(cx, cy);
            point(ex, ey);
            if (duration < 1) {
                throw new IllegalArgumentException("duration must be positive milliseconds");
            }
            input.checkScope();
            synchronized (this) {
                if (started || finished) throw new IllegalStateException("gesture already consumed");
                started = true;
                down = input.uptime();
                deadline = duration > Long.MAX_VALUE - down ? Long.MAX_VALUE : down + duration;
                heartbeatDeadline = down + HEARTBEAT_TIMEOUT_MS;
                x = cx;
                y = cy;
                // Mark before injection: a throwing Binder call may have delivered DOWN.
                pressed = true;
                input.send(0, down, x, y);
                if (input.uptime() >= deadline) {
                    release();
                    return;
                }
                x = ex;
                y = ey;
                input.send(2, down, x, y);
            }
        }

        boolean move(float px, float py) throws Exception {
            point(px, py);
            input.checkScope();
            synchronized (this) {
                if (finished || !pressed || expiryReason() != null) return false;
                if (++moves > 32) throw new IllegalArgumentException("too many corrections");
                x = px;
                y = py;
                input.send(2, down, x, y);
                return true;
            }
        }

        String keepAlive() throws Exception {
            input.checkScope();
            synchronized (this) {
                if (!started) throw new IllegalStateException("no active gesture");
                // The watchdog may have released while checkScope was running.
                // Let that owner report its reason; never revive the contact.
                if (finished) return null;
                String expired = expiryReason();
                if (expired != null) return expired; // Never revive an expired contact.
                heartbeatDeadline = input.uptime() + HEARTBEAT_TIMEOUT_MS;
                return null; // The original duration/deadline and downTime never change.
            }
        }

        synchronized String expiryReason() {
            if (finished) return null;
            long now = input.uptime();
            if (!started) return now >= idleDeadline ? "idle_timeout" : null;
            if (now >= deadline) return "deadline";
            return now >= heartbeatDeadline ? "heartbeat_timeout" : null;
        }

        synchronized void release() throws Exception {
            finished = true;
            if (pressed) {
                // Cleanup is permitted even after focus/rotation change or a failed MOVE.
                // Never validate scope here: releasing our own contact is mandatory.
                input.send(1, down, x, y);
                pressed = false;
            }
        }
    }

    private static final class AndroidInput implements Input {
        private final Method uptime, obtain, setSource, setDisplay, recycle, inject;
        private final Method focused, rotation, realSize;
        private final Object manager, activityTaskManager, display;
        private final Class<?> motion, point;
        private final String expectedPackage, expectedActivity, expectedBoot;
        private final int width, height, expectedRotation, deviceId;
        private final WindowScope windowScope;

        AndroidInput(int width, int height, int rotation, String packageName,
                String activityName, String boot, WindowScope windowScope) throws Exception {
            this.width = width;
            this.height = height;
            expectedRotation = rotation;
            expectedPackage = packageName;
            expectedActivity = activityName.startsWith(".") ? packageName + activityName : activityName;
            this.windowScope = windowScope;
            expectedBoot = boot;
            uptime = Class.forName("android.os.SystemClock").getMethod("uptimeMillis");
            motion = Class.forName("android.view.MotionEvent");
            obtain = motion.getMethod("obtain", long.class, long.class, int.class,
                    float.class, float.class, float.class, float.class, int.class,
                    float.class, float.class, int.class, int.class);
            setSource = motion.getMethod("setSource", int.class);
            setDisplay = motion.getMethod("setDisplayId", int.class);
            recycle = motion.getMethod("recycle");
            Class<?> inputManager;
            try {
                inputManager = Class.forName("android.hardware.input.InputManagerGlobal");
            } catch (ClassNotFoundException absent) {
                inputManager = Class.forName("android.hardware.input.InputManager");
            }
            manager = inputManager.getMethod("getInstance").invoke(null);
            inject = inputManager.getMethod("injectInputEvent",
                    Class.forName("android.view.InputEvent"), int.class);
            Class<?> device = Class.forName("android.view.InputDevice");
            int chosenDevice = 0;
            for (int id : (int[]) device.getMethod("getDeviceIds").invoke(null)) {
                Object candidate = device.getMethod("getDevice", int.class).invoke(null, id);
                if (candidate != null && (Boolean) device.getMethod("supportsSource", int.class)
                        .invoke(candidate, 0x1002)) {
                    chosenDevice = id;
                    break;
                }
            }
            deviceId = chosenDevice;
            activityTaskManager = Class.forName("android.app.ActivityTaskManager")
                    .getMethod("getService").invoke(null);
            Class<?> taskManager = Class.forName("android.app.IActivityTaskManager");
            Method top;
            try {
                top = taskManager.getMethod("getFocusedRootTaskInfo");
            } catch (NoSuchMethodException oldAndroid) {
                top = taskManager.getMethod("getFocusedStackInfo");
            }
            focused = top;
            Class<?> dm = Class.forName("android.hardware.display.DisplayManagerGlobal");
            display = dm.getMethod("getRealDisplay", int.class)
                    .invoke(dm.getMethod("getInstance").invoke(null), 0);
            Class<?> dc = Class.forName("android.view.Display");
            this.rotation = dc.getMethod("getRotation");
            point = Class.forName("android.graphics.Point");
            realSize = dc.getMethod("getRealSize", point);
        }

        public long uptime() {
            try { return (Long) uptime.invoke(null); }
            catch (Exception error) { throw new IllegalStateException(error); }
        }

        public void checkScope() throws Exception {
            if (!expectedBoot.equals(windowScope.boot())) {
                throw new IllegalStateException("bound device boot changed");
            }
            // Top activity alone is insufficient: a notification/keyboard/system
            // overlay can own the focused window while the game activity stays on top.
            Matcher focus = Pattern.compile("mCurrentFocus=Window\\{[^\\n]*?\\bu\\d+\\s+([^\\s}]+)")
                    .matcher(windowScope.read());
            if (!focus.find()) throw new IllegalStateException("focused window unavailable");
            String[] parts = focus.group(1).split("/", 2);
            String activityName = parts.length == 2 && parts[1].startsWith(".")
                    ? parts[0] + parts[1] : parts.length == 2 ? parts[1] : "";
            if (focus.find() || parts.length != 2 || !expectedPackage.equals(parts[0])
                    || !expectedActivity.equals(activityName)) {
                throw new IllegalStateException("bound window lost focus");
            }
            Object info = focused.invoke(activityTaskManager);
            Object activity = info == null ? null : info.getClass().getField("topActivity").get(info);
            Class<?> component = Class.forName("android.content.ComponentName");
            if (activity == null || !expectedPackage.equals(
                    component.getMethod("getPackageName").invoke(activity))
                    || !expectedActivity.equals(component.getMethod("getClassName").invoke(activity))) {
                throw new IllegalStateException("selected activity is not foreground");
            }
            Object size = point.getConstructor().newInstance();
            realSize.invoke(display, size);
            if ((Integer) rotation.invoke(display) != expectedRotation
                    || point.getField("x").getInt(size) != width
                    || point.getField("y").getInt(size) != height) {
                throw new IllegalStateException("bound display changed");
            }
        }

        public void send(int action, long down, float x, float y) throws Exception {
            Object event = obtain.invoke(null, down, uptime(), action, x, y,
                    action == 1 ? 0f : 1f, 1f, 0, 1f, 1f, deviceId, 0);
            try {
                setSource.invoke(event, 0x1002);
                setDisplay.invoke(event, 0);
                // Asynchronous injection avoids waiting for an unresponsive game.
                if (!Boolean.TRUE.equals(inject.invoke(manager, event, 0))) {
                    throw new IllegalStateException("Android rejected input injection");
                }
            } finally { recycle.invoke(event); }
        }

        String capture(int left, int top, int cropWidth, int cropHeight) throws Exception {
            if (left < 0 || top < 0 || cropWidth < 1 || cropHeight < 1
                    || cropWidth > 512 || cropHeight > 512
                    || left + cropWidth > width || top + cropHeight > height) {
                throw new IllegalArgumentException("invalid minimap crop");
            }
            checkScope();
            // Use the Android 10/11 screenshot API, then encode only the crop.
            // A full-screen screencap PNG encode/transfer is too slow for this
            // lease. Unsupported framework versions fail closed, before DOWN.
            Object bitmap = null, cropped = null, software = null;
            Class<?> type = Class.forName("android.graphics.Bitmap");
            try {
                Class<?> rect = Class.forName("android.graphics.Rect");
                bitmap = Class.forName("android.view.SurfaceControl")
                        .getMethod("screenshot", rect, int.class, int.class, int.class)
                        .invoke(null, rect.getConstructor().newInstance(), width, height, expectedRotation);
                if (bitmap == null || (Integer) type.getMethod("getWidth").invoke(bitmap) != width
                        || (Integer) type.getMethod("getHeight").invoke(bitmap) != height) {
                    throw new IllegalStateException("screenshot geometry changed");
                }
                cropped = type.getMethod("createBitmap", type, int.class, int.class, int.class, int.class)
                        .invoke(null, bitmap, left, top, cropWidth, cropHeight);
                Class<?> config = Class.forName("android.graphics.Bitmap$Config");
                software = type.getMethod("copy", config, boolean.class)
                        .invoke(cropped, config.getField("ARGB_8888").get(null), false);
                int[] colors = new int[cropWidth * cropHeight];
                type.getMethod("getPixels", int[].class, int.class, int.class, int.class,
                        int.class, int.class, int.class)
                        .invoke(software, colors, 0, cropWidth, 0, 0, cropWidth, cropHeight);
                // The tracker uses luminance only. Match Pillow RGB -> L exactly;
                // transfer lossless grayscale, not a larger RGB PNG or lossy JPEG.
                byte[] pixels = new byte[colors.length];
                for (int n = 0; n < colors.length; n++) {
                    int color = colors[n];
                    pixels[n] = (byte) ((((color >> 16) & 255) * 19595
                            + ((color >> 8) & 255) * 38470 + (color & 255) * 7471 + 32768) >> 16);
                }
                ByteArrayOutputStream output = new ByteArrayOutputStream();
                try (DeflaterOutputStream compressed = new DeflaterOutputStream(output)) {
                    compressed.write(pixels);
                }
                checkScope();
                return Base64.getEncoder().encodeToString(output.toByteArray());
            } finally {
                if (software != null) type.getMethod("recycle").invoke(software);
                if (cropped != null && cropped != bitmap) type.getMethod("recycle").invoke(cropped);
                if (bitmap != null) type.getMethod("recycle").invoke(bitmap);
            }
        }
    }

    private static synchronized void reply(String message) {
        System.out.println("T2T2 " + message);
        System.out.flush();
    }

    public static void main(String[] args) {
        run(args, new WindowScope() {
            public String read() throws Exception { return windowScope(); }
            public String boot() throws Exception {
                try (BufferedReader reader = new BufferedReader(new FileReader("/proc/sys/kernel/random/boot_id"))) {
                    return reader.readLine();
                }
            }
        });
    }

    static void run(String[] args, WindowScope scope) {
        Gesture gesture = null;
        try {
            if (args.length != 6) throw new IllegalArgumentException("expected geometry, package, activity and boot");
            int width = Integer.parseInt(args[0]), height = Integer.parseInt(args[1]);
            int rotation = Integer.parseInt(args[2]);
            if (width < 1 || width > 8192 || height < 1 || height > 8192
                    || rotation < 0 || rotation > 3
                    || !args[3].matches("[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)+")
                    || !args[4].matches("[A-Za-z0-9_.$]+") || !args[5].matches("[0-9a-f-]{36}")) {
                throw new IllegalArgumentException("invalid bound target");
            }
            AndroidInput input = new AndroidInput(width, height, rotation, args[3], args[4], args[5], scope);
            input.checkScope();
            gesture = new Gesture(input, width, height);
            final Gesture active = gesture;
            Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                try { active.release(); } catch (Exception ignored) { }
            }));
            // Requested duration and lost-host timeout are separate. Neither
            // a blocked scope check nor a missing host UP may block this watchdog.
            Thread watchdog = new Thread(() -> {
                try {
                    while (true) {
                        String expired = active.expiryReason();
                        if (expired != null) {
                            active.release();
                            reply("RELEASED " + expired);
                            System.exit(0);
                        }
                        Thread.sleep(10);
                    }
                } catch (Exception error) {
                    reply("ERROR watchdog");
                    System.exit(1);
                }
            }, "bounded-touch-release");
            watchdog.setDaemon(true);
            watchdog.start();
            reply("READY");
            AtomicBoolean capturing = new AtomicBoolean();
            BufferedReader reader = new BufferedReader(new InputStreamReader(System.in, "UTF-8"));
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.length() > 256) throw new IllegalArgumentException("oversized command");
                String[] parts = line.trim().split("\\s+");
                if (parts.length == 5 && parts[0].equals("FRAME")) {
                    if (!capturing.compareAndSet(false, true)) {
                        throw new IllegalArgumentException("frame already pending");
                    }
                    final int left = Integer.parseInt(parts[1]), top = Integer.parseInt(parts[2]);
                    final int cw = Integer.parseInt(parts[3]), ch = Integer.parseInt(parts[4]);
                    // Keep stdin free for immediate UP/EOF while capture is in flight.
                    Thread frame = new Thread(() -> {
                        try {
                            String pixels = input.capture(left, top, cw, ch);
                            capturing.set(false);
                            reply("FRAME " + pixels);
                        }
                        catch (Exception error) {
                            try { active.release(); reply("RELEASED capture_error"); }
                            catch (Exception releaseError) { reply("ERROR release"); }
                            reply("ERROR capture_" + error.getClass().getSimpleName());
                        } finally { capturing.set(false); }
                    }, "bounded-touch-frame");
                    frame.setDaemon(true);
                    frame.start();
                } else if (parts.length == 6 && parts[0].equals("START")) {
                    if (capturing.get()) throw new IllegalArgumentException("frame pending");
                    active.begin(Long.parseLong(parts[1]), Float.parseFloat(parts[2]),
                            Float.parseFloat(parts[3]), Float.parseFloat(parts[4]), Float.parseFloat(parts[5]));
                    reply("STARTED");
                } else if (parts.length == 1 && parts[0].equals("KEEPALIVE")) {
                    String expired = active.keepAlive();
                    if (expired != null) {
                        active.release();
                        reply("RELEASED " + expired);
                        return;
                    }
                    reply("ALIVE");
                } else if (parts.length == 3 && parts[0].equals("MOVE")) {
                    if (capturing.get()) throw new IllegalArgumentException("frame pending");
                    if (!active.move(Float.parseFloat(parts[1]), Float.parseFloat(parts[2]))) break;
                    reply("MOVED");
                } else if (parts.length == 1 && parts[0].equals("UP")) {
                    break;
                } else {
                    throw new IllegalArgumentException("invalid command");
                }
            }
            active.release();
            reply("RELEASED requested");
        } catch (Exception error) {
            reply("ERROR " + error.getClass().getSimpleName());
            System.err.println(error.toString());
        } finally {
            if (gesture != null) {
                try { gesture.release(); }
                catch (Exception error) { reply("ERROR release"); }
            }
        }
    }
}

// Runs one loopback RDP control on its own STA, for the lifetime of its parent's stdin.
// The on-demand task starts only the Cua daemon, in the confirmed child logon session.
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Diagnostics.Eventing.Reader;
using System.Drawing;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;
using Microsoft.Win32.SafeHandles;

[ComImport, Guid("302D8188-0052-4807-806A-362B628F9AC5"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IRdpSettings {
    void Put([MarshalAs(UnmanagedType.BStr)] string key, [MarshalAs(UnmanagedType.Struct)] ref object value);
    void Get([MarshalAs(UnmanagedType.BStr)] string key, [MarshalAs(UnmanagedType.Struct)] out object value);
}

// Choose where Windows may prompt if its child-session single sign-on needs help.
[ComImport, Guid("F50FA8AA-1C7D-4F59-B15C-A90CACAE1FCB"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IRdpClientLogin {
    void _VtblGap1_44();
    void PromptForCredsOnClient([MarshalAs(UnmanagedType.VariantBool)] bool enable);
}

sealed class RdpControl : AxHost {
    public RdpControl() : base("A0C63C30-F08D-4AB4-907C-34905D770C7D") {}
    public object Client { get { return GetOcx(); } }
}

sealed class ChildDesktopHost : Form {
    [DllImport("wtsapi32.dll", SetLastError=true)] static extern bool WTSGetChildSessionId(out int id);
    [DllImport("wtsapi32.dll", SetLastError=true)] static extern bool WTSIsChildSessionsEnabled(out bool enabled);
    [DllImport("wtsapi32.dll", SetLastError=true)] static extern bool WTSLogoffSession(IntPtr server, int session, bool wait);
    [DllImport("wtsapi32.dll", SetLastError=true)] static extern bool WTSQuerySessionInformationW(IntPtr server, int session, int field, out IntPtr buffer, out int length);
    [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr buffer);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetNamedPipeServerProcessId(SafePipeHandle pipe, out int pid);
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    struct StartupInfo {
        public int size; public string reserved, desktop, title;
        public int x, y, width, height, columns, rows, fill, flags;
        public short show, reservedBytes;
        public IntPtr reservedPtr, stdin, stdout, stderr;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct ProcessInfo { public IntPtr process, thread; public int pid, tid; }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool CreateProcessW(string app, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes,
        bool inheritHandles, uint flags, IntPtr env, string cwd, ref StartupInfo startup, out ProcessInfo process);
    [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr handle, out int code);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] static extern bool FreeConsole();
    [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int kind);
    [DllImport("user32.dll")] static extern IntPtr GetShellWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out int pid);
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool DuplicateTokenEx(IntPtr token, uint access,
        IntPtr security, int level, int type, out IntPtr primary);
    [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool CreateProcessWithTokenW(IntPtr token, uint logon, string app, StringBuilder command,
        uint flags, IntPtr env, string cwd, ref StartupInfo startup, out ProcessInfo process);
    [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool CreateProcessAsUserW(IntPtr token, string app, StringBuilder command, IntPtr processAcl,
        IntPtr threadAcl, bool inherit, uint flags, IntPtr env, string cwd, ref StartupInfo startup, out ProcessInfo process);
    [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr process, uint status);
    [DllImport("kernel32.dll")] static extern ulong GetTickCount64();
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern uint GetDpiForSystem();
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsChild(IntPtr parent, IntPtr child);

    readonly string driver;
    string pipe, taskName;
    readonly bool interactive;
    readonly int parentSession = Process.GetCurrentProcess().SessionId;
    readonly IntPtr previousForeground = GetForegroundWindow();
    readonly RdpControl control = new RdpControl();
    readonly Size desktopSize;
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer { Interval = 200 };
    readonly Stopwatch startup = Stopwatch.StartNew();
    readonly Guid eventsId = new Guid("336D5562-EFA8-482E-8CB3-C5C0FC7A7DB6");
    dynamic client, taskFolder, registeredTask, runningTask;
    readonly Stopwatch daemonStartup = new Stopwatch();
    Process daemon;
    bool registered, loggedIn, closing, viewerOpen;
    int ownedSession = -1, reconnectSession = -1;
    volatile bool stopRequested;

    static int ChildId() {
        int id;
        if (WTSGetChildSessionId(out id)) return id;
        int error = Marshal.GetLastWin32Error();
        if (error == 1168) return -1; // ERROR_NOT_FOUND
        throw new Win32Exception(error, "Cannot query Windows child session (" + error + ")");
    }

    static bool SessionExists(int id) {
        if (id == -1) return false;
        IntPtr buffer;
        int length;
        if (WTSQuerySessionInformationW(IntPtr.Zero, id, 8, out buffer, out length)) {
            WTSFreeMemory(buffer);
            return true; // Includes disconnected sessions belonging to another app.
        }
        // Windows may retain the last child ID after its logon desktop has disappeared.
        int error = Marshal.GetLastWin32Error();
        if (error == 2 || error == 7022) return false;
        throw new Win32Exception(error);
    }

    static string SessionField(int id, int field) {
        IntPtr buffer;
        int length;
        if (!WTSQuerySessionInformationW(IntPtr.Zero, id, field, out buffer, out length))
            throw new Win32Exception();
        try { return Marshal.PtrToStringUni(buffer); }
        finally { WTSFreeMemory(buffer); }
    }

    // The Interception filter can exhaust its ten keyboard slots across RDP logons.
    // Require the actual filter, occupied slots and a matching failure from THIS boot.
    // A retained WTS id or the presence of the driver alone is normal.
    static bool KeyboardSlotsExhausted(int id) {
        if (id <= 0) return false;
        try {
            const string keyboardClass = @"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Class\{4d36e96b-e325-11ce-bfc1-08002be10318}";
            var filters = Registry.GetValue(keyboardClass, "UpperFilters", null) as string[];
            if (filters == null || !Array.Exists(filters, f => String.Equals(f, "keyboard", StringComparison.OrdinalIgnoreCase))) return false;
            var driver = FileVersionInfo.GetVersionInfo(Path.Combine(Environment.SystemDirectory, @"drivers\keyboard.sys"));
            if (!String.Equals(driver.ProductName, "Interception", StringComparison.OrdinalIgnoreCase)) return false;
            using (var map = Registry.LocalMachine.OpenSubKey(@"HARDWARE\DEVICEMAP\KeyboardClass")) {
                if (map == null) return false;
                for (int slot = 0; slot < 10; slot++) {
                    if (map.GetValue(@"\Device\KeyboardClass" + slot) == null) return false;
                }
            }
            var query = new EventLogQuery("Microsoft-Windows-Kernel-PnP/Configuration", PathType.LogName,
                "*[System[EventID=411 and TimeCreated[timediff(@SystemTime) <= " + GetTickCount64() +
                "]] and EventData[Data[@Name='ServiceName']='terminpt']]") { ReverseDirection = true };
            using (var events = new EventLogReader(query))
            using (var fields = new EventLogPropertySelector(new[] { "Event/EventData/Data[@Name='DeviceInstanceId']",
                       "Event/EventData/Data[@Name='Problem']", "Event/EventData/Data[@Name='Status']" })) {
                EventRecord entry;
                while ((entry = events.ReadEvent()) != null) using (entry) {
                    var data = ((EventLogRecord)entry).GetPropertyValues(fields);
                    var device = data[0] as string;
                    if (device != null && device.StartsWith(@"TERMINPUT_BUS\", StringComparison.OrdinalIgnoreCase)
                            && device.IndexOf("&Session" + id + "Keyboard", StringComparison.OrdinalIgnoreCase) >= 0
                            && Convert.ToUInt32(data[1]) == 10
                            && unchecked((uint)Convert.ToInt64(data[2])) == 0xC0000184) return true;
                }
            }
        } catch (EventLogException) { } catch (UnauthorizedAccessException) { } catch (IOException) { }
        catch (System.Security.SecurityException) { } catch (FormatException) { } catch (InvalidCastException) { }
        return false;
    }

    public ChildDesktopHost(string driverPath, Guid nonce, bool showLogin) {
        interactive = showLogin;
        driver = Path.GetFullPath(driverPath);
        pipe = "trace2task-child-" + nonce.ToString("N");
        taskName = "Trace2Task-Child-" + nonce.ToString("N");
        Text = "Trace2Task · Windows 后台桌面登录";
        FormBorderStyle = FormBorderStyle.SizableToolWindow;
        ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
        var screen = Screen.PrimaryScreen;
        var area = screen.WorkingArea;
        desktopSize = FitInside(screen.Bounds.Size, new Size(8192, 8192));
        AutoScaleMode = AutoScaleMode.None;
        BackColor = Color.Black;
        Bounds = interactive ? new Rectangle(area.Left + Math.Max(0, (area.Width - 900) / 2),
            area.Top + Math.Max(0, (area.Height - 590) / 2), Math.Min(900, area.Width), Math.Min(590, area.Height))
            : new Rectangle(new Point(-20000, -20000), desktopSize);
        Controls.Add(control);
        LayoutViewer();
        ClientSizeChanged += delegate { LayoutViewer(); };
        timer.Tick += delegate { Guard(TickHost); };
        Shown += delegate { Guard(Connect); };
    }

    static Size FitInside(Size source, Size available) {
        double ratio = Math.Min(1.0, Math.Min((double)available.Width / source.Width,
                                              (double)available.Height / source.Height));
        return new Size(Math.Max(1, (int)Math.Round(source.Width * ratio)),
                        Math.Max(1, (int)Math.Round(source.Height * ratio)));
    }

    void LayoutViewer() {
        var size = FitInside(desktopSize, ClientSize);
        control.Bounds = new Rectangle((ClientSize.Width - size.Width) / 2, (ClientSize.Height - size.Height) / 2,
                                       size.Width, size.Height);
    }

    void ConfigureDisplay() {
        client.DesktopWidth = desktopSize.Width;
        client.DesktopHeight = desktopSize.Height;
        uint scale = Math.Max(100u, Math.Min(500u, (GetDpiForSystem() * 100 + 48) / 96));
        var extended = (IRdpSettings)control.Client;
        object desktopScale = scale;
        object deviceScale = scale < 125 ? 100u : scale < 175 ? 140u : 180u;
        extended.Put("DesktopScaleFactor", ref desktopScale);
        extended.Put("DeviceScaleFactor", ref deviceScale);
        Console.WriteLine("DISPLAY " + desktopSize.Width + "x" + desktopSize.Height + " " + scale + "%");
    }

    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams {
        get { var p = base.CreateParams; p.ExStyle |= 0x80; if (!interactive) p.ExStyle |= 0x08000000; return p; }
    }

    void Guard(Action action) {
        try { action(); }
        catch (Exception e) {
            Console.WriteLine(e);
            Fail(e.GetBaseException().Message);
        }
    }
    void Fail(string reason) {
        if (closing) return;
        Console.WriteLine("ERROR " + reason.Replace('\n', ' ').Replace('\r', ' '));
        Environment.ExitCode = 1;
        stopRequested = true;
        Close();
    }

    // Only the workbench owning this window can enable input after pausing the
    // executor. The RDP control itself renders and delivers the user's keys.
    protected override void WndProc(ref Message m) {
        if (m.Msg == 0x84D2 && ownedSession > 0 && !closing) {
            int mode = m.WParam.ToInt32(); // 1: show, 2: take control, 3: hand back, 4: hide the viewer
            if (mode == 4) { HideViewer(); m.Result = new IntPtr(1); return; }
            if (mode < 1 || mode > 3) return;
            if (mode != 1) control.Enabled = mode == 2;
            Text = "Trace2Task · 后台桌面 " + ownedSession + (control.Enabled ? " · 手动操作" : " · 实时观看（只读）");
            if (mode != 3) {
                if (!viewerOpen) {
                    var work = Screen.PrimaryScreen.WorkingArea;
                    var frame = Size - ClientSize;
                    ClientSize = FitInside(desktopSize, work.Size - frame);
                    Location = new Point(work.Left + (work.Width - Width) / 2, work.Top + (work.Height - Height) / 2);
                }
                viewerOpen = true;
                if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
                Activate();
                if (control.Enabled) control.Focus();
            }
            m.Result = new IntPtr(1);
            return;
        }
        base.WndProc(ref m);
    }

    void Connect() {
        bool enabled;
        if (!WTSIsChildSessionsEnabled(out enabled)) throw new Win32Exception();
        if (!enabled) throw new InvalidOperationException("Windows child sessions are disabled");
        int previousSession = ChildId();
        if (parentSession <= 0 || (SessionExists(previousSession) && !FindDisconnectedWorkspace(previousSession)))
            throw new InvalidOperationException("Windows child session is already occupied or this is not an interactive logon");
        if (reconnectSession == -1 && KeyboardSlotsExhausted(previousSession))
            throw new InvalidOperationException("RDP_KEYBOARD_SLOTS " + previousSession);
        client = control.Client;
        client.Server = "localhost";
        // Let Windows use this logon's child-session credentials. Supplying a
        // username and forcing a new credential dialog preempts native SSO.
        ConfigureDisplay();
        client.ColorDepth = 32;
        dynamic settings = client.AdvancedSettings9;
        settings.EnableCredSspSupport = true;
        settings.GrabFocusOnConnect = false;
        settings.SmartSizing = true;
        settings.EnableAutoReconnect = false;
        settings.RedirectClipboard = false;
        settings.RedirectDrives = false;
        settings.RedirectPrinters = false;
        if (interactive) {
            ((IRdpClientLogin)control.Client).PromptForCredsOnClient(true);
        }
        ComEventsHelper.Combine(control.Client, eventsId, 2, new Action(delegate { Console.WriteLine("CONNECTED"); }));
        ComEventsHelper.Combine(control.Client, eventsId, 3, new Action(delegate { loggedIn = true; Console.WriteLine("LOGGED_IN"); }));
        ComEventsHelper.Combine(control.Client, eventsId, 22, new Action<int>(delegate(int reason) {
            Console.WriteLine("LOGON " + reason);
        }));
        ComEventsHelper.Combine(control.Client, eventsId, 4, new Action<int>(delegate(int reason) {
            if (!closing) BeginInvoke(new Action(delegate {
                int extendedReason = (int)client.ExtendedDisconnectReason;
                if (!loggedIn && reason == 3 && extendedReason == 0) {
                    int id = ChildId();
                    if (KeyboardSlotsExhausted(id)) { Fail("RDP_KEYBOARD_SLOTS " + id); return; }
                }
                string phase = loggedIn ? "after child logon" : "before Windows finished initializing the child desktop";
                Fail("RDP disconnected " + phase + " (" + reason + "/" + extendedReason + "): "
                    + client.GetErrorDescription(reason, extendedReason));
            }));
        }));
        new Thread(delegate() { try { Console.ReadLine(); } finally { stopRequested = true; } })
            { IsBackground = true }.Start();
        timer.Start();
        object child = true;
        var extended = (IRdpSettings)control.Client;
        extended.Put("ConnectToChildSession", ref child);
        object confirmed;
        extended.Get("ConnectToChildSession", out confirmed);
        if (!Object.Equals(confirmed, true)) throw new InvalidOperationException("RDP child connection mode was not accepted");
        client.Connect();
    }

    // An abruptly closed workbench can leave its child logon and driver alive.
    // Reconnect only our own disconnected child. Never run an existing task's
    // executable; the pipe server is independently verified before READY.
    bool FindDisconnectedWorkspace(int id) {
        IntPtr state; int length;
        if (id <= 0 || id == parentSession || !WTSQuerySessionInformationW(IntPtr.Zero, id, 8, out state, out length)) return false;
        try { if (Marshal.ReadInt32(state) != 4) return false; } finally { WTSFreeMemory(state); }
        var user = new NTAccount(SessionField(id, 7), SessionField(id, 5));
        if (!WindowsIdentity.GetCurrent().User.Equals(user.Translate(typeof(SecurityIdentifier)))) return false;
        dynamic scheduler = Activator.CreateInstance(Type.GetTypeFromProgID("Schedule.Service", true));
        scheduler.Connect();
        taskFolder = scheduler.GetFolder("\\");
        const string prefix = "Trace2Task-Child-";
        foreach (dynamic task in taskFolder.GetTasks(0)) {
            string name = task.Name; Guid nonce;
            if (!name.StartsWith(prefix) || !Guid.TryParseExact(name.Substring(prefix.Length), "N", out nonce)
                    || (int)task.State != 4) continue;
            dynamic definition = task.Definition;
            if ((int)definition.Actions.Count != 1 || (int)definition.Principal.LogonType != 3
                    || (int)definition.Principal.RunLevel != 0) continue;
            dynamic action = definition.Actions[1];
            string endpoint = "trace2task-child-" + nonce.ToString("N");
            string arguments = "serve " + id + " \"" + driver + "\" " + endpoint;
            var helper = new FileInfo((string)action.Path);
            if (!helper.Name.Equals("ChildDesktopHost.exe", StringComparison.OrdinalIgnoreCase)
                    || !helper.Directory.Name.StartsWith("trace2task-child-")
                    || !String.Equals((string)action.Arguments, arguments, StringComparison.OrdinalIgnoreCase)) continue;
            if (registeredTask != null) return false; // ambiguous ownership
            registeredTask = task; taskName = name; pipe = endpoint;
        }
        if (registeredTask == null) return false;
        reconnectSession = id;
        Console.WriteLine("RECONNECT " + id + " " + pipe.Substring("trace2task-child-".Length));
        return true;
    }

    void StartDaemon(int id) {
        // The workbench can be elevated, while the child uses the same user's
        // normal token. Give it read/execute access to only this private helper.
        var user = WindowsIdentity.GetCurrent().User;
        var image = new FileInfo(Application.ExecutablePath);
        var read = new FileSystemAccessRule(user, FileSystemRights.ReadAndExecute, AccessControlType.Allow);
        var directoryAcl = image.Directory.GetAccessControl();
        directoryAcl.AddAccessRule(read);
        image.Directory.SetAccessControl(directoryAcl);
        var fileAcl = image.GetAccessControl();
        fileAcl.AddAccessRule(read);
        image.SetAccessControl(fileAcl);
        dynamic scheduler = Activator.CreateInstance(Type.GetTypeFromProgID("Schedule.Service", true));
        scheduler.Connect();
        taskFolder = scheduler.GetFolder("\\");
        dynamic def = scheduler.NewTask(0);
        string sid = user.Value;
        def.Principal.UserId = sid;
        def.Principal.LogonType = 3; // TASK_LOGON_INTERACTIVE_TOKEN, no password or elevation
        def.Settings.DisallowStartIfOnBatteries = false;
        def.Settings.StopIfGoingOnBatteries = false;
        def.Settings.ExecutionTimeLimit = "PT0S";
        dynamic exec = def.Actions.Create(0);
        exec.Path = Application.ExecutablePath;
        exec.Arguments = "serve " + id + " \"" + driver + "\" " + pipe;
        registeredTask = taskFolder.RegisterTaskDefinition(taskName, def, 2, sid, null, 3);
        registered = true;
        runningTask = registeredTask.RunEx(null, 4, id, null); // TASK_RUN_USE_SESSION_ID
        daemonStartup.Start();
    }

    static int Serve(string[] args) {
        int expected = int.Parse(args[1]);
        if (expected <= 0 || expected != Process.GetCurrentProcess().SessionId)
            throw new InvalidOperationException("Refusing to start Cua outside the bound child session");
        // Task Scheduler may allocate a console in the child. Detach before exposing a screenshot.
        FreeConsole();
        string driverPath = Path.GetFullPath(args[2]);
        string pipeName = args[3];
        Guid nonce;
        const string prefix = "trace2task-child-";
        if (!pipeName.StartsWith(prefix) || !Guid.TryParseExact(pipeName.Substring(prefix.Length), "N", out nonce))
            throw new ArgumentException("Invalid private pipe name");
        string pidFile = Path.Combine(Path.GetTempPath(), pipeName + ".pid");
        var cmd = new StringBuilder("\"" + driverPath + "\" serve --no-overlay --socket " + @"\\.\pipe\" + pipeName
            + " --pid-file \"" + pidFile + "\"");
        Environment.SetEnvironmentVariable("CUA_DRIVER_RS_TELEMETRY_ENABLED", "false");
        // A scheduled task can have the right session ID but the wrong window station.
        var startup = new StartupInfo { size = Marshal.SizeOf(typeof(StartupInfo)), desktop = @"WinSta0\Default" };
        ProcessInfo info;
        if (!CreateProcessW(driverPath, cmd, IntPtr.Zero, IntPtr.Zero, false, 0x08000000, IntPtr.Zero,
                            Path.GetDirectoryName(driverPath), ref startup, out info)) throw new Win32Exception();
        CloseHandle(info.thread);
        try {
            WaitForSingleObject(info.process, UInt32.MaxValue);
            int code;
            if (!GetExitCodeProcess(info.process, out code)) throw new Win32Exception();
            return code;
        } finally { CloseHandle(info.process); File.Delete(pidFile); }
    }

    // An elevated CLI cannot be identified by the standard-user Cua server in
    // another logon session. Issue the original CLI call with our own desktop
    // shell's standard-user token, retaining its stdin, stdout and stderr.
    static int CallDriver(string[] args) {
        string path = Path.GetFullPath(args[1]);
        Guid nonce;
        if (!Guid.TryParseExact(args[3], "N", out nonce)
                || !System.Text.RegularExpressions.Regex.IsMatch(args[2], @"\A[a-z_]+\z"))
            throw new ArgumentException("Invalid child driver call");
        var cmd = new StringBuilder("\"" + path + "\" call " + args[2] + " --socket "
                                    + @"\\.\pipe\trace2task-child-" + nonce.ToString("N"));
        var startup = new StartupInfo { size = Marshal.SizeOf(typeof(StartupInfo)), flags = 0x100,
            stdin = GetStdHandle(-10), stdout = GetStdHandle(-11), stderr = GetStdHandle(-12) };
        int shellPid;
        if (GetWindowThreadProcessId(GetShellWindow(), out shellPid) == 0) throw new InvalidOperationException("Windows shell is unavailable");
        IntPtr shell = OpenProcess(0x1000, false, shellPid);
        if (shell == IntPtr.Zero) throw new Win32Exception();
        try {
            IntPtr shellToken, primary;
            if (!OpenProcessToken(shell, 0xA, out shellToken)) throw new Win32Exception();
            try {
                using (var identity = new WindowsIdentity(shellToken)) using (var current = WindowsIdentity.GetCurrent()) {
                    if (!current.User.Equals(identity.User) || new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator))
                        throw new InvalidOperationException("A standard-user shell for the same account is required");
                }
                if (!DuplicateTokenEx(shellToken, 0x02000000, IntPtr.Zero, 2, 1, out primary)) throw new Win32Exception();
            } finally { CloseHandle(shellToken); }
            // The standard-user client only needs its local profile and system
            // paths. Keep the same telemetry setting as the daemon.
            var vars = new StringBuilder();
            foreach (string name in new[] {"APPDATA", "CUA_DRIVER_RS_TELEMETRY_ENABLED", "LOCALAPPDATA",
                                           "SystemDrive", "SystemRoot", "TEMP", "TMP", "USERPROFILE", "WINDIR"}) {
                string value = name == "CUA_DRIVER_RS_TELEMETRY_ENABLED" ? "false" : Environment.GetEnvironmentVariable(name);
                if (value != null) vars.Append(name).Append('=').Append(value).Append('\0');
            }
            IntPtr env = Marshal.StringToHGlobalUni(vars.ToString() + "\0");
            try {
                ProcessInfo info;
                if (!CreateProcessAsUserW(primary, path, new StringBuilder(cmd.ToString()), IntPtr.Zero, IntPtr.Zero,
                        true, 0x08000400, env, null, ref startup, out info)) {
                    int error = Marshal.GetLastWin32Error();
                    if (error != 1314) throw new Win32Exception(error, "CreateProcessAsUser " + error);
                    if (!CreateProcessWithTokenW(primary, 0, path, cmd, 0x08000400, env,
                            null, ref startup, out info)) throw new Win32Exception();
                }
                CloseHandle(info.thread);
                try {
                    if (WaitForSingleObject(info.process, 18000) != 0) {
                        TerminateProcess(info.process, 1);
                        throw new TimeoutException("Cua call timed out; input effect unknown");
                    }
                    int code;
                    if (!GetExitCodeProcess(info.process, out code)) throw new Win32Exception();
                    return code;
                } finally { CloseHandle(info.process); }
            } finally { Marshal.FreeHGlobal(env); CloseHandle(primary); }
        } finally { CloseHandle(shell); }
    }

    void TickHost() {
        if (stopRequested) { Close(); return; }
        if (daemon != null) {
            if (daemon.HasExited || ChildId() != ownedSession || (int)client.Connected != 1)
                Fail("The bound child session or its Cua daemon exited");
            return;
        }
        if (!loggedIn && startup.Elapsed.TotalSeconds > (interactive ? 180 : 75)) { Fail("RDP_LOGIN_TIMEOUT"); return; }
        if (loggedIn && ((ownedSession == -1 && startup.Elapsed.TotalSeconds > (interactive ? 180 : 75))
                || daemonStartup.Elapsed.TotalSeconds > 30)) { Fail("Windows has signed in but the child Cua driver did not become ready"); return; }
        if (!loggedIn || (int)client.Connected != 1) return;
        if (ownedSession == -1) {
            int id = ChildId();
            if (id == -1) return;
            if (id <= 0 || id == parentSession) throw new InvalidOperationException("Invalid child session identity");
            if (reconnectSession > 0 && id != reconnectSession) throw new InvalidOperationException("Windows did not return the selected existing child logon");
            string user = SessionField(id, 5); // WTSUserName
            if (String.IsNullOrEmpty(user)) return; // Logon can precede the WTS account record.
            var account = new NTAccount(SessionField(id, 7), user); // WTSDomainName
            if (!WindowsIdentity.GetCurrent().User.Equals(account.Translate(typeof(SecurityIdentifier))))
                throw new InvalidOperationException("Please sign in to the child desktop with the current Windows account");
            ownedSession = id;
            if (reconnectSession == -1) Console.WriteLine("BOUND " + id);
            IntPtr active = GetForegroundWindow();
            if (previousForeground != IntPtr.Zero && (active == Handle || IsChild(Handle, active)))
                SetForegroundWindow(previousForeground);
            // Continue rendering offscreen; only the Cua daemon should supply input here.
            control.Enabled = false;
            Location = new Point(-20000, -20000);
            if (reconnectSession == -1) StartDaemon(id);
            else daemonStartup.Start();
        }
        using (var peer = new NamedPipeClientStream(".", pipe, PipeDirection.InOut)) {
            try { peer.Connect(10); } catch (TimeoutException) {
                if (daemonStartup.Elapsed.TotalSeconds > 3 && (int)registeredTask.State == 3) {
                    int result = (int)registeredTask.LastTaskResult;
                    if (result != 0x41303) Fail("Child Cua driver exited before connecting (0x" + result.ToString("X8") + ")");
                }
                return;
            }
            int pid;
            // A pipe instance can be opened before the server finishes accepting
            // it. Keep the same handle while Windows finishes the handshake.
            for (int attempt = 0; !GetNamedPipeServerProcessId(peer.SafePipeHandle, out pid); attempt++) {
                int error = Marshal.GetLastWin32Error();
                if (error != 109 && error != 232 && error != 233) throw new Win32Exception(error);
                if (attempt == 8) return;
                Thread.Sleep(15);
            }
            var candidate = Process.GetProcessById(pid);
            if (candidate.SessionId != ownedSession || !String.Equals(candidate.MainModule.FileName, driver, StringComparison.OrdinalIgnoreCase)) {
                candidate.Dispose();
                throw new InvalidOperationException("Cua pipe owner does not match the child session");
            }
            daemon = candidate;
            if (reconnectSession > 0) {
                runningTask = registeredTask.GetInstances(0)[1];
                registered = true;
            }
            Console.WriteLine("READY " + ownedSession + " " + pid + " " + Handle.ToInt64());
        }
    }

    void Cleanup(Action action) {
        try { action(); } catch (Exception e) { Console.WriteLine("CLEANUP " + e.GetBaseException().Message); Environment.ExitCode = 1; }
    }
    void HideViewer() {
        viewerOpen = false;
        WindowState = FormWindowState.Normal;
        Location = new Point(-20000, -20000);
    }
    protected override void OnFormClosing(FormClosingEventArgs e) {
        if (viewerOpen && !stopRequested && e.CloseReason == CloseReason.UserClosing) {
            e.Cancel = true;
            HideViewer();
            return;
        }
        closing = true;
        timer.Stop();
        bool loggingOff = false;
        if (ownedSession > 0 && (reconnectSession == -1 || daemon != null)) Cleanup(delegate {
            if ((int)client.Connected == 1 && ChildId() == ownedSession) {
                if (!WTSLogoffSession(IntPtr.Zero, ownedSession, false)) throw new Win32Exception();
                loggingOff = true;
            }
        });
        // A successful logoff owns both process teardown and RDP disconnect.
        // Avoid waiting for them a second time on the RDP control's UI thread.
        if (!loggingOff && runningTask != null) Cleanup(delegate { runningTask.Stop(); });
        if (!loggingOff && daemon != null) Cleanup(delegate {
            try { if (!daemon.HasExited) daemon.Kill(); }
            catch (InvalidOperationException) { if (!daemon.HasExited) throw; }
        });
        if (registered) Cleanup(delegate { taskFolder.DeleteTask(taskName, 0); });
        if (!loggingOff && client != null) Cleanup(delegate { if ((int)client.Connected != 0) client.Disconnect(); });
        if (daemon != null) daemon.Dispose();
        base.OnFormClosing(e);
    }

    [STAThread] static int Main(string[] args) {
        try {
            Console.OutputEncoding = new UTF8Encoding(false);
            if (args.Length == 4 && args[0] == "call") return CallDriver(args);
            if (args.Length == 4 && args[0] == "serve") return Serve(args);
            if (args.Length != 3 || !File.Exists(args[0])) throw new ArgumentException("Missing Cua driver or login mode");
            SetProcessDPIAware(); // Read physical monitor pixels before WinForms creates any windows.
            // Serialize Trace2Task launches through the entire logon and teardown.
            bool created;
            using (var gate = new Mutex(true, @"Global\Trace2Task.ChildDesktop", out created)) {
                if (!created) throw new InvalidOperationException("Windows child session is already occupied by Trace2Task");
                using (var host = new ChildDesktopHost(args[0], Guid.ParseExact(args[1], "N"), args[2] == "login")) Application.Run(host);
            }
            return Environment.ExitCode;
        } catch (Exception e) { Console.WriteLine("ERROR " + e.GetBaseException().Message); return 1; }
    }
}

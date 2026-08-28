const elements = {
  version: document.querySelector("#version"),
  taskpack: document.querySelector("#taskpack"),
  taskMeta: document.querySelector("#task-meta"),
  model: document.querySelector("#model"),
  reasoningEffort: document.querySelector("#reasoning-effort"),
  instruction: document.querySelector("#instruction"),
  charCount: document.querySelector("#char-count"),
  warning: document.querySelector("#capability-warning"),
  error: document.querySelector("#form-error"),
  planButton: document.querySelector("#plan-button"),
  executeButton: document.querySelector("#execute-button"),
  empty: document.querySelector("#empty-state"),
  jobView: document.querySelector("#job-view"),
  status: document.querySelector("#status-pill"),
  jobMode: document.querySelector("#job-mode"),
  jobTask: document.querySelector("#job-task"),
  jobModel: document.querySelector("#job-model"),
  jobEffort: document.querySelector("#job-effort"),
  jobInstruction: document.querySelector("#job-instruction"),
  jobLog: document.querySelector("#job-log"),
  resultPanel: document.querySelector("#result-panel"),
  jobResult: document.querySelector("#job-result"),
  liveDot: document.querySelector("#live-dot"),
  stopButton: document.querySelector("#stop-button"),
  viewTabs: [...document.querySelectorAll(".view-tab")],
  viewPanels: [...document.querySelectorAll(".view-panel")],
  recordWindow: document.querySelector("#record-window"),
  recordName: document.querySelector("#record-name"),
  windowMeta: document.querySelector("#window-meta"),
  refreshWindows: document.querySelector("#refresh-windows"),
  recordButton: document.querySelector("#record-button"),
  recordError: document.querySelector("#record-error"),
  refreshLibrary: document.querySelector("#refresh-library"),
  taskpackList: document.querySelector("#taskpack-list"),
  recordingList: document.querySelector("#recording-list"),
  taskpackCount: document.querySelector("#taskpack-count"),
  recordingCount: document.querySelector("#recording-count"),
};

const statusLabels = {
  queued: "排队中",
  running: "运行中",
  stopping: "停止中",
  stopped: "已停止",
  completed: "已完成",
  partial: "录制成功",
  failed: "失败",
};

const modelLabels = {
  "gpt-5.6-sol": "Sol · 最强",
  "gpt-5.6-terra": "Terra · 平衡",
  "gpt-5.6-luna": "Luna · 更快",
};

const effortLabels = {
  low: "Low · 快速",
  medium: "Medium · 平衡",
  high: "High · 深入",
  xhigh: "XHigh · 更深入",
  max: "Max · 最强",
};

let taskpacks = [];
let recordings = [];
let localWindows = [];
let activeJobId = null;
let pollTimer = null;
let refreshedRecordingJobId = null;

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

function selectedTask() {
  return taskpacks.find((task) => task.path === elements.taskpack.value) || null;
}

function selectedWindow() {
  const handle = Number(elements.recordWindow.value);
  return localWindows.find((windowInfo) => windowInfo.handle === handle) || null;
}

function switchView(view) {
  elements.viewTabs.forEach((tabButton) => {
    tabButton.classList.toggle("active", tabButton.dataset.view === view);
  });
  elements.viewPanels.forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== `${view}-panel`);
  });
  if (view === "record" && !localWindows.length) refreshWindows();
  if (view === "library") refreshState();
}

function canExecuteTask(task) {
  if (!task || !task.confirmed) return false;
  const isWechat = /Weixin|WeChat/i.test(task.process_name || "");
  return !isWechat || !task.missing_message_capabilities.includes("type_text");
}

function renderTaskMeta() {
  const task = selectedTask();
  if (!task) {
    elements.taskMeta.textContent = "没有找到可用的 Windows 示范任务。";
    elements.warning.classList.add("hidden");
    return;
  }
  const target = [task.process_name, task.title_contains].filter(Boolean).join(" · ");
  elements.taskMeta.textContent = `${task.confirmed ? "已确认" : "草稿"} · ${target || "未命名窗口"} · 最多 ${task.max_actions} 步`;
  if (task.missing_message_capabilities.length) {
    elements.warning.textContent = `这份旧示范缺少消息输入能力（${task.missing_message_capabilities.join(", ")}）。可以先测试规划，但正式发消息前需要升级任务模板。`;
    elements.warning.classList.remove("hidden");
  } else {
    elements.warning.classList.add("hidden");
  }
  elements.executeButton.disabled = !canExecuteTask(task) || isBusy();
}

function populateTaskpacks(records) {
  const previous = elements.taskpack.value;
  taskpacks = records;
  elements.taskpack.replaceChildren();
  records.forEach((task) => {
    const option = document.createElement("option");
    option.value = task.path;
    option.textContent = `${task.task_id} · ${task.process_name || "Windows"}${task.confirmed ? "" : "（草稿）"}`;
    elements.taskpack.append(option);
  });
  const preferred = records.find((task) => task.path === previous)
    || records.find((task) => task.confirmed && /Weixin|WeChat/i.test(task.process_name || ""))
    || records.find((task) => task.confirmed)
    || records[0];
  if (preferred) elements.taskpack.value = preferred.path;
  renderTaskMeta();
  renderLibrary();
}

function populateAgentOptions(options) {
  if (!options) return;
  const previousModel = elements.model.value || options.defaults?.model;
  const previousEffort = elements.reasoningEffort.value
    || options.defaults?.reasoning_effort;

  elements.model.replaceChildren();
  (options.models || []).forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = modelLabels[model] || model;
    elements.model.append(option);
  });
  elements.reasoningEffort.replaceChildren();
  (options.reasoning_efforts || []).forEach((effort) => {
    const option = document.createElement("option");
    option.value = effort;
    option.textContent = effortLabels[effort] || effort;
    elements.reasoningEffort.append(option);
  });

  elements.model.value = [...elements.model.options].some(
    (option) => option.value === previousModel,
  ) ? previousModel : options.defaults?.model;
  elements.reasoningEffort.value = [...elements.reasoningEffort.options].some(
    (option) => option.value === previousEffort,
  ) ? previousEffort : options.defaults?.reasoning_effort;
}

function makeMiniButton(label, action, accent = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `mini-button${accent ? " accent" : ""}`;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

function renderLibrary() {
  elements.taskpackCount.textContent = `${taskpacks.length} 项`;
  elements.recordingCount.textContent = `${recordings.length} 项`;
  elements.taskpackList.replaceChildren();
  elements.recordingList.replaceChildren();

  if (!taskpacks.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "还没有本地 Windows 经验";
    elements.taskpackList.append(empty);
  }
  taskpacks.forEach((task) => {
    const item = document.createElement("article");
    item.className = "library-item";
    const top = document.createElement("div");
    top.className = "library-item-top";
    const nameBlock = document.createElement("div");
    const title = document.createElement("div");
    title.className = "library-title";
    title.textContent = task.task_id;
    const subtitle = document.createElement("div");
    subtitle.className = "library-subtitle";
    subtitle.textContent = `${task.process_name || "Windows"} · ${task.title_contains || "未命名窗口"}`;
    nameBlock.append(title, subtitle);
    const status = document.createElement("span");
    status.className = `mini-tag ${task.confirmed ? "ok" : "warn"}`;
    status.textContent = task.confirmed ? "已确认" : "草稿";
    top.append(nameBlock, status);

    const tags = document.createElement("div");
    tags.className = "library-tags";
    const isMessagingTask = /Weixin|WeChat/i.test(task.process_name || "");
    if (isMessagingTask && task.missing_message_capabilities.length) {
      const missing = document.createElement("span");
      missing.className = "mini-tag warn";
      missing.textContent = `缺少 ${task.missing_message_capabilities.join(", ")}`;
      tags.append(missing);
    } else if (isMessagingTask) {
      const ready = document.createElement("span");
      ready.className = "mini-tag ok";
      ready.textContent = "消息能力完整";
      tags.append(ready);
    } else {
      const general = document.createElement("span");
      general.className = "mini-tag";
      general.textContent = `${task.actions.length} 个可用动作`;
      tags.append(general);
    }

    const actions = document.createElement("div");
    actions.className = "library-actions";
    if (isMessagingTask && task.missing_message_capabilities.length) {
      actions.append(makeMiniButton("补齐消息能力", () => upgradeTask(task), true));
    }
    if (!task.confirmed) {
      actions.append(makeMiniButton("确认经验", () => confirmTask(task)));
    }
    actions.append(makeMiniButton("在本地查看", () => openLocal(task.local_path)));
    item.append(top, tags, actions);
    elements.taskpackList.append(item);
  });

  if (!recordings.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "还没有原始录制";
    elements.recordingList.append(empty);
  }
  recordings.forEach((recording) => {
    const item = document.createElement("article");
    item.className = "library-item";
    const title = document.createElement("div");
    title.className = "library-title";
    title.textContent = recording.task_id || "未命名录制";
    const subtitle = document.createElement("div");
    subtitle.className = "library-subtitle";
    subtitle.textContent = `${recording.process_name || "Windows"} · ${recording.input_events} 个输入事件`;
    const tags = document.createElement("div");
    tags.className = "library-tags";
    const status = document.createElement("span");
    status.className = `mini-tag ${recording.success ? "ok" : "warn"}`;
    status.textContent = recording.success ? "录制成功" : "未完成";
    tags.append(status);
    const actions = document.createElement("div");
    actions.className = "library-actions";
    if (recording.success) {
      actions.append(makeMiniButton("编译为经验", () => compileRecording(recording), true));
    }
    actions.append(makeMiniButton("在本地查看", () => openLocal(recording.local_path)));
    item.append(title, subtitle, tags, actions);
    elements.recordingList.append(item);
  });
}

function isBusy() {
  return ["queued", "running", "stopping"].includes(elements.status.dataset.status);
}

function setBusy(busy) {
  elements.planButton.disabled = busy || !taskpacks.length;
  const task = selectedTask();
  elements.executeButton.disabled = busy || !canExecuteTask(task);
  elements.taskpack.disabled = busy;
  elements.model.disabled = busy;
  elements.reasoningEffort.disabled = busy;
  elements.instruction.disabled = busy;
  elements.viewTabs.forEach((button) => { button.disabled = busy; });
  elements.recordWindow.disabled = busy;
  elements.recordName.disabled = busy;
  elements.refreshWindows.disabled = busy;
  elements.recordButton.disabled = busy || !selectedWindow();
  elements.refreshLibrary.disabled = busy;
}

function renderJob(job) {
  activeJobId = job.job_id;
  elements.empty.classList.add("hidden");
  elements.jobView.classList.remove("hidden");
  elements.status.dataset.status = job.status;
  elements.status.className = `status-pill ${job.status}`;
  elements.status.textContent = statusLabels[job.status] || job.status;
  elements.jobMode.textContent = job.kind === "recording"
    ? "录制"
    : job.mode === "execute" ? "执行" : "预演";
  elements.jobTask.textContent = job.task_id;
  elements.jobModel.textContent = job.kind === "recording"
    ? "—" : (modelLabels[job.model] || job.model || "—");
  elements.jobEffort.textContent = job.kind === "recording"
    ? "—" : (effortLabels[job.reasoning_effort] || job.reasoning_effort || "—");
  elements.jobInstruction.textContent = job.instruction;
  elements.jobLog.replaceChildren();
  (job.logs || []).forEach((line) => {
    const item = document.createElement("div");
    item.className = "log-line";
    item.textContent = line;
    elements.jobLog.append(item);
  });
  elements.jobLog.scrollTop = elements.jobLog.scrollHeight;
  const busy = ["queued", "running", "stopping"].includes(job.status);
  elements.liveDot.classList.toggle("active", busy);
  elements.stopButton.classList.toggle(
    "hidden",
    !busy || !["execute", "record"].includes(job.mode),
  );
  setBusy(busy);
  if (job.result || job.error) {
    elements.resultPanel.classList.remove("hidden");
    elements.jobResult.textContent = job.error || JSON.stringify(job.result, null, 2);
  } else {
    elements.resultPanel.classList.add("hidden");
  }
  if (!busy && pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

async function pollJob() {
  if (!activeJobId) return;
  try {
    const job = await request(`/api/jobs/${activeJobId}`);
    renderJob(job);
    if (["queued", "running", "stopping"].includes(job.status)) {
      pollTimer = setTimeout(pollJob, 850);
    } else if (job.kind === "recording" && refreshedRecordingJobId !== job.job_id) {
      refreshedRecordingJobId = job.job_id;
      await refreshState();
    }
  } catch (error) {
    showError(error.message);
  }
}

function showError(message) {
  elements.error.textContent = message;
  elements.error.classList.remove("hidden");
}

function clearError() {
  elements.error.classList.add("hidden");
  elements.error.textContent = "";
}

function showRecordError(message) {
  elements.recordError.textContent = message;
  elements.recordError.classList.remove("hidden");
}

function clearRecordError() {
  elements.recordError.classList.add("hidden");
  elements.recordError.textContent = "";
}

async function refreshState() {
  const state = await request("/api/state");
  elements.version.textContent = `v${state.version}`;
  recordings = state.recordings || [];
  populateAgentOptions(state.agent_options);
  populateTaskpacks(state.taskpacks || []);
  return state;
}

async function refreshWindows() {
  clearRecordError();
  elements.refreshWindows.disabled = true;
  try {
    const payload = await request("/api/windows");
    localWindows = payload.windows || [];
    elements.recordWindow.replaceChildren();
    localWindows.forEach((windowInfo) => {
      const option = document.createElement("option");
      option.value = String(windowInfo.handle);
      option.textContent = `${windowInfo.process_name} · ${windowInfo.title}`;
      elements.recordWindow.append(option);
    });
    renderWindowMeta();
  } catch (error) {
    showRecordError(error.message);
  } finally {
    elements.refreshWindows.disabled = false;
  }
}

function renderWindowMeta() {
  const windowInfo = selectedWindow();
  if (!windowInfo) {
    elements.windowMeta.textContent = "没有找到可录制的可见窗口。";
    elements.recordButton.disabled = true;
    return;
  }
  elements.windowMeta.textContent = `${windowInfo.client_width} × ${windowInfo.client_height} · ${windowInfo.is_foreground ? "当前前台" : "录制时自动切换到前台"}`;
  elements.recordButton.disabled = isBusy();
}

async function startRecording() {
  clearRecordError();
  const windowInfo = selectedWindow();
  const taskId = elements.recordName.value.trim();
  if (!windowInfo) return showRecordError("请选择一个本地目标窗口");
  if (!taskId) return showRecordError("请输入经验名称");
  setBusy(true);
  try {
    const job = await request("/api/recordings", {
      method: "POST",
      body: JSON.stringify({ handle: windowInfo.handle, task_id: taskId }),
    });
    renderJob(job);
    pollTimer = setTimeout(pollJob, 250);
  } catch (error) {
    setBusy(false);
    showRecordError(error.message);
  }
}

async function upgradeTask(task) {
  const confirmed = window.confirm(
    `将为“${task.task_id}”加入 type_text、press_key 和 hotkey，并把任务重新标记为待确认草稿。继续吗？`,
  );
  if (!confirmed) return;
  try {
    await request("/api/taskpacks/upgrade", {
      method: "POST",
      body: JSON.stringify({ task_path: task.path }),
    });
    await refreshState();
  } catch (error) {
    showError(error.message);
  }
}

async function confirmTask(task) {
  const confirmed = window.confirm(
    `确认你已经审查“${task.task_id}”的示范、目标窗口、允许动作和成功参考图吗？`,
  );
  if (!confirmed) return;
  try {
    await request("/api/taskpacks/confirm", {
      method: "POST",
      body: JSON.stringify({ task_path: task.path }),
    });
    await refreshState();
  } catch (error) {
    showError(error.message);
  }
}

async function openLocal(path) {
  try {
    await request("/api/open-local", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
  } catch (error) {
    showError(error.message);
  }
}

async function compileRecording(recording) {
  try {
    await request("/api/recordings/compile", {
      method: "POST",
      body: JSON.stringify({ trace_path: recording.trace_path }),
    });
    await refreshState();
  } catch (error) {
    showError(error.message);
  }
}

async function startJob(mode) {
  clearError();
  const task = selectedTask();
  const instruction = elements.instruction.value.trim();
  const model = elements.model.value;
  const reasoningEffort = elements.reasoningEffort.value;
  if (!task) return showError("请选择一个示范经验");
  if (!instruction) return showError("请输入一条任务指令");
  if (mode === "execute") {
    const confirmed = window.confirm(
      `即将用 ${modelLabels[model] || model} / ${effortLabels[reasoningEffort] || reasoningEffort} 控制 ${task.process_name || "目标窗口"} 并执行：\n\n${instruction}\n\n运行期间可按 F9 紧急停止。确认继续吗？`,
    );
    if (!confirmed) return;
  }
  setBusy(true);
  try {
    const job = await request("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        task_path: task.path,
        instruction,
        mode,
        model,
        reasoning_effort: reasoningEffort,
      }),
    });
    renderJob(job);
    pollTimer = setTimeout(pollJob, 250);
  } catch (error) {
    setBusy(false);
    showError(error.message);
  }
}

async function stopJob() {
  if (!activeJobId) return;
  elements.stopButton.disabled = true;
  try {
    const job = await request(`/api/jobs/${activeJobId}/stop`, {
      method: "POST",
      body: "{}",
    });
    renderJob(job);
  } catch (error) {
    showError(error.message);
  } finally {
    elements.stopButton.disabled = false;
  }
}

async function initialize() {
  try {
    const state = await refreshState();
    elements.status.dataset.status = "idle";
    if (state.active_job) {
      renderJob(state.active_job);
      if (["queued", "running", "stopping"].includes(state.active_job.status)) {
        pollTimer = setTimeout(pollJob, 500);
      }
    } else {
      setBusy(false);
    }
  } catch (error) {
    showError(`控制台初始化失败：${error.message}`);
  }
}

elements.taskpack.addEventListener("change", renderTaskMeta);
elements.instruction.addEventListener("input", () => {
  elements.charCount.textContent = `${elements.instruction.value.length} / 2000`;
});
elements.planButton.addEventListener("click", () => startJob("plan"));
elements.executeButton.addEventListener("click", () => startJob("execute"));
elements.stopButton.addEventListener("click", stopJob);
elements.viewTabs.forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});
elements.recordWindow.addEventListener("change", renderWindowMeta);
elements.refreshWindows.addEventListener("click", refreshWindows);
elements.recordButton.addEventListener("click", startRecording);
elements.refreshLibrary.addEventListener("click", async () => {
  elements.refreshLibrary.disabled = true;
  try {
    await refreshState();
  } catch (error) {
    showError(error.message);
  } finally {
    elements.refreshLibrary.disabled = isBusy();
  }
});

initialize();

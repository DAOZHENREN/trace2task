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
  recordModel: document.querySelector("#record-model"),
  recordReasoningEffort: document.querySelector("#record-reasoning-effort"),
  compilerModel: document.querySelector("#compiler-model"),
  compilerReasoningEffort: document.querySelector("#compiler-reasoning-effort"),
  windowMeta: document.querySelector("#window-meta"),
  refreshWindows: document.querySelector("#refresh-windows"),
  recordButton: document.querySelector("#record-button"),
  recordError: document.querySelector("#record-error"),
  refreshLibrary: document.querySelector("#refresh-library"),
  taskpackList: document.querySelector("#taskpack-list"),
  candidateList: document.querySelector("#candidate-list"),
  recordingList: document.querySelector("#recording-list"),
  taskpackCount: document.querySelector("#taskpack-count"),
  candidateCount: document.querySelector("#candidate-count"),
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
let candidates = [];
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

function canAutoExecute() {
  return taskpacks.some((task) => canExecuteTask(task));
}

function renderTaskMeta() {
  const task = selectedTask();
  if (!task) {
    elements.taskMeta.textContent = taskpacks.length
      ? `将从 ${taskpacks.filter((item) => item.confirmed).length} 个已确认 Trace 中自动选择；低置信度会拒绝执行。`
      : "没有找到可用的 Windows 示范任务。";
    elements.warning.classList.add("hidden");
    elements.executeButton.disabled = !canAutoExecute() || isBusy();
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
  const automatic = document.createElement("option");
  automatic.value = "";
  automatic.textContent = "自动选择经验（推荐）";
  elements.taskpack.append(automatic);
  records.forEach((task) => {
    const option = document.createElement("option");
    option.value = task.path;
    option.textContent = `${task.task_id} · ${task.process_name || "Windows"}${task.confirmed ? "" : "（草稿）"}`;
    elements.taskpack.append(option);
  });
  const previousTask = records.find((task) => task.path === previous);
  elements.taskpack.value = previousTask ? previousTask.path : "";
  renderTaskMeta();
  renderLibrary();
}

function populateAgentOptions(options) {
  if (!options) return;
  const previousRuntimeModel = elements.model.value || options.defaults?.model;
  const previousRuntimeEffort = elements.reasoningEffort.value
    || options.defaults?.reasoning_effort;
  const compilerDefaults = options.compiler_defaults || options.defaults || {};
  const previousCompilerModel = elements.compilerModel.value
    || elements.recordModel.value
    || compilerDefaults.model;
  const previousCompilerEffort = elements.compilerReasoningEffort.value
    || elements.recordReasoningEffort.value
    || compilerDefaults.reasoning_effort;

  elements.model.replaceChildren();
  elements.recordModel.replaceChildren();
  elements.compilerModel.replaceChildren();
  (options.models || []).forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = modelLabels[model] || model;
    elements.model.append(option);
    elements.recordModel.append(option.cloneNode(true));
    elements.compilerModel.append(option.cloneNode(true));
  });
  elements.reasoningEffort.replaceChildren();
  elements.recordReasoningEffort.replaceChildren();
  elements.compilerReasoningEffort.replaceChildren();
  (options.reasoning_efforts || []).forEach((effort) => {
    const option = document.createElement("option");
    option.value = effort;
    option.textContent = effortLabels[effort] || effort;
    elements.reasoningEffort.append(option);
    elements.recordReasoningEffort.append(option.cloneNode(true));
    elements.compilerReasoningEffort.append(option.cloneNode(true));
  });

  elements.model.value = [...elements.model.options].some(
    (option) => option.value === previousRuntimeModel,
  ) ? previousRuntimeModel : options.defaults?.model;
  elements.reasoningEffort.value = [...elements.reasoningEffort.options].some(
    (option) => option.value === previousRuntimeEffort,
  ) ? previousRuntimeEffort : options.defaults?.reasoning_effort;
  const compilerModel = [...elements.compilerModel.options].some(
    (option) => option.value === previousCompilerModel,
  ) ? previousCompilerModel : compilerDefaults.model;
  const compilerEffort = [...elements.compilerReasoningEffort.options].some(
    (option) => option.value === previousCompilerEffort,
  ) ? previousCompilerEffort : compilerDefaults.reasoning_effort;
  syncCompilerSettings(compilerModel, compilerEffort);
}

function syncCompilerSettings(model, reasoningEffort) {
  elements.compilerModel.value = model;
  elements.recordModel.value = model;
  elements.compilerReasoningEffort.value = reasoningEffort;
  elements.recordReasoningEffort.value = reasoningEffort;
}

function makeMiniButton(label, action, accent = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `mini-button${accent ? " accent" : ""}`;
  button.textContent = label;
  button.addEventListener("click", () => action(button));
  return button;
}

function renderLibrary() {
  elements.taskpackCount.textContent = `${taskpacks.length} 项`;
  elements.candidateCount.textContent = `${candidates.length} 项`;
  elements.recordingCount.textContent = `${recordings.length} 项`;
  elements.taskpackList.replaceChildren();
  elements.candidateList.replaceChildren();
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
    if (task.semantic_experience) {
      const semantic = document.createElement("span");
      semantic.className = `mini-tag ${task.semantic_experience.status === "confirmed" ? "ok" : "warn"}`;
      semantic.textContent = `${task.semantic_experience.stage_count} 个语义阶段`;
      tags.append(semantic);
      const compiler = document.createElement("span");
      compiler.className = "mini-tag";
      const compilerModel = modelLabels[task.semantic_experience.model]
        || task.semantic_experience.model;
      const compilerEffort = effortLabels[task.semantic_experience.reasoning_effort]
        || task.semantic_experience.reasoning_effort;
      compiler.textContent = `由 ${compilerModel} / ${compilerEffort} 编译`;
      tags.append(compiler);
    } else {
      const missingSemantic = document.createElement("span");
      missingSemantic.className = "mini-tag warn";
      missingSemantic.textContent = "尚无语义编译";
      tags.append(missingSemantic);
    }

    let storyboard = null;
    if (task.semantic_experience) {
      storyboard = document.createElement("details");
      storyboard.className = "semantic-storyboard";
      const summary = document.createElement("summary");
      summary.textContent = `查看 Compiler Agent 阶段 · ${task.semantic_experience.summary}`;
      const stages = document.createElement("div");
      stages.className = "semantic-stages";
      task.semantic_experience.stages.forEach((stage, index) => {
        const stageItem = document.createElement("div");
        stageItem.className = "semantic-stage";
        const stageTitle = document.createElement("strong");
        stageTitle.textContent = `${index + 1}. ${stage.name} · ${Math.round(stage.confidence * 100)}%`;
        const transition = document.createElement("p");
        transition.textContent = `${stage.state_before} → ${stage.intent} → ${stage.state_after}`;
        stageItem.append(stageTitle, transition);
        if (stage.dynamic_decisions.length) {
          const decisions = document.createElement("p");
          decisions.className = "semantic-uncertain";
          decisions.textContent = `动态决定：${stage.dynamic_decisions.map((item) => item.description).join("；")}`;
          stageItem.append(decisions);
        }
        stages.append(stageItem);
      });
      storyboard.append(summary, stages);
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
    const deleteTaskButton = makeMiniButton("删除", () => deleteTaskpack(task));
    deleteTaskButton.classList.add("danger");
    actions.append(deleteTaskButton);
    item.append(top, tags);
    if (storyboard) item.append(storyboard);
    item.append(actions);
    elements.taskpackList.append(item);
  });

  if (!candidates.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "成功执行后会在这里生成待审核候选经验";
    elements.candidateList.append(empty);
  }
  candidates.forEach((candidate) => {
    const item = document.createElement("article");
    item.className = "library-item";
    const title = document.createElement("div");
    title.className = "library-title";
    title.textContent = candidate.task_id || "未命名候选";
    const subtitle = document.createElement("div");
    subtitle.className = "library-subtitle";
    subtitle.textContent = candidate.instruction || "未记录本次指令";
    const tags = document.createElement("div");
    tags.className = "library-tags";
    const status = document.createElement("span");
    status.className = "mini-tag warn";
    status.textContent = "待审核";
    const metrics = document.createElement("span");
    metrics.className = "mini-tag";
    metrics.textContent = `${candidate.metrics?.executed_actions || 0} 步 · ${candidate.metrics?.replans || 0} 次重规划`;
    tags.append(status, metrics);
    const actions = document.createElement("div");
    actions.className = "library-actions";
    actions.append(makeMiniButton("在本地查看", () => openLocal(candidate.local_path)));
    const deleteCandidateButton = makeMiniButton("删除", () => deleteCandidate(candidate));
    deleteCandidateButton.classList.add("danger");
    actions.append(deleteCandidateButton);
    item.append(title, subtitle, tags, actions);
    elements.candidateList.append(item);
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
      actions.append(
        makeMiniButton(
          "编译为经验",
          (button) => compileRecording(recording, button),
          true,
        ),
      );
    }
    actions.append(makeMiniButton("在本地查看", () => openLocal(recording.local_path)));
    const deleteRecordingButton = makeMiniButton("删除", () => deleteRecording(recording));
    deleteRecordingButton.classList.add("danger");
    actions.append(deleteRecordingButton);
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
  elements.executeButton.disabled = busy || (task ? !canExecuteTask(task) : !canAutoExecute());
  elements.taskpack.disabled = busy;
  elements.model.disabled = busy;
  elements.reasoningEffort.disabled = busy;
  elements.instruction.disabled = busy;
  elements.viewTabs.forEach((button) => { button.disabled = busy; });
  elements.recordWindow.disabled = busy;
  elements.recordName.disabled = busy;
  elements.recordModel.disabled = busy;
  elements.recordReasoningEffort.disabled = busy;
  elements.compilerModel.disabled = busy;
  elements.compilerReasoningEffort.disabled = busy;
  elements.refreshWindows.disabled = busy;
  elements.recordButton.disabled = busy || !selectedWindow();
  elements.refreshLibrary.disabled = busy;
  document.querySelectorAll(".mini-button").forEach((button) => {
    button.disabled = busy;
  });
}

function renderJob(job) {
  activeJobId = job.job_id;
  elements.empty.classList.add("hidden");
  elements.jobView.classList.remove("hidden");
  elements.status.dataset.status = job.status;
  elements.status.className = `status-pill ${job.status}`;
  elements.status.textContent = job.kind === "compilation" && job.status === "partial"
    ? "部分完成"
    : statusLabels[job.status] || job.status;
  elements.jobMode.textContent = job.kind === "recording"
    ? "录制"
    : job.kind === "compilation" ? "编译" : job.mode === "execute" ? "执行" : "预演";
  elements.jobTask.textContent = job.task_id;
  elements.jobModel.textContent = modelLabels[job.model] || job.model || "—";
  elements.jobEffort.textContent = effortLabels[job.reasoning_effort] || job.reasoning_effort || "—";
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
    } else if ((["recording", "compilation"].includes(job.kind)
      || job.result?.candidate_experience)
      && refreshedRecordingJobId !== job.job_id) {
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
  candidates = state.candidates || [];
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
      body: JSON.stringify({
        handle: windowInfo.handle,
        task_id: taskId,
        model: elements.recordModel.value,
        reasoning_effort: elements.recordReasoningEffort.value,
      }),
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
  const confirmationText = task.semantic_experience
    ? `确认你已经审查“${task.task_id}”的示范、目标窗口、允许动作、成功参考图，以及 Compiler Agent 生成的 ${task.semantic_experience.stage_count} 个阶段、状态与不确定项吗？`
    : `确认你已经审查“${task.task_id}”的示范、目标窗口、允许动作和成功参考图吗？\n\n注意：这份旧经验尚无 V0.7 语义层。`;
  const confirmed = window.confirm(
    confirmationText,
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

async function compileRecording(recording, button) {
  if (isBusy()) return;
  const originalLabel = button?.textContent || "编译为经验";
  if (button) {
    button.disabled = true;
    button.textContent = "正在提交…";
  }
  setBusy(true);
  try {
    const job = await request("/api/recordings/compile", {
      method: "POST",
      body: JSON.stringify({
        trace_path: recording.trace_path,
        model: elements.compilerModel.value,
        reasoning_effort: elements.compilerReasoningEffort.value,
      }),
    });
    renderJob(job);
    pollTimer = setTimeout(pollJob, 250);
  } catch (error) {
    setBusy(false);
    if (button) {
      button.disabled = false;
      button.textContent = originalLabel;
    }
    showError(error.message);
  }
}

async function deleteTaskpack(task) {
  const confirmed = window.confirm(
    `删除任务经验“${task.task_id}”吗？\n\n它会移动到 taskpacks/.trash，可以手动恢复；原始录制不会被删除。`,
  );
  if (!confirmed) return;
  await deleteLocalAsset("/api/taskpacks/delete", { task_path: task.path });
}

async function deleteRecording(recording) {
  const confirmed = window.confirm(
    `删除原始录制“${recording.task_id}”吗？\n\n它会移动到 runs/.trash，可以手动恢复；已有任务经验不会被删除。`,
  );
  if (!confirmed) return;
  await deleteLocalAsset("/api/recordings/delete", { trace_path: recording.trace_path });
}

async function deleteCandidate(candidate) {
  const confirmed = window.confirm(
    `删除候选经验“${candidate.task_id || "未命名候选"}”吗？\n\n它会移动到 runs/.trash，可以手动恢复。`,
  );
  if (!confirmed) return;
  await deleteLocalAsset("/api/candidates/delete", { path: candidate.local_path });
}

async function deleteLocalAsset(endpoint, payload) {
  if (isBusy()) return;
  setBusy(true);
  try {
    await request(endpoint, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await refreshState();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function startJob(mode) {
  clearError();
  const task = selectedTask();
  const instruction = elements.instruction.value.trim();
  const model = elements.model.value;
  const reasoningEffort = elements.reasoningEffort.value;
  if (!instruction) return showError("请输入一条任务指令");
  if (mode === "execute") {
    let executionTask = task;
    let routeSummary = "手动选择经验";
    if (!executionTask) {
      try {
        const route = await request("/api/experience-route", {
          method: "POST",
          body: JSON.stringify({ instruction }),
        });
        executionTask = route.task;
        routeSummary = `自动选择“${route.task_id}”（置信度 ${Math.round(route.confidence * 100)}%）`;
      } catch (error) {
        return showError(error.message);
      }
    }
    const confirmed = window.confirm(
      `${routeSummary}\n即将用 ${modelLabels[model] || model} / ${effortLabels[reasoningEffort] || reasoningEffort} 控制 ${executionTask.process_name || "目标窗口"} 并执行：\n\n${instruction}\n\n运行期间可按 F9 紧急停止。确认继续吗？`,
    );
    if (!confirmed) return;
  }
  setBusy(true);
  try {
    const job = await request("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        task_path: task?.path || "",
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
elements.recordModel.addEventListener("change", () => {
  syncCompilerSettings(elements.recordModel.value, elements.recordReasoningEffort.value);
});
elements.recordReasoningEffort.addEventListener("change", () => {
  syncCompilerSettings(elements.recordModel.value, elements.recordReasoningEffort.value);
});
elements.compilerModel.addEventListener("change", () => {
  syncCompilerSettings(elements.compilerModel.value, elements.compilerReasoningEffort.value);
});
elements.compilerReasoningEffort.addEventListener("change", () => {
  syncCompilerSettings(elements.compilerModel.value, elements.compilerReasoningEffort.value);
});
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

const elements = {
  executionScope: document.querySelector("#execution-scope"),
  operationScope: document.querySelector("#operation-scope"),
  desktopWorkflowSettings: document.querySelector("#desktop-workflow-settings"),
  desktopOrchestration: document.querySelector("#desktop-orchestration"),
  desktopResume: document.querySelector("#desktop-resume"),
  experienceHelp: document.querySelector("#experience-help"),
  version: document.querySelector("#version"),
  taskpack: document.querySelector("#taskpack"),
  taskMeta: document.querySelector("#task-meta"),
  model: document.querySelector("#model"),
  modelProvider: document.querySelector("#model-provider"),
  localModelSettings: document.querySelector("#local-model-settings"),
  localModel: document.querySelector("#local-model"),
  localPromptSettings: document.querySelector("#local-prompt-settings"),
  localSystemPrompt: document.querySelector("#local-system-prompt"),
  localTurnTemplate: document.querySelector("#local-turn-template"),
  localPromptsSave: document.querySelector("#local-prompts-save"),
  localPromptsReset: document.querySelector("#local-prompts-reset"),
  localPromptsStatus: document.querySelector("#local-prompts-status"),
  chatPromptSettings: document.querySelector("#chat-prompt-settings"),
  chatPromptHelp: document.querySelector("#chat-prompt-help"),
  chatPromptGuidance: document.querySelector("#chat-prompt-guidance"),
  chatPromptsSave: document.querySelector("#chat-prompts-save"),
  chatPromptsReset: document.querySelector("#chat-prompts-reset"),
  chatPromptsStatus: document.querySelector("#chat-prompts-status"),
  codexModelSettings: document.querySelector("#codex-model-settings"),
  apiModelSettings: document.querySelector("#api-model-settings"),
  apiBaseUrl: document.querySelector("#api-base-url"),
  apiModel: document.querySelector("#api-model"),
  apiKey: document.querySelector("#api-key"),
  apiReasoningEffort: document.querySelector("#api-reasoning-effort"),
  apiResponseFormat: document.querySelector("#api-response-format"),
  apiTimeout: document.querySelector("#api-timeout"),
  apiSaveSettings: document.querySelector("#api-save-settings"),
  apiClearSettings: document.querySelector("#api-clear-settings"),
  apiSettingsStatus: document.querySelector("#api-settings-status"),
  reasoningEffort: document.querySelector("#reasoning-effort"),
  inputMode: document.querySelector("#input-mode"),
  inputModeHelp: document.querySelector("#input-mode-help"),
  adaptiveReasoning: {checked: false, disabled: true}, // Automatic escalation is not exposed.
  instruction: document.querySelector("#instruction"),
  useExperience: {checked: true, disabled: false}, // Derived from the task selector, not a separate control.
  charCount: document.querySelector("#char-count"),
  warning: document.querySelector("#capability-warning"),
  error: document.querySelector("#form-error"),
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
  modelChat: document.querySelector("#model-chat"),
  resultPanel: document.querySelector("#result-panel"),
  jobPerformance: document.querySelector("#job-performance"),
  jobResult: document.querySelector("#job-result"),
  liveDot: document.querySelector("#live-dot"),
  stopButton: document.querySelector("#stop-button"),
  viewTabs: [...document.querySelectorAll(".view-tab")],
  viewPanels: [...document.querySelectorAll(".view-panel")],
  recordSource: document.querySelector("#record-source"),
  waaRecordingFields: document.querySelector("#waa-recording-fields"),
  waaRoot: document.querySelector("#waa-root"),
  waaExample: document.querySelector("#waa-example"),
  waaTaskMeta: document.querySelector("#waa-task-meta"),
  recordName: document.querySelector("#record-name"),
  recordModel: document.querySelector("#record-model"),
  recordReasoningEffort: document.querySelector("#record-reasoning-effort"),
  recordNarration: document.querySelector("#record-narration"),
  recordDeferCompilation: document.querySelector("#record-defer-compilation"),
  narrationReview: document.querySelector("#narration-review"),
  narrationTranscript: document.querySelector("#narration-transcript"),
  narrationStatus: document.querySelector("#narration-status"),
  narrationSubmit: document.querySelector("#narration-submit"),
  narrationDiscard: document.querySelector("#narration-discard"),
  compilerModel: document.querySelector("#compiler-model"),
  compilerReasoningEffort: document.querySelector("#compiler-reasoning-effort"),
  experienceModelSummary: document.querySelector("#experience-model-summary"),
  recordButton: document.querySelector("#record-button"),
  recordStopButton: document.querySelector("#record-stop-button"),
  recordError: document.querySelector("#record-error"),
  refreshLibrary: document.querySelector("#refresh-library"),
  taskpackList: document.querySelector("#taskpack-list"),
  candidateList: document.querySelector("#candidate-list"),
  recordingList: document.querySelector("#recording-list"),
  taskpackCount: document.querySelector("#taskpack-count"),
  candidateCount: document.querySelector("#candidate-count"),
  recordingCount: document.querySelector("#recording-count"),
  taskDetailPanel: document.querySelector("#task-detail-panel"),
  taskDetailBack: document.querySelector("#task-detail-back"),
  taskDetailKicker: document.querySelector("#task-detail-kicker"),
  taskDetailTitle: document.querySelector("#task-detail-title"),
  taskDetailMeta: document.querySelector("#task-detail-meta"),
  taskDetailTags: document.querySelector("#task-detail-tags"),
  taskDetailBody: document.querySelector("#task-detail-body"),
  taskDetailActions: document.querySelector("#task-detail-actions"),
  rsiStatus: document.querySelector("#rsi-status"),
  rsiMessage: document.querySelector("#rsi-message"),
  rsiChecks: document.querySelector("#rsi-checks"),
  rsiInstruction: document.querySelector("#rsi-instruction"),
  rsiModel: document.querySelector("#rsi-model"),
  rsiReasoningEffort: document.querySelector("#rsi-reasoning-effort"),
  rsiMaxCalls: document.querySelector("#rsi-max-calls"),
  rsiWallSeconds: document.querySelector("#rsi-wall-seconds"),
  rsiProjectBudget: document.querySelector("#rsi-project-budget"),
  rsiStart: document.querySelector("#rsi-start"),
  rsiRefresh: document.querySelector("#rsi-refresh"),
  rsiStop: document.querySelector("#rsi-stop"),
  rsiRunCount: document.querySelector("#rsi-run-count"),
  rsiRunList: document.querySelector("#rsi-run-list"),
  rsiDetail: document.querySelector("#rsi-detail"),
};

const statusLabels = {
  queued: "排队中",
  running: "运行中",
  stopping: "停止中",
  awaiting_recording_start: "等待同步开始",
  awaiting_narration: "等待讲解确认",
  stopped: "已停止",
  completed: "已完成",
  partial: "录制成功",
  failed: "失败",
  preflight: "预检中",
  needs_recovery: "需要恢复",
  finalizing: "正在清理资源",
  cancelled: "已取消",
};

const modelLabels = {
  "gpt-6-sol": "GPT-6 Sol",
  "gpt-6-luna": "GPT-6 Luna",
  "gpt-5.6-sol": "Sol · 最强",
  "gpt-5.6-terra": "Terra · 平衡",
  "gpt-5.6-luna": "Luna · 更快",
};

const effortLabels = {
  default: "服务商默认",
  none: "None · 不推理",
  minimal: "Minimal · 最少",
  low: "Low · 快速",
  medium: "Medium · 平衡",
  high: "High · 深入",
  xhigh: "XHigh · 更深入",
  max: "Max · 最强",
};

const guidanceScopeLabels = {
  global: "全局",
  state: "状态",
  transition: "转移",
  terminal: "终止",
};

function guidanceScopeLabel(scope) {
  const type = scope?.type || "state";
  const id = scope?.id || "未知";
  return `${guidanceScopeLabels[type] || type} · ${id}`;
}

function guidanceRuntimeBinding(scope, semanticExperience) {
  const type = scope?.type || "state";
  const id = scope?.id || "未知";
  const states = semanticExperience?.states || [];
  const terminals = semanticExperience?.terminals || [];
  if (type === "global") {
    return {
      label: "整个任务（全局）",
      description: "不绑定单个状态；适用于这份任务的所有阶段。",
      trigger: "第一次规划以及每次后续规划都会输入 Agent。",
    };
  }
  if (type === "state") {
    const state = states.find((item) => item.id === id);
    return {
      label: state ? `状态“${state.name}” (${id})` : `状态 ${id}`,
      description: state?.description || "该规则绑定到指定任务状态。",
      trigger: `第一次规划随完整经验输入；之后仅当 Agent 当前识别为 ${id} 时输入。`,
    };
  }
  if (type === "transition") {
    const source = states.find((state) =>
      (state.outgoing || []).some((edge) => edge.id === id)
    );
    const edge = source?.outgoing?.find((item) => item.id === id);
    const target = edge
      ? states.find((state) => state.id === edge.target_id)
        || terminals.find((terminal) => terminal.id === edge.target_id)
      : null;
    return {
      label: edge
        ? `转移“${edge.action_goal}” (${id})`
        : `转移 ${id}`,
      description: edge
        ? `${source.name} → ${target?.name || edge.target_id}；条件：${edge.condition}`
        : "该规则绑定到指定状态转移。",
      trigger: "第一次规划随完整经验输入；之后仅当该转移是当前状态的可走出边时输入。",
    };
  }
  const terminal = terminals.find((item) => item.id === id);
  return {
    label: terminal ? `终态“${terminal.name}” (${id})` : `终态 ${id}`,
    description: terminal?.condition || "该规则绑定到指定成功或失败终态。",
    trigger: "第一次规划随完整经验输入；之后仅当该终态是当前状态的候选结果时输入。",
  };
}

function makeGuidanceField(label, value, className = "") {
  const row = document.createElement("p");
  row.className = `guidance-rule-field ${className}`.trim();
  const heading = document.createElement("strong");
  heading.textContent = `${label}：`;
  row.append(heading, document.createTextNode(value || "未填写"));
  return row;
}

let taskpacks = [];
let candidates = [];
let recordings = [];
let activeJobId = null;
let pollTimer = null;
let modelChatSignature = "";
let refreshedRecordingJobId = null;
let backendSupportsIncrementalGuidance = false;
let narrationCapture = null;
let narrationReviewJobId = null;
let narrationReviewPreparing = false;
let waaGoStartingJobId = null;
let waaGoSentJobId = null;
let waaTasks = [];
let waaTaskCatalogRoot = null;
let defaultWaaExamplePath = "";
let dictationSession = null;
let rsiHealth = null;
let rsiRuns = [];
const RSI_POLL_INTERVAL_MS = 3_000;
let selectedRsiRunId = null;
let rsiEventCursor = 0;
let rsiEvents = [];
let rsiPollTimer = null;
let rsiRecovery = null;
let rsiRecoveryRunId = null;
// Candidate prose and a user's unfinished review note are local, per-page
// state.  They are keyed by the immutable candidate digest so a later run or
// candidate cannot inherit an earlier run's text.
const rsiCandidateViews = new Map();
const taskDetailFragments = new Map();

// Task-end audio: only observed active -> terminal transitions, never old results.
let taskAudio = null;
const soundJobs = new Map();
const soundedJobs = new Set();
const taskSound = document.querySelector("#task-sound");
const taskSoundStatus = document.querySelector("#task-sound-status");
try { taskSound.checked = localStorage.getItem("trace2task.taskSound") !== "off"; } catch (_) { /* optional */ }

async function unlockTaskAudio() {
  if (!taskSound.checked) return;
  try {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) throw Error("此浏览器不支持提示音");
    taskAudio ||= new Audio();
    if (taskAudio.state === "suspended") await taskAudio.resume();
  } catch (error) {
    taskSoundStatus.textContent = `提示音不可用：${error.message}`;
  }
}

function playTaskSound(status) {
  if (!taskSound.checked) return;
  if (!taskAudio || taskAudio.state !== "running") {
    taskSoundStatus.textContent = "任务已结束，但声音未启用；请点击“试听”并检查浏览器静音设置。";
    return;
  }
  try {
    const notes = status === "completed" ? [660, 880] : [440, 330, 330];
    notes.forEach((frequency, index) => {
      const start = taskAudio.currentTime + index * .22;
      const oscillator = taskAudio.createOscillator();
      const gain = taskAudio.createGain();
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(.12, start + .015);
      gain.gain.linearRampToValueAtTime(0, start + .17);
      oscillator.connect(gain);
      gain.connect(taskAudio.destination);
      oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
      oscillator.start(start);
      oscillator.stop(start + .18);
    });
    taskSoundStatus.textContent = status === "completed" ? "任务已结束，已播放提示音。" : "任务失败或停止，已播放提示音。";
  } catch (_) {
    taskSoundStatus.textContent = "任务已结束，但提示音播放失败。";
  }
}

function notifyTaskEnd(job) {
  const previous = soundJobs.get(job.job_id);
  soundJobs.set(job.job_id, job.status);
  const active = ["queued", "running", "stopping", "awaiting_recording_start", "awaiting_narration"];
  if (active.includes(previous) && ["completed", "failed", "stopped", "partial"].includes(job.status)
      && !soundedJobs.has(job.job_id)) {
    soundedJobs.add(job.job_id);
    playTaskSound(job.status);
  }
}

document.addEventListener("pointerdown", unlockTaskAudio, {passive: true});
document.addEventListener("keydown", unlockTaskAudio, {passive: true});
taskSound.addEventListener("change", () => {
  try { localStorage.setItem("trace2task.taskSound", taskSound.checked ? "on" : "off"); } catch (_) { /* optional */ }
  if (taskSound.checked) unlockTaskAudio();
  taskSoundStatus.textContent = taskSound.checked ? "提示音已开启，可点击试听。" : "提示音已关闭。";
});
document.querySelector("#task-sound-test").addEventListener("click", async () => {
  await unlockTaskAudio();
  playTaskSound("completed");
});
// End task-end audio.

async function request(path, options = {}) {
  const csrf = document.querySelector('meta[name="trace2task-csrf"]')?.content;
  if (!csrf) throw new Error("页面安全令牌缺失；请刷新 Trace2Task 控制台后重试");
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      "X-Trace2Task-CSRF": csrf,
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

function selectedTask() {
  const path = elements.taskpack.value.replace(/^baseline:/, "");
  return taskpacks.find((task) => task.path === path) || null;
}

function usesWaaRecording() {
  return elements.recordSource.value === "waa";
}

function selectedWaaTask() {
  return waaTasks.find((task) => task.example_path === elements.waaExample.value) || null;
}

function renderWaaTaskMeta() {
  const task = selectedWaaTask();
  if (!task) {
    elements.waaTaskMeta.textContent = waaTasks.length
      ? "请选择一个已配置 Reset 的 WAA 标准任务。"
      : "当前 WAA 目录没有可安全录制的任务。";
    return;
  }
  const apps = task.related_apps?.length ? task.related_apps.join("、") : task.domain;
  const evaluator = task.evaluator?.length ? task.evaluator.join(" + ") : "WAA evaluator";
  const variant = task.variant_id
    ? ` · 实验组：${task.experience_family_id} · ${task.variant_id}（仅演示）`
    : "";
  elements.waaTaskMeta.textContent = `${task.instruction}${variant} · 应用：${apps} · Evaluator：${evaluator} · Reset：${task.reset_paths.length} 条规则已配置`;
}

async function refreshWaaTasks({ force = false } = {}) {
  const root = elements.waaRoot.value.trim();
  if (!root) {
    waaTasks = [];
    elements.waaExample.replaceChildren();
    renderWaaTaskMeta();
    return;
  }
  if (!force && waaTaskCatalogRoot === root && waaTasks.length) return;
  elements.waaExample.disabled = true;
  elements.waaTaskMeta.textContent = "正在读取 WAA 任务与 Reset 规则…";
  try {
    const payload = await request(`/api/waa/tasks?root=${encodeURIComponent(root)}`);
    const previous = elements.waaExample.value || defaultWaaExamplePath;
    waaTasks = payload.tasks || [];
    waaTaskCatalogRoot = root;
    elements.waaExample.replaceChildren();
    waaTasks.forEach((task) => {
      const option = document.createElement("option");
      option.value = task.example_path;
      const variant = task.variant_id ? `参数化演示 ${task.variant_id} · ` : "";
      option.textContent = `${task.domain} · ${variant}${task.instruction}`;
      elements.waaExample.append(option);
    });
    const selected = waaTasks.find((task) => task.example_path === previous);
    elements.waaExample.value = selected?.example_path || waaTasks[0]?.example_path || "";
    renderWaaTaskMeta();
  } catch (error) {
    waaTasks = [];
    waaTaskCatalogRoot = null;
    elements.waaExample.replaceChildren();
    elements.waaTaskMeta.textContent = `任务目录加载失败：${error.message}`;
  } finally {
    elements.waaExample.disabled = isBusy();
    renderRecordingSource();
  }
}

function renderRecordingSource() {
  const waa = usesWaaRecording();
  const native = elements.recordSource.value === "opencua";
  elements.recordNarration.disabled = native || isBusy();
  if (native) elements.recordNarration.checked = false;
  elements.recordDeferCompilation.disabled = native || isBusy();
  if (native) elements.recordDeferCompilation.checked = true;
  elements.waaRecordingFields.classList.toggle("hidden", !waa);
  elements.recordButton.disabled = isBusy()
    || (waa && !selectedWaaTask());
}

function switchView(view) {
  if (view !== "rsi") clearTimeout(rsiPollTimer);
  closeTaskDetail();
  document.body.classList.toggle("library-mode", view === "library");
  document.body.classList.toggle("rsi-mode", view === "rsi");
  elements.viewTabs.forEach((tabButton) => {
    tabButton.classList.toggle("active", tabButton.dataset.view === view);
  });
  elements.viewPanels.forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== `${view}-panel`);
  });
  if (view === "library") refreshState();
  if (view === "rsi") refreshRsi();
}

function canExecuteTask(task) {
  if (!task || !task.confirmed || task.execution_scope === "desktop") return false;
  const isWechat = /Weixin|WeChat/i.test(task.process_name || "");
  return !isWechat || !task.missing_message_capabilities.includes("type_text");
}

function renderTaskMeta() {
  elements.useExperience.checked = elements.taskpack.value !== "__baseline__"
    && !elements.taskpack.value.startsWith("baseline:");
  elements.experienceHelp.textContent = elements.executionScope.value === "desktop"
    ? "开启后手动选择语义经验，使用任务状态图和人工规则指导桌面规划，不复用录制坐标。关闭即 Baseline。"
    : "在下拉列表中选择使用经验，或仅选择目标窗口（不使用经验）；后者保留允许操作和结果验证。";
  if (!backendMatchesScope()) {
    elements.warning.textContent = elements.operationScope.value === "selected_windows"
      ? "指定窗口 / 应用目前需要 Cua 后端；请在模型旁选择 Cua。"
      : "全桌面目前需要 Win32 后端；请在模型旁选择 Win32。";
    elements.warning.classList.remove("hidden");
    elements.executeButton.disabled = true;
    return;
  }
  if (elements.executionScope.value === "desktop") {
    const task = selectedTask();
    elements.taskMeta.textContent = elements.useExperience.checked
      ? (task ? `经验：${task.task_id} · 来源：${task.execution_scope === "desktop" ? "桌面" : "单窗口（仅对应应用内参考）"}` : "请手动选择已语义编译的经验。")
      : "Baseline：只发送指令、主屏截图和最近操作，不读取任何经验。";
    elements.warning.textContent = elements.operationScope.value === "selected_windows"
      ? "仅操作下方授权的窗口或应用。后台优先；明确拒绝后该窗口改走前台。F9 或停止按钮中止。模型自报完成不等于独立验证成功。"
      : "会控制主显示器上的多个程序并占用鼠标键盘。请先关闭敏感内容；F9 或停止按钮中止。模型自报完成不等于独立验证成功。";
    elements.warning.classList.remove("hidden");
    elements.executeButton.disabled = isBusy() || (elements.useExperience.checked && (!task?.confirmed || !task?.semantic_experience));
    return;
  }
  const task = selectedTask();
  if (!task) {
    elements.taskMeta.textContent = taskpacks.length
      ? "请选择目标任务；使用经验时必须手动选择，Baseline 可直接指定窗口。"
      : "没有找到可用的 Windows 示范任务。";
    elements.warning.classList.add("hidden");
    elements.executeButton.disabled = true;
    return;
  }
  const target = [task.process_name, task.title_contains].filter(Boolean).join(" · ");
  elements.taskMeta.textContent = `${elements.useExperience.checked ? "使用经验" : "不使用经验，仅确定目标窗口"} · ${task.confirmed ? "已确认" : "草稿"} · ${target || "未命名窗口"} · 最多 ${task.max_actions} 步`;
  if (task.missing_message_capabilities.length) {
    elements.warning.textContent = `这份旧示范缺少消息输入能力（${task.missing_message_capabilities.join(", ")}）。可以先测试规划，但正式发消息前需要升级任务模板。`;
    elements.warning.classList.remove("hidden");
  } else {
    elements.warning.classList.add("hidden");
  }
  elements.executeButton.disabled = !canExecuteTask(task) || isBusy();
}

function renderInputModeHelp() {
  const desktop = elements.executionScope.value === "desktop";
  document.querySelector("#input-mode-field").hidden = desktop;
  elements.inputModeHelp.hidden = desktop;
  if (elements.executionScope.value === "desktop") {
    elements.inputModeHelp.textContent = "桌面范围只支持主显示器前台操作，可跨窗口；运行期间请勿使用鼠标键盘。";
    return;
  }
  elements.inputModeHelp.textContent = elements.inputMode.value === "background"
    ? "后台执行不会抢占焦点，但目标必须保持可见且不能最小化；部分游戏、模拟器和 GPU 窗口不兼容。"
    : "前台执行会聚焦目标窗口；适用于游戏、模拟器和不接受后台消息的应用。";
}

function populateTaskpacks(records) {
  const previous = elements.taskpack.value;
  taskpacks = records;
  elements.taskpack.replaceChildren();
  const automatic = document.createElement("option");
  const desktop = elements.executionScope.value === "desktop";
  automatic.value = desktop ? "__baseline__" : "";
  automatic.textContent = desktop ? "不使用经验 · 桌面 Baseline" : "请选择任务经验";
  elements.taskpack.append(automatic);
  records.forEach((task) => {
    const option = document.createElement("option");
    option.value = task.path;
    option.textContent = `${task.task_id}${task.confirmed ? "" : "（草稿）"}`;
    elements.taskpack.append(option);
  });
  if (!desktop) {
    const group = document.createElement("optgroup");
    group.label = "不使用经验 · 仅选择目标窗口";
    records.filter(task => task.execution_scope !== "desktop").forEach(task => {
      const option = document.createElement("option");
      option.value = `baseline:${task.path}`;
      option.textContent = `${task.task_id} · ${task.process_name || "Windows"}（不使用经验）`;
      group.append(option);
    });
    elements.taskpack.append(group);
  }
  elements.taskpack.value = [...elements.taskpack.options].some(option => option.value === previous)
    ? previous : automatic.value;
  renderTaskMeta();
  renderLibrary();
}

let modelApiAvailable = false;
let apiSettingsAvailable = false;
let apiSettingsInitialized = false;
let savedAPISettings = null;

function apiFormEndpoint() {
  const base = elements.apiBaseUrl.value.trim().replace(/\/+$/, "");
  return base.endsWith("/chat/completions") ? base : `${base}/chat/completions`;
}

function renderAPISettingsStatus() {
  const savedBase = savedAPISettings?.base_url?.replace(/\/+$/, "") || "";
  const savedEndpoint = savedBase.endsWith("/chat/completions")
    ? savedBase : `${savedBase}/chat/completions`;
  const reusableKey = savedAPISettings?.has_saved_key && savedEndpoint === apiFormEndpoint();
  elements.apiKey.placeholder = reusableKey
    ? "已安全保存；留空复用，输入新 Key 可替换"
    : "输入密钥；留空则读取服务端 TRACE2TASK_API_KEY";
  elements.apiSettingsStatus.textContent = !apiSettingsAvailable
    ? "后台尚未支持保存配置，请重启网页后台后刷新。"
    : savedAPISettings?.error || (savedAPISettings?.has_saved_key && !reusableKey
      ? "API 地址已变化，旧密钥不会用于新地址；请重新输入密钥。"
      : reusableKey
        ? "API 配置和密钥已保存；刷新或重启后可直接复用，密钥不会回显。"
        : savedAPISettings?.saved
          ? "API 配置已保存；未保存密钥，可使用服务器环境变量。"
          : "尚未保存 API 配置。填写后点击保存，下次不必重新输入。");
  if (apiSettingsAvailable && !savedAPISettings?.secure_key_storage) {
    elements.apiSettingsStatus.textContent += " 此系统暂不支持密钥加密保存，请留空并使用环境变量。";
  }
}

async function saveAPISettings() {
  if (isBusy()) return;
  if (!apiSettingsAvailable) return showError("请重启网页后台后刷新，以启用 API 配置保存。");
  clearError();
  setBusy(true);
  try {
    savedAPISettings = await request("/api/model-settings/save", {
      method: "POST",
      body: JSON.stringify({
        base_url: elements.apiBaseUrl.value.trim(),
        model: elements.apiModel.value.trim(),
        reasoning_effort: elements.apiReasoningEffort.value,
        api_key: elements.apiKey.value,
        response_format: elements.apiResponseFormat.value,
        timeout_seconds: Number(elements.apiTimeout.value),
      }),
    });
    elements.apiKey.value = "";
    renderAPISettingsStatus();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function clearAPISettings() {
  if (isBusy() || !apiSettingsAvailable) return;
  if (!window.confirm("清除本机保存的 API 配置和加密密钥？不会删除任务、经验或环境变量。")) return;
  clearError();
  setBusy(true);
  try {
    savedAPISettings = await request("/api/model-settings/clear", {
      method: "POST", body: "{}",
    });
    elements.apiKey.value = "";
    renderAPISettingsStatus();
    elements.apiSettingsStatus.textContent = "已清除保存的配置和密钥。当前表单未保存，环境变量不受影响。";
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

let localServiceBusy = false;
for (const action of ["start", "stop"]) {
  document.querySelector(`#local-service-${action}`).addEventListener("click", async () => {
    if (localServiceBusy) return;
    const status = document.querySelector("#local-service-status");
    if (isBusy()) { status.textContent = "请先停止当前任务，再管理模型服务。"; return; }
    localServiceBusy = true;
    const buttons = ["start", "stop"].map(value => document.querySelector(`#local-service-${value}`));
    buttons.forEach(button => button.disabled = true);
    status.textContent = action === "start" ? "正在清理旧服务并加载所选模型，请稍候…" : "正在关闭本地模型服务，释放显存…";
    try {
      const result = await request("/api/local-model/service", {method: "POST", body: JSON.stringify({action, model: elements.localModel.value})});
      status.textContent = result.message;
    } catch (error) { status.textContent = `操作失败：${error.message}`; }
    finally { localServiceBusy = false; buttons.forEach(button => button.disabled = false); }
  });
}

function usesTrainedModel() {
  return elements.modelProvider.value === "local" && ["trained_d", "qwen3-vl-2b", "gui-owl-2b", "mai-ui-2b"].includes(elements.localModel.value);
}

let promptProfileKey = "";
let promptDirty = false;
let promptLoaded = false;
let chatPromptProviderLoaded = "";
let chatPromptDirty = false;

function editableChatPromptProvider() {
  if (elements.modelProvider.value === "codex") return "codex";
  return usesModelApi() ? "api" : "";
}

async function loadChatPrompts() {
  const provider = editableChatPromptProvider();
  elements.chatPromptSettings.classList.toggle("hidden", !provider);
  if (!provider) { chatPromptProviderLoaded = ""; chatPromptDirty = false; return; }
  elements.chatPromptHelp.textContent = provider === "api"
    ? "此内容会附加到实际 API 请求的 system 消息；下方逐轮日志会显示完整系统消息。"
    : "Codex 的内置系统提示词由 App Server 管理，当前接口不会返回或允许编辑；此处编辑的是本程序每轮发送给 Codex 的执行指导，逐轮日志会如实标注。";
  if (provider === chatPromptProviderLoaded) return;
  chatPromptProviderLoaded = "";
  elements.chatPromptsStatus.textContent = "正在读取已保存提示词…";
  try {
    const profile = await request(`/api/chat-prompts?provider=${provider}`);
    if (editableChatPromptProvider() !== provider) return;
    elements.chatPromptGuidance.value = profile.guidance;
    chatPromptProviderLoaded = provider;
    chatPromptDirty = false;
    elements.chatPromptsStatus.textContent = profile.customized ? "正在使用已保存的自定义指导。" : "使用默认提示词。";
  } catch (error) {
    if (editableChatPromptProvider() === provider) elements.chatPromptsStatus.textContent = `提示词读取失败：${error.message}`;
  }
}

async function saveChatPrompts(reset = false) {
  const provider = editableChatPromptProvider();
  if (!provider || provider !== chatPromptProviderLoaded || isBusy()) return;
  elements.chatPromptsSave.disabled = true;
  elements.chatPromptsReset.disabled = true;
  try {
    const profile = await request("/api/chat-prompts", {method: "POST", body: JSON.stringify({
      provider, guidance: reset ? "" : elements.chatPromptGuidance.value,
    })});
    if (editableChatPromptProvider() !== provider) return;
    elements.chatPromptGuidance.value = profile.guidance;
    chatPromptDirty = false;
    elements.chatPromptsStatus.textContent = reset ? "已恢复默认。" : "已保存；下一次任务生效。";
  } catch (error) {
    elements.chatPromptsStatus.textContent = `保存失败：${error.message}`;
  } finally {
    elements.chatPromptsSave.disabled = false;
    elements.chatPromptsReset.disabled = false;
  }
}

function editableLocalPromptKey() {
  if (!usesTrainedModel() || elements.localModel.value === "trained_d") return "";
  return `${elements.localModel.value}:${document.querySelector("#local-executor").value}`;
}

async function loadLocalPrompts() {
  const key = editableLocalPromptKey();
  elements.localPromptSettings.classList.toggle("hidden", !key);
  if (!key) { promptProfileKey = ""; promptDirty = false; promptLoaded = false; return; }
  if (key === promptProfileKey) return;
  promptProfileKey = key;
  promptLoaded = false;
  elements.localPromptsStatus.textContent = "正在读取已保存提示词…";
  try {
    const [model, backend] = key.split(":");
    const profile = await request(`/api/local-prompts?model=${encodeURIComponent(model)}&backend=${backend}`);
    if (promptProfileKey !== key) return;
    elements.localSystemPrompt.value = profile.effective.system_prompt;
    elements.localTurnTemplate.value = profile.effective.turn_template;
    promptDirty = false;
    promptLoaded = true;
    elements.localPromptsStatus.textContent = profile.customized
      ? "正在使用本机保存的自定义提示词。" : "正在使用模型默认提示词。";
  } catch (error) {
    if (promptProfileKey === key) elements.localPromptsStatus.textContent = `提示词读取失败：${error.message}`;
  }
}

async function saveLocalPrompts(reset = false) {
  const key = editableLocalPromptKey();
  if (!key || key !== promptProfileKey || isBusy()) return;
  const [model, backend] = key.split(":");
  elements.localPromptsSave.disabled = true;
  elements.localPromptsReset.disabled = true;
  try {
    const profile = await request("/api/local-prompts", {method: "POST", body: JSON.stringify({
      model, backend,
      profile: reset ? null : {system_prompt: elements.localSystemPrompt.value,
        turn_template: elements.localTurnTemplate.value},
    })});
    if (promptProfileKey !== key) return;
    elements.localSystemPrompt.value = profile.effective.system_prompt;
    elements.localTurnTemplate.value = profile.effective.turn_template;
    promptDirty = false;
    promptLoaded = true;
    elements.localPromptsStatus.textContent = reset ? "已恢复模型默认提示词。" : "已保存；下一次任务开始生效。";
  } catch (error) {
    elements.localPromptsStatus.textContent = `保存失败：${error.message}`;
  } finally {
    elements.localPromptsSave.disabled = false;
    elements.localPromptsReset.disabled = false;
  }
}

function usesModelApi() {
  return elements.modelProvider.value === "api" || (elements.modelProvider.value === "local" && !usesTrainedModel());
}

function backendMatchesScope() {
  return elements.operationScope.value === "desktop"
    || document.querySelector("#local-executor").value === "cua";
}

function cuaJobTarget(selection) {
  return selection ? {targets: selection.targets, initial_index: selection.initial_index}
    : document.querySelector("#local-executor").value === "cua"
      ? {kind: "desktop", display_id: "primary"} : null;
}

function syncProviderFields() {
  const trained = usesTrainedModel();
  const backend = document.querySelector("#local-executor");
  const selected = elements.operationScope.value === "selected_windows";
  if (selected) elements.desktopOrchestration.value = "legacy";
  elements.desktopWorkflowSettings.hidden = selected;
  document.querySelector("#execution-backend-help").textContent = !backendMatchesScope()
    ? (selected ? "指定窗口 / 应用目前使用 Cua；请选择 Cua 后端。" : "全桌面目前使用 Win32；请选择 Win32 后端。")
    : selected
    ? "仅操作已授权目标。后台优先；明确拒绝后该窗口改走前台，并尝试恢复原焦点。"
    : "全桌面可跨应用；本机桌面输入会占用系统键鼠。";
  document.querySelector("#cua-target-settings").hidden = !selected || backend.value !== "cua";
  if (trained) {
    elements.executionScope.value = "desktop";
    populateTaskpacks(taskpacks);
    if (elements.localModel.value === "trained_d") elements.taskpack.value = "__baseline__";
    renderTaskMeta();
    renderInputModeHelp();
  }
  document.querySelector("#trained-model-settings").classList.toggle("hidden", !trained);
  elements.codexModelSettings.classList.toggle("hidden", usesModelApi() || trained);
  elements.apiModelSettings.classList.toggle("hidden", elements.modelProvider.value !== "api");
  elements.localModelSettings.classList.toggle("hidden", elements.modelProvider.value !== "local");
  document.querySelector("#api-model-primary").hidden = elements.modelProvider.value !== "api";
  document.querySelector("#codex-advanced").hidden = elements.modelProvider.value !== "codex";
  document.querySelector("#local-advanced").hidden = elements.modelProvider.value !== "local" || trained;
  document.querySelector("#trained-advanced").hidden = !trained;
  loadLocalPrompts();
  loadChatPrompts();
  if (elements.modelProvider.value === "api" && !savedAPISettings?.saved) {
    document.querySelector("#execution-advanced").open = true;
  }
  elements.adaptiveReasoning.disabled = isBusy() || usesModelApi() || elements.executionScope.value === "desktop";
  setBusy(isBusy());
}

function populateAgentOptions(options) {
  if (!options) return;
  modelApiAvailable = Boolean(options.api_defaults);
  const previousApiEffort = elements.apiReasoningEffort.value || "default";
  elements.apiReasoningEffort.replaceChildren();
  (options.api_defaults?.reasoning_efforts || ["default"]).forEach((effort) => {
    const option = document.createElement("option");
    option.value = effort;
    option.textContent = effort === "default"
      ? "服务商默认 · 不传参数" : effort === "none" ? "关闭思考" : effortLabels[effort] || effort;
    elements.apiReasoningEffort.append(option);
  });
  elements.apiReasoningEffort.value = previousApiEffort;
  apiSettingsAvailable = Boolean(options.api_defaults?.saved_settings);
  savedAPISettings = options.api_defaults?.saved_settings || null;
  if (!apiSettingsInitialized && savedAPISettings?.saved) {
    elements.apiBaseUrl.value = savedAPISettings.base_url;
    elements.apiModel.value = savedAPISettings.model;
    elements.apiReasoningEffort.value = savedAPISettings.thinking_mode === "disabled"
      ? "none" : savedAPISettings.thinking_mode === "enabled" && savedAPISettings.reasoning_effort === "default"
        ? "high" : savedAPISettings.reasoning_effort;
    elements.apiResponseFormat.value = savedAPISettings.response_format;
    elements.apiTimeout.value = savedAPISettings.timeout_seconds;
    elements.modelProvider.value = "api";
  }
  apiSettingsInitialized = true;
  renderAPISettingsStatus();
  syncProviderFields();
  if (!elements.waaRoot.value.trim() && options.waa_defaults?.root) {
    elements.waaRoot.value = options.waa_defaults.root;
  }
  defaultWaaExamplePath = options.waa_defaults?.example_path || defaultWaaExamplePath;
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
  elements.experienceModelSummary.textContent = `${modelLabels[model] || model} / ${effortLabels[reasoningEffort] || reasoningEffort}`;
}

function makeMiniButton(label, action, accent = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `mini-button${accent ? " accent" : ""}`;
  button.textContent = label;
  button.addEventListener("click", () => action(button));
  return button;
}

function formatTimestamp(value) {
  if (!value) return "时间未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function formatDuration(milliseconds) {
  const value = Number(milliseconds || 0);
  if (value >= 60_000) return `${(value / 60_000).toFixed(1)} 分`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)} 秒`;
  return `${Math.round(value)} 毫秒`;
}

function waaConditionLabel(condition) {
  return {
    baseline: "无 Trace 基线",
    trace: "原始 Trace",
    compiled: "Compiler 经验",
    narrated_compiled: "人工讲解 Compiler",
    feedback: "人工反馈经验",
  }[condition] || condition || "WAA";
}

function humanActionLabel(action) {
  const skill = action?.skill;
  const args = action?.args || {};
  if (skill === "click") {
    const x = Number(args.x);
    const y = Number(args.y);
    const position = Number.isFinite(x) && Number.isFinite(y)
      ? `（画面 ${Math.round(x * 100)}%, ${Math.round(y * 100)}%）`
      : "";
    return `点击${position}`;
  }
  if (skill === "type_text") return `输入文本：“${args.text || ""}”`;
  if (skill === "press_key") return `按键：${args.key || "未知"}`;
  if (skill === "hotkey") return `组合键：${(args.keys || []).join(" + ")}`;
  if (skill === "wait") return `等待 ${formatDuration(args.duration_ms)}`;
  if (skill) return `${skill} · ${JSON.stringify(args)}`;
  return action?.raw || "未解析操作";
}

function makeReviewImage(path, label) {
  const link = document.createElement("a");
  link.className = "run-review-image-link";
  link.href = `/api/local-image?path=${encodeURIComponent(path)}`;
  link.target = "_blank";
  link.rel = "noopener";
  const caption = document.createElement("span");
  caption.textContent = label;
  const image = document.createElement("img");
  image.className = "run-review-image";
  image.loading = "lazy";
  image.alt = label;
  image.src = link.href;
  link.append(caption, image);
  return link;
}

function makeCandidateReview(candidate) {
  const review = candidate.review_timeline;
  if (!review || !Array.isArray(review.rounds)) return null;
  const container = document.createElement("div");
  container.className = "run-review";
  review.rounds.forEach((round) => {
    const card = document.createElement("section");
    card.className = "run-review-round";
    const heading = document.createElement("div");
    heading.className = "run-review-round-heading";
    const title = document.createElement("strong");
    title.textContent = `模型回合 ${round.round}${round.stage_id ? ` · ${round.stage_id}` : ""}`;
    const timing = document.createElement("span");
    timing.textContent = `${round.actions?.length || 0} 步 · 模型 ${formatDuration(round.model_roundtrip_ms)}`;
    heading.append(title, timing);
    const goal = document.createElement("p");
    goal.className = "run-review-goal";
    goal.textContent = round.stage_goal || "本轮没有记录阶段目标。";
    const reason = document.createElement("p");
    reason.className = "run-review-reason";
    reason.textContent = `为什么这样做：${round.reason || "模型没有留下理由。"}`;
    const actions = document.createElement("ol");
    actions.className = "run-review-actions";
    if (round.decision) {
      const decision = document.createElement("li");
      decision.className = "run-review-decision";
      decision.textContent = round.decision === "DONE"
        ? "模型判断：任务已经完成"
        : "模型判断：任务失败并停止";
      actions.append(decision);
    }
    (round.actions || []).forEach((action) => {
      const row = document.createElement("li");
      row.textContent = humanActionLabel(action);
      if (action.raw) row.title = action.raw;
      actions.append(row);
    });
    const images = document.createElement("div");
    images.className = "run-review-images";
    if (round.before_frame) images.append(makeReviewImage(round.before_frame, "执行前"));
    if (round.after_frame) images.append(makeReviewImage(round.after_frame, "执行后"));
    card.append(heading, goal, reason, actions, images);
    container.append(card);
  });
  return container;
}

function makePerformanceTimeline(performance, stages = [], job = null) {
  if (!performance || !Object.keys(performance).length) return null;
  const submitted = job?.created_at ? Date.parse(job.created_at) : NaN;
  const finished = job?.updated_at ? Date.parse(job.updated_at) : NaN;
  const jobWallMs = Number.isFinite(submitted) && Number.isFinite(finished)
    && finished >= submitted && !["queued", "running", "stopping"].includes(job.status)
    ? finished - submitted : null;
  const details = document.createElement("details");
  details.className = "performance-timeline";
  const summary = document.createElement("summary");
  summary.textContent = `查看性能时间轴 · 模型 ${formatDuration(performance.model_roundtrip_ms ?? performance.planning_ms)} / ${jobWallMs === null ? "Agent 循环" : "任务提交至结束"} ${formatDuration(jobWallMs ?? performance.total_elapsed_ms)}`;
  const grid = document.createElement("div");
  grid.className = "performance-grid";
  const metrics = [
    ["任务提交至结束", jobWallMs],
    ["Agent 循环", performance.total_elapsed_ms],
    ...(performance.model_roundtrip_ms === undefined ? [["规划总计", performance.planning_ms]] : []),
    ["模型回合", performance.model_roundtrip_ms],
    ["模型生成等待", performance.model_completion_wait_ms],
    ["截图", performance.capture_ms],
    ["显式等待", performance.explicit_wait_ms],
    ["本地等稳", performance.local_wait_until_ms],
    ["本地动作", performance.action_ms],
  ];
  if (performance.startup_ms !== undefined) metrics.push(["启动 / 装载", performance.startup_ms]);
  if (performance.cancel_wait_ms !== undefined) metrics.push(["停止等待", performance.cancel_wait_ms]);
  metrics.forEach(([label, value]) => {
    if (value === null || value === undefined) return;
    const metric = document.createElement("div");
    const name = document.createElement("span");
    const duration = document.createElement("strong");
    name.textContent = label;
    duration.textContent = formatDuration(value);
    metric.append(name, duration);
    grid.append(metric);
  });
  details.append(summary, grid);
  const scope = document.createElement("p");
  scope.className = "field-help";
  scope.textContent = "“Agent 循环”只统计执行器开始到退出；“任务提交至结束”还包含排队、初始化与结果整理，不包含提交前的准备或其他任务。各分项并非全部耗时，勿相加当作总计。";
  details.append(scope);
  if (stages.length) {
    const stageList = document.createElement("div");
    stageList.className = "performance-stages";
    stages.forEach((stage) => {
      const row = document.createElement("div");
      const stageName = document.createElement("strong");
      const stageMetrics = document.createElement("span");
      stageName.textContent = stage.stage_id || "unknown";
      stageMetrics.textContent = `${stage.plans || 0} 次模型 · ${stage.executed_actions || 0} 步 · 规划 ${formatDuration(stage.planning_ms)} · 等待 ${formatDuration((stage.explicit_wait_ms || 0) + (stage.local_wait_until_ms || 0))}`;
      row.append(stageName, stageMetrics);
      stageList.append(row);
    });
    details.append(stageList);
  }
  return details;
}

function modelIoStatusLabel(round) {
  const status = String(round?.status || "unknown").toLowerCase();
  if (status === "cancel_pending") {
    return "正在等待模型调用取消；未执行未返回的计划";
  }
  if (status === "discarded") return "停止后返回，未执行";
  if (status === "format_rejected") return "动作格式无效，未执行；请求模型纠正";
  if (["cancelled", "canceled", "stopped", "aborted"].includes(status)) {
    return "已取消；未执行未返回的计划";
  }
  if (["error", "failed", "failure"].includes(status)) return "模型调用失败";
  if (["no_output", "empty", "no-response", "no_response"].includes(status)) {
    return "模型未返回可用输出";
  }
  if (["completed", "complete", "success", "predicted"].includes(status)) {
    return "模型回合已归档";
  }
  return `状态：${round?.status || "未记录"}`;
}

function makeRunAuditLinks(result) {
  if (!result || typeof result !== "object") return null;
  const entries = [
    ["打开完整运行日志", result.trace_path],
    ["打开 I/O 审计归档", result.audit_path || result.audit_index_path],
  ].filter(([, path]) => typeof path === "string" && path);
  if (!entries.length) return null;
  const actions = document.createElement("div");
  actions.className = "library-actions";
  entries.forEach(([label, path]) => actions.append(makeModelArchiveButton(label, path)));
  return actions;
}

function modelIoDuration(round) {
  const timings = round?.timings || {};
  return Number(
    round?.model_roundtrip_ms
    ?? timings.model_roundtrip_ms
    ?? timings.elapsed_ms
    ?? timings.total_elapsed_ms
    ?? timings.total_ms
    ?? 0,
  );
}

function modelIoText(value) {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch (_) {
    return String(value);
  }
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "未记录";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  return `${Math.round(bytes)} B`;
}

function formatModelDuration(milliseconds) {
  const value = Number(milliseconds || 0);
  if (value >= 1_000) return `${(value / 1_000).toFixed(2)} 秒`;
  return `${Math.round(value)} 毫秒`;
}

function makeModelPayload(label, value) {
  if (value === undefined || value === null || value === "") return null;
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = label;
  const content = document.createElement("pre");
  content.textContent = modelIoText(value);
  details.append(summary, content);
  return details;
}

function makeModelArchiveButton(label, path) {
  if (!path || typeof path !== "string") return null;
  return makeMiniButton(label, () => openLocal(path));
}

function makeModelIoTimeline(rounds) {
  if (!Array.isArray(rounds) || !rounds.length) return null;
  const timeline = document.createElement("details");
  timeline.className = "performance-timeline";
  const summary = document.createElement("summary");
  summary.textContent = `查看模型输入与输出 · ${rounds.length} 回合`;
  const list = document.createElement("div");
  list.className = "performance-stages";

  rounds.forEach((round, index) => {
    const entry = document.createElement("details");
    entry.className = "guidance-revision";
    const entrySummary = document.createElement("summary");
    const stepIndex = Number.isInteger(round?.step_index) ? round.step_index + 1 : index + 1;
    entrySummary.textContent = `模型回合 ${stepIndex} · ${round?.purpose === "verify_completion" ? "只读完成核验" : modelIoStatusLabel(round)} · ${formatModelDuration(modelIoDuration(round))}`;
    const body = document.createElement("div");
    body.className = "guidance-revision-body";

    const metadata = document.createElement("p");
    const requestId = round?.request_id ? ` · 请求 ${round.request_id}` : "";
    metadata.textContent = `本轮耗时：${formatModelDuration(modelIoDuration(round))}${requestId}`;
    body.append(metadata);

    const tokens = round?.tokens || {};
    const memory = round?.memory || {};
    const diagnostics = [
      ["输入 Token", tokens.input_tokens],
      ["输出 Token", tokens.output_tokens],
      ["显存起始 · 已分配", memory.before?.allocated_bytes === undefined ? undefined : formatBytes(memory.before.allocated_bytes)],
      ["显存起始 · 预留", memory.before?.reserved_bytes === undefined ? undefined : formatBytes(memory.before.reserved_bytes)],
      ["显存峰值 · 已分配", (memory.peak?.peak_allocated_bytes ?? memory.peak?.allocated_bytes) === undefined
        ? undefined : formatBytes(memory.peak?.peak_allocated_bytes ?? memory.peak?.allocated_bytes)],
      ["显存峰值 · 预留", (memory.peak?.peak_reserved_bytes ?? memory.peak?.reserved_bytes) === undefined
        ? undefined : formatBytes(memory.peak?.peak_reserved_bytes ?? memory.peak?.reserved_bytes)],
      ["回收后显存 · 已分配", memory.after_cleanup?.allocated_bytes === undefined ? undefined : formatBytes(memory.after_cleanup.allocated_bytes)],
      ["回收后显存 · 预留", memory.after_cleanup?.reserved_bytes === undefined ? undefined : formatBytes(memory.after_cleanup.reserved_bytes)],
    ].filter(([, value]) => value !== undefined && value !== null);
    if (diagnostics.length) {
      const grid = document.createElement("div");
      grid.className = "performance-grid";
      diagnostics.forEach(([label, value]) => {
        const metric = document.createElement("div");
        const name = document.createElement("span");
        const metricValue = document.createElement("strong");
        name.textContent = label;
        metricValue.textContent = String(value);
        metric.append(name, metricValue);
        grid.append(metric);
      });
      body.append(grid);
    }

    const timingLabels = {
      load_ms: "载入",
      preprocess_ms: "预处理",
      generate_ms: "生成",
      decode_ms: "解码",
      total_ms: "本轮总计",
    };
    const phaseMs = round?.timings;
    const phaseEntries = phaseMs && typeof phaseMs === "object"
      ? Object.entries(phaseMs).filter(([name, value]) => name in timingLabels && Number.isFinite(Number(value)))
      : [];
    if (phaseEntries.length) {
      const phaseText = document.createElement("p");
      phaseText.textContent = `分段耗时：${phaseEntries
        .map(([name, value]) => `${timingLabels[name]} ${formatModelDuration(value)}`)
        .join(" · ")}`;
      body.append(phaseText);
    }

    if (round?.error) {
      const error = document.createElement("p");
      error.className = "guidance-feedback";
      error.textContent = `错误：${modelIoText(round.error)}`;
      body.append(error);
    }

    if (round?.execution) {
      const execution = document.createElement("p");
      const value = round.execution;
      const label = value.status === "delivered"
        ? value.delivery_mode_requested === "foreground" && value.background_refusal
          ? (value.effect === "confirmed"
            ? "后台拒绝，前台重试的效果已确认"
            : "后台拒绝，前台已尝试；效果待观察")
          : (value.effect === "confirmed" ? "动作已送达，驱动已确认效果" : "动作已送达，效果待确认")
        : value.status === "driver_refused" ? "驱动拒绝后台输入，未记为已送达"
        : value.status === "no_progress" ? "无进展保护：重复操作未执行，要求模型纠正"
        : value.status === "rejected" ? "动作未执行，预检拒绝" : "执行中断或结果未知，不自动重试";
      execution.textContent = `执行器：${label}${value.reason ? ` · ${value.reason}` : ""}。动作效果不等于整个任务成功。`;
      body.append(execution);
    }
    if (round?.protocol_normalizations?.includes("complete_json_missing_tool_call_close")) {
      const note = document.createElement("p");
      note.textContent = "格式兼容：生成正常结束、JSON 完整，仅缺少工具调用结束标签；原始输出未修改。";
      body.append(note);
    }
    if (round?.verification) {
      const note = document.createElement("p");
      const labels = {complete: "视觉证据支持完成", incomplete: "尚未完成", unknown: "无法确认"};
      note.textContent = `只读核验：${labels[round.verification.verdict] || "结果无效"} · ${round.verification.evidence} · ${round.verification.missing || "无可见缺项"}。由同一本地模型判断，不是独立应用验证；本轮未执行动作。`;
      body.append(note);
    }
    [
      ["查看实际请求内容", round?.request ?? round?.model_input ?? round?.input],
      ["查看模型原始输出", round?.raw_output],
      ["查看解码后的预测（不是执行结果）", round?.prediction],
      ["查看实际执行结果", round?.execution],
      ["查看只读核验证据", round?.verification],
      ["查看响应内容", round?.response ?? round?.model_output ?? round?.output],
    ].forEach(([label, value]) => {
      const payload = makeModelPayload(label, value);
      if (payload) body.append(payload);
    });

    const actions = document.createElement("div");
    actions.className = "library-actions";
    const inputPath = round?.input_path || round?.request_path;
    const requestButton = makeModelArchiveButton("打开输入归档", inputPath);
    const requestRecordButton = round?.request_path && round.request_path !== inputPath
      ? makeModelArchiveButton("打开请求记录", round.request_path)
      : null;
    const responseButton = makeModelArchiveButton("打开输出归档", round?.response_path);
    const outputButton = makeModelArchiveButton("打开本轮目录", round?.output_directory);
    [requestButton, requestRecordButton, responseButton, outputButton]
      .filter(Boolean)
      .forEach(button => actions.append(button));
    if (round?.screenshot) {
      const image = document.createElement("a");
      image.className = "mini-button";
      image.textContent = "查看输入截图";
      image.href = `/api/local-image?path=${encodeURIComponent(round.screenshot)}`;
      image.target = "_blank";
      image.rel = "noopener";
      actions.append(image);
    }
    if (actions.childElementCount) body.append(actions);
    entry.append(entrySummary, body);
    list.append(entry);
  });
  timeline.append(summary, list);
  return timeline;
}

function makeChatBubble(role, label, value) {
  const bubble = document.createElement("div");
  bubble.className = `model-chat-bubble ${role}`;
  const heading = document.createElement("strong");
  heading.textContent = label;
  bubble.append(heading);
  const text = modelIoText(value ?? "");
  const content = document.createElement("pre");
  content.textContent = text || "尚未返回";
  bubble.append(content);
  return bubble;
}

function describeUnifiedAction(action) {
  const args = action?.args || {};
  const point = (x, y) => `画面位置 ${Math.round(x * 100)}%、${Math.round(y * 100)}%`;
  const button = {left: "左键", right: "右键", middle: "中键"}[args.button] || "左键";
  switch (action?.skill) {
    case "click": return `${button}点击 ${point(args.x, args.y)}`;
    case "double_click": return `双击 ${point(args.x, args.y)}`;
    case "move_cursor": return `移动鼠标到 ${point(args.x, args.y)}`;
    case "hold_mouse": return `${button}按住 ${point(args.x, args.y)}，持续 ${args.duration_ms} 毫秒`;
    case "drag": return `从 ${point(args.start_x, args.start_y)} 拖到 ${point(args.end_x, args.end_y)}，持续 ${args.duration_ms} 毫秒`;
    case "type_text": return `输入文字「${args.text}」${args.x === undefined ? "（当前焦点）" : `，定位于 ${point(args.x, args.y)}`}`;
    case "press_key": return `按键 ${args.key}`;
    case "hold_key": return `按住 ${args.key}，持续 ${args.duration_ms} 毫秒`;
    case "hotkey": return `组合键 ${args.keys?.join(" + ")}`;
    case "wait": return `等待 ${args.duration_ms} 毫秒`;
    case "scroll": return `向${{up: "上", down: "下", left: "左", right: "右"}[args.direction] || args.direction}滚动 ${args.amount} ${args.by === "page" ? "页" : "行"}${args.x === undefined ? "" : `，位置 ${point(args.x, args.y)}`}`;
    case "switch_window": return `切换到窗口 PID ${args.pid} / HWND ${args.window_id}`;
    case "launch_app": return `启动已授权应用 ${args.app_id}`;
    default: return action?.skill || "未知动作";
  }
}

function makeActionFlow(execution) {
  const actions = execution?.normalized_plan?.actions;
  if (!Array.isArray(actions)) return null;
  const flow = document.createElement("div");
  flow.className = "model-action-flow";
  const heading = document.createElement("strong");
  heading.textContent = "动作去向 · 模型原文 → 统一动作 → 执行器 → 回执";
  flow.append(heading);
  actions.forEach((action, index) => {
    const card = document.createElement("div");
    card.className = "model-action-card";
    const line = document.createElement("strong");
    line.textContent = action.done ? "完成声明 · 不发送键鼠动作" : `动作 ${index + 1} · ${describeUnifiedAction(action)}`;
    card.append(line);
    if (!action.done) {
      const attempts = (execution.executor_requests || []).filter(item => item.action_index === index + 1);
      if (attempts.length) {
        attempts.forEach(item => {
          const request = item.request || {};
          const target = request.backend === "cua" ? "Cua" : request.backend === "win32" ? "Win32" : request.backend || "执行器";
          const mode = {background: "后台", foreground: "前台", local: "本地", control: "目标控制"}[request.delivery_mode] || "";
          const coords = request.backend === "cua" && Number.isFinite(request.args?.x) && Number.isFinite(request.args?.y)
            ? ` · 像素坐标 ${request.args.x}、${request.args.y}` : "";
          const states = {delivered: "已送达", refused: "明确拒绝，未送达", not_sent: "未发送", unknown: "结果未知", attempted: "已调用，送达待确认"};
          const detail = document.createElement("p");
          detail.textContent = `执行器收到：${target} ${mode} ${request.operation || "动作"}${coords} · ${states[item.status] || item.status}`;
          card.append(detail);
        });
      } else {
        const unsent = document.createElement("p");
        unsent.textContent = `未发送到执行器${index === (execution.steps?.length || 0) && execution.reason ? `：${execution.reason}` : "（本轮未进入此动作）"}`;
        card.append(unsent);
      }
      const step = execution.steps?.[index];
      if (step?.receipt) {
        const receipt = document.createElement("p");
        receipt.textContent = `回执：${step.receipt.effect === "confirmed" ? "驱动确认效果" : "输入已送达，实际效果待看下一张截图"}${step.receipt.screen_position ? ` · 屏幕像素 ${step.receipt.screen_position.join("、")}` : ""}`;
        card.append(receipt);
      }
    }
    flow.append(card);
  });
  return flow;
}

function modelRoundImages(round) {
  const images = [];
  if (round.screenshot) images.push({path: round.screenshot, label: "当前截图 · 本轮操作依据"});
  if (round.previous_screenshot) images.push({path: round.previous_screenshot, label: "对照截图 · 上一次动作前，仅供比较"});
  const attachments = (round.input?.messages || []).flatMap(message =>
    Array.isArray(message.content) ? message.content : []).filter(part =>
      ["image", "image_url", "localImage", "input_image"].includes(part.type)).length;
  const count = attachments || images.length;
  const missing = Math.max(0, count - images.length);
  const summary = count ? `本轮图片：${count} 张 · 可预览 ${images.length} 张`
    + (missing ? `；${missing} 张缺少预览路径，请查看 I/O 归档` :
      round.previous_screenshot ? "；包含当前截图和动作前对照图" : "；无动作前对照图")
    : "本轮日志未记录图片信息，无法确认是否发送截图";
  return {images, summary};
}

function makeModelChat(rounds) {
  const chat = document.createElement("div");
  if (!Array.isArray(rounds) || !rounds.length) {
    chat.textContent = "任务开始后，这里会按轮次显示模型输入、回答和执行结果。";
    return chat;
  }
  rounds.forEach((round, index) => {
    const group = document.createElement("section");
    group.className = "model-chat-round";
    const title = document.createElement("h4");
    const step = Number.isInteger(round.step_index) ? round.step_index + 1 : index + 1;
    title.textContent = `第 ${step} 轮 · ${round.purpose === "verify_completion" ? "只读完成核验" : modelIoStatusLabel(round)} · ${formatModelDuration(modelIoDuration(round))}`;
    group.append(title);
    const messages = round.input?.messages || [];
    if (round.system_managed_externally || round.provider === "codex") {
      group.append(makeChatBubble("system", "Codex 内置系统指令 · 未由 App Server 返回",
        "以下展示本程序实际发送的任务文字；Codex 内置系统指令不能从当前接口读取，也未伪装成已归档内容。"));
    }
    if (messages.length) {
      messages.forEach((message, messageIndex) => {
        const content = Array.isArray(message.content)
          ? message.content.map(part => part.type === "text" ? part.text :
            ["image", "image_url", "localImage", "input_image"].includes(part.type)
              ? "[图片附件：用途及预览见下方“本轮图片”]" : modelIoText(part)).join("\n\n")
          : message.content;
        const labels = {system: "系统消息 · 实际发送", user: "输入给模型 · 实际发送",
          assistant: "历史模型回答 · 实际发送"};
        group.append(makeChatBubble(message.role, `${labels[message.role] || message.role} · ${messageIndex + 1}/${messages.length}`, content));
      });
    } else {
      group.append(makeChatBubble("user", "输入给模型",
        round.status === "pending" ? "模型请求正在准备中…" : round.input?.task || "输入见归档"));
    }
    const imageInfo = modelRoundImages(round);
    const imageSummary = document.createElement("p");
    imageSummary.className = "field-help";
    imageSummary.textContent = imageInfo.summary;
    group.append(imageSummary);
    const gallery = document.createElement("div");
    gallery.className = "model-chat-images";
    imageInfo.images.forEach((item) => {
      const figure = document.createElement("figure");
      const caption = document.createElement("figcaption");
      caption.textContent = item.label;
      const link = document.createElement("a");
      link.href = `/api/local-image?path=${encodeURIComponent(item.path)}`;
      link.target = "_blank";
      link.rel = "noopener";
      const image = document.createElement("img");
      image.className = "model-chat-image";
      image.alt = `第 ${step} 轮 ${item.label}`;
      image.loading = "lazy";
      image.src = link.href;
      image.addEventListener("error", () => {
        caption.textContent = `${item.label} · 图片加载失败，文件可能已移动或删除`;
      });
      link.append(image);
      figure.append(caption, link);
      gallery.append(figure);
    });
    group.append(gallery);
    if (round.raw_output || round.error || round.status !== "pending") {
      group.append(makeChatBubble("assistant", round.raw_output_kind === "native_structured_actions"
        ? "模型原生结构化输出 · 未经过统一协议转换" : "模型回答 · 原文",
        round.raw_output || round.error || "模型未返回可用回答"));
    }
    const actionFlow = makeActionFlow(round.execution);
    if (actionFlow) group.append(actionFlow);
    if ((round.execution && !actionFlow) || round.verification) {
      const receipt = round.execution || round.verification;
      group.append(makeChatBubble("executor", round.verification ? "只读完成核验" : "执行器回执 · 非任务完成证明", receipt));
    }
    chat.append(group);
  });
  return chat;
}

function renderLibrary() {
  elements.taskpackCount.textContent = `${taskpacks.length} 项`;
  elements.candidateCount.textContent = `${candidates.length} 项`;
  elements.recordingCount.textContent = `${recordings.length} 项`;
  elements.taskpackList.replaceChildren();
  elements.candidateList.replaceChildren();
  elements.recordingList.replaceChildren();
  taskDetailFragments.clear();

  if (!taskpacks.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "还没有本地 Windows 经验";
    elements.taskpackList.append(empty);
  }
  taskpacks.forEach((task) => {
    const item = document.createElement("article");
    item.className = "library-item task-summary-card";
    item.tabIndex = 0;
    item.setAttribute("role", "link");
    const top = document.createElement("div");
    top.className = "library-item-top";
    const nameBlock = document.createElement("div");
    const title = document.createElement("div");
    title.className = "library-title";
    title.textContent = task.task_id;
    const subtitle = document.createElement("div");
    subtitle.className = "library-subtitle";
    subtitle.textContent = task.semantic_experience?.goal || task.instruction || "未设置任务目标";
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
      semantic.textContent = `${task.semantic_experience.state_count} 个状态 · ${task.semantic_experience.transition_count} 条转移`;
      tags.append(semantic);
      const compiler = document.createElement("span");
      compiler.className = "mini-tag";
      const compilerModel = modelLabels[task.semantic_experience.model]
        || task.semantic_experience.model;
      const compilerEffort = effortLabels[task.semantic_experience.reasoning_effort]
        || task.semantic_experience.reasoning_effort;
      compiler.textContent = `由 ${compilerModel} / ${compilerEffort} 编译`;
      tags.append(compiler);
      const compilerVariant = document.createElement("span");
      const narrationKind = task.semantic_experience.narration_kind || "none";
      compilerVariant.className = `mini-tag ${narrationKind === "human" ? "info" : ""}`;
      compilerVariant.textContent = narrationKind === "human"
        ? "人类讲解编译"
        : (narrationKind === "task_instruction" ? "任务说明辅助编译" : "纯 Trace 编译");
      tags.append(compilerVariant);
      const completion = document.createElement("span");
      completion.className = `mini-tag ${task.semantic_experience.completion.mode === "cycle" ? "info" : ""}`;
      completion.textContent = task.semantic_experience.completion.mode === "cycle"
        ? "循环完成 · 必须离开再返回"
        : "终态完成";
      tags.append(completion);
      const motorPolicy = document.createElement("span");
      motorPolicy.className = "mini-tag info";
      motorPolicy.textContent = "原始坐标已隔离";
      tags.append(motorPolicy);
    } else {
      const missingSemantic = document.createElement("span");
      missingSemantic.className = "mini-tag warn";
      missingSemantic.textContent = "尚无语义编译";
      tags.append(missingSemantic);
    }
    if (task.human_guidance) {
      const guidance = document.createElement("span");
      guidance.className = "mini-tag guidance";
      guidance.textContent = `人工诀窍 v${task.human_guidance.revision} · ${task.human_guidance.rule_count} 条`;
      guidance.title = task.human_guidance.summary;
      tags.append(guidance);
    }
    if (task.guidance_review_pending?.length) {
      const pending = document.createElement("span");
      pending.className = "mini-tag warn";
      pending.textContent = `${task.guidance_review_pending.length} 条旧规则暂停生效 · 待复核`;
      tags.append(pending);
    }

    let storyboard = null;
    if (task.semantic_experience) {
      storyboard = document.createElement("details");
      storyboard.className = "semantic-storyboard";
      const summary = document.createElement("summary");
      summary.textContent = `查看任务状态图 v${task.semantic_experience.revision || 0} · ${task.semantic_experience.summary}`;
      const stages = document.createElement("div");
      stages.className = "semantic-stages";
      const canonicalInstruction = document.createElement("p");
      canonicalInstruction.className = "semantic-contract";
      canonicalInstruction.textContent = `标准任务说明：${task.semantic_experience.canonical_instruction}`;
      const completionPolicy = document.createElement("p");
      completionPolicy.className = "semantic-contract";
      completionPolicy.textContent = `完成条件：${task.semantic_experience.completion.success_condition}（${task.semantic_experience.completion.reason}）`;
      stages.append(canonicalInstruction, completionPolicy);
      const graph = document.createElement("div");
      graph.className = "semantic-stages";
      task.semantic_experience.states.forEach((state) => {
        const stateItem = document.createElement("div");
        stateItem.className = "semantic-stage";
        const stateCopy = document.createElement("div");
        const stateTitle = document.createElement("strong");
        stateTitle.textContent = `${state.id === task.semantic_experience.entry_state_id ? "入口 · " : ""}${state.name} (${state.id})`;
        const stateDescription = document.createElement("p");
        stateDescription.textContent = state.description;
        const outgoing = document.createElement("p");
        outgoing.className = "semantic-uncertain";
        outgoing.textContent = state.outgoing.length
          ? `允许转移：${state.outgoing.map((edge) => `${edge.action_goal} → ${edge.target_id}（${edge.condition}）`).join("；")}`
          : "没有普通出边";
        stateCopy.append(stateTitle, stateDescription, outgoing);
        stateItem.append(stateCopy);
        graph.append(stateItem);
      });
      task.semantic_experience.terminals.forEach((terminal) => {
        const terminalItem = document.createElement("div");
        terminalItem.className = "semantic-stage";
        const title = document.createElement("strong");
        title.textContent = `${terminal.kind === "success" ? "成功终止" : "失败终止"} · ${terminal.name}`;
        const condition = document.createElement("p");
        condition.textContent = terminal.condition;
        terminalItem.append(title, condition);
        graph.append(terminalItem);
      });
      const graphDetails = document.createElement("details");
      graphDetails.className = "narration-claims";
      const graphSummary = document.createElement("summary");
      graphSummary.textContent = `查看有向状态图 · 允许分支、循环和回退`;
      graphDetails.append(graphSummary, graph);
      stages.append(graphDetails);
      task.semantic_experience.stages.forEach((stage, index) => {
        const stageItem = document.createElement("div");
        stageItem.className = "semantic-stage";
        const evidencePair = document.createElement("div");
        evidencePair.className = "semantic-stage-evidence-pair";
        [
          ["前置", stage.evidence_before],
          ["结果", stage.evidence_after || stage.evidence_frame],
        ].forEach(([label, source]) => {
          const evidenceBlock = document.createElement("div");
          evidenceBlock.className = "semantic-stage-evidence";
          const evidenceLabel = document.createElement("span");
          evidenceLabel.textContent = label;
          const evidence = document.createElement("img");
          evidence.className = "semantic-stage-frame";
          evidence.loading = "lazy";
          evidence.src = `/api/local-image?path=${encodeURIComponent(source)}`;
          evidence.alt = `${stage.name} 的 Trace ${label}证据`;
          evidenceBlock.append(evidenceLabel, evidence);
          evidencePair.append(evidenceBlock);
        });
        const stageTitle = document.createElement("strong");
        stageTitle.textContent = `${index + 1}. ${stage.name} · ${Math.round(stage.confidence * 100)}%`;
        const transition = document.createElement("p");
        transition.textContent = `${stage.state_before} → ${stage.intent} → ${stage.state_after}`;
        const stageCopy = document.createElement("div");
        stageCopy.append(stageTitle, transition);
        stageItem.append(evidencePair, stageCopy);
        if (stage.dynamic_decisions.length) {
          const decisions = document.createElement("p");
          decisions.className = "semantic-uncertain";
          decisions.textContent = `动态决定：${stage.dynamic_decisions.map((item) => item.description).join("；")}`;
          stageCopy.append(decisions);
        }
        stages.append(stageItem);
      });
      if (task.semantic_experience.narration_claims?.length) {
        const claimDetails = document.createElement("details");
        claimDetails.className = "narration-claims";
        const claimSummary = document.createElement("summary");
        const supported = task.semantic_experience.narration_claims.filter(
          (claim) => claim.verdict === "supported",
        ).length;
        claimSummary.textContent = `查看口语声明审计 · ${supported} 条有 Trace 支撑 · 不直接变成运行指令`;
        const claimList = document.createElement("div");
        claimList.className = "narration-claim-list";
        const verdictLabels = {
          supported: "有证据支持",
          advisory: "仅作参考",
          rejected: "已拒绝",
        };
        task.semantic_experience.narration_claims.forEach((claim) => {
          const claimItem = document.createElement("div");
          claimItem.className = `narration-claim ${claim.verdict}`;
          const claimTitle = document.createElement("strong");
          claimTitle.textContent = `${verdictLabels[claim.verdict] || claim.verdict} · ${claim.type} · 动作 ${claim.action_range[0]}–${claim.action_range[1]}`;
          const claimText = document.createElement("p");
          claimText.textContent = claim.text;
          const claimReason = document.createElement("p");
          claimReason.className = "semantic-uncertain";
          claimReason.textContent = `判断：${claim.reason}（${Math.round(claim.confidence * 100)}%）`;
          claimItem.append(claimTitle, claimText, claimReason);
          claimList.append(claimItem);
        });
        claimDetails.append(claimSummary, claimList);
        stages.append(claimDetails);
      }
      if (task.semantic_experience.history?.length) {
        const historyDetails = document.createElement("details");
        historyDetails.className = "narration-claims";
        const historySummary = document.createElement("summary");
        historySummary.textContent = `查看任务模型版本 · ${task.semantic_experience.history.length} 版`;
        const historyList = document.createElement("div");
        historyList.className = "semantic-stages";
        task.semantic_experience.history.forEach((revision) => {
          const row = document.createElement("div");
          row.className = "semantic-stage";
          const heading = document.createElement("strong");
          heading.textContent = `v${revision.revision}${revision.is_active ? " · 当前启用" : ""} · ${revision.state_count} 状态 / ${revision.transition_count} 转移`;
          const copy = document.createElement("p");
          copy.textContent = revision.feedback || revision.summary || "初始 Compiler 版本";
          row.append(heading, copy);
          historyList.append(row);
        });
        historyDetails.append(historySummary, historyList);
        stages.append(historyDetails);
      }
      storyboard.append(summary, stages);
    }

    let guidanceDetails = null;
    if (task.human_guidance) {
      guidanceDetails = document.createElement("details");
      guidanceDetails.className = "semantic-storyboard guidance-storyboard";
      const guidanceSummary = document.createElement("summary");
      const history = task.human_guidance.history?.length
        ? task.human_guidance.history
        : [{
            revision: task.human_guidance.revision,
            summary: task.human_guidance.summary,
            rule_count: task.human_guidance.rule_count,
            rules: task.human_guidance.rules || [],
            merge_mode: "legacy_snapshot",
            operations: [],
            feedback: "",
            is_active: true,
          }];
      guidanceSummary.textContent = `查看经验融合记录 · 当前 v${task.human_guidance.revision} · 共 ${history.length} 版`;
      if (task.human_guidance.inheritance) {
        guidanceSummary.textContent += ` · 继承自 ${task.human_guidance.inheritance.source_task_id}`;
        const renamed = task.human_guidance.inheritance.renamed_local_rules?.length || 0;
        if (renamed) {
          guidanceSummary.textContent += ` · ${renamed} 条本地规则已保留并重新编号`;
        }
      }
      const runtimeNote = document.createElement("div");
      runtimeNote.className = "guidance-runtime-note";
      const runtimeTitle = document.createElement("strong");
      runtimeTitle.textContent = "运行 Agent 实际读取什么";
      const runtimeCopy = document.createElement("p");
      runtimeCopy.textContent = "第一次规划读取当前生效的合并摘要和全部当前 trick；之后始终读取合并摘要，但只检索全局规则、当前状态规则、当前可走转移规则和候选终态规则。";
      const auditCopy = document.createElement("p");
      auditCopy.textContent = "旧版本、本轮人工反馈、增量操作及其原因只用于审查和追溯，不会直接输入运行 Agent。";
      runtimeNote.append(runtimeTitle, runtimeCopy, auditCopy);
      const timeline = document.createElement("div");
      timeline.className = "guidance-timeline";
      const operationLabels = {
        keep: "保留",
        add: "新增",
        update: "修改",
        deprecate: "废弃",
        conflict: "冲突",
      };
      history.forEach((revision) => {
        const revisionDetails = document.createElement("details");
        revisionDetails.className = `guidance-revision${revision.is_active ? " active" : ""}`;
        const revisionSummary = document.createElement("summary");
        const modeLabel = revision.merge_mode === "incremental"
          ? `增量融合自 v${revision.parent_revision}`
          : "旧版整包替换";
        revisionSummary.textContent = `v${revision.revision} · ${revision.is_active ? "Agent 当前使用" : "历史存档，Agent 不读取"} · ${modeLabel} · ${revision.rule_count} 条`;
        const revisionBody = document.createElement("div");
        revisionBody.className = "guidance-revision-body";
        const runtimeStatus = document.createElement("div");
        runtimeStatus.className = `guidance-runtime-status ${revision.is_active ? "active" : "archived"}`;
        runtimeStatus.textContent = revision.is_active
          ? "运行输入：本版本的合并摘要始终输入；规则按当前状态图对象动态检索。"
          : "审计存档：此版本的摘要和规则不会输入运行 Agent。";
        const summaryText = document.createElement("p");
        summaryText.className = "guidance-revision-summary";
        summaryText.textContent = `${revision.is_active ? "运行 Agent 使用的合并摘要" : "历史合并摘要"}：${revision.summary || "未记录版本摘要"}`;
        revisionBody.append(runtimeStatus, summaryText);
        if (revision.feedback) {
          const feedback = document.createElement("p");
          feedback.className = "guidance-feedback";
          feedback.textContent = `融合证据（Agent 不直接读取）· 本轮人工反馈：${revision.feedback}`;
          revisionBody.append(feedback);
        }
        if (revision.merge_mode === "incremental") {
          const changes = document.createElement("div");
          changes.className = "guidance-changes";
          const changesTitle = document.createElement("strong");
          changesTitle.textContent = "增量融合审计（Agent 不直接读取）";
          changes.append(changesTitle);
          (revision.operations || []).forEach((operation) => {
            const change = document.createElement("p");
            const ruleId = operation.result_rule_id || operation.target_rule_id || "新规则";
            change.textContent = `${operationLabels[operation.operation] || operation.operation} ${ruleId}（${guidanceScopeLabel(operation.scope)}）：${operation.reason}`;
            changes.append(change);
          });
          revisionBody.append(changes);
        } else {
          const legacy = document.createElement("p");
          legacy.className = "semantic-uncertain guidance-legacy-note";
          legacy.textContent = "该版本由 V0.8/V0.9 生成，是独立规则快照，不代表已经融合上一版。";
          revisionBody.append(legacy);
        }
        const rules = document.createElement("div");
        rules.className = "semantic-stages guidance-rule-list";
        const rulesTitle = document.createElement("strong");
        rulesTitle.className = "guidance-rules-title";
        rulesTitle.textContent = revision.is_active
          ? "当前生效 trick · 命中绑定对象时输入 Agent"
          : "历史 trick 快照 · Agent 不读取";
        rules.append(rulesTitle);
        (revision.rules || []).forEach((rule, index) => {
          const ruleItem = document.createElement("div");
          ruleItem.className = `semantic-stage guidance-rule ${revision.is_active ? "runtime-active" : "archived"}`;
          const ruleTitle = document.createElement("strong");
          ruleTitle.textContent = `${index + 1}. ${rule.id || "未命名规则"} · ${guidanceScopeLabel(rule.scope)} · ${rule.priority}`;
          const binding = guidanceRuntimeBinding(rule.scope, task.semantic_experience);
          const bindingBox = document.createElement("div");
          bindingBox.className = "guidance-binding";
          const bindingTitle = document.createElement("strong");
          bindingTitle.textContent = `Agent 使用对象：${binding.label}`;
          const bindingDescription = document.createElement("p");
          bindingDescription.textContent = binding.description;
          const bindingTrigger = document.createElement("p");
          bindingTrigger.textContent = `输入时机：${binding.trigger}`;
          bindingBox.append(bindingTitle, bindingDescription, bindingTrigger);
          const fields = document.createElement("div");
          fields.className = "guidance-rule-fields";
          fields.append(
            makeGuidanceField("触发条件 when", rule.when),
            makeGuidanceField("优先策略 prefer", rule.prefer),
            makeGuidanceField("避免操作 avoid", rule.avoid?.join("；") || "无"),
            makeGuidanceField(
              "重新规划条件 replan_when",
              rule.replan_when?.join("；") || "无",
            ),
            makeGuidanceField("预期效果 expected_effect", rule.expected_effect),
            makeGuidanceField("优先级 priority", rule.priority),
          );
          ruleItem.append(ruleTitle, bindingBox, fields);
          rules.append(ruleItem);
        });
        revisionBody.append(rules);
        revisionDetails.append(revisionSummary, revisionBody);
        timeline.append(revisionDetails);
      });
      guidanceDetails.append(guidanceSummary, runtimeNote, timeline);
    }

    const actions = document.createElement("div");
    actions.className = "library-actions";
    actions.append(makeMiniButton("查看详情", () => openTaskDetail(task), true));
    if (isMessagingTask && task.missing_message_capabilities.length) {
      actions.append(makeMiniButton("补齐消息能力", () => upgradeTask(task), true));
    }
    if (!task.confirmed) {
      actions.append(makeMiniButton("确认经验", () => confirmTask(task)));
    }
    actions.append(makeMiniButton("在本地查看", () => openLocal(task.local_path)));
    if (task.human_guidance) {
      const deleteGuidanceButton = makeMiniButton(
        "删除人工反馈经验",
        () => deleteHumanGuidance(task),
      );
      deleteGuidanceButton.classList.add("danger");
      actions.append(deleteGuidanceButton);
    }
    const deleteTaskButton = makeMiniButton("删除整个任务", () => deleteTaskpack(task));
    deleteTaskButton.classList.add("danger");
    actions.append(deleteTaskButton);
    item.append(top, tags);
    item.append(actions);
    item.addEventListener("click", (event) => {
      if (!event.target.closest("button, a, select, input, textarea")) {
        openTaskDetail(task);
      }
    });
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openTaskDetail(task);
      }
    });
    taskDetailFragments.set(task.path, {
      storyboard,
      guidanceDetails,
      tags: tags.cloneNode(true),
    });
    elements.taskpackList.append(item);
  });

  if (!candidates.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "执行并留下轨迹后，会在这里生成可反馈运行";
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
    subtitle.textContent = `${formatTimestamp(candidate.created_at)} · ${candidate.instruction || "未记录本次指令"}`;
    const tags = document.createElement("div");
    tags.className = "library-tags";
    const status = document.createElement("span");
    status.className = `mini-tag ${candidate.status === "feedback_applied" ? "ok" : "warn"}`;
    status.textContent = candidate.status === "feedback_applied" ? "已用于修订" : "待反馈";
    const metrics = document.createElement("span");
    metrics.className = "mini-tag";
    metrics.textContent = `${candidate.metrics?.executed_actions || 0} 步 · ${candidate.metrics?.replans || 0} 次模型 · 平均 ${candidate.metrics?.average_batch_size || 0} 步/批`;
    const outcome = document.createElement("span");
    const taskComplete = candidate.outcome?.task_complete;
    outcome.className = `mini-tag ${taskComplete === true ? "ok" : "warn"}`;
    outcome.textContent = taskComplete === true
      ? candidate.outcome?.verified === true
        ? "效果已独立验证"
        : candidate.outcome?.verification_outcome === "completed_unverified"
          ? "已完成 · 尚未独立验证"
          : "任务完成"
      : taskComplete === false
        ? candidate.outcome?.verification_outcome === "reconciliation_required"
          ? "结果冲突 · 需要协调"
          : `任务未完成${candidate.outcome?.stop_reason ? ` · ${candidate.outcome.stop_reason}` : ""}`
        : "历史运行 · 结果未记录";
    if (candidate.outcome?.failure_message) {
      outcome.title = candidate.outcome.failure_message;
    }
    tags.append(status, outcome, metrics);
    if (candidate.waa) {
      const experiment = document.createElement("span");
      experiment.className = "mini-tag info";
      experiment.textContent = `WAA · ${candidate.waa.condition_label || waaConditionLabel(candidate.waa.condition)} · 第 ${candidate.waa.repetition || 1} 次`;
      tags.append(experiment);
    }
    if ((candidate.metrics?.visual_checkpoints || 0) > 0) {
      const checkpoints = document.createElement("span");
      checkpoints.className = `mini-tag ${(candidate.metrics?.visual_checkpoint_failures || 0) > 0 ? "warn" : "ok"}`;
      checkpoints.textContent = `${candidate.metrics.visual_checkpoints} 次本地视觉检查 · ${candidate.metrics?.visual_checkpoint_failures || 0} 次异常`;
      tags.append(checkpoints);
    }
    if ((candidate.metrics?.interrupted_batches || 0) > 0) {
      const interrupted = document.createElement("span");
      interrupted.className = "mini-tag warn";
      interrupted.textContent = `${candidate.metrics.interrupted_batches} 次批次中断`;
      tags.append(interrupted);
    }
    if (candidate.revision) {
      const revisionTag = document.createElement("span");
      const hasConflicts = (candidate.revision.conflict_count || 0) > 0;
      revisionTag.className = `mini-tag ${candidate.revision.status === "confirmed" ? "ok" : "warn"}`;
      revisionTag.textContent = candidate.revision.status === "confirmed"
        ? `诀窍 v${candidate.revision.confirmed_revision} 已启用`
        : hasConflicts
          ? `融合草稿 · ${candidate.revision.conflict_count} 个冲突`
          : `融合草稿 v${candidate.revision.base_revision || 0} → v${candidate.revision.proposed_revision} · ${candidate.revision.rule_count} 条有效规则`;
      tags.append(revisionTag);
    }
    if (candidate.task_model_revision) {
      const taskModelTag = document.createElement("span");
      const blocked = (candidate.task_model_revision.blocking_issue_count || 0) > 0;
      taskModelTag.className = `mini-tag ${candidate.task_model_revision.status === "confirmed" ? "ok" : "warn"}`;
      taskModelTag.textContent = candidate.task_model_revision.status === "confirmed"
        ? `任务图 v${candidate.task_model_revision.confirmed_revision} 已启用`
        : blocked
          ? `任务图草稿 · ${candidate.task_model_revision.blocking_issue_count} 个映射冲突`
          : `任务图草稿 v${candidate.task_model_revision.base_revision || 0} → v${candidate.task_model_revision.proposed_revision}`;
      tags.append(taskModelTag);
    }
    let revisionPanel = null;
    if (candidate.status !== "feedback_applied") {
      revisionPanel = document.createElement("div");
      revisionPanel.className = "candidate-feedback";
      if (candidate.revision?.status === "draft") {
        const changes = candidate.revision.changes || [];
        const changeDetails = document.createElement("details");
        changeDetails.className = "semantic-storyboard guidance-storyboard";
        const changeSummary = document.createElement("summary");
        changeSummary.textContent = `查看本轮融合变化 · ${changes.length} 项`;
        const changeList = document.createElement("div");
        changeList.className = "semantic-stages";
        const operationLabels = {
          keep: "保留",
          add: "新增",
          update: "修改",
          deprecate: "废弃",
          conflict: "冲突",
        };
        changes.forEach((change) => {
          const row = document.createElement("div");
          row.className = "semantic-stage";
          const heading = document.createElement("strong");
          const ruleId = change.result_rule_id || change.target_rule_id || "新规则";
          heading.textContent = `${operationLabels[change.operation] || change.operation} · ${ruleId} · ${guidanceScopeLabel(change.scope)}`;
          const reason = document.createElement("p");
          reason.textContent = change.reason || "未记录原因";
          row.append(heading, reason);
          changeList.append(row);
        });
        changeDetails.append(changeSummary, changeList);
        const proposalLabel = document.createElement("label");
        proposalLabel.className = "candidate-feedback-label";
        proposalLabel.textContent = "融合后的经验摘要（确认前可编辑）";
        const proposal = document.createElement("textarea");
        proposal.className = "candidate-feedback-input candidate-summary-input";
        proposal.maxLength = 1000;
        proposal.rows = 3;
        proposal.value = candidate.revision.summary || "";
        const proposalActions = document.createElement("div");
        proposalActions.className = "library-actions";
        const saveButton = makeMiniButton(
          "保存摘要修改",
          (button) => saveCandidateRevisionSummary(candidate, proposal, button),
        );
        const confirmButton = makeMiniButton(
          (candidate.revision.conflict_count || 0) > 0
            ? "存在冲突，补充反馈后再确认"
            : "确认启用融合经验",
          () => confirmCandidateRevision(candidate, proposal),
          true,
        );
        if ((candidate.revision.conflict_count || 0) > 0) {
          confirmButton.disabled = true;
          confirmButton.title = "Revision Agent 发现新旧规则冲突，系统不会静默覆盖旧规则";
        }
        proposalActions.append(saveButton, confirmButton);
        revisionPanel.append(changeDetails, proposalLabel, proposal);
        attachVoiceInput(proposal, "融合后的经验摘要");
        revisionPanel.append(proposalActions);
      }
      const feedbackLabel = document.createElement("label");
      feedbackLabel.className = "candidate-feedback-label";
      feedbackLabel.textContent = candidate.revision
        ? "补充反馈并重新融合（已启用规则默认保留）"
        : "告诉 Agent 这次运行应该怎样改进";
      const feedback = document.createElement("textarea");
      feedback.className = "candidate-feedback-input";
      feedback.maxLength = 2000;
      feedback.rows = 3;
      feedback.placeholder = candidate.revision
        ? "例如：保留等待规则，但把成功判断改为检查绿色完成标记。"
        : "例如：攻击按钮出现后连续选择三张卡，不要每点一次都重新规划。";
      const feedbackActions = document.createElement("div");
      feedbackActions.className = "library-actions";
      feedbackActions.append(
        makeMiniButton(
          candidate.revision ? "重新融合反馈" : "生成融合草稿",
          (button) => reviseCandidate(candidate, feedback, button),
          true,
        ),
      );
      revisionPanel.append(feedbackLabel, feedback);
      attachVoiceInput(feedback, "人工运行反馈");
      revisionPanel.append(feedbackActions);
    }
    const taskModelPanel = document.createElement("div");
    taskModelPanel.className = "candidate-feedback";
    if (candidate.task_model_revision?.status === "draft") {
      const proposal = candidate.task_model_revision;
      const changeDetails = document.createElement("details");
      changeDetails.className = "semantic-storyboard guidance-storyboard";
      const changeSummary = document.createElement("summary");
      changeSummary.textContent = `查看任务结构差异 · ${proposal.operation_count || 0} 项`;
      const changeList = document.createElement("div");
      changeList.className = "semantic-stages";
      (proposal.operations || []).forEach((change) => {
        const row = document.createElement("div");
        row.className = "semantic-stage";
        const heading = document.createElement("strong");
        heading.textContent = `${change.operation} · ${change.target_id || "任务级设置"}`;
        row.append(heading);
        changeList.append(row);
      });
      (proposal.blocking_issues || []).forEach((issue) => {
        const row = document.createElement("div");
        row.className = "semantic-stage";
        const warning = document.createElement("strong");
        warning.textContent = `阻塞：${issue}`;
        row.append(warning);
        changeList.append(row);
      });
      const review = proposal.guidance_review;
      if (review) {
        const retained = document.createElement("div");
        retained.className = "semantic-stage";
        retained.textContent = `继续生效的人工规则：${(review.carried_rule_ids || []).join("、") || "无"}`;
        changeList.append(retained);
        (review.pending || []).forEach((item) => {
          const row = document.createElement("div");
          row.className = "semantic-stage";
          row.textContent = `待人工复核（不会给 Agent）：${item.rule?.id || "未知规则"} · ${item.reason}`;
          changeList.append(row);
        });
      }
      changeDetails.append(changeSummary, changeList);
      const confirmActions = document.createElement("div");
      confirmActions.className = "library-actions";
      const confirm = makeMiniButton(
        (proposal.blocking_issue_count || 0) > 0 ? "先解决 Guidance 映射冲突" : "确认启用任务状态图",
        () => confirmTaskModelRevision(candidate),
        true,
      );
      if ((proposal.blocking_issue_count || 0) > 0) {
        confirm.disabled = true;
      }
      confirmActions.append(confirm);
      taskModelPanel.append(changeDetails, confirmActions);
    }
    const structureLabel = document.createElement("label");
    structureLabel.className = "candidate-feedback-label";
    structureLabel.textContent = candidate.task_model_revision
      ? "补充任务结构反馈并重新生成草稿"
      : "修正 Compiler 的阶段、状态、转移或结束条件";
    const structureFeedback = document.createElement("textarea");
    structureFeedback.className = "candidate-feedback-input";
    structureFeedback.maxLength = 2000;
    structureFeedback.rows = 3;
    structureFeedback.placeholder = "例如：战斗状态不是线性阶段；技能不足时应从攻击选择回到技能处理，战斗胜利是独立终止状态。";
    const structureActions = document.createElement("div");
    structureActions.className = "library-actions";
    structureActions.append(
      makeMiniButton(
        candidate.task_model_revision ? "重新生成任务图草稿" : "生成任务图修订草稿",
        (button) => reviseTaskModel(candidate, structureFeedback, button),
        true,
      ),
    );
    taskModelPanel.append(structureLabel, structureFeedback);
    attachVoiceInput(structureFeedback, "任务结构反馈");
    taskModelPanel.append(structureActions);
    const actions = document.createElement("div");
    actions.className = "library-actions";
    if (candidate.review_timeline) {
      actions.append(
        makeMiniButton("查看轨迹与截图", () => openCandidateReview(candidate), true),
      );
    }
    actions.append(makeMiniButton("在本地查看", () => openLocal(candidate.local_path)));
    const deleteCandidateButton = makeMiniButton("删除", () => deleteCandidate(candidate));
    deleteCandidateButton.classList.add("danger");
    actions.append(deleteCandidateButton);
    item.append(title, subtitle, tags);
    const performanceTimeline = makePerformanceTimeline(
      candidate.metrics?.performance,
      candidate.metrics?.stage_timings || [],
    );
    if (performanceTimeline) item.append(performanceTimeline);
    if (revisionPanel) item.append(revisionPanel);
    item.append(taskModelPanel);
    item.append(actions);
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
    title.textContent = `${recording.task_id || "未命名录制"}${recording.execution_scope === "desktop" ? " · 桌面录制" : ""}`;
    const subtitle = document.createElement("div");
    subtitle.className = "library-subtitle";
    subtitle.textContent = `${formatTimestamp(recording.created_at)} · ${recording.process_name || "Windows"} · ${recording.input_events} 个输入事件`;
    const tags = document.createElement("div");
    tags.className = "library-tags";
    const status = document.createElement("span");
    status.className = `mini-tag ${recording.success ? "ok" : "warn"}`;
    status.textContent = recording.success ? "录制成功" : "未完成";
    tags.append(status);
    if (recording.narrated) {
      const narrated = document.createElement("span");
      narrated.className = "mini-tag info";
      narrated.textContent = `含讲解 · ${recording.narration_chars || 0} 字`;
      tags.append(narrated);
    }
    const actions = document.createElement("div");
    actions.className = "library-actions";
    if (recording.recording_backend === "opencua") {
      const native = document.createElement("span");
      native.className = "mini-tag info";
      native.textContent = "OpenCUA 原生归档 · 暂未接 Compiler";
      tags.append(native);
      if (recording.derivation?.status === "completed") {
        const derived = document.createElement("span");
        derived.className = "mini-tag ok";
        derived.textContent = `${recording.derivation.action_count} 个动作组 · 已配图`;
        tags.append(derived);
        actions.append(makeMiniButton("打开动作图文目录", () => openLocal(recording.derivation.review_path)));
      }
    }
    if (recording.success && recording.compilation_supported !== false) {
      actions.append(
        makeMiniButton(
          "编译 / 重试",
          (button) => compileRecording(recording, button),
          true,
        ),
      );
    }
    actions.append(makeMiniButton("在本地查看", () => openLocal(recording.local_path)));
    if (recording.recording_backend !== "opencua") {
      const deleteRecordingButton = makeMiniButton("删除", () => deleteRecording(recording));
      deleteRecordingButton.classList.add("danger");
      actions.append(deleteRecordingButton);
    }
    item.append(title, subtitle, tags, actions);
    elements.recordingList.append(item);
  });
  renderTaskDetailRoute();
}

function taskDetailPathFromHash() {
  const prefix = "#task/";
  if (!window.location.hash.startsWith(prefix)) return null;
  try {
    return decodeURIComponent(window.location.hash.slice(prefix.length));
  } catch {
    return null;
  }
}

function candidateDetailPathFromHash() {
  const prefix = "#run/";
  if (!window.location.hash.startsWith(prefix)) return null;
  try {
    return decodeURIComponent(window.location.hash.slice(prefix.length));
  } catch {
    return null;
  }
}

function openTaskDetail(task) {
  window.location.hash = `task/${encodeURIComponent(task.path)}`;
  renderTaskDetailRoute();
}

function openCandidateReview(candidate) {
  window.location.hash = `run/${encodeURIComponent(candidate.local_path)}`;
  renderTaskDetailRoute();
}

function closeTaskDetail(updateHistory = true) {
  document.body.classList.remove("task-detail-mode");
  elements.taskDetailPanel.classList.add("hidden");
  if (updateHistory && (taskDetailPathFromHash() || candidateDetailPathFromHash())) {
    history.pushState(null, "", `${window.location.pathname}${window.location.search}`);
  }
}

function renderCandidateReviewRoute(candidate) {
  const review = candidate.review_timeline || {};
  document.body.classList.add("task-detail-mode", "library-mode");
  elements.taskDetailPanel.classList.remove("hidden");
  elements.taskDetailBack.textContent = "← 返回可反馈运行";
  elements.taskDetailKicker.textContent = "WAA 运行审查";
  elements.taskDetailTitle.textContent = candidate.task_id || "WAA 运行";
  elements.taskDetailMeta.textContent = [
    formatTimestamp(candidate.created_at),
    candidate.waa?.condition_label || waaConditionLabel(candidate.waa?.condition),
    `第 ${candidate.waa?.repetition || 1} 次`,
  ].join(" · ");
  elements.taskDetailTags.replaceChildren();
  [
    candidate.outcome?.task_complete ? "WAA 验证成功" : "WAA 未通过",
    `${review.round_count || 0} 个模型回合`,
    `${review.action_count || 0} 个操作`,
    `模型 ${candidate.waa?.model || "未记录"} / ${candidate.waa?.reasoning_effort || "未记录"}`,
  ].forEach((label, index) => {
    const tag = document.createElement("span");
    tag.className = `mini-tag ${index === 0 && candidate.outcome?.task_complete ? "ok" : ""}`;
    tag.textContent = label;
    elements.taskDetailTags.append(tag);
  });
  elements.taskDetailBody.replaceChildren();
  const intro = document.createElement("section");
  intro.className = "system-planning-policy run-review-intro";
  const introHeading = document.createElement("div");
  const introTitle = document.createElement("strong");
  introTitle.textContent = "人工审查视图";
  const introBadge = document.createElement("span");
  introBadge.textContent = "由 WAA 原始证据归一化";
  introHeading.append(introTitle, introBadge);
  const instruction = document.createElement("p");
  instruction.textContent = `任务：${candidate.instruction || "未记录"}`;
  const explanation = document.createElement("p");
  explanation.textContent = "每张卡片对应一次真实模型调用；同一回答中的多个操作合并展示，截图分别表示这批操作执行前和执行后。底层 traj.jsonl、逐步截图和 WAA traj.html 均未改写。";
  intro.append(introHeading, instruction, explanation);
  elements.taskDetailBody.append(intro);
  const timeline = makeCandidateReview(candidate);
  if (timeline) {
    elements.taskDetailBody.append(timeline);
  } else {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "这次 WAA 运行没有可读取的轨迹。";
    elements.taskDetailBody.append(empty);
  }
  if (candidate.status !== "feedback_applied") {
    const feedbackPanel = document.createElement("section");
    feedbackPanel.className = "candidate-feedback run-review-feedback";
    const label = document.createElement("label");
    label.className = "candidate-feedback-label";
    label.textContent = "看完轨迹后，告诉 Agent 哪些做法要保留、修改或禁止";
    const feedback = document.createElement("textarea");
    feedback.className = "candidate-feedback-input";
    feedback.rows = 4;
    feedback.maxLength = 2000;
    feedback.placeholder = "例如：搜索结果出现后直接打开记事本；保存时应先确认 Documents 路径，不要重复打开开始菜单。";
    const feedbackActions = document.createElement("div");
    feedbackActions.className = "library-actions";
    feedbackActions.append(
      makeMiniButton(
        candidate.revision ? "重新融合反馈" : "生成融合草稿",
        (button) => reviseCandidate(candidate, feedback, button),
        true,
      ),
    );
    feedbackPanel.append(label, feedback);
    attachVoiceInput(feedback, "WAA 人工运行反馈");
    feedbackPanel.append(feedbackActions);
    elements.taskDetailBody.append(feedbackPanel);
  }
  elements.taskDetailActions.replaceChildren(
    makeMiniButton("在本地查看归一化证据", () => openLocal(candidate.local_path)),
  );
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderTaskDetailRoute() {
  const candidatePath = candidateDetailPathFromHash();
  if (candidatePath) {
    const candidate = candidates.find((item) => item.local_path === candidatePath);
    if (!candidate) {
      closeTaskDetail();
      return;
    }
    renderCandidateReviewRoute(candidate);
    return;
  }
  const path = taskDetailPathFromHash();
  if (!path) {
    closeTaskDetail(false);
    return;
  }
  const task = taskpacks.find((item) => item.path === path);
  const fragments = taskDetailFragments.get(path);
  if (!task || !fragments) {
    closeTaskDetail();
    return;
  }
  document.body.classList.add("task-detail-mode", "library-mode");
  elements.taskDetailPanel.classList.remove("hidden");
  elements.taskDetailBack.textContent = "← 返回任务经验";
  elements.taskDetailKicker.textContent = "任务经验详情";
  elements.taskDetailTitle.textContent = task.task_id;
  elements.taskDetailMeta.textContent = [
    task.process_name || "Windows",
    task.title_contains || "未命名窗口",
    task.confirmed ? "已确认" : "草稿",
  ].join(" · ");
  elements.taskDetailTags.replaceChildren(
    ...[...fragments.tags.children].map((tag) => tag.cloneNode(true)),
  );
  elements.taskDetailBody.replaceChildren();
  const systemPolicy = document.createElement("section");
  systemPolicy.className = "system-planning-policy";
  const systemPolicyHeading = document.createElement("div");
  const systemPolicyTitle = document.createElement("strong");
  systemPolicyTitle.textContent = "系统执行策略 · 多动作规划";
  const systemPolicyBadge = document.createElement("span");
  systemPolicyBadge.textContent = "所有任务共用";
  systemPolicyHeading.append(systemPolicyTitle, systemPolicyBadge);
  const systemPolicyCopy = document.createElement("p");
  systemPolicyCopy.textContent = "任务未完成时，模型应返回一个有序的多动作 plan，而不是只返回下一次点击。网页运行默认最多 12 步；当前画面足以确定后续时优先规划 5–8 步，并把必要等待与等待后的确定操作放在同一计划中。遇到尚不可见的结果或未知选择时停止扩展，由新截图重新规划。";
  const systemPolicyBoundary = document.createElement("p");
  systemPolicyBoundary.textContent = "该要求由 Windows Agent 统一注入，不属于下面任何单任务 Trace、Compiler 经验或人工 trick。";
  systemPolicy.append(systemPolicyHeading, systemPolicyCopy, systemPolicyBoundary);
  elements.taskDetailBody.append(systemPolicy);
  if (fragments.storyboard) {
    fragments.storyboard.open = true;
    elements.taskDetailBody.append(fragments.storyboard);
  } else {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "这份任务还没有 Compiler Agent 语义经验。";
    elements.taskDetailBody.append(empty);
  }
  if (fragments.guidanceDetails) {
    fragments.guidanceDetails.open = true;
    elements.taskDetailBody.append(fragments.guidanceDetails);
  }
  if (task.guidance_review_pending?.length) {
    const pending = document.createElement("details");
    pending.className = "semantic-storyboard guidance-storyboard";
    pending.open = true;
    const summary = document.createElement("summary");
    summary.textContent = `暂停生效的旧规则 · ${task.guidance_review_pending.length} 条`;
    pending.append(summary);
    task.guidance_review_pending.forEach((item) => {
      const row = document.createElement("div");
      row.className = "semantic-stage";
      const rule = item.rule || {};
      row.textContent = `${rule.id || "未知规则"}：${item.reason || "待复核"}。原建议：${rule.prefer || "无"}。这些内容目前不会发送给 Agent；如仍适用，请在后续反馈中重新确认。`;
      pending.append(row);
    });
    elements.taskDetailBody.append(pending);
  }
  elements.taskDetailActions.replaceChildren();
  if (!task.confirmed) {
    elements.taskDetailActions.append(makeMiniButton("确认经验", () => confirmTask(task)));
  }
  elements.taskDetailActions.append(
    makeMiniButton("在本地查看", () => openLocal(task.local_path)),
  );
  if (task.human_guidance) {
    const deleteGuidance = makeMiniButton(
      "删除人工反馈经验",
      () => deleteHumanGuidance(task),
    );
    deleteGuidance.classList.add("danger");
    elements.taskDetailActions.append(deleteGuidance);
  }
  const deleteTask = makeMiniButton("删除整个任务", () => deleteTaskpack(task));
  deleteTask.classList.add("danger");
  elements.taskDetailActions.append(deleteTask);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function narrationMimeType() {
  if (!window.MediaRecorder) return "";
  return ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]
    .find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

async function startMicrophoneCapture() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    throw new Error("当前浏览器不支持麦克风录制");
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  const mimeType = narrationMimeType();
  const recorder = mimeType
    ? new MediaRecorder(stream, { mimeType })
    : new MediaRecorder(stream);
  const capture = {
    stream,
    recorder,
    chunks: [],
    active: true,
    blob: null,
  };
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data?.size) capture.chunks.push(event.data);
  });
  recorder.start(1000);
  return capture;
}

async function stopMicrophoneCapture(capture) {
  if (!capture || !capture.active) return capture;
  capture.active = false;
  if (capture.recorder.state !== "inactive") {
    await new Promise((resolve) => {
      capture.recorder.addEventListener("stop", resolve, { once: true });
      capture.recorder.stop();
    });
  }
  capture.stream.getTracks().forEach((track) => track.stop());
  capture.blob = new Blob(capture.chunks, {
    type: capture.recorder.mimeType || capture.chunks[0]?.type || "audio/webm",
  });
  return capture;
}

function renderLiveNarration(capture, interim = "") {
  const text = [...capture.finalParts, interim].filter(Boolean).join(" ").trim();
  elements.narrationTranscript.value = text;
}

async function startNarrationCapture() {
  const capture = await startMicrophoneCapture();
  Object.assign(capture, {
    recognition: null,
    recognitionAvailable: false,
    finalParts: [],
    segments: [],
    startedAt: performance.now(),
    startedAtEpochMs: Date.now(),
    lastSegmentEnd: 0,
    audioArchived: false,
    transcriptionEngine: null,
  });

  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (Recognition) {
    const recognition = new Recognition();
    capture.recognition = recognition;
    capture.recognitionAvailable = true;
    recognition.lang = "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onresult = (event) => {
      let interim = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const text = event.results[index][0].transcript.trim();
        if (!text) continue;
        if (event.results[index].isFinal) {
          const endMs = Math.max(0, Math.round(performance.now() - capture.startedAt));
          capture.finalParts.push(text);
          capture.segments.push({
            start_ms: capture.lastSegmentEnd,
            end_ms: endMs,
            text,
          });
          capture.lastSegmentEnd = endMs;
        } else {
          interim = `${interim} ${text}`.trim();
        }
      }
      renderLiveNarration(capture, interim);
    };
    recognition.onerror = () => {
      elements.narrationStatus.textContent = "自动转写暂不可用，录音仍在继续";
    };
    recognition.onend = () => {
      if (!capture.active) return;
      try { recognition.start(); } catch (_) { /* browser is already restarting */ }
    };
    try { recognition.start(); } catch (_) { capture.recognitionAvailable = false; }
  }
  narrationCapture = capture;
  elements.narrationTranscript.value = "";
  elements.narrationStatus.textContent = capture.recognitionAvailable
    ? "正在录音和转写"
    : "正在录音；转写不可用，结束后可手工输入";
}

async function stopNarrationCapture() {
  const capture = narrationCapture;
  if (!capture || !capture.active) return capture;
  if (capture.recognition) {
    try { capture.recognition.stop(); } catch (_) { /* already stopped */ }
  }
  await stopMicrophoneCapture(capture);
  renderLiveNarration(capture);
  return capture;
}

async function discardNarrationCapture() {
  await stopNarrationCapture();
  narrationCapture = null;
  narrationReviewJobId = null;
  narrationReviewPreparing = false;
  elements.narrationReview.classList.add("hidden");
}

async function prepareNarrationReview(job) {
  if (narrationReviewPreparing || narrationReviewJobId === job.job_id) return;
  narrationReviewPreparing = true;
  try {
    let capture = await stopNarrationCapture();
    const pendingNarration = job.result?.narration;
    if (!capture && pendingNarration?.status === "awaiting_review") {
      capture = {
        active: false,
        blob: null,
        segments: Array.isArray(pendingNarration.segments) ? pendingNarration.segments : [],
        recognitionAvailable: false,
        audioArchived: true,
        transcriptionEngine: pendingNarration.engine || "faster_whisper:turbo",
      };
      narrationCapture = capture;
      elements.narrationTranscript.value = pendingNarration.transcript || "";
    }
    narrationReviewJobId = job.job_id;
    elements.narrationReview.classList.remove("hidden");
    elements.narrationSubmit.disabled = true;
    const audioTooLarge = capture?.blob?.size > 20 * 1024 * 1024;
    if (capture?.audioArchived && !capture?.blob?.size) {
      elements.narrationStatus.textContent = "已恢复本地 Turbo 转写 · 可修改";
    } else if (audioTooLarge) {
      elements.narrationStatus.textContent = "录音超过 20 MB；保留浏览器草稿，请修改后确认";
    } else if (capture?.blob?.size) {
      elements.narrationStatus.textContent = "本地 Whisper Turbo 正在转写；首次使用需要下载模型…";
      try {
        const mimeType = capture.blob.type.split(";", 1)[0] || "audio/webm";
        const transcriptionResult = await request("/api/recordings/transcribe", {
          method: "POST",
          body: JSON.stringify({
            job_id: job.job_id,
            audio_base64: await blobToBase64(capture.blob),
            mime_type: mimeType,
          }),
        });
        const transcription = transcriptionResult.transcription || {};
        if (String(transcription.transcript || "").trim()) {
          elements.narrationTranscript.value = transcription.transcript;
        }
        capture.segments = Array.isArray(transcription.segments)
          ? transcription.segments
          : capture.segments;
        capture.transcriptionEngine = `faster_whisper:${transcription.model || "turbo"}`;
        capture.audioArchived = true;
        elements.narrationStatus.textContent =
          `Turbo 转写完成 · ${transcription.device || "本地"}/${transcription.compute_type || "自动"} · 可修改`;
      } catch (error) {
        elements.narrationStatus.textContent =
          `Turbo 转写失败，已保留浏览器草稿：${error.message}`;
      }
    } else {
      elements.narrationStatus.textContent = capture?.recognitionAvailable
        ? "没有取得录音；请检查或修改浏览器转写"
        : "没有取得录音；请手工填写讲解";
    }
    elements.narrationSubmit.disabled = false;
    elements.narrationTranscript.focus();
  } finally {
    narrationReviewPreparing = false;
  }
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("读取讲解录音失败"));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.readAsDataURL(blob);
  });
}

function appendDictation(target, transcript) {
  const spoken = String(transcript || "").trim();
  if (!spoken) throw new Error("没有识别到有效文字，请靠近麦克风后重试");
  const existing = target.value.trimEnd();
  const separator = existing ? (target.tagName === "TEXTAREA" ? "\n" : " ") : "";
  let combined = `${existing}${separator}${spoken}`;
  if (target.maxLength > 0 && combined.length > target.maxLength) {
    combined = combined.slice(0, target.maxLength);
  }
  target.value = combined;
  target.dispatchEvent(new Event("input", { bubbles: true }));
  target.dispatchEvent(new Event("change", { bubbles: true }));
  target.focus();
}

async function finishDictation(session) {
  session.button.disabled = true;
  session.button.textContent = "正在转写…";
  session.status.textContent = "本地 Whisper Turbo 正在转写；录音不会保存";
  try {
    const capture = await stopMicrophoneCapture(session.capture);
    if (!capture.blob?.size) throw new Error("没有取得麦克风录音");
    if (capture.blob.size > 20 * 1024 * 1024) {
      throw new Error("录音超过 20 MB，请缩短后重试");
    }
    const mimeType = capture.blob.type.split(";", 1)[0] || "audio/webm";
    const result = await request("/api/transcribe", {
      method: "POST",
      body: JSON.stringify({
        audio_base64: await blobToBase64(capture.blob),
        mime_type: mimeType,
        context: session.context,
      }),
    });
    const transcription = result.transcription || {};
    appendDictation(session.target, transcription.transcript);
    session.status.textContent = `Turbo 转写完成 · ${transcription.device || "本地"}/${transcription.compute_type || "自动"}`;
  } catch (error) {
    session.status.textContent = `语音输入失败：${error.message}`;
  } finally {
    session.button.disabled = false;
    session.button.textContent = "🎙 语音输入";
    session.button.classList.remove("recording");
    if (dictationSession === session) dictationSession = null;
  }
}

async function toggleDictation(target, button, status, context) {
  if (dictationSession) {
    if (dictationSession.target === target) {
      await finishDictation(dictationSession);
    } else {
      status.textContent = "请先结束另一个输入框的语音录制";
    }
    return;
  }
  try {
    const capture = await startMicrophoneCapture();
    dictationSession = { target, button, status, context, capture };
    button.textContent = "■ 结束并转写";
    button.classList.add("recording");
    status.textContent = "正在录音；说完后再次点击";
  } catch (error) {
    status.textContent = `无法开始语音输入：${error.message}`;
  }
}

function attachVoiceInput(target, context) {
  if (!target || target.dataset.voiceInputAttached === "true") return;
  target.dataset.voiceInputAttached = "true";
  const controls = document.createElement("div");
  controls.className = "voice-input-controls";
  const button = document.createElement("button");
  button.className = "voice-input-button";
  button.type = "button";
  button.textContent = "🎙 语音输入";
  button.setAttribute("aria-label", `${context}语音输入`);
  const status = document.createElement("span");
  status.className = "voice-input-status";
  status.textContent = "本地 Turbo 转写 · 不保存录音";
  button.addEventListener("click", () => toggleDictation(target, button, status, context));
  controls.append(button, status);
  target.insertAdjacentElement("afterend", controls);
}

async function submitNarration() {
  if (!narrationReviewJobId) return;
  clearRecordError();
  elements.narrationSubmit.disabled = true;
  try {
    const capture = narrationCapture;
    const keepAudio = capture?.blob?.size
      && capture.blob.size <= 20 * 1024 * 1024
      && !capture.audioArchived;
    const audioBase64 = keepAudio ? await blobToBase64(capture.blob) : null;
    const job = await request("/api/recordings/narration", {
      method: "POST",
      body: JSON.stringify({
        job_id: narrationReviewJobId,
        transcript: elements.narrationTranscript.value,
        segments: capture?.segments || [],
        audio_base64: audioBase64,
        mime_type: keepAudio ? capture.blob.type.split(";", 1)[0] : null,
        transcription_engine: capture?.transcriptionEngine
          || (capture?.recognitionAvailable ? "browser_web_speech" : "manual"),
      }),
    });
    narrationCapture = null;
    narrationReviewJobId = null;
    elements.narrationReview.classList.add("hidden");
    renderJob(job);
    pollTimer = setTimeout(pollJob, 250);
  } catch (error) {
    elements.narrationSubmit.disabled = false;
    showRecordError(error.message);
  }
}

function rsiRunIsActive(run) {
  return ["queued", "preflight", "running", "finalizing"].includes(run?.state);
}

function rsiRunBlocksNewPractice(run) {
  return rsiRunIsActive(run) || run?.state === "needs_recovery";
}

const rsiRecoveryReasonLabels = {
  completed_boundary_verified: "已验证最后一个完成项目边界。",
  cleanup_incomplete: "远端资源尚未完成清理，暂时不能恢复。",
  immutable_manifest_mismatch: "运行版本或配置与原始清单不一致，拒绝恢复。",
  official_boundary_rejected: "官方完成边界校验未通过，不能恢复。",
  no_completed_boundary: "尚无可验证的完成项目边界，不能恢复。",
  stop_requested: "该练习已请求停止，不能恢复。",
};

const rsiCodexAuthenticationRequired = "codex_authentication_required";

function rsiHasCodexAuthenticationFailure(value) {
  try {
    return JSON.stringify(value || {}).toLowerCase().includes(rsiCodexAuthenticationRequired);
  } catch (_) {
    return false;
  }
}

function rsiResultForDisplay(result) {
  if (!rsiHasCodexAuthenticationFailure(result)) return result;
  return {
    ...result,
    error_category: rsiCodexAuthenticationRequired,
    error: "Codex 授权已失效或刷新失败；本次练习未自动重试。请在远端服务器终端使用既有账号重新登录 Codex 后，刷新本页并新建练习。",
  };
}

function formatRsiMilliseconds(value) {
  if (!Number.isInteger(value) || value < 0) return "未记录";
  const seconds = Math.floor(value / 1000);
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function rsiRecoveryReasonText(recovery) {
  const reason = recovery?.reason || "recovery_not_allowed";
  return rsiRecoveryReasonLabels[reason] || `远端未允许恢复（${reason}）。`;
}

function rsiSetStatus(health, message = "") {
  const ready = health?.ready === true;
  const state = health?.connection_state || "unconfigured";
  elements.rsiStatus.className = `status-pill ${ready ? "running" : state === "unready" ? "partial" : "failed"}`;
  elements.rsiStatus.textContent = ready ? "远端可用"
    : state === "unready" ? "资源未就绪"
    : state === "unreachable" ? "连接不可用" : "未配置";
  elements.rsiMessage.textContent = message || health?.message
    || (ready ? "远端隔离练习已就绪。练习结果必须经独立 Verifier 后才会成为可审查候选。" : "远端 RSI 部署尚未就绪。");
  elements.rsiChecks.replaceChildren();
  const missing = (health?.checks || []).filter((check) => check?.ready !== true);
  if (state !== "unready" || !missing.length) {
    elements.rsiChecks.classList.add("hidden");
    return;
  }
  elements.rsiChecks.classList.remove("hidden");
  const title = document.createElement("strong");
  title.textContent = "尚未通过的远端准备检查";
  const list = document.createElement("ul");
  missing.forEach((check) => {
    const item = document.createElement("li");
    item.textContent = check.label || "未命名准备检查";
    list.append(item);
  });
  const help = document.createElement("p");
  help.textContent = "请在远端部署完成下载、Codex 授权或运行时安装后刷新；本机不会自动修复远端资源。";
  elements.rsiChecks.append(title, list, help);
}

function rsiPopulateOptions(health) {
  const replace = (element, values, preferred) => {
    const current = element.value;
    element.replaceChildren();
    values.forEach((value) => element.append(new Option(value, value)));
    element.value = values.includes(current) ? current : (values.includes(preferred) ? preferred : values[0] || "");
  };
  replace(elements.rsiModel, health?.models || [], "gpt-6-astra");
  replace(elements.rsiReasoningEffort, health?.reasoning_efforts || [], "low");
  const clampToProfileMaximum = (element, maximum) => {
    if (!Number.isInteger(maximum)) return;
    element.max = String(maximum);
    if (Number(element.value) > maximum) element.value = String(maximum);
  };
  clampToProfileMaximum(elements.rsiMaxCalls, health?.max_model_calls);
  clampToProfileMaximum(elements.rsiWallSeconds, health?.max_wall_seconds);
  clampToProfileMaximum(elements.rsiProjectBudget, health?.max_project_budget);
}

function rsiSetControls() {
  const ready = rsiHealth?.ready === true;
  const hasRemoteChoices = Boolean(elements.rsiModel.value && elements.rsiReasoningEffort.value);
  const active = rsiRuns.some(rsiRunBlocksNewPractice);
  const selected = rsiRuns.find((run) => run.id === selectedRsiRunId);
  const canStop = rsiRunBlocksNewPractice(selected) && !selected.stop_requested;
  [elements.rsiInstruction, elements.rsiModel, elements.rsiReasoningEffort,
    elements.rsiMaxCalls, elements.rsiWallSeconds, elements.rsiProjectBudget].forEach((field) => {
    field.disabled = !ready || active;
  });
  elements.rsiStart.disabled = !ready || !hasRemoteChoices || active || !elements.rsiInstruction.value.trim();
  elements.rsiStop.disabled = !canStop;
}

function rsiRunTitle(run) {
  const direction = run.spec?.instruction || "未记录练习方向";
  const state = run.state === "completed"
    ? "练习已结束"
    : statusLabels[run.state] || run.state || "未知状态";
  return `${state} · ${direction}`;
}

function rsiEventText(event) {
  const parts = [event.time || "时间未记录", event.kind || "event"];
  const payload = event.payload || {};
  for (const [key, value] of Object.entries(payload)) parts.push(`${key}=${value}`);
  return parts.join(" · ");
}

function rsiLedgerSnapshot(run, events) {
  const attempts = new Map();
  let callsUsed = null;
  for (const event of events || []) {
    const payload = event?.payload || {};
    const attemptNo = payload.attempt_no;
    if (event.kind === "attempt_claimed" && Number.isInteger(attemptNo) && attemptNo > 0) {
      attempts.set(attemptNo, {
        number: attemptNo,
        mode: payload.mode || "fresh",
        state: "active",
        cleanupConfirmed: null,
        activeElapsedMs: null,
      });
      if (Number.isInteger(payload.model_calls_before)) {
        callsUsed = Math.max(callsUsed ?? 0, payload.model_calls_before);
      }
    }
    if (event.kind === "model_call_reserved" && Number.isInteger(payload.global_call_index)) {
      callsUsed = Math.max(callsUsed ?? 0, payload.global_call_index);
    }
    if ((event.kind === "attempt_finalized" || event.kind === "attempt_abandoned_conservatively")
        && Number.isInteger(attemptNo) && attemptNo > 0) {
      const prior = attempts.get(attemptNo) || { number: attemptNo, mode: "未记录" };
      attempts.set(attemptNo, {
        ...prior,
        state: event.kind === "attempt_finalized" ? "finalized" : "abandoned",
        cleanupConfirmed: typeof payload.cleanup_confirmed === "boolean"
          ? payload.cleanup_confirmed : false,
        activeElapsedMs: Number.isInteger(payload.active_elapsed_ms)
          ? payload.active_elapsed_ms : null,
      });
      if (Number.isInteger(payload.model_calls_used)) {
        callsUsed = Math.max(callsUsed ?? 0, payload.model_calls_used);
      }
    }
  }
  const ordered = [...attempts.values()].sort((left, right) => left.number - right.number);
  const current = ordered.at(-1) || null;
  const settledElapsedMs = ordered.reduce(
    (total, attempt) => total + (Number.isInteger(attempt.activeElapsedMs) ? attempt.activeElapsedMs : 0), 0,
  );
  const callLimit = run.spec?.max_model_calls;
  const wallLimitMs = Number.isInteger(run.spec?.wall_seconds) ? run.spec.wall_seconds * 1000 : null;
  return {
    current,
    attempts: ordered.length,
    callsUsed,
    callsRemaining: Number.isInteger(callsUsed) && Number.isInteger(callLimit)
      ? Math.max(0, callLimit - callsUsed) : null,
    settledElapsedMs,
    settledRemainingMs: Number.isInteger(wallLimitMs) ? Math.max(0, wallLimitMs - settledElapsedMs) : null,
  };
}

function rsiLedgerText(run, events) {
  const ledger = rsiLedgerSnapshot(run, events);
  const calls = Number.isInteger(ledger.callsUsed)
    ? `${ledger.callsUsed} 已用 / ${ledger.callsRemaining ?? "?"} 剩余`
    : "尚无已保留的调用账本记录";
  const time = `已结算累计时间：${formatRsiMilliseconds(ledger.settledElapsedMs)}；已结算剩余：${formatRsiMilliseconds(ledger.settledRemainingMs)}。`;
  if (!ledger.current) return `调用：${calls}。${time} 尚未取得 attempt 记录。`;
  const cleanup = ledger.current.cleanupConfirmed === true ? "已确认"
    : ledger.current.cleanupConfirmed === false ? "未确认" : "尚未进入清理";
  const activeNotice = ledger.current.state === "active"
    ? "当前 attempt 仍在运行，未结算的执行时间会在清理完成后入账。" : "";
  return `Attempt #${ledger.current.number} · ${ledger.current.mode} · ${ledger.current.state}；清理：${cleanup}。调用：${calls}。${time} ${activeNotice}`;
}

function rsiCandidateKey(runId, digest) {
  return typeof runId === "string" && typeof digest === "string" && digest
    ? `${runId}--${digest}` : null;
}

function rsiSelectedCandidateMatches(runId, digest, allowReviewed = false) {
  const selected = rsiRuns.find((run) => run.id === selectedRsiRunId);
  return selectedRsiRunId === runId && selected?.candidate_sha256 === digest
    && (allowReviewed || !selected.review);
}

function rsiCandidateEditorFocused(run) {
  const key = rsiCandidateKey(run?.id, run?.candidate_sha256);
  const editor = key && elements.rsiDetail.querySelector(`[data-rsi-candidate-key="${key}"] textarea`);
  return Boolean(editor && document.activeElement === editor);
}

function updateFocusedRsiDetail(run) {
  // A server-confirmed review always wins over local focus/draft preservation:
  // the editor must disappear rather than leaving a stale submit affordance.
  if (run.review || !rsiSelectedCandidateMatches(run.id, run.candidate_sha256)
      || elements.rsiDetail.dataset.rsiRunId !== run.id || !rsiCandidateEditorFocused(run)) return false;
  const ledger = elements.rsiDetail.querySelector(".rsi-ledger-detail");
  if (ledger) ledger.textContent = rsiLedgerText(run, rsiEvents);
  const log = elements.rsiDetail.querySelector(".rsi-event-log");
  if (log) log.textContent = rsiEvents.length ? rsiEvents.map(rsiEventText).join("\n") : "尚无可显示事件。";
  return true;
}

function makeRsiCandidateViewer(run, entry) {
  const key = rsiCandidateKey(run.id, run.candidate_sha256);
  const candidate = entry.candidate || {};
  const verification = candidate.verification && typeof candidate.verification === "object"
    ? candidate.verification : {};
  const viewer = document.createElement("section");
  viewer.className = "rsi-candidate-view";
  viewer.dataset.rsiCandidateKey = key;
  const heading = document.createElement("h4");
  heading.textContent = run.review
    ? "已归档候选经验（只读，尚未生效）" : "独立验证后的候选经验（尚未生效）";
  const provenance = document.createElement("p");
  provenance.className = "rsi-candidate-provenance";
  const evidence = Array.isArray(verification.evidence) ? verification.evidence[0] : null;
  const source = typeof candidate.source === "string" ? candidate.source : "未记录来源";
  const verdict = typeof verification.verdict === "string" ? verification.verdict : "未记录结论";
  const artifact = typeof verification.artifact === "string" ? verification.artifact : "未记录证据文件";
  const evidenceHash = typeof evidence?.sha256 === "string" ? evidence.sha256 : "未记录";
  provenance.textContent = `独立验证来源：${source}；结论：${verdict}；证据：${artifact}；证据 SHA-256：${evidenceHash}`;
  const scope = document.createElement("p");
  scope.className = "field-help";
  scope.textContent = typeof verification.scope === "string"
    ? verification.scope : "验证范围未记录；候选仍需人工审查。";
  const memoryHeading = document.createElement("h5");
  memoryHeading.textContent = "候选记忆文件";
  const memory = candidate.memory && typeof candidate.memory === "object" && !Array.isArray(candidate.memory)
    ? candidate.memory : {};
  const memoryEntries = Object.entries(memory);
  const memoryList = document.createElement("div");
  memoryList.className = "rsi-candidate-memory-list";
  if (!memoryEntries.length) {
    const empty = document.createElement("p");
    empty.className = "field-help";
    empty.textContent = "候选中没有可显示的记忆文件。";
    memoryList.append(empty);
  }
  memoryEntries.forEach(([name, value]) => {
    const file = document.createElement("section");
    file.className = "rsi-candidate-memory-file";
    const fileName = document.createElement("h6");
    fileName.textContent = name;
    const fileText = document.createElement("pre");
    // Intentionally textContent: remote candidate prose is untrusted model output.
    fileText.textContent = typeof value?.text === "string" ? value.text : "[未提供可读文本]";
    const fileHash = document.createElement("p");
    fileHash.className = "field-help";
    fileHash.textContent = `文件 SHA-256：${typeof value?.sha256 === "string" ? value.sha256 : "未记录"}`;
    file.append(fileName, fileText, fileHash);
    memoryList.append(file);
  });
  const raw = document.createElement("details");
  raw.className = "rsi-candidate-raw";
  const rawSummary = document.createElement("summary");
  rawSummary.textContent = "完整候选 JSON（审计）";
  const rawContent = document.createElement("pre");
  rawContent.textContent = JSON.stringify(candidate, null, 2);
  raw.append(rawSummary, rawContent);
  viewer.append(heading, provenance, scope, memoryHeading, memoryList, raw);
  // The review is immutable, but its underlying evidence must remain readable.
  if (run.review) return viewer;
  const note = document.createElement("textarea");
  note.maxLength = 4000;
  note.rows = 3;
  note.placeholder = "可选：记录人工审查意见。接受只归档，不会自动修改当前经验。";
  note.value = entry.draft || "";
  note.addEventListener("input", () => { entry.draft = note.value; });
  const accept = makeMiniButton("归档为接受", () => reviewRsiCandidate(run.id, run.candidate_sha256, "accepted"), true);
  const reject = makeMiniButton("归档为拒绝", () => reviewRsiCandidate(run.id, run.candidate_sha256, "rejected"));
  viewer.append(note, accept, reject);
  return viewer;
}

function renderRsiDetail(run) {
  if (run && updateFocusedRsiDetail(run)) return;
  elements.rsiDetail.replaceChildren();
  if (!run) {
    elements.rsiDetail.classList.add("hidden");
    return;
  }
  elements.rsiDetail.classList.remove("hidden");
  elements.rsiDetail.dataset.rsiRunId = run.id;
  const heading = document.createElement("h3");
  heading.textContent = "练习详情";
  const metadata = document.createElement("p");
  metadata.className = "field-help";
  metadata.textContent = `${run.id} · ${run.spec?.model || "未记录模型"} / ${run.spec?.reasoning_effort || "未记录强度"} · ${formatTimestamp(run.updated)}`;
  const direction = document.createElement("p");
  direction.className = "instruction-preview";
  direction.textContent = run.spec?.instruction || "未记录练习方向";
  elements.rsiDetail.append(heading, metadata, direction);

  const ledger = document.createElement("section");
  ledger.className = "rsi-ledger";
  const ledgerHeading = document.createElement("h4");
  ledgerHeading.textContent = "调用与清理账本";
  const ledgerDetail = document.createElement("p");
  ledgerDetail.className = "field-help rsi-ledger-detail";
  ledgerDetail.textContent = rsiLedgerText(run, rsiEvents);
  ledger.append(ledgerHeading, ledgerDetail);
  elements.rsiDetail.append(ledger);

  if (run.state === "completed" && !run.candidate_sha256) {
    const noCandidate = document.createElement("p");
    noCandidate.className = "rsi-no-candidate";
    noCandidate.textContent = "练习流程已结束，但未生成可审查的已验证候选；流程结束不代表每个项目成功，请查看项目的独立验证结果。";
    elements.rsiDetail.append(noCandidate);
  }

  if (run.stop_requested && run.state !== "cancelled") {
    const stop = document.createElement("p");
    stop.className = "rsi-stop-pending";
    stop.textContent = "已请求停止：这不是已停止。正在等待远端 Worker 清理其拥有的虚拟机并写入最终状态。";
    elements.rsiDetail.append(stop);
  }
  if (run.state === "needs_recovery" && !run.stop_requested) {
    const recovery = document.createElement("section");
    recovery.className = `rsi-recovery${rsiRecoveryRunId === run.id && rsiRecovery?.eligible ? " eligible" : ""}`;
    const recoveryHeading = document.createElement("h4");
    recoveryHeading.textContent = "恢复检查";
    const explanation = document.createElement("p");
    if (rsiRecoveryRunId !== run.id) {
      explanation.textContent = "正在检查是否可从已完成项目边界恢复…";
      recovery.append(recoveryHeading, explanation);
    } else if (!rsiRecovery) {
      explanation.textContent = "恢复检查暂不可用；不会重放未完成项目的任何操作。";
      recovery.append(recoveryHeading, explanation);
    } else {
      explanation.textContent = rsiRecoveryReasonText(rsiRecovery);
      const budget = document.createElement("p");
      budget.className = "field-help";
      const projects = Number.isInteger(rsiRecovery.boundary_projects)
        ? `已完成项目边界：${rsiRecovery.boundary_projects}。` : "已完成项目边界：未记录。";
      budget.textContent = `${projects} 剩余模型调用：${rsiRecovery.remaining_model_calls ?? "未记录"}；剩余练习时间：${formatRsiMilliseconds(rsiRecovery.remaining_wall_ms)}。`;
      recovery.append(recoveryHeading, explanation, budget);
      if (rsiRecovery.eligible === true) {
        recovery.append(makeMiniButton("从已完成项目边界恢复", () => recoverRsiPractice(run), true));
      }
    }
    const safety = document.createElement("p");
    safety.className = "field-help";
    safety.textContent = "恢复会再次由服务器验证；未完成项目的操作不会重放，模型调用和练习时间继续累计且不能重设。";
    recovery.append(safety);
    elements.rsiDetail.append(recovery);
  }
  const logHeading = document.createElement("h4");
  logHeading.textContent = "受限运行事件";
  const log = document.createElement("pre");
  log.className = "rsi-event-log";
  log.textContent = rsiEvents.length ? rsiEvents.map(rsiEventText).join("\n") : "尚无可显示事件。";
  elements.rsiDetail.append(logHeading, log);

  if (run.result?.status || run.result?.reason || run.result?.error) {
    if (rsiHasCodexAuthenticationFailure(run.result)) {
      const authentication = document.createElement("p");
      authentication.className = "rsi-stop-pending";
      authentication.textContent = "Codex 授权已失效或刷新失败。本次练习已停止，不会自动重试、复制凭据或回退到付费 API；请在远端服务器终端使用既有账号重新登录 Codex 后，新建练习。";
      elements.rsiDetail.append(authentication);
    }
    const result = document.createElement("pre");
    result.className = "rsi-result";
    result.textContent = JSON.stringify(rsiResultForDisplay(run.result), null, 2);
    elements.rsiDetail.append(result);
  }
  if (run.review) {
    const reviewed = document.createElement("p");
    reviewed.className = "rsi-reviewed";
    reviewed.textContent = `候选经验已归档为人工${run.review.decision === "accepted" ? "接受" : "拒绝"}；它未自动写入当前 Trace 或生效经验。`;
    elements.rsiDetail.append(reviewed);
    const receipt = document.createElement("pre");
    receipt.className = "rsi-review-receipt";
    receipt.textContent = `审查时间：${formatTimestamp(run.review.time)}\n候选 SHA-256：${run.review.sha256}\n审查意见：${run.review.note || "未填写"}`;
    elements.rsiDetail.append(receipt);
  }
  if (run.candidate_sha256) {
    const candidateActions = document.createElement("div");
    candidateActions.className = "button-row";
    candidateActions.append(makeMiniButton(run.review ? "查看已归档候选（只读）" : "查看已验证候选", () => loadRsiCandidate(run), true));
    elements.rsiDetail.append(candidateActions);
    const entry = rsiCandidateViews.get(rsiCandidateKey(run.id, run.candidate_sha256));
    if (entry?.candidate) elements.rsiDetail.append(makeRsiCandidateViewer(run, entry));
  }
}

function renderRsiRuns() {
  elements.rsiRunList.replaceChildren();
  elements.rsiRunCount.textContent = `${rsiRuns.length} 项`;
  if (!rsiRuns.length) {
    const empty = document.createElement("p");
    empty.className = "field-help";
    empty.textContent = "尚无远程练习记录。";
    elements.rsiRunList.append(empty);
  }
  rsiRuns.forEach((run) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `rsi-run${run.id === selectedRsiRunId ? " selected" : ""}`;
    const title = document.createElement("strong");
    title.textContent = rsiRunTitle(run);
    const meta = document.createElement("span");
    meta.textContent = `${formatTimestamp(run.created)} · ${run.spec?.max_model_calls ?? "?"} 次调用上限 · ${run.spec?.wall_seconds ?? "?"} 秒上限`;
    item.append(title, meta);
    item.addEventListener("click", () => selectRsiRun(run.id));
    elements.rsiRunList.append(item);
  });
  renderRsiDetail(rsiRuns.find((run) => run.id === selectedRsiRunId));
  rsiSetControls();
}

async function refreshRsiEvents() {
  const run = rsiRuns.find((item) => item.id === selectedRsiRunId);
  if (!run) return;
  const runId = run.id;
  const cursor = rsiEventCursor;
  const payload = await request(`/api/rsi/events?run_id=${encodeURIComponent(runId)}&after=${cursor}`);
  // A slow response for a previously selected run must never update the
  // events, candidate viewer, or draft of the newly selected run.
  if (selectedRsiRunId !== runId) return;
  const received = payload.events || [];
  received.forEach((event) => {
    if (Number.isInteger(event.seq) && event.seq > rsiEventCursor) {
      rsiEventCursor = event.seq;
      rsiEvents.push(event);
    }
  });
  rsiEvents = rsiEvents.slice(-300);
  renderRsiDetail(run);
}

async function selectRsiRun(runId) {
  selectedRsiRunId = runId;
  rsiEventCursor = 0;
  rsiEvents = [];
  rsiRecovery = null;
  rsiRecoveryRunId = null;
  renderRsiRuns();
  try {
    const payload = await request(`/api/rsi/get?run_id=${encodeURIComponent(runId)}`);
    if (selectedRsiRunId !== runId) return;
    const index = rsiRuns.findIndex((run) => run.id === runId);
    if (index >= 0 && payload.run) rsiRuns[index] = payload.run;
    await refreshRsiEvents();
    const current = rsiRuns.find((run) => run.id === runId);
    if (current?.state === "needs_recovery") await refreshRsiRecovery(current);
    renderRsiRuns();
  } catch (error) {
    rsiSetStatus(rsiHealth, `读取远程练习详情失败：${error.message}`);
  }
}

async function refreshRsiRecovery(run) {
  if (!run || run.id !== selectedRsiRunId || run.state !== "needs_recovery") return;
  rsiRecoveryRunId = run.id;
  rsiRecovery = null;
  renderRsiDetail(run);
  try {
    const payload = await request(`/api/rsi/recovery?run_id=${encodeURIComponent(run.id)}`);
    if (selectedRsiRunId === run.id) {
      rsiRecovery = payload.recovery || null;
      renderRsiDetail(rsiRuns.find((item) => item.id === run.id));
    }
  } catch (error) {
    if (selectedRsiRunId === run.id) {
      rsiRecovery = null;
      rsiSetStatus(rsiHealth, `恢复检查失败：${error.message}`);
      renderRsiDetail(rsiRuns.find((item) => item.id === run.id));
    }
  }
}

async function refreshRsi({poll = false} = {}) {
  if (!poll) elements.rsiRefresh.disabled = true;
  try {
    // Health starts a remote admission probe and can be materially more
    // expensive than reading the durable run ledger.  Background polling is
    // deliberately observation-only: initial/manual refresh and every start
    // or recovery still call this full check through `poll === false`.
    const shouldRefreshHealth = !poll || !rsiHealth;
    if (shouldRefreshHealth) {
      rsiHealth = await request("/api/rsi/health");
      rsiSetStatus(rsiHealth);
      rsiPopulateOptions(rsiHealth);
    }
    if (rsiHealth.configured) {
      const payload = await request("/api/rsi/runs?limit=50");
      rsiRuns = payload.runs || [];
      if (selectedRsiRunId && !rsiRuns.some((run) => run.id === selectedRsiRunId)) {
        selectedRsiRunId = null;
        rsiEvents = [];
        rsiEventCursor = 0;
        rsiRecovery = null;
        rsiRecoveryRunId = null;
      }
      renderRsiRuns();
      if (selectedRsiRunId) {
        await refreshRsiEvents();
        const selected = rsiRuns.find((run) => run.id === selectedRsiRunId);
        if (selected?.state === "needs_recovery" && rsiRecoveryRunId !== selected.id) {
          await refreshRsiRecovery(selected);
        }
      }
    } else {
      rsiRuns = [];
      selectedRsiRunId = null;
      renderRsiRuns();
    }
  } catch (error) {
    rsiHealth = {
      configured: rsiHealth?.configured === true,
      ready: false,
      connection_state: "unreachable",
      checks: [], models: [], reasoning_efforts: [],
      message: `远程 RSI 状态不可用：${error.message}`,
    };
    rsiSetStatus(rsiHealth);
    rsiSetControls();
  } finally {
    elements.rsiRefresh.disabled = false;
    clearTimeout(rsiPollTimer);
    if (rsiRuns.some(rsiRunIsActive)) {
      rsiPollTimer = setTimeout(() => refreshRsi({poll: true}), RSI_POLL_INTERVAL_MS);
    }
  }
}

async function startRsiPractice() {
  if (rsiHealth?.ready !== true) return;
  const instruction = elements.rsiInstruction.value.trim();
  if (!instruction) return;
  const confirmed = window.confirm(
    `将在远程隔离虚拟机中启动一次有界自主练习。\n\n方向：${instruction}\n模型调用上限：${elements.rsiMaxCalls.value}\n最长时间：${elements.rsiWallSeconds.value} 秒\n\n不会操作本机桌面，也不会自动改写当前经验。确认开始？`,
  );
  if (!confirmed) return;
  elements.rsiStart.disabled = true;
  try {
    const payload = await request("/api/rsi/start", {
      method: "POST",
      body: JSON.stringify({
        instruction,
        model: elements.rsiModel.value,
        reasoning_effort: elements.rsiReasoningEffort.value,
        max_model_calls: Number(elements.rsiMaxCalls.value),
        wall_seconds: Number(elements.rsiWallSeconds.value),
        project_budget: Number(elements.rsiProjectBudget.value),
      }),
    });
    selectedRsiRunId = payload.run?.id || null;
    rsiEvents = [];
    rsiEventCursor = 0;
    await refreshRsi();
  } catch (error) {
    rsiSetStatus(rsiHealth, `无法启动远程练习：${error.message}`);
    rsiSetControls();
  }
}

async function stopRsiPractice() {
  const run = rsiRuns.find((item) => item.id === selectedRsiRunId);
  if (!run || !rsiRunBlocksNewPractice(run) || run.stop_requested) return;
  if (!window.confirm("请求停止此远程练习？请求会先持久化；远端虚拟机完成清理前不能视为已停止。")) return;
  elements.rsiStop.disabled = true;
  try {
    await request("/api/rsi/stop", {method: "POST", body: JSON.stringify({run_id: run.id})});
    await refreshRsi();
  } catch (error) {
    rsiSetStatus(rsiHealth, `无法请求停止：${error.message}`);
    rsiSetControls();
  }
}

async function recoverRsiPractice(run) {
  if (!run || run.id !== selectedRsiRunId || rsiRecovery?.eligible !== true) return;
  const confirmed = window.confirm(
    "仅会从服务器已验证的完成项目边界恢复。\n\n未完成项目的操作和临时环境不会重放。模型调用和练习时间继续累计，不能重设。确认恢复？",
  );
  if (!confirmed) return;
  try {
    await request("/api/rsi/recover", {method: "POST", body: JSON.stringify({run_id: run.id})});
    rsiRecovery = null;
    rsiRecoveryRunId = null;
    rsiEvents = [];
    rsiEventCursor = 0;
    await refreshRsi();
  } catch (error) {
    rsiSetStatus(rsiHealth, `无法恢复远程练习：${error.message}`);
  }
}

async function loadRsiCandidate(run) {
  const runId = run?.id;
  const digest = run?.candidate_sha256;
  const key = rsiCandidateKey(runId, digest);
  if (!key || !rsiSelectedCandidateMatches(runId, digest, true)) return;
  const existing = rsiCandidateViews.get(key);
  if (existing?.candidate) {
    renderRsiDetail(rsiRuns.find((item) => item.id === runId));
    return;
  }
  const entry = existing || {runId, digest, candidate: null, draft: ""};
  rsiCandidateViews.set(key, entry);
  try {
    const payload = await request(`/api/rsi/candidate?run_id=${encodeURIComponent(runId)}&digest=${encodeURIComponent(digest)}`);
    if (!rsiSelectedCandidateMatches(runId, digest, true) || rsiCandidateViews.get(key) !== entry) return;
    entry.candidate = payload.candidate || {};
    renderRsiDetail(rsiRuns.find((item) => item.id === runId));
  } catch (error) {
    if (rsiSelectedCandidateMatches(runId, digest, true)) {
      rsiSetStatus(rsiHealth, `读取候选经验失败：${error.message}`);
    }
  }
}

async function reviewRsiCandidate(runId, digest, decision) {
  if (!rsiSelectedCandidateMatches(runId, digest)) return;
  const entry = rsiCandidateViews.get(rsiCandidateKey(runId, digest));
  if (!entry?.candidate) return;
  const label = decision === "accepted" ? "接受" : "拒绝";
  if (!window.confirm(`${label}该候选经验并写入不可变审查记录？这不会自动修改当前 Trace 或生效经验。`)) return;
  try {
    await request("/api/rsi/review", {
      method: "POST",
      body: JSON.stringify({run_id: runId, digest, decision, note: entry.draft}),
    });
    // A late review response cannot mutate a different selected run.  The
    // subsequent list refresh supplies the immutable reviewed state, whose
    // renderer intentionally does not include this editable viewer.
    if (!rsiSelectedCandidateMatches(runId, digest)) return;
    await refreshRsi();
  } catch (error) {
    if (rsiSelectedCandidateMatches(runId, digest)) {
      rsiSetStatus(rsiHealth, `候选审查提交失败：${error.message}`);
    }
  }
}

function isBusy() {
  return ["queued", "running", "stopping", "awaiting_recording_start", "awaiting_narration"]
    .includes(elements.status.dataset.status);
}

function setBusy(busy) {
  const desktop = elements.executionScope.value === "desktop";
  elements.executionScope.disabled = busy;
  elements.operationScope.disabled = busy;
  elements.desktopWorkflowSettings.hidden = !desktop || elements.operationScope.value === "selected_windows";
  elements.desktopOrchestration.disabled = busy;
  elements.desktopResume.disabled = busy || elements.desktopOrchestration.value !== "langgraph";
  const task = selectedTask();
  elements.executeButton.disabled = busy || !backendMatchesScope() || (desktop
    ? elements.useExperience.checked && (!task?.confirmed || !task?.semantic_experience)
    : task ? !canExecuteTask(task) || task.execution_scope === "desktop" : true);
  elements.taskpack.disabled = busy;
  elements.model.disabled = busy;
  [
    elements.modelProvider, elements.localModel, elements.apiBaseUrl, elements.apiModel, elements.apiKey,
    elements.apiReasoningEffort, elements.apiResponseFormat, elements.apiTimeout,
    elements.apiSaveSettings, elements.apiClearSettings,
  ].forEach((element) => { element.disabled = busy; });
  elements.reasoningEffort.disabled = busy;
  elements.inputMode.disabled = busy || desktop;
  elements.adaptiveReasoning.disabled = busy || desktop || usesModelApi();
  elements.instruction.disabled = busy;
  [elements.localSystemPrompt, elements.localTurnTemplate, elements.localPromptsSave,
    elements.localPromptsReset, document.querySelector("#local-executor")].forEach((field) => {
    field.disabled = busy;
  });
  elements.useExperience.disabled = busy;
  if (usesTrainedModel()) {
    elements.executeButton.disabled = busy || !backendMatchesScope() || (elements.useExperience.checked
      && (!task?.confirmed || !task?.semantic_experience));
    elements.executeButton.title = "本地模型主屏连续执行；F9 停止";
    [elements.executionScope, elements.desktopOrchestration, elements.desktopResume, elements.inputMode,
      elements.adaptiveReasoning].forEach(el => { el.disabled = true; });
    elements.taskpack.disabled = busy || elements.localModel.value === "trained_d";
  } else {
    elements.executeButton.title = "";
  }
  elements.viewTabs.forEach((button) => { button.disabled = busy; });
  elements.recordSource.disabled = busy;
  elements.waaRoot.disabled = busy;
  elements.waaExample.disabled = busy;
  elements.recordName.disabled = busy;
  elements.recordModel.disabled = busy;
  elements.recordReasoningEffort.disabled = busy;
  elements.recordNarration.disabled = busy;
  elements.recordDeferCompilation.disabled = busy;
  elements.compilerModel.disabled = busy;
  elements.compilerReasoningEffort.disabled = busy;
  elements.recordButton.disabled = busy
    || (usesWaaRecording() && !selectedWaaTask());
  elements.refreshLibrary.disabled = busy;
  document.querySelectorAll(".mini-button").forEach((button) => {
    button.disabled = busy;
  });
  document.querySelectorAll(".voice-input-button").forEach((button) => {
    button.disabled = busy && dictationSession?.button !== button;
  });
}

function renderJob(job) {
  notifyTaskEnd(job);
  const batch = job.provider === "trained_d" && job.status === "running" ? job.pending_batch : null;
  document.querySelector("#trained-batch").hidden = !batch;
  if (batch) {
    document.querySelector("#trained-batch-actions").textContent = JSON.stringify(batch.actions, null, 2);
    document.querySelector("#trained-batch-image").src = batch.annotated_image || "";
    document.querySelector("#trained-approve").onclick = async () => {
      try {
        await request(`/api/jobs/${job.job_id}/approve`, {method: "POST", body: JSON.stringify({token: batch.token})});
        document.querySelector("#trained-batch").hidden = true;
      } catch (error) { showError(error.message); }
    };
  }
  activeJobId = job.job_id;
  elements.empty.classList.add("hidden");
  elements.jobView.classList.remove("hidden");
  elements.status.dataset.status = job.status;
  elements.status.className = `status-pill ${job.status}`;
  elements.status.textContent = job.kind === "compilation" && job.status === "partial"
    ? "部分完成"
    : statusLabels[job.status] || job.status;
  elements.jobMode.textContent = ["recording", "waa_recording"].includes(job.kind)
    ? (job.kind === "waa_recording" ? "WAA 录制" : "录制")
    : job.kind === "compilation"
      ? "编译"
      : job.kind === "revision"
        ? "经验修订"
        : `${job.mode === "execute" ? "执行" : "预演"} · ${job.executor_backend === "cua" && job.cua_target?.kind !== "desktop" ? "指定窗口 / 应用" : job.execution_scope === "desktop" ? "全桌面" : "旧版单窗口"}`;
  elements.jobTask.textContent = job.task_id;
  elements.jobModel.textContent = job.provider === "api"
    ? `API · ${job.model || "—"}`
    : modelLabels[job.model] || job.model || "—";
  elements.jobEffort.textContent = effortLabels[job.reasoning_effort] || job.reasoning_effort || "—";
  elements.jobInstruction.textContent = job.instruction;
  const rounds = job.model_io?.length ? job.model_io : job.result?.model_io;
  const signature = `${job.job_id}:${(rounds || []).map(round =>
    `${round.request_id}:${round.status}:${Boolean(round.input)}:${round.raw_output?.length || 0}:` +
    `${round.execution?.status || ""}:${Boolean(round.execution?.normalized_plan)}:` +
    `${round.execution?.executor_requests?.map(item => item.status).join(",") || ""}`
  ).join("|")}`;
  if (signature !== modelChatSignature) {
    const nearBottom = elements.modelChat.scrollHeight - elements.modelChat.scrollTop - elements.modelChat.clientHeight < 80;
    const oldScroll = elements.modelChat.scrollTop;
    elements.modelChat.replaceChildren(makeModelChat(rounds));
    elements.modelChat.scrollTop = nearBottom ? elements.modelChat.scrollHeight : oldScroll;
    modelChatSignature = signature;
  }
  document.querySelector("#job-latest-progress").textContent = job.logs?.at(-1) || "等待执行…";
  elements.narrationSubmit.textContent = job.defer_compilation
    ? "保存讲解和录制，稍后编译"
    : "确认讲解并开始编译";
  elements.jobLog.replaceChildren();
  (job.logs || []).forEach((line) => {
    const item = document.createElement("div");
    item.className = "log-line";
    item.textContent = line;
    elements.jobLog.append(item);
  });
  elements.jobLog.scrollTop = elements.jobLog.scrollHeight;
  const busy = ["queued", "running", "stopping", "awaiting_recording_start", "awaiting_narration"].includes(job.status);
  elements.liveDot.classList.toggle(
    "active",
    ["queued", "running", "stopping", "awaiting_recording_start"].includes(job.status),
  );
  elements.stopButton.classList.toggle(
    "hidden",
    !busy || job.kind !== "agent" && !["execute", "record"].includes(job.mode),
  );
  elements.recordStopButton.classList.toggle(
    "hidden",
    job.mode !== "record"
      || !["queued", "running", "stopping", "awaiting_recording_start"].includes(job.status),
  );
  setBusy(busy);
  if (job.result || job.error) {
    elements.resultPanel.classList.remove("hidden");
    elements.jobPerformance.replaceChildren();
    if (job.result?.performance) {
      const performanceTimeline = makePerformanceTimeline(
        job.result.performance,
        job.result.stage_timings || [],
        job,
      );
      if (performanceTimeline) elements.jobPerformance.append(performanceTimeline);
    }
    const modelIoTimeline = makeModelIoTimeline(job.result?.model_io);
    if (modelIoTimeline) elements.jobPerformance.append(modelIoTimeline);
    const auditLinks = makeRunAuditLinks(job.result);
    if (auditLinks) elements.jobPerformance.append(auditLinks);
    elements.jobResult.textContent = job.error || JSON.stringify(job.result, null, 2);
  } else {
    elements.resultPanel.classList.add("hidden");
    elements.jobPerformance.replaceChildren();
  }
  if (!busy && pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

async function prepareWaaRecordingStart(job) {
  if (waaGoSentJobId === job.job_id || waaGoStartingJobId === job.job_id) return;
  waaGoStartingJobId = job.job_id;
  try {
    let audioStartedAtEpochMs = null;
    if (job.narrated) {
      await startNarrationCapture();
      audioStartedAtEpochMs = narrationCapture.startedAtEpochMs;
    }
    const started = await request("/api/waa/recordings/go", {
      method: "POST",
      body: JSON.stringify({
        job_id: job.job_id,
        audio_started_at_epoch_ms: audioStartedAtEpochMs,
      }),
    });
    waaGoSentJobId = job.job_id;
    renderJob(started);
  } catch (error) {
    if (narrationCapture) await discardNarrationCapture();
    showRecordError(`WAA 同步启动失败：${error.message}`);
    try {
      await request(`/api/jobs/${job.job_id}/stop`, { method: "POST", body: "{}" });
    } catch (_) { /* original error is more useful */ }
  } finally {
    waaGoStartingJobId = null;
  }
}

async function pollJob() {
  if (!activeJobId) return;
  try {
    const job = await request(`/api/jobs/${activeJobId}`);
    renderJob(job);
    if (job.status === "awaiting_recording_start" && job.kind === "waa_recording") {
      await prepareWaaRecordingStart(job);
      pollTimer = setTimeout(pollJob, 250);
    } else if (job.status === "awaiting_narration") {
      await prepareNarrationReview(job);
    } else if (["queued", "running", "stopping"].includes(job.status)) {
      pollTimer = setTimeout(pollJob, 850);
    } else if ((["recording", "waa_recording", "compilation", "revision", "task_model_revision"].includes(job.kind)
      || job.result?.candidate_experience)
      && refreshedRecordingJobId !== job.job_id) {
      if (["recording", "waa_recording"].includes(job.kind) && narrationCapture) {
        await discardNarrationCapture();
      }
      if (job.kind === "waa_recording") {
        waaGoSentJobId = null;
        waaGoStartingJobId = null;
      }
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
  backendSupportsIncrementalGuidance = state.capabilities?.incremental_guidance === true;
  const backendSupportsV14 = state.capabilities?.coordinate_isolation === true
    && state.capabilities?.narration_claim_audit === true
    && state.capabilities?.experience_family_inheritance === true
    && state.capabilities?.directed_task_graph === true
    && state.capabilities?.task_model_revision === true
    && state.capabilities?.graph_native_guidance === true
    && state.capabilities?.independent_guidance_delete === true
    && state.capabilities?.waa_narrated_recording === true
    && state.capabilities?.waa_task_catalog === true
    && state.capabilities?.deferred_recording_compilation === true;
  elements.version.textContent = backendSupportsV14
    ? `v${state.version}`
    : `v${state.version} · 需要重启`;
  if (!backendSupportsIncrementalGuidance) {
    showError("网页后台仍是旧版本。请停止并重新启动 trace2task web；在此之前已确认经验可能被整包覆写。");
  } else if (!backendSupportsV14) {
    showError("网页后台还没有加载当前版本。请停止并重新启动 trace2task web；重启前 WAA 同步讲解录制不可用。");
  }
  candidates = state.candidates || [];
  recordings = state.recordings || [];
  populateAgentOptions(state.agent_options);
  await refreshWaaTasks();
  populateTaskpacks(state.taskpacks || []);
  return state;
}

async function startRecording() {
  clearRecordError();
  if (dictationSession) {
    return showRecordError("请先结束语音输入并等待转写完成");
  }
  const waa = usesWaaRecording();
  const taskId = elements.recordName.value.trim();
  const native = elements.recordSource.value === "opencua";
  if (!waa && !native) return showRecordError("旧录制入口已停用，请选择 OpenCUA。");
  if (native && !window.confirm("将录制整个主显示器的画面和键鼠操作（可跨程序）。请关闭敏感窗口。F8 完成，F9 取消。继续？")) return;
  if (!taskId) return showRecordError("请输入经验名称");
  if (waa && !elements.waaRoot.value.trim()) return showRecordError("请输入 WAA 根目录");
  if (waa && !selectedWaaTask()) return showRecordError("请选择一个 WAA 标准任务");
  const narrated = !native && elements.recordNarration.checked;
  const deferCompilation = native || elements.recordDeferCompilation.checked;
  setBusy(true);
  try {
    if (!waa && narrated) await startNarrationCapture();
    const job = await request(waa ? "/api/waa/recordings" : "/api/recordings", {
      method: "POST",
      body: JSON.stringify(waa ? {
        waa_root: elements.waaRoot.value.trim(),
        example_path: elements.waaExample.value.trim(),
        task_id: taskId,
        narrated,
        defer_compilation: deferCompilation,
        model: elements.recordModel.value,
        reasoning_effort: elements.recordReasoningEffort.value,
      } : {
        recording_backend: "opencua",
        execution_scope: "desktop",
        task_id: taskId,
        narrated,
        defer_compilation: deferCompilation,
        model: elements.recordModel.value,
        reasoning_effort: elements.recordReasoningEffort.value,
      }),
    });
    renderJob(job);
    pollTimer = setTimeout(pollJob, 250);
  } catch (error) {
    if (narrationCapture) await discardNarrationCapture();
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
    ? `确认你已经审查“${task.task_id}”的示范、目标窗口、允许动作、成功参考图、${task.semantic_experience.stage_count} 个 Trace 片段，以及 ${task.semantic_experience.state_count} 个运行状态和有向转移吗？`
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
  const originalLabel = button?.textContent || "编译 / 重试";
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

async function reviseCandidate(candidate, feedbackInput, button) {
  if (isBusy()) return;
  if (!backendSupportsIncrementalGuidance) {
    return showError("当前后台不支持增量融合。请重启 trace2task web 后再生成草稿。");
  }
  const feedback = feedbackInput.value.trim();
  if (!feedback) return showError("请先写下你希望 Agent 改进的具体行为");
  const originalLabel = button.textContent;
  button.textContent = "正在提交…";
  setBusy(true);
  try {
    const job = await request("/api/candidates/revise", {
      method: "POST",
      body: JSON.stringify({
        path: candidate.local_path,
        feedback,
        model: elements.compilerModel.value,
        reasoning_effort: elements.compilerReasoningEffort.value,
      }),
    });
    renderJob(job);
    pollTimer = setTimeout(pollJob, 250);
  } catch (error) {
    setBusy(false);
    button.textContent = originalLabel;
    showError(error.message);
  }
}

async function reviseTaskModel(candidate, feedbackInput, button) {
  if (isBusy()) return;
  const feedback = feedbackInput.value.trim();
  if (!feedback) return showError("请先说明阶段、状态、转移或结束条件哪里不对");
  const originalLabel = button.textContent;
  button.textContent = "正在提交…";
  setBusy(true);
  try {
    const job = await request("/api/candidates/task-model/revise", {
      method: "POST",
      body: JSON.stringify({
        path: candidate.local_path,
        feedback,
        model: elements.compilerModel.value,
        reasoning_effort: elements.compilerReasoningEffort.value,
      }),
    });
    renderJob(job);
    pollTimer = setTimeout(pollJob, 250);
  } catch (error) {
    setBusy(false);
    button.textContent = originalLabel;
    showError(error.message);
  }
}

async function confirmTaskModelRevision(candidate) {
  const proposal = candidate.task_model_revision;
  if (!proposal || proposal.status !== "draft") return;
  if ((proposal.blocking_issue_count || 0) > 0) {
    return showError("当前任务图草稿仍有 Guidance 映射冲突，不能启用");
  }
  const confirmed = window.confirm(
    `确认启用任务状态图 v${proposal.proposed_revision} 吗？\n\n${proposal.guidance_review?.pending?.length || 0} 条关联语义变化的人工规则会暂停生效，等待人工复核；原始 Trace 与旧版本都会保留。`,
  );
  if (!confirmed) return;
  setBusy(true);
  try {
    await request("/api/candidates/task-model/confirm", {
      method: "POST",
      body: JSON.stringify({ path: candidate.local_path }),
    });
    await refreshState();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function saveCandidateRevisionSummary(candidate, summaryInput, button) {
  const summary = summaryInput.value.trim();
  if (!summary) return showError("经验摘要不能为空");
  const originalLabel = button.textContent;
  button.textContent = "正在保存…";
  setBusy(true);
  try {
    await request("/api/candidates/revisions/summary", {
      method: "POST",
      body: JSON.stringify({ path: candidate.local_path, summary }),
    });
    candidate.revision.summary = summary;
    button.textContent = "已保存";
  } catch (error) {
    button.textContent = originalLabel;
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function confirmCandidateRevision(candidate, summaryInput) {
  if (!backendSupportsIncrementalGuidance) {
    return showError("当前后台不支持增量融合。请重启 trace2task web，旧格式草稿不能确认。");
  }
  const summary = summaryInput.value.trim();
  if (!summary) return showError("经验摘要不能为空");
  const confirmed = window.confirm(
    `确认把“${summary}”作为该任务的新人工诀窍版本吗？\n\n原始 Trace、模型初稿和 Compiler Agent 经验不会被覆盖，可在本地查看历史版本。`,
  );
  if (!confirmed) return;
  setBusy(true);
  try {
    await request("/api/candidates/revisions/summary", {
      method: "POST",
      body: JSON.stringify({ path: candidate.local_path, summary }),
    });
    await request("/api/candidates/revisions/confirm", {
      method: "POST",
      body: JSON.stringify({ path: candidate.local_path }),
    });
    await refreshState();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function deleteTaskpack(task) {
  const confirmed = window.confirm(
    `删除整个任务“${task.task_id}”吗？\n\n任务包、Compiler 经验、状态图和人工反馈经验都会一起移动到 taskpacks/.trash；原始录制不会被删除。`,
  );
  if (!confirmed) return;
  await deleteLocalAsset("/api/taskpacks/delete", { task_path: task.path });
}

async function deleteHumanGuidance(task) {
  const confirmed = window.confirm(
    `只删除“${task.task_id}”的人工反馈经验吗？\n\n只会移除 Guidance 当前版本及历史版本；任务、原始 Trace、Compiler 经验和状态图都会保留。文件会移动到 taskpacks/.trash/guidance，可以手动恢复。`,
  );
  if (!confirmed) return;
  await deleteLocalAsset("/api/taskpacks/guidance/delete", { task_path: task.path });
}

async function deleteRecording(recording) {
  const confirmed = window.confirm(
    `删除原始录制“${recording.task_id}”（${formatTimestamp(recording.created_at)}）吗？\n\n它会移动到 runs/.trash，可以手动恢复；已有任务经验不会被删除。`,
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

let cuaCatalogEntries = [];

function cuaTargetKey(target) {
  if (Number.isInteger(target?.pid) && Number.isInteger(target?.window_id)) {
    return `window:${target.pid}:${target.window_id}`;
  }
  if (typeof target?.launch_path === "string" && target.launch_path) {
    return `launch:${target.launch_path}`;
  }
  return "";
}

// This is deliberately a small, pure boundary: only checked catalog entries cross
// from the UI into a Cua job. The complete operating-system catalog never does.
function cuaTargetConfig(entries, selectedKeys, initialKey) {
  const selected = entries.filter((entry) => selectedKeys.has(entry.key));
  const initialIndex = selected.findIndex((entry) => entry.key === initialKey);
  return {
    targets: selected.map((entry) => entry.target),
    initial_index: initialIndex >= 0 ? initialIndex : 0,
  };
}

function cuaCatalogMatches(entry, query) {
  const needle = query.trim().toLocaleLowerCase();
  return !needle || `${entry.label} ${entry.target.launch_path || ""}`.toLocaleLowerCase().includes(needle);
}

function selectedCuaKeys() {
  return new Set([...document.querySelectorAll("#cua-target-list input[type=checkbox]:checked")]
    .map((input) => input.value));
}

function selectedCuaEntries() {
  const selected = selectedCuaKeys();
  return cuaCatalogEntries.filter((entry) => selected.has(entry.key));
}

function syncCuaInitialTarget(preferredKey = document.querySelector("#cua-target").value) {
  const select = document.querySelector("#cua-target");
  const selected = selectedCuaEntries();
  select.replaceChildren();
  if (!selected.length) {
    select.add(new Option("请先勾选一个目标", ""));
    select.disabled = true;
    return;
  }
  for (const entry of selected) select.add(new Option(entry.label, entry.key));
  select.disabled = false;
  select.value = selected.some((entry) => entry.key === preferredKey) ? preferredKey : selected[0].key;
}

function filterCuaCatalog() {
  const query = document.querySelector("#cua-target-search").value;
  const entries = new Map(cuaCatalogEntries.map((entry) => [entry.key, entry]));
  let visible = 0;
  for (const row of document.querySelectorAll("#cua-target-list .cua-target-choice")) {
    const checked = row.querySelector("input[type=checkbox]").checked;
    const entry = entries.get(row.dataset.key);
    row.hidden = !checked && (!entry || !cuaCatalogMatches(entry, query));
    if (!row.hidden) visible += 1;
  }
  const selected = selectedCuaKeys().size;
  const status = document.querySelector("#cua-target-search-status");
  status.textContent = query.trim()
    ? `${visible ? `显示 ${visible} / ${cuaCatalogEntries.length} 项` : "没有匹配项"}；已选 ${selected} 项（已选目标始终显示）。`
    : `${cuaCatalogEntries.length} 项；已选 ${selected} 项。搜索只筛选列表，不改变授权。`;
}

function renderCuaCatalog(catalog) {
  const previousKeys = selectedCuaKeys();
  const previousInitial = document.querySelector("#cua-target").value;
  const entries = [];
  for (const windowInfo of catalog.windows || []) {
    const target = {pid: windowInfo.pid, window_id: windowInfo.window_id};
    const key = cuaTargetKey(target);
    if (!key) continue;
    entries.push({key, target, label: `窗口：${windowInfo.app_name || "未知应用"} · ${windowInfo.title || "无标题"}`});
  }
  for (const app of catalog.apps || []) {
    const target = {launch_path: app.launch_path};
    const key = cuaTargetKey(target);
    if (!key) continue;
    entries.push({key, target, label: `启动：${app.name || app.launch_path}`});
  }
  cuaCatalogEntries = [...new Map(entries.map((entry) => [entry.key, entry])).values()];

  const list = document.querySelector("#cua-target-list");
  list.replaceChildren();
  if (!entries.length) {
    list.textContent = "未发现可授权的窗口或可启动应用。";
    syncCuaInitialTarget("");
    filterCuaCatalog();
    return;
  }
  for (const entry of cuaCatalogEntries) {
    const label = document.createElement("label");
    label.className = "cua-target-choice";
    label.dataset.key = entry.key;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = entry.key;
    checkbox.checked = previousKeys.has(entry.key);
    checkbox.addEventListener("change", () => {
      syncCuaInitialTarget();
      filterCuaCatalog();
    });
    const text = document.createElement("span");
    text.textContent = entry.label;
    label.append(checkbox, text);
    list.append(label);
  }
  // A refresh retains only the same explicit identities. Newly discovered items
  // are never selected implicitly.
  syncCuaInitialTarget(previousInitial);
  filterCuaCatalog();
}

function cuaTargetSelection() {
  const config = cuaTargetConfig(cuaCatalogEntries, selectedCuaKeys(), document.querySelector("#cua-target").value);
  return {...config, labels: selectedCuaEntries().map((entry) => entry.label)};
}

elements.operationScope.addEventListener("change", () => {
  syncProviderFields();
  renderTaskMeta();
  setBusy(isBusy());
});
document.querySelector("#local-executor").addEventListener("change", () => {
  syncProviderFields();
  renderTaskMeta();
});
document.querySelector("#cua-target-search").addEventListener("input", filterCuaCatalog);
document.querySelector("#cua-refresh").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "正在枚举…";
  try {
    const catalog = await request("/api/cua/windows", {method: "POST", body: "{}"});
    renderCuaCatalog(catalog);
  } catch (error) { showError(error.message); }
  finally { button.disabled = false; button.textContent = "刷新窗口 / 应用"; }
});

async function startJob(mode) {
  clearError();
  if (!backendMatchesScope()) {
    return showError(elements.operationScope.value === "selected_windows"
      ? "指定窗口 / 应用目前只支持 Cua 后端，请在模型旁切换执行后端。"
      : "全桌面目前只支持 Win32 后端，请在模型旁切换执行后端。");
  }
  if (usesTrainedModel()) {
    if (promptDirty) return showError("提示词有未保存修改；请先保存或恢复默认后再开始任务");
    if (editableLocalPromptKey() && (!promptLoaded || promptProfileKey !== editableLocalPromptKey())) {
      return showError("本地模型提示词尚未加载完成，请稍候");
    }
    const guidedTask = elements.useExperience.checked ? selectedTask() : null;
    if (elements.useExperience.checked && (!guidedTask?.confirmed || !guidedTask?.semantic_experience)) {
      return showError("请先选择已确认、已语义编译的经验");
    }
    const executorBackend = document.querySelector("#local-executor").value;
    const cuaSelection = executorBackend === "cua" && elements.operationScope.value === "selected_windows" ? cuaTargetSelection() : null;
    if (cuaSelection && !cuaSelection.targets.length) return showError("请刷新并勾选至少一个 Cua 目标窗口或应用");
    if (cuaSelection && cuaSelection.targets.length > 12) return showError("每次最多授权 12 个目标，请取消勾选无关项目");
    const instruction = elements.instruction.value.trim();
    if (!instruction) return showError("请输入任务指令");
    const continuous = true;
    const scopeNotice = cuaSelection
      ? `仅授权 ${cuaSelection.labels.join("；")}。后台优先；明确拒绝时会短暂切前台重试同一动作，此后该窗口改走前台，并尝试恢复原焦点。启动项允许请求启动该应用并重新定位窗口。停止需等待驱动调用返回。`
      : "将操作主屏桌面，连续执行。";
    if (!window.confirm(`${elements.localModel.selectedOptions[0].textContent} ${scopeNotice} ${guidedTask ? `指导经验：${guidedTask.task_id}。` : "无经验 Baseline。"} 最多 40 个动作，F9 停止。\n请先使用临时记事本测试。\n\n${instruction}`)) return;
    setBusy(true);
    try {
      const job = await request("/api/jobs", {method: "POST", body: JSON.stringify({
        provider: "trained_d", model: elements.localModel.value === "trained_d" ? "D-5970" : elements.localModel.value, mode: "execute", instruction, executor_backend: executorBackend,
        execution_scope: "desktop", use_experience: Boolean(guidedTask), task_path: guidedTask?.path || "", orchestration: "legacy", continuous,
        cua_target: cuaJobTarget(cuaSelection),
      })});
      renderJob(job);
      pollTimer = setTimeout(pollJob, 250);
    } catch (error) { setBusy(false); showError(error.message); }
    return;
  }
  if (chatPromptDirty) return showError("执行提示词有未保存修改；请先保存或恢复默认");
  if (editableChatPromptProvider() !== chatPromptProviderLoaded) {
    return showError("执行提示词尚未加载完成，请稍候");
  }
  if (dictationSession) return showError("请先结束语音输入并等待转写完成");
  const desktop = elements.executionScope.value === "desktop";
  const task = desktop && !elements.useExperience.checked ? null : selectedTask();
  if (desktop && elements.useExperience.checked && (!task || !task.semantic_experience)) {
    return showError("请手动选择已语义编译的经验，或关闭“使用经验”运行 Baseline。");
  }
  if (!desktop && elements.useExperience.checked && !task) {
    return showError("请手动选择任务经验；不再自动匹配。");
  }
  if (!desktop && !elements.useExperience.checked && !task) {
    return showError("不使用经验时，请先手动选择任务以确定目标窗口和允许的操作。");
  }
  const instruction = elements.instruction.value.trim();
  const local = elements.modelProvider.value === "local";
  const provider = usesModelApi() ? "api" : "codex";
  const executorBackend = document.querySelector("#local-executor").value;
  const cuaSelection = executorBackend === "cua" && elements.operationScope.value === "selected_windows" ? cuaTargetSelection() : null;
  if (executorBackend === "cua" && (!desktop || elements.desktopOrchestration.value !== "legacy"
      || mode !== "execute")) {
    return showError("指定窗口 / 应用当前仅支持独立任务执行；请关闭任务恢复后重试");
  }
  if (cuaSelection && (!cuaSelection.targets.length || cuaSelection.targets.length > 12)) {
    return showError("请勾选 1–12 个 Cua 目标窗口或应用");
  }
  if (usesModelApi() && !modelApiAvailable) {
    return showError("页面已更新，但后台仍是旧版本。请停止并重启本地 trace2task web，再刷新页面后使用模型 API。");
  }
  const model = local ? elements.localModel.value
    : usesModelApi() ? elements.apiModel.value.trim() : elements.model.value;
  const reasoningEffort = local ? "default" : usesModelApi()
    ? elements.apiReasoningEffort.value : elements.reasoningEffort.value;
  const inputMode = desktop ? "foreground" : elements.inputMode.value;
  const adaptiveReasoning = false;
  if (!instruction) return showError("请输入一条任务指令");
  if (!model) return showError("请输入 API 视觉模型 ID");
  const apiOptions = local ? {
    base_url: "http://127.0.0.1:8081/v1",
    api_key: "local-only",
    response_format: "json_schema",
    timeout_seconds: 120,
  } : usesModelApi() ? {
    base_url: elements.apiBaseUrl.value.trim(),
    api_key: elements.apiKey.value,
    response_format: elements.apiResponseFormat.value,
    timeout_seconds: Number(elements.apiTimeout.value),
  } : undefined;
  if (apiOptions && (!apiOptions.base_url || !Number.isFinite(apiOptions.timeout_seconds)
      || apiOptions.timeout_seconds < 1 || apiOptions.timeout_seconds > 600)) {
    return showError("请填写 API 地址，并将超时设为 1 到 600 秒");
  }
  if (desktop && mode === "execute" && !window.confirm(
    `${cuaSelection ? `仅操作已选窗口 / 应用：${cuaSelection.labels.join("；")}。优先后台，明确拒绝后该窗口改走前台` : "将控制整个主显示器（可跨程序，并占用键鼠）"}，${elements.useExperience.checked ? `使用经验：${task.task_id}` : "不使用任何经验"}。目标画面会发送给 ${model}。\n\n${instruction}\n\n请先关闭敏感内容；窗口切前台期间请勿同时操作键鼠。F9 停止。确认继续？`
  )) return;
  if (!desktop && mode === "execute") {
    const confirmed = window.confirm(
      `手动选择经验：${task.task_id}\n即将用 ${modelLabels[model] || model} / ${effortLabels[reasoningEffort] || reasoningEffort}，以${inputMode === "background" ? "后台" : "前台"}模式控制 ${task.process_name || "目标窗口"} 并执行：\n\n${instruction}\n\n${inputMode === "background" ? "目标必须保持可见且不能最小化；不兼容后台消息或后台截图的应用会安全失败。\n\n" : ""}运行期间可按 F9 紧急停止。确认继续吗？`,
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
        provider,
        executor_backend: executorBackend,
        cua_target: cuaJobTarget(cuaSelection),
        api: apiOptions,
        reasoning_effort: reasoningEffort,
        input_mode: inputMode,
        adaptive_reasoning: adaptiveReasoning,
        use_experience: elements.useExperience.checked,
        execution_scope: desktop ? "desktop" : "window",
        orchestration: desktop ? elements.desktopOrchestration.value : "legacy",
        resume_from: desktop && elements.desktopOrchestration.value === "langgraph" ? elements.desktopResume.value : "",
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
  elements.recordStopButton.disabled = true;
  elements.narrationDiscard.disabled = true;
  try {
    await discardNarrationCapture();
    const job = await request(`/api/jobs/${activeJobId}/stop`, {
      method: "POST",
      body: "{}",
    });
    renderJob(job);
  } catch (error) {
    showError(error.message);
  } finally {
    elements.stopButton.disabled = false;
    elements.recordStopButton.disabled = false;
    elements.narrationDiscard.disabled = false;
  }
}

async function initialize() {
  try {
    attachVoiceInput(elements.instruction, "本次指令");
    attachVoiceInput(elements.recordName, "经验名称");
    attachVoiceInput(elements.narrationTranscript, "讲解转写");
    const state = await refreshState();
    elements.status.dataset.status = "idle";
    if (state.active_job) {
      renderJob(state.active_job);
      if (state.active_job.status === "awaiting_narration") {
        await prepareNarrationReview(state.active_job);
      } else if (state.active_job.status === "awaiting_recording_start"
        && state.active_job.kind === "waa_recording") {
        await prepareWaaRecordingStart(state.active_job);
        pollTimer = setTimeout(pollJob, 250);
      } else if (["queued", "running", "stopping"].includes(state.active_job.status)) {
        pollTimer = setTimeout(pollJob, 500);
      }
    } else {
      setBusy(false);
    }
  } catch (error) {
    showError(`控制台初始化失败：${error.message}`);
  }
}

elements.taskpack.addEventListener("change", () => {
  renderTaskMeta();
  syncProviderFields();
  setBusy(isBusy());
});
let windowScopeOptions = null;
async function refreshDesktopCheckpoints() {
  try {
    const data = await request("/api/desktop-checkpoints");
    const selected = elements.desktopResume.value;
    elements.desktopResume.replaceChildren(new Option("开始新任务", ""));
    for (const run of data.runs) {
      const option = new Option(`${run.name} · ${run.instruction}${run.uncertain ? "（结果不确定，需核对）" : ""}`, run.path);
      option.dataset.instruction = run.instruction;
      option.disabled = run.uncertain;
      elements.desktopResume.append(option);
    }
    if ([...elements.desktopResume.options].some(option => option.value === selected)) elements.desktopResume.value = selected;
  } catch (error) { showError(error.message); }
}
elements.desktopOrchestration.addEventListener("change", () => {
  syncProviderFields();
  setBusy(isBusy());
  refreshDesktopCheckpoints();
});
elements.desktopResume.addEventListener("focus", refreshDesktopCheckpoints);
elements.desktopResume.addEventListener("change", () => {
  const option = elements.desktopResume.selectedOptions[0];
  if (option?.value) {
    elements.instruction.value = option.dataset.instruction;
    elements.instruction.dispatchEvent(new Event("input"));
  }
});
elements.executionScope.addEventListener("change", () => {
  if (elements.executionScope.value === "desktop") {
    windowScopeOptions = {
      taskSelection: elements.taskpack.value,
      useExperience: elements.useExperience.checked,
      inputMode: elements.inputMode.value,
      adaptiveReasoning: elements.adaptiveReasoning.checked,
    };
    elements.useExperience.checked = false;
    elements.taskpack.value = "";
    elements.inputMode.value = "foreground";
    elements.adaptiveReasoning.checked = false;
    refreshDesktopCheckpoints();
  } else if (windowScopeOptions) {
    populateTaskpacks(taskpacks);
    elements.taskpack.value = windowScopeOptions.taskSelection;
    elements.useExperience.checked = windowScopeOptions.useExperience;
    elements.inputMode.value = windowScopeOptions.inputMode;
    elements.adaptiveReasoning.checked = windowScopeOptions.adaptiveReasoning;
    windowScopeOptions = null;
  }
  populateTaskpacks(taskpacks);
  renderInputModeHelp();
  syncProviderFields();
  setBusy(isBusy());
});
elements.inputMode.addEventListener("change", renderInputModeHelp);
elements.instruction.addEventListener("input", () => {
  elements.charCount.textContent = `${elements.instruction.value.length} / 2000`;
});
elements.modelProvider.addEventListener("change", syncProviderFields);
elements.localModel.addEventListener("change", syncProviderFields);
for (const field of [elements.localSystemPrompt, elements.localTurnTemplate]) {
  field.addEventListener("input", () => {
    promptDirty = true;
    elements.localPromptsStatus.textContent = "有未保存的修改；保存后用于下一次任务。";
  });
}
elements.localPromptsSave.addEventListener("click", () => saveLocalPrompts());
elements.localPromptsReset.addEventListener("click", () => saveLocalPrompts(true));
elements.chatPromptGuidance.addEventListener("input", () => {
  chatPromptDirty = true;
  elements.chatPromptsStatus.textContent = "有未保存的修改；保存后用于下一次任务。";
});
elements.chatPromptsSave.addEventListener("click", () => saveChatPrompts());
elements.chatPromptsReset.addEventListener("click", () => saveChatPrompts(true));
elements.apiBaseUrl.addEventListener("input", renderAPISettingsStatus);
elements.apiSaveSettings.addEventListener("click", saveAPISettings);
elements.apiClearSettings.addEventListener("click", clearAPISettings);
elements.executeButton.addEventListener("click", () => startJob("execute"));
elements.stopButton.addEventListener("click", stopJob);
elements.rsiInstruction.addEventListener("input", rsiSetControls);
elements.rsiStart.addEventListener("click", startRsiPractice);
elements.rsiStop.addEventListener("click", stopRsiPractice);
elements.rsiRefresh.addEventListener("click", () => refreshRsi());
elements.taskDetailBack.addEventListener("click", () => {
  closeTaskDetail();
  switchView("library");
});
window.addEventListener("hashchange", renderTaskDetailRoute);
elements.viewTabs.forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});
elements.recordSource.addEventListener("change", () => {
  renderRecordingSource();
  if (usesWaaRecording()) refreshWaaTasks();
});
elements.waaRoot.addEventListener("change", () => refreshWaaTasks({ force: true }));
elements.waaExample.addEventListener("change", () => {
  renderWaaTaskMeta();
  renderRecordingSource();
});
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
elements.recordButton.addEventListener("click", startRecording);
elements.recordStopButton.addEventListener("click", stopJob);
elements.narrationSubmit.addEventListener("click", submitNarration);
elements.narrationDiscard.addEventListener("click", stopJob);
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

function arrangeExecutionLayout() {
  const panel = document.querySelector("#execute-panel");
  const grid = document.createElement("div");
  grid.className = "task-primary-grid";
  panel.querySelector(".card-heading").after(grid);
  const field = (id) => {
    const group = document.createElement("div");
    group.className = "task-primary-field";
    const control = document.getElementById(id);
    group.append(panel.querySelector(`label[for="${id}"]`), control.closest(".select-wrap") || control);
    grid.append(group);
    return group;
  };
  field("operation-scope");
  field("taskpack");
  field("model-provider");
  const models = document.createElement("div");
  models.className = "task-primary-field";
  grid.append(models);
  const backendSettings = document.querySelector("#execution-backend-settings");
  backendSettings.classList.add("task-primary-field");
  grid.append(backendSettings);

  const advanced = document.createElement("details");
  advanced.id = "execution-advanced";
  advanced.className = "execution-advanced";
  const summary = document.createElement("summary");
  summary.textContent = "高级选项 · 连接配置、思考强度、输入模式与任务恢复";
  advanced.append(summary);
  panel.querySelector(".actions").after(advanced);
  const section = (id) => {
    const node = document.createElement("div");
    node.id = id;
    advanced.append(node);
    return node;
  };
  section("codex-advanced").append(elements.reasoningEffort.closest(".agent-settings > div"));
  models.append(elements.codexModelSettings, elements.localModelSettings);
  const local = section("local-advanced");
  elements.localModelSettings.querySelectorAll("p.field-help").forEach(p => local.append(p));
  const apiPrimary = document.createElement("div");
  apiPrimary.id = "api-model-primary";
  apiPrimary.append(elements.apiModel.closest(".agent-settings > div"));
  models.append(apiPrimary);
  advanced.append(elements.apiModelSettings, elements.desktopWorkflowSettings,
    panel.querySelector(".execution-options"), elements.inputModeHelp);
  const trainedAdvanced = section("trained-advanced");
  const trained = document.querySelector("#trained-model-settings");
  trained.querySelectorAll("p.field-help").forEach(p => trainedAdvanced.append(p));
  trainedAdvanced.append(elements.localPromptSettings);
  advanced.append(elements.chatPromptSettings);
  const logDetails = document.createElement("details");
  logDetails.className = "runtime-log-details";
  const logSummary = document.createElement("summary");
  logSummary.textContent = "查看运行日志";
  const heading = elements.jobLog.previousElementSibling;
  heading.before(logDetails);
  logDetails.append(logSummary, heading, elements.jobLog);
  const resultDetails = document.createElement("details");
  const resultSummary = document.createElement("summary");
  resultSummary.textContent = "查看完整结果数据";
  elements.jobResult.before(resultDetails);
  resultDetails.append(resultSummary, elements.jobResult);
  renderInputModeHelp();
}

arrangeExecutionLayout();
renderRecordingSource();
initialize();

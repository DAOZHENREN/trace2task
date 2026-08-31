const elements = {
  version: document.querySelector("#version"),
  taskpack: document.querySelector("#taskpack"),
  taskMeta: document.querySelector("#task-meta"),
  model: document.querySelector("#model"),
  reasoningEffort: document.querySelector("#reasoning-effort"),
  inputMode: document.querySelector("#input-mode"),
  inputModeHelp: document.querySelector("#input-mode-help"),
  adaptiveReasoning: document.querySelector("#adaptive-reasoning"),
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
  jobPerformance: document.querySelector("#job-performance"),
  jobResult: document.querySelector("#job-result"),
  liveDot: document.querySelector("#live-dot"),
  stopButton: document.querySelector("#stop-button"),
  viewTabs: [...document.querySelectorAll(".view-tab")],
  viewPanels: [...document.querySelectorAll(".view-panel")],
  recordSource: document.querySelector("#record-source"),
  localRecordingFields: document.querySelector("#local-recording-fields"),
  waaRecordingFields: document.querySelector("#waa-recording-fields"),
  waaRoot: document.querySelector("#waa-root"),
  waaExample: document.querySelector("#waa-example"),
  waaTaskMeta: document.querySelector("#waa-task-meta"),
  recordWindow: document.querySelector("#record-window"),
  recordName: document.querySelector("#record-name"),
  recordModel: document.querySelector("#record-model"),
  recordReasoningEffort: document.querySelector("#record-reasoning-effort"),
  recordNarration: document.querySelector("#record-narration"),
  narrationReview: document.querySelector("#narration-review"),
  narrationTranscript: document.querySelector("#narration-transcript"),
  narrationStatus: document.querySelector("#narration-status"),
  narrationSubmit: document.querySelector("#narration-submit"),
  narrationDiscard: document.querySelector("#narration-discard"),
  compilerModel: document.querySelector("#compiler-model"),
  compilerReasoningEffort: document.querySelector("#compiler-reasoning-effort"),
  experienceModelSummary: document.querySelector("#experience-model-summary"),
  windowMeta: document.querySelector("#window-meta"),
  refreshWindows: document.querySelector("#refresh-windows"),
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
  taskDetailTitle: document.querySelector("#task-detail-title"),
  taskDetailMeta: document.querySelector("#task-detail-meta"),
  taskDetailTags: document.querySelector("#task-detail-tags"),
  taskDetailBody: document.querySelector("#task-detail-body"),
  taskDetailActions: document.querySelector("#task-detail-actions"),
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
let localWindows = [];
let activeJobId = null;
let pollTimer = null;
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
const taskDetailFragments = new Map();

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
  elements.waaTaskMeta.textContent = `${task.instruction} · 应用：${apps} · Evaluator：${evaluator} · Reset：${task.reset_paths.length} 条规则已配置`;
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
      option.textContent = `${task.domain} · ${task.instruction}`;
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
  elements.localRecordingFields.classList.toggle("hidden", waa);
  elements.waaRecordingFields.classList.toggle("hidden", !waa);
  elements.recordButton.disabled = isBusy()
    || (!waa && !selectedWindow())
    || (waa && !selectedWaaTask());
}

function switchView(view) {
  closeTaskDetail();
  document.body.classList.toggle("library-mode", view === "library");
  elements.viewTabs.forEach((tabButton) => {
    tabButton.classList.toggle("active", tabButton.dataset.view === view);
  });
  elements.viewPanels.forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== `${view}-panel`);
  });
  if (view === "record" && !usesWaaRecording() && !localWindows.length) refreshWindows();
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

function renderInputModeHelp() {
  elements.inputModeHelp.textContent = elements.inputMode.value === "background"
    ? "后台执行不会抢占焦点，但目标必须保持可见且不能最小化；部分游戏、模拟器和 GPU 窗口不兼容。"
    : "前台执行会聚焦目标窗口；适用于游戏、模拟器和不接受后台消息的应用。";
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

function makePerformanceTimeline(performance, stages = []) {
  if (!performance || !Object.keys(performance).length) return null;
  const details = document.createElement("details");
  details.className = "performance-timeline";
  const summary = document.createElement("summary");
  summary.textContent = `查看性能时间轴 · 模型 ${formatDuration(performance.model_roundtrip_ms || performance.planning_ms)} / 总计 ${formatDuration(performance.total_elapsed_ms)}`;
  const grid = document.createElement("div");
  grid.className = "performance-grid";
  [
    ["总耗时", performance.total_elapsed_ms],
    ["规划总计", performance.planning_ms],
    ["模型回合", performance.model_roundtrip_ms],
    ["模型生成等待", performance.model_completion_wait_ms],
    ["截图", performance.capture_ms],
    ["显式等待", performance.explicit_wait_ms],
    ["本地等稳", performance.local_wait_until_ms],
    ["本地动作", performance.action_ms],
  ].forEach(([label, value]) => {
    const metric = document.createElement("div");
    const name = document.createElement("span");
    const duration = document.createElement("strong");
    name.textContent = label;
    duration.textContent = formatDuration(value);
    metric.append(name, duration);
    grid.append(metric);
  });
  details.append(summary, grid);
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
    if (task.experience_family_id) {
      const family = document.createElement("span");
      family.className = "mini-tag";
      family.textContent = `经验族：${task.experience_family_id}`;
      tags.append(family);
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
    title.textContent = recording.task_id || "未命名录制";
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
    if (recording.success) {
      actions.append(
        makeMiniButton(
          "编译 / 重试",
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

function openTaskDetail(task) {
  window.location.hash = `task/${encodeURIComponent(task.path)}`;
  renderTaskDetailRoute();
}

function closeTaskDetail(updateHistory = true) {
  document.body.classList.remove("task-detail-mode");
  elements.taskDetailPanel.classList.add("hidden");
  if (updateHistory && taskDetailPathFromHash()) {
    history.pushState(null, "", `${window.location.pathname}${window.location.search}`);
  }
}

function renderTaskDetailRoute() {
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

function isBusy() {
  return ["queued", "running", "stopping", "awaiting_recording_start", "awaiting_narration"]
    .includes(elements.status.dataset.status);
}

function setBusy(busy) {
  elements.planButton.disabled = busy || !taskpacks.length;
  const task = selectedTask();
  elements.executeButton.disabled = busy || (task ? !canExecuteTask(task) : !canAutoExecute());
  elements.taskpack.disabled = busy;
  elements.model.disabled = busy;
  elements.reasoningEffort.disabled = busy;
  elements.inputMode.disabled = busy;
  elements.adaptiveReasoning.disabled = busy;
  elements.instruction.disabled = busy;
  elements.viewTabs.forEach((button) => { button.disabled = busy; });
  elements.recordSource.disabled = busy;
  elements.waaRoot.disabled = busy;
  elements.waaExample.disabled = busy;
  elements.recordWindow.disabled = busy;
  elements.recordName.disabled = busy;
  elements.recordModel.disabled = busy;
  elements.recordReasoningEffort.disabled = busy;
  elements.recordNarration.disabled = busy;
  elements.compilerModel.disabled = busy;
  elements.compilerReasoningEffort.disabled = busy;
  elements.refreshWindows.disabled = busy;
  elements.recordButton.disabled = busy
    || (!usesWaaRecording() && !selectedWindow())
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
        : job.mode === "execute" ? "执行" : "预演";
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
  const busy = ["queued", "running", "stopping", "awaiting_recording_start", "awaiting_narration"].includes(job.status);
  elements.liveDot.classList.toggle(
    "active",
    ["queued", "running", "stopping", "awaiting_recording_start"].includes(job.status),
  );
  elements.stopButton.classList.toggle(
    "hidden",
    !busy || !["execute", "record"].includes(job.mode),
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
      );
      if (performanceTimeline) elements.jobPerformance.append(performanceTimeline);
    }
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
    && state.capabilities?.waa_task_catalog === true;
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
    elements.recordButton.disabled = !usesWaaRecording() || !selectedWaaTask();
    return;
  }
  elements.windowMeta.textContent = `${windowInfo.client_width} × ${windowInfo.client_height} · ${windowInfo.is_foreground ? "当前前台" : "录制时自动切换到前台"}`;
  elements.recordButton.disabled = isBusy();
}

async function startRecording() {
  clearRecordError();
  if (dictationSession) {
    return showRecordError("请先结束语音输入并等待转写完成");
  }
  const waa = usesWaaRecording();
  const windowInfo = selectedWindow();
  const taskId = elements.recordName.value.trim();
  if (!waa && !windowInfo) return showRecordError("请选择一个本地目标窗口");
  if (!taskId) return showRecordError("请输入经验名称");
  if (waa && !elements.waaRoot.value.trim()) return showRecordError("请输入 WAA 根目录");
  if (waa && !selectedWaaTask()) return showRecordError("请选择一个 WAA 标准任务");
  const narrated = elements.recordNarration.checked;
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
        model: elements.recordModel.value,
        reasoning_effort: elements.recordReasoningEffort.value,
      } : {
        handle: windowInfo.handle,
        task_id: taskId,
        narrated,
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
    `确认启用任务状态图 v${proposal.proposed_revision} 吗？\n\n只会替换派生的任务说明、状态图和结束条件；原始 Trace 与旧版本都会保留。`,
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

async function startJob(mode) {
  clearError();
  if (dictationSession) return showError("请先结束语音输入并等待转写完成");
  const task = selectedTask();
  const instruction = elements.instruction.value.trim();
  const model = elements.model.value;
  const reasoningEffort = elements.reasoningEffort.value;
  const inputMode = elements.inputMode.value;
  const adaptiveReasoning = elements.adaptiveReasoning.checked;
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
      `${routeSummary}\n即将用 ${modelLabels[model] || model} / ${effortLabels[reasoningEffort] || reasoningEffort}，以${inputMode === "background" ? "后台" : "前台"}模式控制 ${executionTask.process_name || "目标窗口"} 并执行：\n\n${instruction}\n\n${inputMode === "background" ? "目标必须保持可见且不能最小化；不兼容后台消息或后台截图的应用会安全失败。\n\n" : ""}运行期间可按 F9 紧急停止。确认继续吗？`,
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
        input_mode: inputMode,
        adaptive_reasoning: adaptiveReasoning,
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

elements.taskpack.addEventListener("change", renderTaskMeta);
elements.inputMode.addEventListener("change", renderInputModeHelp);
elements.instruction.addEventListener("input", () => {
  elements.charCount.textContent = `${elements.instruction.value.length} / 2000`;
});
elements.planButton.addEventListener("click", () => startJob("plan"));
elements.executeButton.addEventListener("click", () => startJob("execute"));
elements.stopButton.addEventListener("click", stopJob);
elements.taskDetailBack.addEventListener("click", () => {
  closeTaskDetail();
  switchView("library");
});
window.addEventListener("hashchange", renderTaskDetailRoute);
elements.viewTabs.forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});
elements.recordWindow.addEventListener("change", renderWindowMeta);
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
elements.refreshWindows.addEventListener("click", refreshWindows);
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

renderRecordingSource();
initialize();

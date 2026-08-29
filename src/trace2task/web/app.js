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
  recordWindow: document.querySelector("#record-window"),
  recordName: document.querySelector("#record-name"),
  recordModel: document.querySelector("#record-model"),
  recordReasoningEffort: document.querySelector("#record-reasoning-effort"),
  recordNarration: document.querySelector("#record-narration"),
  narrationReview: document.querySelector("#narration-review"),
  narrationTranscript: document.querySelector("#narration-transcript"),
  narrationStatus: document.querySelector("#narration-status"),
  narrationSubmit: document.querySelector("#narration-submit"),
  compilerModel: document.querySelector("#compiler-model"),
  compilerReasoningEffort: document.querySelector("#compiler-reasoning-effort"),
  revisionModel: document.querySelector("#revision-model"),
  revisionReasoningEffort: document.querySelector("#revision-reasoning-effort"),
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
  const revisionDefaults = options.revision_defaults || compilerDefaults;
  const previousRevisionModel = elements.revisionModel.value || revisionDefaults.model;
  const previousRevisionEffort = elements.revisionReasoningEffort.value
    || revisionDefaults.reasoning_effort;

  elements.model.replaceChildren();
  elements.recordModel.replaceChildren();
  elements.compilerModel.replaceChildren();
  elements.revisionModel.replaceChildren();
  (options.models || []).forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = modelLabels[model] || model;
    elements.model.append(option);
    elements.recordModel.append(option.cloneNode(true));
    elements.compilerModel.append(option.cloneNode(true));
    elements.revisionModel.append(option.cloneNode(true));
  });
  elements.reasoningEffort.replaceChildren();
  elements.recordReasoningEffort.replaceChildren();
  elements.compilerReasoningEffort.replaceChildren();
  elements.revisionReasoningEffort.replaceChildren();
  (options.reasoning_efforts || []).forEach((effort) => {
    const option = document.createElement("option");
    option.value = effort;
    option.textContent = effortLabels[effort] || effort;
    elements.reasoningEffort.append(option);
    elements.recordReasoningEffort.append(option.cloneNode(true));
    elements.compilerReasoningEffort.append(option.cloneNode(true));
    elements.revisionReasoningEffort.append(option.cloneNode(true));
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
  elements.revisionModel.value = [...elements.revisionModel.options].some(
    (option) => option.value === previousRevisionModel,
  ) ? previousRevisionModel : revisionDefaults.model;
  elements.revisionReasoningEffort.value = [...elements.revisionReasoningEffort.options].some(
    (option) => option.value === previousRevisionEffort,
  ) ? previousRevisionEffort : revisionDefaults.reasoning_effort;
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
      summary.textContent = `查看 Compiler Agent 阶段 · ${task.semantic_experience.summary}`;
      const stages = document.createElement("div");
      stages.className = "semantic-stages";
      const canonicalInstruction = document.createElement("p");
      canonicalInstruction.className = "semantic-contract";
      canonicalInstruction.textContent = `标准任务说明：${task.semantic_experience.canonical_instruction}`;
      const completionPolicy = document.createElement("p");
      completionPolicy.className = "semantic-contract";
      completionPolicy.textContent = `完成条件：${task.semantic_experience.completion.success_condition}（${task.semantic_experience.completion.reason}）`;
      stages.append(canonicalInstruction, completionPolicy);
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
        revisionSummary.textContent = `v${revision.revision}${revision.is_active ? " · 当前生效" : ""} · ${modeLabel} · ${revision.rule_count} 条`;
        const revisionBody = document.createElement("div");
        revisionBody.className = "guidance-revision-body";
        const summaryText = document.createElement("p");
        summaryText.className = "guidance-revision-summary";
        summaryText.textContent = revision.summary || "未记录版本摘要";
        revisionBody.append(summaryText);
        if (revision.feedback) {
          const feedback = document.createElement("p");
          feedback.className = "guidance-feedback";
          feedback.textContent = `本轮人工反馈：${revision.feedback}`;
          revisionBody.append(feedback);
        }
        if (revision.merge_mode === "incremental") {
          const changes = document.createElement("div");
          changes.className = "guidance-changes";
          (revision.operations || []).forEach((operation) => {
            const change = document.createElement("p");
            const ruleId = operation.result_rule_id || operation.target_rule_id || "新规则";
            change.textContent = `${operationLabels[operation.operation] || operation.operation} ${ruleId}（${operation.stage_id}）：${operation.reason}`;
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
        rules.className = "semantic-stages";
        (revision.rules || []).forEach((rule, index) => {
          const ruleItem = document.createElement("div");
          ruleItem.className = "semantic-stage";
          const ruleTitle = document.createElement("strong");
          ruleTitle.textContent = `${index + 1}. ${rule.id || "未命名规则"} · ${rule.stage_id} · ${rule.priority}`;
          const ruleBody = document.createElement("p");
          ruleBody.textContent = `当：${rule.when} → 优先：${rule.prefer} → 预期：${rule.expected_effect}`;
          ruleItem.append(ruleTitle, ruleBody);
          if (rule.avoid?.length) {
            const avoid = document.createElement("p");
            avoid.className = "semantic-uncertain";
            avoid.textContent = `避免：${rule.avoid.join("；")}`;
            ruleItem.append(avoid);
          }
          if (rule.replan_when?.length) {
            const replan = document.createElement("p");
            replan.className = "semantic-uncertain";
            replan.textContent = `重新规划条件：${rule.replan_when.join("；")}`;
            ruleItem.append(replan);
          }
          rules.append(ruleItem);
        });
        revisionBody.append(rules);
        revisionDetails.append(revisionSummary, revisionBody);
        timeline.append(revisionDetails);
      });
      guidanceDetails.append(guidanceSummary, timeline);
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
    if (guidanceDetails) item.append(guidanceDetails);
    item.append(actions);
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
      ? "任务完成"
      : taskComplete === false
        ? `任务未完成${candidate.outcome?.stop_reason ? ` · ${candidate.outcome.stop_reason}` : ""}`
        : "历史运行 · 结果未记录";
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
          heading.textContent = `${operationLabels[change.operation] || change.operation} · ${ruleId} · ${change.stage_id}`;
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
        revisionPanel.append(changeDetails, proposalLabel, proposal, proposalActions);
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
      revisionPanel.append(feedbackLabel, feedback, feedbackActions);
    }
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

function narrationMimeType() {
  if (!window.MediaRecorder) return "";
  return ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]
    .find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function renderLiveNarration(capture, interim = "") {
  const text = [...capture.finalParts, interim].filter(Boolean).join(" ").trim();
  elements.narrationTranscript.value = text;
}

async function startNarrationCapture() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    throw new Error("当前浏览器不支持麦克风录制，请关闭“同时录制人工讲解”后重试");
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
    recognition: null,
    recognitionAvailable: false,
    finalParts: [],
    segments: [],
    startedAt: performance.now(),
    lastSegmentEnd: 0,
    active: true,
    blob: null,
    audioArchived: false,
    transcriptionEngine: null,
  };
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data?.size) capture.chunks.push(event.data);
  });
  recorder.start(1000);

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
  capture.active = false;
  if (capture.recognition) {
    try { capture.recognition.stop(); } catch (_) { /* already stopped */ }
  }
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
  return ["queued", "running", "stopping", "awaiting_narration"]
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
  elements.recordWindow.disabled = busy;
  elements.recordName.disabled = busy;
  elements.recordModel.disabled = busy;
  elements.recordReasoningEffort.disabled = busy;
  elements.recordNarration.disabled = busy;
  elements.compilerModel.disabled = busy;
  elements.compilerReasoningEffort.disabled = busy;
  elements.revisionModel.disabled = busy;
  elements.revisionReasoningEffort.disabled = busy;
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
  const busy = ["queued", "running", "stopping", "awaiting_narration"].includes(job.status);
  elements.liveDot.classList.toggle(
    "active",
    ["queued", "running", "stopping"].includes(job.status),
  );
  elements.stopButton.classList.toggle(
    "hidden",
    !busy || !["execute", "record"].includes(job.mode),
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

async function pollJob() {
  if (!activeJobId) return;
  try {
    const job = await request(`/api/jobs/${activeJobId}`);
    renderJob(job);
    if (job.status === "awaiting_narration") {
      await prepareNarrationReview(job);
    } else if (["queued", "running", "stopping"].includes(job.status)) {
      pollTimer = setTimeout(pollJob, 850);
    } else if ((["recording", "compilation", "revision"].includes(job.kind)
      || job.result?.candidate_experience)
      && refreshedRecordingJobId !== job.job_id) {
      if (job.kind === "recording" && narrationCapture) await discardNarrationCapture();
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
  const backendSupportsV13 = state.capabilities?.coordinate_isolation === true
    && state.capabilities?.narration_claim_audit === true
    && state.capabilities?.experience_family_inheritance === true;
  elements.version.textContent = backendSupportsV13
    ? `v${state.version}`
    : `v${state.version} · 需要重启`;
  if (!backendSupportsIncrementalGuidance) {
    showError("网页后台仍是旧版本。请停止并重新启动 trace2task web；在此之前已确认经验可能被整包覆写。");
  } else if (!backendSupportsV13) {
    showError("网页后台还没有加载 V0.13。请停止并重新启动 trace2task web；重启前运行 Agent 仍可能收到旧的原始动作上下文。");
  }
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
  const narrated = elements.recordNarration.checked;
  setBusy(true);
  try {
    if (narrated) await startNarrationCapture();
    const job = await request("/api/recordings", {
      method: "POST",
      body: JSON.stringify({
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
        model: elements.revisionModel.value,
        reasoning_effort: elements.revisionReasoningEffort.value,
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
    `删除任务经验“${task.task_id}”吗？\n\n它会移动到 taskpacks/.trash，可以手动恢复；原始录制不会被删除。`,
  );
  if (!confirmed) return;
  await deleteLocalAsset("/api/taskpacks/delete", { task_path: task.path });
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
      if (state.active_job.status === "awaiting_narration") {
        await prepareNarrationReview(state.active_job);
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
elements.narrationSubmit.addEventListener("click", submitNarration);
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

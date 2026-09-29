import json
import re
import subprocess
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from trace2task.rsi_remote import RSIRemoteProfile, RSIRemoteRequestError
from trace2task.web_console import WebConsoleController, _rsi_event_summary, create_web_server

RUN_ID = "a" * 32
DIGEST = "b" * 64


class Remote:
    profile = RSIRemoteProfile(
        ssh_host="dongjintao@100.70.228.85",
        remote_config_path="/mnt/sda/dongjintao/trace2task-rsi/deployment.json",
        control_launcher_argv=("python", "-m", "trace2task.rsi_control"),
        max_model_calls=30,
        max_wall_seconds=1_200,
        max_project_budget=3,
    )

    def __init__(self):
        self.calls = []
        self.run = {
            "id": RUN_ID, "state": "running", "created": "2026-09-26T00:00:00+00:00",
            "updated": "2026-09-26T00:00:01+00:00", "stop_requested": False,
            "candidate_sha256": DIGEST,
            "spec": {"instruction": "Practice reliable saving", "model": "gpt-6-astra",
                     "reasoning_effort": "low", "max_model_calls": 20,
                     "wall_seconds": 900, "project_budget": 1},
        }

    def health(self):
        return {"ready": True, "message": "server-ready", "secret": "must not leak"}

    def list(self, *, limit):
        self.calls.append(("list", limit))
        return {"runs": [self.run]}

    def get(self, run_id):
        self.calls.append(("get", run_id))
        return {"run": self.run}

    def events(self, run_id, *, after):
        self.calls.append(("events", run_id, after))
        return {"events": [{"seq": 1, "time": "2026-09-26T00:00:01+00:00", "kind": "state",
                            "payload": {"state": "running", "secret": "not exposed"}}]}

    def start(self, **payload):
        self.calls.append(("start", payload))
        return {"run": self.run}

    def stop(self, run_id):
        self.calls.append(("stop", run_id))
        return {"run": {**self.run, "stop_requested": True}, "stop_status": "pending"}

    def recovery(self, run_id):
        self.calls.append(("recovery", run_id))
        return {
            "eligible": True,
            "reason": "completed_boundary_verified",
            "boundary_projects": 1,
            "remaining_model_calls": 6,
            "remaining_wall_ms": 480_000,
            "private_path": "/mnt/private/never-display",
        }

    def recover(self, run_id):
        self.calls.append(("recover", run_id))
        return {"run": {**self.run, "state": "preflight"}}

    def candidate(self, run_id, *, digest):
        self.calls.append(("candidate", run_id, digest))
        return {"candidate": {"memory": {"rule.txt": {"text": "<b>untrusted prose</b>"}},
                "verification": {"verdict": "PASS", "artifact": "episodes/1/outcome.json"}},
                "sha256": digest}

    def review(self, run_id, *, digest, decision, note):
        self.calls.append(("review", run_id, digest, decision, note))
        return {"run": {**self.run, "state": "completed", "review": {
            "decision": decision, "note": note, "sha256": digest, "time": "2026-09-26T00:02:00+00:00",
        }}}


def csrf(opener, base):
    with opener.open(base + "/") as response:
        page = response.read().decode("utf-8")
    token = re.search(r'name="trace2task-csrf" content="([^"]+)"', page)[1]
    return {"Content-Type": "application/json", "Origin": base, "X-Trace2Task-CSRF": token}


def test_rsi_controller_is_separate_from_local_job_and_sanitizes_remote_data(tmp_path: Path):
    remote = Remote()
    controller = WebConsoleController(tmp_path, rsi_client=remote)
    health = controller.rsi_health()
    assert health["ready"] is True
    assert "secret" not in json.dumps(health)
    listed = controller.list_rsi_runs(10)
    assert listed["runs"][0]["id"] == RUN_ID
    assert controller.active_job() is None
    events = controller.rsi_events(RUN_ID)
    assert events["events"][0]["payload"] == {"state": "running"}
    candidate = controller.get_rsi_candidate(RUN_ID, DIGEST)
    assert candidate["candidate"]["memory"]["rule.txt"]["text"] == "<b>untrusted prose</b>"
    reviewed = controller.review_rsi_candidate({
        "run_id": RUN_ID, "digest": DIGEST, "decision": "accepted", "note": "archive only",
    })
    assert reviewed["run"]["review"]["decision"] == "accepted"
    recovery = controller.rsi_recovery(RUN_ID)
    assert recovery["recovery"] == {
        "eligible": True,
        "reason": "completed_boundary_verified",
        "boundary_projects": 1,
        "remaining_model_calls": 6,
        "remaining_wall_ms": 480_000,
    }
    assert controller.recover_rsi_practice(RUN_ID)["run"]["state"] == "preflight"
    assert controller.active_job() is None


def test_rsi_detail_projects_only_allowlisted_ledger_fields():
    event = _rsi_event_summary({
        "seq": 8, "time": "2026-09-26T00:00:01+00:00", "kind": "attempt_finalized",
        "payload": {
            "attempt_no": 2, "model_calls_used": 7, "active_elapsed_ms": 139_910,
            "cleanup_confirmed": True, "private_path": "/srv/private", "nested": {"secret": True},
        },
    })
    assert event["payload"] == {
        "attempt_no": 2, "model_calls_used": 7, "active_elapsed_ms": 139_910,
        "cleanup_confirmed": True,
    }
    source = (Path(__file__).parents[1] / "src/trace2task/web/app.js").read_text(encoding="utf-8")
    assert 'ledgerHeading.textContent = "调用与清理账本"' in source
    assert "未结算的执行时间会在清理完成后入账" in source


def test_rsi_background_poll_is_ledger_only_but_manual_refresh_rechecks_health():
    source = (Path(__file__).parents[1] / "src/trace2task/web/app.js").read_text(encoding="utf-8")
    assert "const RSI_POLL_INTERVAL_MS = 3_000;" in source
    assert "const shouldRefreshHealth = !poll || !rsiHealth;" in source
    assert "if (shouldRefreshHealth) {\n      rsiHealth = await request(\"/api/rsi/health\");" in source
    assert "setTimeout(() => refreshRsi({poll: true}), RSI_POLL_INTERVAL_MS)" in source


def _javascript_function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"Could not extract JavaScript function {name}")


def test_rsi_candidate_identity_is_scoped_by_run_and_digest_in_javascript():
    source = (Path(__file__).parents[1] / "src/trace2task/web/app.js").read_text(encoding="utf-8")
    run_one, run_two, digest = "a" * 32, "c" * 32, "b" * 64
    program = "\n".join((
        f"let selectedRsiRunId = {run_one!r};",
        (f"let rsiRuns = [{{id: {run_one!r}, candidate_sha256: {digest!r}, review: null}}, "
         f"{{id: {run_two!r}, candidate_sha256: {digest!r}, review: null}}];"),
        _javascript_function(source, "rsiCandidateKey"),
        _javascript_function(source, "rsiSelectedCandidateMatches"),
        ("console.log(JSON.stringify({"
         f"first: rsiCandidateKey({run_one!r}, {digest!r}), "
         f"second: rsiCandidateKey({run_two!r}, {digest!r}), "
         f"selected: rsiSelectedCandidateMatches({run_one!r}, {digest!r}), "
         f"other: rsiSelectedCandidateMatches({run_two!r}, {digest!r}), "
         f"reviewed: (rsiRuns[0].review = {{}}, rsiSelectedCandidateMatches({run_one!r}, {digest!r})), "
         f"readReviewed: rsiSelectedCandidateMatches({run_one!r}, {digest!r}, true), "
         f"readOther: rsiSelectedCandidateMatches({run_two!r}, {digest!r}, true)"
         "}));"),
    ))
    output = subprocess.run(["node", "-e", program], check=True, capture_output=True, text=True).stdout
    result = json.loads(output)
    assert result["first"] != result["second"]
    assert result["selected"] is True
    assert result["other"] is False
    assert result["reviewed"] is False
    assert result["readReviewed"] is True
    assert result["readOther"] is False
    assert "if (run && updateFocusedRsiDetail(run)) return;" in source
    assert "if (run.review || !rsiSelectedCandidateMatches(run.id, run.candidate_sha256)" in source
    assert "if (!rsiSelectedCandidateMatches(runId, digest, true) || rsiCandidateViews.get(key) !== entry) return;" in source
    assert 'run.review ? "查看已归档候选（只读）" : "查看已验证候选"' in source


@pytest.mark.parametrize("decision", ["accepted", "rejected"])
def test_rsi_archived_candidate_remains_readable_without_review_controls(decision):
    source = (Path(__file__).parents[1] / "src/trace2task/web/app.js").read_text(encoding="utf-8")
    program = "\n".join((
        ("class Element { constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; } "
         "append(...children) { this.children.push(...children); } addEventListener() {} }"),
        "const document = {createElement: (tag) => new Element(tag)};",
        "function makeMiniButton(text) { const b = new Element('button'); b.textContent = text; return b; }",
        _javascript_function(source, "rsiCandidateKey"),
        _javascript_function(source, "makeRsiCandidateViewer"),
        ("const entry = {candidate: {memory: {'rule.txt': {text: '<b>literal evidence</b>', sha256: 'abc'}}, "
         "verification: {verdict: 'PASS', artifact: 'episodes/ep001/outcome.json'}}};"),
        f"const archived = makeRsiCandidateViewer({{id: 'run', candidate_sha256: 'hash', review: {{decision: {decision!r}}}}}, entry);",
        "const pending = makeRsiCandidateViewer({id: 'run', candidate_sha256: 'hash'}, entry);",
        "function flatten(node) { return [node, ...node.children.flatMap(flatten)]; }",
        "console.log(JSON.stringify({archived: flatten(archived), pending: flatten(pending)}));",
    ))
    output = subprocess.run(
        ["node", "-e", program], check=True, capture_output=True, encoding="utf-8",
    ).stdout
    result = json.loads(output)
    assert not any(node["tag"] in {"textarea", "button"} for node in result["archived"])
    assert any(node.get("textContent") == "<b>literal evidence</b>" for node in result["archived"])
    assert any(node["tag"] == "textarea" for node in result["pending"])
    assert len([node for node in result["pending"] if node["tag"] == "button"]) == 2


def test_rsi_active_practice_error_is_actionable_in_the_local_console(tmp_path: Path, monkeypatch):
    remote = Remote()
    monkeypatch.setattr(
        remote,
        "start",
        lambda **_payload: (_ for _ in ()).throw(RSIRemoteRequestError("practice_already_active")),
    )
    with pytest.raises(RuntimeError, match="已有远程练习正在运行，请刷新查看，不要重复启动"):
        WebConsoleController(tmp_path, rsi_client=remote).start_rsi_practice({
            "instruction": "Practice reliable saving", "model": "gpt-6-astra", "reasoning_effort": "low",
            "max_model_calls": 20, "wall_seconds": 900, "project_budget": 1,
        })


def test_rsi_http_requires_csrf_and_exposes_only_allowlisted_remote_operations(tmp_path: Path):
    remote = Remote()
    server = create_web_server(tmp_path, port=0, controller=WebConsoleController(tmp_path, rsi_client=remote))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(base + "/api/rsi/health") as response:
            health = json.load(response)
        assert health["ready"] is True and "secret" not in health
        with opener.open(base + "/api/rsi/runs?limit=1") as response:
            assert json.load(response)["runs"][0]["id"] == RUN_ID
        with pytest.raises(HTTPError) as error:
            opener.open(Request(base + "/api/rsi/start", data=b"{}", headers={"Content-Type": "application/json"}))
        assert error.value.code == 400
        request = Request(base + "/api/rsi/start", data=json.dumps({
            "instruction": "Practice reliable saving", "model": "gpt-6-astra", "reasoning_effort": "low",
            "max_model_calls": 20, "wall_seconds": 900, "project_budget": 1,
            "ssh_host": "attacker@host", "command": "do-not-run",
        }).encode(), headers=csrf(opener, base))
        with opener.open(request) as response:
            assert json.load(response)["run"]["id"] == RUN_ID
        assert remote.calls[-1] == ("start", {
            "instruction": "Practice reliable saving", "model": "gpt-6-astra", "reasoning_effort": "low",
            "max_model_calls": 20, "wall_seconds": 900, "project_budget": 1,
        })
        with opener.open(base + f"/api/rsi/candidate?run_id={RUN_ID}&digest={DIGEST}") as response:
            assert json.load(response)["candidate"]["memory"]["rule.txt"]["text"].startswith("<b>")
        with opener.open(base + f"/api/rsi/recovery?run_id={RUN_ID}") as response:
            assert json.load(response)["recovery"]["eligible"] is True
        request = Request(
            base + "/api/rsi/recover",
            data=json.dumps({"run_id": RUN_ID, "ssh_host": "attacker@host"}).encode(),
            headers=csrf(opener, base),
        )
        with opener.open(request) as response:
            assert json.load(response)["run"]["state"] == "preflight"
        assert remote.calls[-1] == ("recover", RUN_ID)
        source = (Path(__file__).parents[1] / "src/trace2task/web/app.js").read_text(encoding="utf-8")
        assert "rawContent.textContent = JSON.stringify(candidate, null, 2)" in source
        assert "innerHTML" not in source[source.index("async function loadRsiCandidate"):source.index("function isBusy")]
        assert "从已完成项目边界恢复" in source
        assert "未完成项目的操作和临时环境不会重放" in source
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_rsi_health_is_clear_when_no_profile_is_configured(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TRACE2TASK_RSI_PROFILE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no-profile-here"))
    health = WebConsoleController(tmp_path).rsi_health()
    assert health == {
        "configured": False, "ready": False,
        "connection_state": "unconfigured",
        "message": "未配置远程 RSI 部署（未找到本机部署配置）",
        "checks": [], "models": [], "reasoning_efforts": [],
    }


def test_rsi_view_hides_only_the_local_activity_card_and_labels_new_run_budget():
    web_root = Path(__file__).parents[1] / "src/trace2task/web"
    app = (web_root / "app.js").read_text(encoding="utf-8")
    html = (web_root / "index.html").read_text(encoding="utf-8")
    styles = (web_root / "styles.css").read_text(encoding="utf-8")

    assert 'document.body.classList.toggle("rsi-mode", view === "rsi");' in app
    assert ".rsi-mode .activity-card { display: none; }" in styles
    assert "以下方向、模型与预算仅用于新建隔离练习" in html
    assert 'id="rsi-max-calls" type="number" min="1" max="50" value="25"' in html
    assert 'id="rsi-wall-seconds" type="number" min="1" max="3600" value="1800"' in html
    assert "默认预算覆盖虚拟机初始化、项目执行与回滚；30 分钟是最长上限，不是固定等待时间。" in html
    assert "clampToProfileMaximum(elements.rsiMaxCalls, health?.max_model_calls);" in app


def test_rsi_completed_without_candidate_is_not_presented_as_success():
    web_root = Path(__file__).parents[1] / "src/trace2task/web"
    app = (web_root / "app.js").read_text(encoding="utf-8")

    assert 'run.state === "completed"\n    ? "练习已结束"' in app
    assert 'run.state === "completed" && !run.candidate_sha256' in app
    assert "练习流程已结束，但未生成可审查的已验证候选；流程结束不代表每个项目成功，请查看项目的独立验证结果。" in app


def test_rsi_candidate_review_prioritizes_memory_text_and_verification_provenance():
    web_root = Path(__file__).parents[1] / "src/trace2task/web"
    app = (web_root / "app.js").read_text(encoding="utf-8")
    styles = (web_root / "styles.css").read_text(encoding="utf-8")

    assert 'memoryHeading.textContent = "候选记忆文件"' in app
    assert 'fileName.textContent = name;' in app
    assert 'fileText.textContent = typeof value?.text === "string" ? value.text : "[未提供可读文本]";' in app
    assert "独立验证来源：${source}；结论：${verdict}；证据：${artifact}；证据 SHA-256：${evidenceHash}" in app
    assert 'rawSummary.textContent = "完整候选 JSON（审计）"' in app
    assert "rawContent.textContent = JSON.stringify(candidate, null, 2);" in app
    assert ".rsi-candidate-memory-file" in styles


def test_rsi_health_distinguishes_connected_resources_not_ready(tmp_path: Path):
    class UnreadyRemote(Remote):
        def health(self):
            return {
                "configured": True,
                "ready": False,
                "checks": {
                    "official_runtime": False,
                    "instruction_corpus": False,
                    "locked_vm_image": False,
                    "model_transport_smoke": True,
                    "vm_runtime_image": True,
                    "guest_isolation_smoke": False,
                    "secret_server_check": False,
                },
                "models": ["gpt-6-astra", "not-locally-approved"],
                "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"],
            }

    health = WebConsoleController(tmp_path, rsi_client=UnreadyRemote()).rsi_health()

    assert health["configured"] is True
    assert health["ready"] is False
    assert health["connection_state"] == "unready"
    assert health["models"] == ["gpt-6-astra"]
    assert health["reasoning_efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert health["checks"] == [
        {"id": "official_runtime", "label": "官方 RSIAgent 运行时", "ready": False},
        {"id": "instruction_corpus", "label": "练习指令语料与清单", "ready": False},
        {"id": "locked_vm_image", "label": "已校验的隔离虚拟机镜像", "ready": False},
        {"id": "model_transport_smoke", "label": "Codex 模型传输验证", "ready": True},
        {"id": "vm_runtime_image", "label": "虚拟机容器运行镜像", "ready": True},
        {"id": "guest_isolation_smoke", "label": "真实隔离与回滚验收", "ready": False},
    ]
    assert "secret_server_check" not in json.dumps(health, ensure_ascii=False)
    source = (Path(__file__).parents[1] / "src/trace2task/web/app.js").read_text(encoding="utf-8")
    assert 'state === "unready" ? "资源未就绪"' in source


def test_rsi_json_profile_converts_only_trusted_array_fields(tmp_path: Path, monkeypatch):
    profile = tmp_path / "rsi-profile.json"
    profile.write_text(json.dumps({
        "ssh_host": "dongjintao@100.70.228.85",
        "remote_config_path": "/mnt/sda/dongjintao/trace2task-rsi/deployment.json",
        "control_launcher_argv": ["python", "-m", "trace2task.rsi_control"],
        "allowed_models": ["gpt-6-astra"],
        "allowed_reasoning_efforts": ["low"],
    }), encoding="utf-8")
    monkeypatch.setenv("TRACE2TASK_RSI_PROFILE", str(profile))

    controller = WebConsoleController(tmp_path)

    assert controller._rsi_client is not None
    assert controller._rsi_client.profile.control_launcher_argv == (
        "python", "-m", "trace2task.rsi_control",
    )
    assert controller._rsi_client.profile.allowed_models == ("gpt-6-astra",)


def test_rsi_loads_persistent_local_app_data_profile(tmp_path: Path, monkeypatch):
    local_app_data = tmp_path / "local-app-data"
    profile_dir = local_app_data / "Trace2Task"
    profile_dir.mkdir(parents=True)
    (profile_dir / "rsi-profile.json").write_text(json.dumps({
        "ssh_host": "dongjintao@100.70.228.85",
        "remote_config_path": "/mnt/sda/dongjintao/trace2task-rsi/deployment.json",
        "control_launcher_argv": ["python", "-m", "trace2task.rsi_control"],
    }), encoding="utf-8")
    monkeypatch.delenv("TRACE2TASK_RSI_PROFILE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    controller = WebConsoleController(tmp_path)

    assert controller._rsi_client is not None
    assert controller._rsi_profile_error is None


def test_rsi_json_profile_rejects_non_array_command_field(tmp_path: Path, monkeypatch):
    profile = tmp_path / "rsi-profile.json"
    profile.write_text(json.dumps({
        "ssh_host": "dongjintao@100.70.228.85",
        "remote_config_path": "/mnt/sda/dongjintao/trace2task-rsi/deployment.json",
        "control_launcher_argv": "python -m trace2task.rsi_control",
    }), encoding="utf-8")
    monkeypatch.setenv("TRACE2TASK_RSI_PROFILE", str(profile))

    health = WebConsoleController(tmp_path).rsi_health()

    assert health == {
        "configured": False,
        "ready": False,
        "connection_state": "unconfigured",
        "message": "远程 RSI 部署配置无效；请由管理员检查本机配置",
        "checks": [],
        "models": [],
        "reasoning_efforts": [],
    }

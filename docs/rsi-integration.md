# Remote RSIAgent integration

**Status: deployed and live end-to-end accepted for the bounded offline profile
described here.** Real subscription inference, official Actor execution,
independent Verifier PASS, committed memory, interrupted-run recovery, subsequent
memory reuse and an immutable acceptance record have passed. The archived
candidate remains readable in the local UI. These checks do not prove unattended
reliability or an improvement over an untrained baseline. The source/local console
is updated; an older installed binary is not silently replaced or repackaged.

## Architecture and scope

Trace2Task uses the official [AetherLabsAI/RSIAgent](https://github.com/AetherLabsAI/RSIAgent)
practice engine, not a reconstructed Actor/Verifier loop. The local app is a
control/review surface. Practice happens in a dedicated remote OSWorld Ubuntu
guest, not the user's Windows desktop or existing WAA/training containers.

```text
Trace2Task UI: budgets, start / stop / recovery, logs, candidate review
                       │ allowlisted SSH JSON protocol
Trusted supervisor ── durable run / attempt / budget ledger
                       │
Official Curriculum → Actor → independent Verifier → committed memory
                       │ disposable guest + QEMU rollback
                       └── immutable evidence → human review (no auto-activation)
```

This is **subscription-adapted, offline, distribution-guided capability practice**,
not an official benchmark reproduction or a guarantee to execute the supplied
desktop task. The original role configurations and actual subscription model are
recorded. Paid OpenRouter benchmark graders/user simulators are not invoked or
counted as passing.

## Pinned inputs

| Component | Identity |
| --- | --- |
| RSIAgent | `a9e56263f6deaa493496ad6b155fe24bf131bc12` (Apache-2.0) |
| OSWorld-V2 | `d578d2d4e0dc82b43e270fdaa7fa89d9708cd154`, `v2026.08.08` |
| Task sources | `xlangai/osworld_v2_tasks@3736efa55d9d5dc78f57e873ef78886663e41200` |
| 108 task-source files | 2,668,122 bytes; tree SHA256 `1b923d2b8ec94c15cf4056ff9242985dbe922614fd1f7a84fd20407b460ad18d` |
| VM archive | `xlangai/v2-image@8213366932c553e5fe758d0f2c8c8b81ffc3be8c`, `osworld-v2-ubuntu-x86.qcow2.zip` (bytes of lock tag `v2026.06.24`) |
| Archive size / SHA256 | 14,189,763,267 bytes / `eb737ae70b49849e24af407de6a518439a23de05a8497096a948334ce0a909aa` |
| Extracted qcow2 size / SHA256 | 27,402,633,216 bytes / `3d632f031459583cf936e0c4c5bb939122df0fec85aecb0d044ef2d3e5863335` |
| Codex CLI | `0.156.1`; another version needs boundary revalidation |
| Base runtime | `ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58` |
| Official OSWorld Docker runtime | image ID `sha256:fe8d9a5e5ad6c593d059887ea2c790481b3f32dd42fa961f2441cdbbe2c70cf4`; registry manifest `sha256:0e6497a9295647cf05bf2b2af522fdd79bdeba2737595259cab310a3bcf6baa9` |

The controller is built with `scripts/rsi/Dockerfile.controller`: Git, OSWorld
native libraries and declared Linux build dependencies. Deployment pins its built
image ID, not a mutable tag. Restricted model containers still use the base
runtime without build tools. A Debian transport mirror may be selected at build
time; signature/hash verification must remain enabled.

Before a practice run is admitted, the trusted controller uses the pinned
controller image for a short, network-disabled, read-only, capability-dropped
`statvfs` utility against Docker's fixed `DockerRootDir`. It requires at least
120 GiB available for the guest's 60 GiB temporary disk and 50 GiB overlay.
This is an admission preflight rather than a disk quota; practice remains
limited to one active run, and stopping a run never depends on this probe.

Gated task sources use the user's authorized Hugging Face account and feed only
the host-side anti-leakage corpus. They are not copied into Actor memory or the
guest. Public VM downloads may use a mirror, but the official size/hash must pass
`scripts/rsi/verify_vm_image.py` before extraction. Never forward HF credentials
to a third-party mirror.

The upstream `v2026.06.24` URL now resolves to a different July archive
(`f53ac2da...`, 13,995,873,636 bytes). It was rejected against the lock. The
historical immutable commit above contains the expected `eb737ae...` archive;
use that commit URL, never change the expected hash to admit a drifting tag.

Two locked OSWorld task classes (`task_026`, `task_041`) import the GitLab
controller before their static instruction can be read. Building the host-only
anti-leakage corpus therefore uses
`scripts/rsi/build_instruction_corpus.py`: the other 106 tasks use RSIAgent's
official loader; only those two class-level literal `base_instruction` values
are extracted with AST, never imported or instantiated. The receipt records the
two source/text hashes and explicitly says that official task instantiation was
not attempted. It also records an upstream `source-manifest.json` builder-hash
drift; admission is instead the clean pinned RSIAgent Git commit plus a
`git show HEAD` hash. This does not change upstream sources, task setup,
evaluation, Actor context or guest memory.

The deployed corpus has 108 instructions / 14,219 shingles, SHA256
`ac14d48d5b728e5330ca6958630d0b5f3eedcf159d23aa7081a16df8088d3605`.
Set `instruction_corpus` in the trusted server configuration to this generated
file. Admission and the worker use the same file; the incomplete 106-task
upstream default is not silently substituted. The lineage records its hash.

The official `tools.exam_fence` also lazily builds a grader-constant denylist.
Run the trusted controller's `--prepare-exam-fence` setup command before making
the upstream checkout read-only to workers. It calls the frozen official builder,
not a replacement checker, and saves a hash/provenance receipt beside
`upstream/results/audit/fence/grader_constants.json`. Readiness verifies both;
missing or modified files block admission. This host-only data is never added to
model context or guest memory. The current verified cache is 3,471 bytes, SHA256
`d95e9c102bebd7525d364b407d54e2f71de32629c16e0f750d46fef600eb1b93`.

## Boundaries implemented

- `rsi_codex.py` replaces `llm.client.chat` before upstream modules bind it. Each
  call receives the complete official transcript and image evidence; contexts
  are not secretly shared across roles or restored branches.
- `rsi_model_session.py` launches one non-root, read-only model container per call:
  finite CPU/RAM/PIDs, dropped capabilities, no Docker socket, KVM, GPU, training
  directories or other roles' evidence.
- `rsi_model_only.py` disables native operational tools, project instructions,
  plugins/MCP and delegation. Original server configuration stays unchanged.
  An unexpected tool request terminates that model container.
- System instructions, images/hashes, exact requests/responses, errors and timing
  are archived. Private chain-of-thought is not fabricated. Unsupported sampling
  and output-token controls are recorded as unsupported, not claimed to work.
- The `codex_subscription_v2` profile appends an execution-topology clarification
  after the unmodified official role instructions: Codex native tools are disabled;
  returned program text is interpreted by the official isolated-guest harness,
  with that role's own guest permissions. The exact combined `model_system` is
  archived. This does not make the model container writable or enable its tools.
- For this subscription profile, official `trace_context_max_chars=12000` bounds
  each program observation using the upstream 40% head / 60% tail mechanism.
  Omitted output is explicitly labeled, while the full original `trace.txt` stays
  archived. Instructions, ordinary history and model I/O receipts are not silently
  truncated. This is an explicit input-protocol difference, not a claim to use
  the official unbounded default. A very long conversation can still exhaust
  provider context and must fail visibly rather than execute a missing reply.
- The agentic Verifier reloads its YAML independently. Each new run therefore
  includes a run-local copy that changes only `trace_context_max_chars`; its source
  and content hashes, plus all effective role configurations, are bound in the
  manifest. Recovery only verifies an existing copy; it never rewrites an old
  run to fit a newer protocol.
- Official Actor, memory-writer, Curriculum and rollback-mirror Verifier seats
  retain independent configurations. Actor completion is never a verifier PASS.
- `rsi_isolation.py` checks owned labels, loopback ports and the sole read-only
  qcow bind, then runs official `TargetIsolationSeal(mode="null")` and live probes
  after initial/internal resets. A failed receipt closes the guest before model work.
- The null seal blocks guest network egress and outer-container services. Network
  tasks are unavailable in this initial deployment. This is stronger than the
  official Phase-1 default and is recorded as a protocol difference. QEMU rollback
  does not remove the outer-container firewall.
- The trusted controller controls Docker; it is not a model tool. Formal immunity
  to container/hypervisor escape is not claimed.
- Both the control launcher and worker need `--device /dev/kvm:/dev/kvm:rwm`.
  Readiness checks the KVM API, and the official provider can then enable hardware
  acceleration. Model containers never receive this device. The VM runtime is
  started by immutable image ID and verified again before role execution.
- The guest itself (not just its controller) is limited to 4 CPU cores, 6 GiB
  memory including swap, and 512 PIDs. Inspect must match the recorded resource
  policy. The model containers have their own independent limits. Normal guest
  removal also removes that guest's anonymous volumes, not other Docker data.

## Stop and recovery

SQLite stores runs, attempts, global model-call reservations, elapsed budget and
ordered events. Calls are charged before network I/O, even if responses are lost.
Each attempt has a separate worker log; call indices remain global across recovery.

The supervisor owns a separate worker process and cleans only matching run/launch
containers. `finalizing` is non-terminal. Stop remains a request until cleanup is
confirmed; late model replies cannot execute after cancellation/budget expiry.

The recovery button is conditional:

1. The previous attempt is closed, with confirmed cleanup and budget remaining.
2. Original source/configuration/corpus/manifest hashes still match.
3. Official `_resume_completed_phase1_boundary` accepts retained memory,
   contiguous closed outcomes and lossless Curriculum context.
4. The worker revalidates before any VM/model work and calls official
   `resume_completed_boundary=True`, without resetting budgets.

Partial next projects, quarantine, changed evidence and old incomplete manifests
are refused, not deleted or guessed through. Unaccounted crashes consume remaining
budget conservatively. `rsi_reconcile.py` settles stop-requested orphan workers only
with positive ownership/cleanup evidence; unknown Docker state remains pending.

## Local configuration and review

**远程 RSI 练习** provides readiness checks, budgets, run history/events, stop,
conditional recovery and candidate inspection/review, independently of local jobs.

Trusted settings come from `TRACE2TASK_RSI_PROFILE`, or by default
`%LOCALAPPDATA%/Trace2Task/rsi-profile.json`. They contain the SSH target and fixed
launcher argv, not credentials. Browser requests cannot supply hosts, commands
or server paths. SSH requires existing host verification/non-interactive login.
Use `scripts/rsi/deployment.example.json` for server settings and replace its image
placeholder with the validated built image ID. `codex_bin` is the absolute
**directory containing** the pinned standalone `codex` executable, not the path
to the executable itself. `codex_home` points to the existing server login.

Only the trusted worker registers candidates: the latest closed episode must have
independent PASS reports and its `memory_after` must exactly match current memory.
Earlier PASS cannot bless later FAIL-derived edits. Human acceptance archives a
hash-bound decision; it does not overwrite human Trace or active guidance.

### 日常使用

1. 在 Trace2Task 打开“远程 RSI 练习”，刷新状态；依赖检查全部通过后再启动。
2. 填写练习方向，选择订阅模型和思考强度，设置调用数、时间和项目数上限。
   当前是离线能力探索，方向不等于保证执行的具体任务。
   新建练习默认最多 25 次调用、30 分钟、1 个项目；部署上限更低时自动收紧。
   时间预算包含虚拟机初始化与回滚，30 分钟是上限，不是固定等待时间。
3. 启动后可关闭本地页面；远程 supervisor 继续运行并保存日志。
   不确定是否已启动时先刷新运行历史，不要连续点击启动。
4. “请求停止”不会立即显示已停止，必须等到远端清理确认。
   “需要恢复”也不代表必然可恢复；只有通过完整项目边界检查才开放恢复。
5. 候选经验只有通过独立验证后才可审查。查看内容和证据后再接受或拒绝；
   接受只记录审查结果，不会直接替换已有任务经验。
   归档后仍可只读查看候选正文、验证依据、审查意见和时间，不能重复审批。

服务器保留现有 Codex 登录。登录失效时在服务器用原账号手动重新登录；
系统不会改动原凭据、创建其他登录目录或切换到收费 API。

### Evidence locations and review semantics

All paths below are relative to the dedicated server deployment root; they are
not a user's ordinary project folders.

| Path | Purpose |
| --- | --- |
| `jobs/runs.sqlite3` | Run states, ordered events, cumulative budgets, attempts and review decisions |
| `jobs/<run>/attempts/attempt-<n>/` | Attempt-specific execution / cleanup logs and isolation receipts |
| `jobs/<run>/model-calls/` | Exact model requests, responses, image references and timing |
| `jobs/<run>/lineage/manifest.json` | Frozen source identities, role configurations and protocol |
| `jobs/<run>/lineage/episodes/ep<n>/` | Actor execution evidence, independent verifier evidence and episode outcome |
| `jobs/<run>/lineage/memory/` | Official engine's current reusable memory, which can include learning from failures |
| `jobs/candidate-<run>-<sha256>.json` | Immutable candidate admitted only after the final PASS and exact memory-manifest match |

The UI exposes sanitized status/events, readable candidate contents and their
verification provenance. Full execution logs stay on the server; inspect them
over the existing SSH connection when diagnosing a run. They are not all shipped
to the browser or committed to Git. Preserve this evidence when reporting results.

“归档为接受” records an operator decision bound to a candidate hash. It does **not**
claim that the candidate is universally correct, and does not install it into a
Windows task's guidance. Within one official practice lineage, committed memory
is reused automatically by subsequent projects; promoting a reviewed candidate
into a different Trace2Task task remains a separate, explicit workflow.

## Verified evidence (2026-09-26)

- Server Codex login reports ChatGPT Pro; credentials remain server-local.
- Real image request answered `red` correctly in **9.03 s** for a synthetic circle
  fixture. Full model I/O is archived; this is not a GUI task.
- The official 108-task source tree matched the release identity.
- The controller imported the real OSWorld `DesktopEnv` and Docker client.
- Local UI connected to the real SSH service and displayed missing resources with
  Start disabled. Dependency banners are kept off the JSON protocol channel.
- `scripts/rsi/smoke_supervisor.py` launched a real detached supervisor after a
  recorded stop request. It reached `cancelled`, confirmed cleanup, charged
  2,898 ms, and made **zero model calls / zero guest boots**.
- `scripts/rsi/smoke_guest.py`, receipt
  `smoke/d75ac8bfc7c7421999b8c8f183dac07c/guest-smoke.json`, booted the locked
  official guest in **245.98 s** total with zero model calls. A real 1920×1080
  screenshot was saved. All seven official null-network probes returned BLOCKED;
  the verifier could not read Actor private memory, its file mutation was rolled
  back, and Actor memory was restored. Cleanup reported no errors. This proves
  the isolated guest/rollback path, not learned task performance; subsequent
  runtime resource-policy changes require a fresh matching smoke receipt.
- The final resource-limited configuration passed again in **179.922 s**:
  `smoke/e2fed6e38b9d4e1994201e6bec27e725/guest-smoke.json`. The receipt binds the
  image and resource policy; host-side inspection also confirmed live cgroup
  values of 6,442,450,944 bytes, CPU quota/period 400,000/100,000, and 512 PIDs.
- Real run `e2cd93c5aeee44de8c90997ceb987f0b` exposed two integration defects:
  the initially missing host-only fence cache (before any model call), then an
  unbounded 1,844,667-character program observation exceeding Codex's input limit.
  Its first completed model response also confused model-container read-only
  permissions with guest permissions. Both attempts, raw requests/responses and
  complete program output remain archived. A real completed-boundary-zero
  recovery preserved the prior 139,910 ms and resumed the same run; no project
  passed. The failed run was subsequently stopped with confirmed cleanup before
  changing the input protocol. It is not accepted as an end-to-end success.
- Real bounded-context validation:
  `smoke/context-profile-20260926T085209Z/context-profile-receipt.json`
  verified all actual role configurations, including the independently reloaded
  agentic Verifier, at a 12,000-character observation limit. The archived failing
  trace had 1,844,292 characters; the official formatter produced a 12,245-character
  labeled observation. The raw trace's SHA256 stayed
  `405cfe6faaa33a8a690e708d1e9bc6a65af47094aa1d79faf94f0618b2cb1fed`.
  This validation used no network, VM, Docker socket or model call.
- `scripts/rsi/smoke_program_boundary.py` used the actual frozen Actor system
  prompt and the restricted subscription container for one real request. In
  **13.1 s**, the reply was accepted as a Program by the official parser and
  passed a static guest-write proposal check. Receipt:
  `smoke/266c20b86270488c88133ca67d40a326/result.json`. No VM was started and
  the program was neither submitted nor executed; this checks the corrected
  model/guest distinction, not actual task success.
- Real two-project acceptance run `5b65ba167ef94e3e9b5647fcc172e2b6` completed
  project 1 with an independent Verifier PASS and committed
  `small_text_verification.md` (2,385 bytes, SHA256
  `c818da8645d272329cd649ca5e564482f3f00772a7aa0424ee381064e0ed43c2`).
  The verifier inspected the original brief and actual artifact bytes, including
  encoding, line endings, line count and content, rather than accepting the
  Actor's completion claim. Episode outcome SHA256:
  `674efc927021b20b1e5aa46d7f34a99e23102a92b2c7fdd053fdc84fd4d9f788`.
- The same run passed the real completed-boundary interruption check:
  `jobs/<run>/interrupt-boundary-attempt-001.json`. The external monitor sent
  SIGINT only to that attempt's executor after `PROJECT_CLOSED(1)`; the
  supervisor confirmed cleanup and production recovery admitted the same run.
  Attempt 2 inherited **14 calls and 997,307 ms**, leaving 36 calls and
  2,602,693 ms. Official `PHASE_RESUMED(after_project=1)` and `PROJECT_OPENED(2)`
  confirm that the first project was not replayed. The interrupted first attempt
  remains recorded as infrastructure interruption, not whole-run success.
- Second-turn Curriculum transcript and calls 16/17 contain the full saved
  `small_text_verification.md` text, not only its name. This proves that the
  committed memory survived recovery and reached subsequent model context;
  it does not by itself establish a task-quality improvement.
- The two-project run ended with **one PASS and one FAIL**, 33 total model calls
  and 2,200,350 ms of cumulative active time across both attempts. Project 2's
  independent verifier accepted the text/document contents and rendered layout,
  but rejected the documented use of `/tmp` for working files against that
  project's all-files-inside-project constraint. The final FAIL-derived memory
  remains archived, but no verified candidate was issued from it. `completed`
  means the practice lifecycle ended, not that every project passed.
- An owned-container cleanup race exposed a harmless-but-disruptive Docker 404
  after interruption. The adapter now treats only SDK `NotFound` for its exact
  owned handle as idempotent removal; all other failures still propagate. The
  isolated real-SDK smoke `smoke/3bd08ba65b5a44f9bbb86a3fb4281046/` removed its
  unique test container twice and confirmed that its exact ID and label no
  longer existed. Receipt SHA256:
  `17529f2c8a12ba6bc446d2c0c06a22605dbbc4a827748e0247ce7e66fb27423d`.
  This smoke used no guest, model or network, and did not change the then-active
  practice worker. The fix was deployed after that run ended and before the
  subsequent one-project candidate acceptance run.
- Final single-project run `a25be18e21d0486f9e16a291e0668de6` reached independent
  **PASS**, committed its memory and finished with confirmed cleanup and exit 0.
  It used **13 model calls / 1,045,406 ms (17 min 25 s)** including VM preparation,
  verification and memory work. Its project budget was one, so the official
  `budget_exhausted` reason means the configured project count was reached, not
  a failed task or a model timeout. The generated `utf8-note-inspection.txt`
  is 2,008 bytes, SHA256
  `ba271dbf649dd581d9e36d79bfada1eaa98646c591ab5804c075c980198d3d16`.
  The immutable candidate is SHA256
  `298cc3597e29ff90a137b9aea461574693298e2ac2941d15fee668ecbb8c76f9`,
  bound to episode outcome SHA256
  `d5fa149d41cb3d82e313f86051fd4594d0bd78e39759ab81e099edc972161ee1`.
  Its learned procedure explicitly limits evidence to this successful instance,
  requires rereading saved bytes and comparing facts against the current brief,
  and warns against broad directory-output dumps. It is not automatically active
  in local task guidance.
- The live local UI displayed that actual candidate's text, file hash and
  independent evidence. A temporary review-note draft survived background
  polling. The initial display check left the real candidate unreviewed. The
  remote health check still passed all 11 gates after the run ended.
- On a later live check, the durable ledger contained a real `accepted` review
  at **2026-09-26T09:57:10.922452+00:00**, event **142**, bound to the unchanged
  `298cc359...c76f9` candidate. The UI displayed the same decision, hash, time
  and note and reopened the archived memory read-only. The note still contains
  the earlier wording “界面验收草稿（尚未提交）”; it was preserved verbatim, not
  rewritten to claim a different review. The workflow state is the recorded
  decision, not the prose in that note. This records the observed transition;
  it is not a claim of independently authenticated reviewer identity.
- Post-review read access remains available for both accepted and rejected
  candidates, without editable notes or repeat-review controls. The 16 web
  integration/JavaScript checks cover pending, accepted and rejected render
  states, run/digest identity, CSRF and allowlisted transport. Archived render
  states in those tests are fixtures, not an operator decision on the real run.

Relevant tests (test doubles unless expressly stated above):

```powershell
uv run --no-sync pytest tests/test_rsi_jobs.py tests/test_rsi_codex.py tests/test_rsi_control.py tests/test_rsi_remote.py tests/test_rsi_model_only.py tests/test_rsi_model_session.py tests/test_rsi_worker.py tests/test_rsi_isolation.py tests/test_rsi_recovery.py tests/test_rsi_reconcile.py tests/test_rsi_web.py tests/test_codex_app_server.py tests/test_web_console.py
```

## Completion audit and operating limits

| Requirement | Current evidence |
| --- | --- |
| Official implementation and fixed inputs | Pinned Git commits, immutable image IDs, corpus/VM hashes and per-run manifest; no replacement Actor/Verifier loop |
| Server subscription; no local credential export or paid fallback | Real 13-call run through restricted server-side Codex containers; fixed SSH control carries JSON only; original login is read-only |
| Guest / verifier isolation | Resource-matched real guest smoke and per-reset null-network receipts; independent Verifier mutation rollback and hidden Actor memory |
| Bounded practice and stop/recovery | Persistent reservation-before-I/O budget ledger; real cancellation and project-boundary interruption/recovery with inherited budgets |
| Actor → Verifier → memory | `a25be18e...` independent PASS, canonical memory and matching immutable candidate; `5b65ba16...` subsequent context reuses committed memory |
| UI and review | Real candidate display, draft retention, event 142 acceptance, post-review read-only access and immutable review receipt |
| Preserve Trace / active guidance | Review writes only the RSI ledger; no automatic promotion endpoint or call into local guidance writers |
| Tests and documentation | RSI automated suite plus real evidence above; current deployment, protocol differences, artifact locations and limits documented here |

- Repeat guest acceptance when the runtime resource policy changes; admission must
  bind the receipt to the current policy as well as the pinned image.
- The user chose to retain the existing login and handle expiration manually.
  Read-only auth currently does
  not mutate the user's original login; expiry must stay a visible infrastructure
  error, never an empty reply or automatic paid-API fallback.
  Explicit Codex `401`, expired-token, or refresh-token failures are recorded as
  `codex_authentication_required`. The web console hides the provider's raw
  message for that category and instructs the operator to re-login on the
  server with the existing account, refresh the page, and start a new practice.
  Timeouts, disconnects, and `5xx` errors are intentionally not classified as
  authentication failures.

No benchmark score, unattended reliability or model-quality improvement is claimed
from installation, mocks, connectivity or the cancellation smoke alone.

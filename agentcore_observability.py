"""
agentcore_observability (obs) — shared code for MLADAS Module 4, "Production Monitoring, Observability, and Evaluation".

Everything here is RUN_ID-scoped (names mladas-m04-… / mladas_m04_…, tags {project: mladas, module: M04, run_id}),
uses the boto3 session you pass in (mc.get_session(): MLADAS_AWS_PROFILE or boto3's standard credential chain, us-east-1), never raises from a setup or cleanup
path, and never shows the account id or the temporary access key id that ADOT records on botocore spans
(aws.auth.account.access_key): rows drop every aws.auth.* attribute and mask the account id; Logs Insights @ptr is dropped.
Verified in a test account in us-east-1 on 2026-09-28 with strands-agents 1.57.1, bedrock-agentcore 1.23.1, boto3 1.43.103,
opentelemetry-sdk 1.45.0 (research: the course team's test runs, Sept 2026).

Public API
  Names        run_suffix(run_id) · is_dev_stack(run_id) · resource_names(run_id) · M04_NAMESPACE ("MLADAS/M04") ·
               SPANS_LOG_GROUP ("aws/spans") · BATCH_RESULTS_LOG_GROUP · JUDGE_MODEL_ID · RUNTIME_MODEL_ID
  Runtime      RuntimeObsDeployment(run_id, session, source_dir, tags=, extra_files=) — agentcore_deploy.RuntimeDeployment
               with entryPoint ["opentelemetry-instrument", "agent.py"] (ADOT), protocol HTTP, bucket mladas-m04-<sfx>:
               .start() .adopt() -> bool .wait(timeout) .ready .status .error .progress() .adopted
               .runtime_arn .runtime_id .name .log_group .service_name .sessions
               .invoke(payload, session_id, *, trace_parent=None, timeout=90, correlation_id=None, book=True) -> dict
               .stop_session(session_id) -> bool · .cost_estimate() -> dict
               .teardown() -> {resource: status} (stops live sessions; runtime + endpoints, its log groups (2 sweeps),
               role, bucket, the log group's resource policy if any) · .verify_gone() -> {resource: "gone ✓" | "NOT CLEANED: …"}
               runtime_session_id(label, run_id) (>= 33 chars) · trace_parent() -> (trace_id, traceparent header) ·
               runtime_metrics(session, runtime, since, until=None) -> {metric: total, "points": {...}} ·
               genai_console_url(region)
  Spans        span_query(session_id) · spans_for_session(session_id, *, session, since, log_group=SPANS_LOG_GROUP,
               min_traces=1, max_wait=30, quiet=False, trace_ids=None) -> SpanRows (list of span rows; .complete, .info;
               trace_ids= gates on THOSE traces, for a session that already has older traces) ·
               span_documents(trace_id, *, session, since) -> masked raw span JSON (FilterLogEvents) ·
               span_rows(readable_spans) · receiver_rows(receiver_spans) · trace_complete(rows, trace_id) ·
               span_group(row) · span_tree(rows) -> [lines] · tokens_by_trace(rows) -> {trace_id: {input, output, chat_spans}}
               (per request = the SUM of the Strands `chat` spans; invoke_agent on the Runtime is cumulative per session) ·
               error_spans(rows) · waterfall_rows(rows, trace_ids=None, headers=None) -> rows for viz.waterfall ·
               mask_attributes(attrs, account) · mask_deep(obj, account)
  Local OTel   LocalTelemetry.setup(session, *, run_id, service_name=, receiver=True, cloudwatch=True, force_fail="")
               — ONE global TracerProvider, ParentBased(SwitchableSampler), 3 processors: in-memory · OTLP/HTTP to an
               in-process receiver ("enterprise backend") · OTLP/HTTP to CloudWatch via the X-Ray OTLP endpoint (SigV4).
               Idempotent per kernel. .rows(session_id=None) .readable_spans(session_id=None) .clear() .force_flush()
               .shutdown() .set_sampler(sampler) .status .receiver .receiver_ok .cloudwatch_ok .xray_exports .endpoint
               OtlpReceiver(host="127.0.0.1", port=0).start() .stop() .spans .requests .received(session_id=None) .stats()
               sigv4_requests_session(boto_session, service="xray") · OTEL_ENV (the env vars §0 sets before any agent)
  Sampling     SwitchableSampler · ALWAYS_ON · ratio_keeps(trace_id_hex, ratio) · tail_keep_errors(exporter, keep_ratio) ·
               head_tail(rows, ratio=0.2) -> dict (same trace ids: like with like) ·
               synthetic_sampling(n_traces=2000, ratio=0.2, error_rate=0.1, seed=7) -> [row per strategy] (PRIVATE provider)
  Loop/alarms  WIRE_PROMPT · WIRE_QUESTION · WIRE_AGENT_NAME · WIRE_TURN_LIMIT · LOOP_MAX_TOOL_CALLS (5) · COST_MAX_RATIO (2)
               WireDesk(status="COMPLETED").tool / .status · wire_agent(desk, hooks=(), limits=, **kw) (mc.make_agent) ·
               LoopMetrics(session, run_id, agent_name=, namespace=, enabled=True) — Strands HookProvider: after every
               tool call PutMetricData ToolCallsInSession + TokensInSession (1-s resolution, dims Agent + RunId);
               .sent .puts .errors .breaches(baseline_tokens, since_index=0) ·
               AlarmSet(session, run_id, agent_name=, namespace=, tags=).create(baseline_tokens) .adopt() -> bool
               .states() .wait_for(state="ALARM", kinds=, max_wait=60, since=None) .wait_armed(max_wait=90)
               .history(kind, since=None) .alarm_seconds_after(kind, t) .teardown() .verify_gone() ·
               DECK_SLIDE29 · slide29_check(session, runtime=None) -> dict (read-only list_metrics) · slide29_corrected(runtime)
  Evaluations  DISCLAIMER · DISCLAIMER_INSTRUCTIONS · DISCLAIMER_SCALE · has_disclaimer(text) · EVALUATOR_SPECS ·
               JUDGE_MODEL_CONFIG · catalog(session) -> counts · eval_state(label, value) -> "pass"|"partial"|"fail"|"error"
               EvaluatorSet(session, run_id, tags=, judge_model=).create() .adopt() -> bool .ids .names .ready .error
               .describe(key) .config_rows() · .evaluate(key, docs, target=None, correlation_id=None, book=True) -> [rows]
               .evaluate_local(spans, keys=, trace_ids=None, correlation_id=None) -> [rows]
               .evaluate_runtime(session_id, runtime, since, keys=, trace_ids="all"|"last"|[...], max_wait=60) -> [rows]
               .teardown() .verify_gone()
               runtime_session_docs(session_id, runtime_log_group, *, session, since, max_wait=60, quiet=False) -> (docs, info)
               OnlineEvaluation(session, run_id, tags=).create_role() .create(runtime, evaluator_ids) .adopt() -> bool
               .ready .status .describe() .results(session_ids=None, since=None) -> [rows] .result_count(since=None)
               .batch(runtime, evaluator_ids, session_ids, max_wait=120) -> dict (the ~60-70 s fallback)
               .teardown() .verify_gone()
  Cost         service_cost_rows(*, runtime=None, evaluators=None, online=None, alarms=None, loop_metrics=None (= every
               LoopMetrics of the kernel), local=None, since=None) -> [{service, units, unit_price, usd, note}] (book each with mc.LEDGER.add_service)
  Cleanup      find_run_resources(session, run_id) -> {kind: [names]} · delete_run_resources(session, run_id) -> {resource: status}
"""

from __future__ import annotations

import gzip
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

import agentcore_audit as at
import mladas_common as mc
from agentcore_deploy import RuntimeDeployment

HERE = Path(__file__).resolve().parent
SPANS_LOG_GROUP = "aws/spans"                               # Transaction Search destination (30-day retention in our test account)
BATCH_RESULTS_LOG_GROUP = "/aws/bedrock-agentcore/evaluations/batch-evaluations/results/default"   # shared by the account
RESULTS_LOG_GROUP_ROOT = "/aws/bedrock-agentcore/evaluations/results/"
M04_NAMESPACE = "MLADAS/M04"                                # custom CloudWatch metrics (loop detection, cost protection)
RUNTIME_MODEL_ID = mc.MODELS["nova-2-lite"]                 # the hosted bank agent's model (Nova only)
JUDGE_MODEL_ID = mc.MODELS["nova-2-lite"]                   # every evaluator's judge (Nova only; never a built-in as-is)
SERVICE_INDEX_POLICY = '{"Fields":["resource.attributes.service.name"]}'   # what CreateOnlineEvaluationConfig adds
GONE = "gone ✓"
_NOT_FOUND = ("ResourceNotFoundException", "NoSuchEntity", "NoSuchEntityException", "NotFoundException", "404",
              "NoSuchBucket", "ResourceNotFound")
_ACCOUNT: dict[str, str] = {}


# ======================================================================================================================
# 0. Names, masking, small helpers
# ======================================================================================================================
def run_suffix(run_id: str) -> str:
    """The RUN_ID's lowercase letters and digits: every resource name carries it ('20260928-000000-devm' ->
    '20260928000000devm'), so both spellings of a run id address the same resources."""
    sfx = re.sub(r"[^a-z0-9]", "", str(run_id).lower())
    if not sfx:
        raise ValueError(f"run id {run_id!r} has no letters or digits")
    return sfx


def is_dev_stack(run_id: str) -> bool:
    """True for a shared builder dev stack (YYYYMMDD-000000-dev?, in any spelling): never torn down by the notebook."""
    try:
        return bool(re.fullmatch(r"\d{14}dev[a-z]", run_suffix(run_id)))
    except ValueError:
        return False


EVALUATOR_KEYS = ("helpful", "goal", "toolsel", "disclaimer")


def resource_names(run_id: str) -> dict[str, Any]:
    """Every name one M04 run uses (the teardown script finds resources by these)."""
    sfx = run_suffix(run_id)
    return {
        "suffix": sfx,
        "runtime": f"mladas_m04_bank_{sfx}"[:48],
        "runtime_role": f"mladas-m04-bank-{sfx}"[:64],
        "bucket": f"mladas-m04-{sfx}"[:63],
        "runtime_log_group_prefix": f"/aws/bedrock-agentcore/runtimes/mladas_m04_bank_{sfx}",
        "evaluator_prefix": f"mladas_m04_{sfx}_",
        "evaluators": {k: f"mladas_m04_{sfx}_{k}" for k in EVALUATOR_KEYS},
        "online_config": f"mladas_m04_{sfx}_online",
        "online_role": f"mladas-m04-oe-{sfx}"[:64],
        "results_log_group_prefix": f"{RESULTS_LOG_GROUP_ROOT}mladas_m04_{sfx}_online",
        "batch_prefix": f"mladas_m04_{sfx}_batch",
        "alarm_prefix": f"mladas-m04-{sfx}-",
        "alarms": {"loop": f"mladas-m04-{sfx}-loop-detection", "cost": f"mladas-m04-{sfx}-cost-protection"},
    }


def _account(session) -> str:
    key = str(id(session))
    if key not in _ACCOUNT:
        _ACCOUNT[key] = session.client("sts").get_caller_identity()["Account"]
    return _ACCOUNT[key]


def _mask(text: Any, account: str | None) -> str:
    s = str(text)
    return s.replace(account, "********" + account[-4:]) if account else s


def mask_deep(obj: Any, account: str | None) -> Any:
    """Mask the account id everywhere in a JSON-like object; drop every aws.auth.* key (ADOT puts the role's temporary
    access key id in aws.auth.account.access_key on botocore spans)."""
    if isinstance(obj, dict):
        return {k: mask_deep(v, account) for k, v in obj.items()
                if not (str(k).startswith("aws.auth.") or "access_key" in str(k).lower() or k == "@ptr")}
    if isinstance(obj, list):
        return [mask_deep(v, account) for v in obj]
    if isinstance(obj, str):
        return _mask(obj, account)
    return obj


def mask_attributes(attrs: dict | None, account: str | None = None) -> dict:
    return mask_deep(dict(attrs or {}), account)


def _code(e: Exception) -> str:
    return e.response["Error"]["Code"] if isinstance(e, ClientError) else type(e).__name__


def _why(e: Exception, account: str | None = None) -> str:
    msg = e.response["Error"].get("Message", "") if isinstance(e, ClientError) else str(e)
    return mc.short(_mask(f"{_code(e)}: {msg}", account), 220)


def _is_gone(check) -> tuple[bool, str]:
    """(gone, detail): gone when check() raises a not-found error or reports a DELETING status."""
    try:
        got = check()
    except ClientError as e:
        code = _code(e)
        return (code in _NOT_FOUND or code.endswith(".NotFound")), code
    except BotoCoreError as e:
        return False, type(e).__name__
    status = got.get("status") if isinstance(got, dict) else None
    return status == "DELETING", status or "found"


def _verify(label: str, check) -> tuple[str, str]:
    gone, detail = _is_gone(check)
    return label, GONE if gone else f"NOT CLEANED: still exists ({detail})"


def _tags_list(tags: dict) -> list[dict]:
    return [{"Key": k, "Value": str(v)} for k, v in tags.items()]


def _safe_id(value: str) -> str:
    """Logs Insights string literal guard: session/trace ids are [A-Za-z0-9_-] only."""
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", str(value)):
        raise ValueError(f"unexpected characters in id {value!r}")
    return str(value)


INSIGHTS_STATS = {"queries": 0, "bytes_scanned": 0.0}      # every Logs Insights query this module ran (cost line)


def _insights(log_group, query: str, since, *, session, max_wait: float = 20) -> list[dict]:
    rows = at.insights(log_group, query, since, session=session, max_wait=max_wait)
    INSIGHTS_STATS["queries"] += 1
    INSIGHTS_STATS["bytes_scanned"] += float(rows.info.get("bytes_scanned") or 0)
    return rows


def genai_console_url(region: str = mc.AWS_REGION) -> str:
    """CloudWatch GenAI Observability (Bedrock AgentCore -> Agents -> Sessions -> Traces). Deeper links are undocumented."""
    return f"https://console.aws.amazon.com/cloudwatch/home?region={region}#gen-ai-observability"


# ======================================================================================================================
# 1. The hosted bank agent on AgentCore Runtime (ADOT auto-instrumentation)
# ======================================================================================================================
def _hex_id() -> str:
    """uuid4 hex without a run of >= 10 decimal digits (a 12-digit run trips the account-id lint on printed outputs)."""
    while True:
        h = uuid.uuid4().hex
        if not re.search(r"\d{10}", h):
            return h


def runtime_session_id(label: str, run_id: str | None = None) -> str:
    """A runtimeSessionId (>= 33 characters): one session = one microVM; reuse it for every turn of a conversation."""
    base = f"mladas-m04-{re.sub(r'[^a-z0-9]', '', label.lower())[:12]}"
    if run_id:
        base += f"-{run_suffix(run_id)[-6:]}"
    return f"{base}-{_hex_id()}"


def trace_parent() -> tuple[str, str]:
    """(trace_id, W3C traceparent) with an X-Ray-compatible trace id (epoch seconds first): pass it as
    invoke_agent_runtime(traceParent=...) and the Runtime's spans use YOUR trace id (research runtime-traces F10)."""
    tid = f"{int(time.time()):08x}{secrets.token_hex(12)}"
    return tid, f"00-{tid}-{secrets.token_hex(8)}-01"


class RuntimeObsDeployment(RuntimeDeployment):
    """The bank assistant on AgentCore Runtime with ADOT auto-instrumentation (no tracing code in agent.py):
    requirements.txt has aws-opentelemetry-distro==0.20.0 and the entry point is ["opentelemetry-instrument",
    "agent.py"] (the same zip with ["agent.py"] emits no spans: research runtime-traces F2-F3). Protocol HTTP
    (BedrockAgentCoreApp, POST /invocations). Spans go to aws/spans (the role has no logs:PutResourcePolicy), the
    GenAI content records to the runtime log group (stream otel-rt-logs)."""

    ENTRY_POINT = ["opentelemetry-instrument", "agent.py"]
    GB_H_PER_SESSION_MIN = 0.018            # ~1.1 GB resident per live session (research runtime-traces F30)
    VCPU_H_PER_SESSION_MIN = 0.00017        # idle live session
    VCPU_H_PER_COLD_START = 0.0018          # one new session (microVM start)

    def __init__(self, run_id: str, session, source_dir: str | Path, *, tags: dict | None = None,
                 model_id: str = RUNTIME_MODEL_ID, extra_files: Iterable[str | Path] = ()):
        mc.model_id(model_id)                                    # refuses anything that is not Amazon Nova
        names = resource_names(run_id)
        super().__init__(name="mladas_m04_bank", source_dir=source_dir, run_id=names["suffix"], session=session,
                         entry_point=list(self.ENTRY_POINT), protocol="HTTP", model_ids=(model_id,),
                         environment={"BANK_MODEL_ID": model_id},
                         description="MLADAS M04 - AnyCompany Bank assistant (ADOT auto-instrumented)",
                         tags=dict(tags or {}), bucket=names["bucket"])
        self.run_id_full, self.model_id = run_id, model_id
        self.extra_files = [Path(p) for p in extra_files]
        self.adopted = False
        self.sessions: dict[str, dict] = {}
        self._dp_clients: dict[float, Any] = {}
        self._lock = threading.Lock()

    # --------------------------------------------------------------------------------------------- lifecycle
    @property
    def log_group(self) -> str | None:
        return f"/aws/bedrock-agentcore/runtimes/{self.runtime_id}-DEFAULT" if self.runtime_id else None

    @property
    def service_name(self) -> str:
        return f"{self.name}.DEFAULT"          # = resource.attributes.service.name on every span (online eval filter)

    def start(self) -> "RuntimeObsDeployment":
        if self.status == "READY" or (self._thread is not None and self._thread.is_alive()):
            return self
        self.error = None
        return super().start()

    def adopt(self) -> bool:
        """Attach to this run's runtime if it exists (dev stacks, a restarted kernel): READY -> ready at once;
        CREATING/UPDATING -> a thread waits for READY. Returns True when something was adopted. Never raises."""
        try:
            found = next((r for r in self._list_runtimes() if r["agentRuntimeName"] == self.name), None)
            if not found:
                return False
            ctl = self.session.client("bedrock-agentcore-control")
            rt = ctl.get_agent_runtime(agentRuntimeId=found["agentRuntimeId"])
        except (ClientError, BotoCoreError) as e:
            self._log("ADOPT_FAILED", _why(e, self.account))
            self.status = "NOT_STARTED"
            return False
        self.runtime_arn, self.runtime_id, self.role_arn = rt["agentRuntimeArn"], rt["agentRuntimeId"], rt.get("roleArn")
        entry = (rt.get("agentRuntimeArtifact", {}).get("codeConfiguration", {}) or {}).get("entryPoint")
        st = rt["status"]
        self._log("ADOPTED", f"{self.runtime_id} ({st}, entryPoint={entry})")
        if st == "READY":
            self.status, self.adopted = "READY", True
            return True
        if st in ("CREATING", "UPDATING"):
            self.adopted = True
            self._thread = threading.Thread(target=self._adopt_wait, name=f"adopt-{self.name}", daemon=True)
            self._thread.start()
            return True
        self.status = "NOT_STARTED"                               # FAILED / DELETING: the caller creates or reports
        self._log("ADOPT_SKIPPED", f"runtime status {st}")
        return False

    def _adopt_wait(self) -> None:
        try:
            self._wait_ready()
        except BaseException as e:  # noqa: BLE001
            self.error = e
            self._log("FAILED", f"{type(e).__name__}: {e}")

    def wait(self, timeout: float = 300, poll: float = 2, verbose: bool = False) -> bool:
        return super().wait(timeout=timeout, poll=poll, verbose=verbose)

    def _package(self) -> Path:
        """Stage agent.py + requirements.txt + extra_files (e.g. the shared bank_data.py) so no stale copy lives next
        to the agent, then package with RuntimeDeployment._package (uv, linux/arm64 wheels only)."""
        original = self.source_dir
        staged = Path(tempfile.mkdtemp(prefix="mladas-m04-src-"))
        try:
            for f in list(original.iterdir()) + self.extra_files:
                if f.is_file() and f.suffix in (".py", ".json", ".txt", ".md") and "__pycache__" not in f.parts:
                    shutil.copy2(f, staged / f.name)
            self.source_dir = staged
            zip_path = super()._package()
        finally:
            self.source_dir = original
            shutil.rmtree(staged, ignore_errors=True)
        self._check_launcher(zip_path)
        return zip_path

    def _check_launcher(self, zip_path: Path) -> None:
        """Fail fast on a launcher AgentCore can't start: uv writes the discovering interpreter into bin/ scripts, and an
        interpreter path with a space becomes a '#!/bin/sh' wrapper that execs the laptop's python (-> 'Runtime
        initialization time exceeded' on every session). A plain '#!<python>' header starts fine (5/5, 2026-09-28)."""
        import zipfile
        name = f"bin/{self.ENTRY_POINT[0]}"
        with zipfile.ZipFile(zip_path) as z:
            if name not in z.namelist():
                raise RuntimeError(f"{name} is missing from code.zip: is aws-opentelemetry-distro in requirements.txt?")
            head = z.read(name)[:300].decode(errors="replace").splitlines()
        if not head or not head[0].startswith("#!") or head[0].startswith("#!/bin/sh"):
            raise RuntimeError(f"{name} starts with {head[:2]!r}: a launcher AgentCore cannot run (uv picked an "
                               "interpreter whose path has a space). Re-run from a folder without a .venv above it.")

    # --------------------------------------------------------------------------------------------- data plane
    def _dp(self, timeout: float):
        if timeout not in self._dp_clients:
            self._dp_clients[timeout] = self.session.client(
                "bedrock-agentcore", config=Config(read_timeout=timeout, connect_timeout=10,
                                                   retries={"total_max_attempts": 1}))
        return self._dp_clients[timeout]

    def invoke(self, payload: dict, session_id: str, *, trace_parent: str | None = None, timeout: float = 90,
               correlation_id: str | None = None, book: bool = True) -> dict:
        """POST one request to the hosted agent. -> the agent's JSON body (answer, trace_id, tools, tool_errors, usage,
        stop_reason, ...) plus wall_s; {"error": ...} instead of raising. With book=True the request's Nova tokens (the
        agent reports latest_agent_invocation usage) become a ledger row in the current LEDGER scope."""
        if not self.runtime_arn:
            return {"error": "runtime not deployed", "wall_s": 0.0, "session_id": session_id}
        kw = {"traceParent": trace_parent} if trace_parent else {}
        t0 = time.time()
        with self._lock:
            s = self.sessions.setdefault(session_id, {"first": t0, "last": t0, "invocations": 0, "stopped": None})
            s["invocations"] += 1
            s["stopped"] = None
        try:
            resp = self._dp(timeout).invoke_agent_runtime(
                agentRuntimeArn=self.runtime_arn, runtimeSessionId=session_id, payload=json.dumps(payload).encode(),
                contentType="application/json", accept="application/json", **kw)
            raw = resp["response"].read()
            body = json.loads(raw) if raw else {}
        except (ClientError, BotoCoreError, ValueError) as e:
            return {"error": _why(e, self.account), "wall_s": round(time.time() - t0, 2), "session_id": session_id}
        wall = time.time() - t0
        with self._lock:
            self.sessions[session_id]["last"] = t0 + wall
        if not isinstance(body, dict):
            body = {"answer": str(body)}
        if body.get("error"):
            body["error"] = _mask(body["error"], self.account)
        body["wall_s"], body["session_id"] = round(wall, 2), session_id
        usage = body.get("usage") or {}
        if book and usage:
            mc.LEDGER.add(agent="bank_assistant (Runtime)", model=self.model_id,
                          input_tokens=int(usage.get("inputTokens", 0)), output_tokens=int(usage.get("outputTokens", 0)),
                          cache_read_tokens=int(usage.get("cacheReadInputTokens", 0)), cache_write_tokens=0,
                          model_calls=int(body.get("model_calls") or 0), latency_s=round(wall, 3),
                          cost_usd=mc.cost_usd(self.model_id, usage), stop_reason=body.get("stop_reason"),
                          correlation_id=correlation_id)
        return body

    def stop_session(self, session_id: str) -> bool:
        """StopRuntimeSession (ends the microVM: GB-hours stop accruing). Never raises. Call it only once the session's
        spans are in CloudWatch (obs.runtime_session_docs / spans_for_session): stopping right after an answer kills the
        microVM before ADOT's batch exporter sends the last request's spans (foundations 2026-09-28: a whole trace lost)."""
        if not self.runtime_arn:
            return False
        try:
            self._dp(30).stop_runtime_session(agentRuntimeArn=self.runtime_arn, runtimeSessionId=session_id,
                                              qualifier="DEFAULT")
            ok = True
        except ClientError as e:
            ok = _code(e) in _NOT_FOUND
        except BotoCoreError:
            ok = False
        with self._lock:
            if session_id in self.sessions and ok:
                self.sessions[session_id]["stopped"] = time.time()
        return ok

    def cost_estimate(self, now: float | None = None) -> dict[str, float]:
        """Runtime vCPU-h / GB-h for the sessions THIS kernel opened, from the rates measured in research (the
        CPU/Memory usage metrics lag ~18 min, so they can't be read during class). An estimate, labelled as one."""
        now = now or time.time()
        minutes = cold = 0.0
        for s in self.sessions.values():
            end = s["stopped"] or min(now, s["last"] + 900)               # idle timeout 900 s
            minutes += max(0.0, end - s["first"]) / 60
            cold += 1
        return {"sessions": int(cold), "session_minutes": round(minutes, 2),
                "vcpu_hours": round(cold * self.VCPU_H_PER_COLD_START + minutes * self.VCPU_H_PER_SESSION_MIN, 6),
                "gb_hours": round(minutes * self.GB_H_PER_SESSION_MIN, 6)}

    # --------------------------------------------------------------------------------------------- cleanup
    def teardown(self, wait: bool = True) -> dict[str, str]:           # type: ignore[override]
        """Stop live sessions, then delete runtime (+ endpoints), its log groups (2 sweeps), role, bucket; a unified-
        destination log-group resource policy goes with its log group. -> {resource: status} after by-id verification."""
        for sid, s in list(self.sessions.items()):
            if not s.get("stopped"):
                self.stop_session(sid)
        lg = self.log_group
        try:
            super().teardown(wait=wait)
        except (ClientError, BotoCoreError) as e:
            out = self.verify_gone(lg)
            out[f"runtime teardown ({self.name})"] = f"NOT CLEANED: {_why(e, self.account)}"
            return out
        return self.verify_gone(lg)

    def verify_gone(self, log_group: str | None = None) -> dict[str, str]:
        ctl, iam, s3, logs = (self.session.client(c) for c in ("bedrock-agentcore-control", "iam", "s3", "logs"))
        out = {}
        if self.runtime_id:
            out.update([_verify(f"AgentCore runtime {self.runtime_id}",
                                lambda: ctl.get_agent_runtime(agentRuntimeId=self.runtime_id))])
        out.update([_verify(f"IAM role {self.role_name}", lambda: iam.get_role(RoleName=self.role_name)),
                    _verify(f"S3 bucket {self.bucket}", lambda: s3.head_bucket(Bucket=self.bucket))])
        left = [g["logGroupName"] for p in logs.get_paginator("describe_log_groups").paginate(
            logGroupNamePrefix=f"/aws/bedrock-agentcore/runtimes/{self.name}") for g in p["logGroups"]]
        out[f"log groups /aws/bedrock-agentcore/runtimes/{self.name}-*"] = GONE if not left else \
            f"NOT CLEANED: {len(left)} left"
        if log_group:
            arn = f"arn:aws:logs:{self.region}:{self.account}:log-group:{log_group}"
            try:
                pols = logs.describe_resource_policies(resourceArn=arn, policyScope="RESOURCE").get("resourcePolicies", [])
                out["log-group resource policy (unified span destination)"] = GONE if not pols else \
                    f"NOT CLEANED: {len(pols)} left"
            except ClientError as e:
                out["log-group resource policy (unified span destination)"] = GONE if _code(e) in _NOT_FOUND else \
                    f"NOT CLEANED: check failed ({_code(e)})"
        return {_mask(k, self.account): _mask(v, self.account) for k, v in out.items()}


def runtime_metrics(session, runtime: RuntimeObsDeployment, since: float, until: float | None = None) -> dict:
    """The Runtime's vended metrics (AWS/Bedrock-AgentCore, 1-min, visible 10-90 s after a call) for this runtime:
    {"Invocations": n, "Errors": n, "SystemErrors": n, "UserErrors": n, "Latency_max_ms": x, "points": {...}}.
    Errors stays 0 when a TOOL fails: the agent handles it and the request returns HTTP 200 (slide 14)."""
    if not runtime or not runtime.runtime_arn:
        return {}
    cw = session.client("cloudwatch")
    dims = [{"Name": "Resource", "Value": runtime.runtime_arn}, {"Name": "Operation", "Value": "InvokeAgentRuntime"},
            {"Name": "Name", "Value": f"{runtime.name}::DEFAULT"}]
    spec = [("inv", "Invocations", "Sum"), ("err", "Errors", "Sum"), ("se", "SystemErrors", "Sum"),
            ("ue", "UserErrors", "Sum"), ("lat", "Latency", "Maximum")]
    q = [{"Id": i, "MetricStat": {"Metric": {"Namespace": "AWS/Bedrock-AgentCore", "MetricName": m, "Dimensions": dims},
                                  "Period": 60, "Stat": st}} for i, m, st in spec]
    start = datetime.fromtimestamp(since - 120, timezone.utc)
    end = datetime.fromtimestamp((until or time.time()) + 120, timezone.utc)
    try:
        res = cw.get_metric_data(MetricDataQueries=q, StartTime=start, EndTime=end)["MetricDataResults"]
    except (ClientError, BotoCoreError) as e:
        return {"error": _why(e)}
    by = {r["Id"]: r for r in res}
    out: dict[str, Any] = {"points": {}}
    for i, m, st in spec:
        vals = by.get(i, {}).get("Values", [])
        out["points"][m] = list(zip([t.isoformat() for t in by.get(i, {}).get("Timestamps", [])], vals))
        if m == "Latency":
            out["Latency_max_ms"] = max(vals) if vals else None
        else:
            out[m] = sum(vals) if vals else 0
    return out


# ======================================================================================================================
# 2. Spans: read back, normalise, tree, tokens, waterfall rows
# ======================================================================================================================
SPAN_FIELDS = ("traceId, spanId, parentSpanId, name, kind, startTimeUnixNano, endTimeUnixNano, durationNano, "
               "status.code as status, status.message as error, attributes.gen_ai.tool.name as tool, "
               "attributes.gen_ai.tool.status as tool_status, attributes.gen_ai.usage.input_tokens as in_tok, "
               "attributes.gen_ai.usage.output_tokens as out_tok, attributes.gen_ai.request.model as model, "
               "scope.name as scope, attributes.session.id as session_id")


def span_query(session_id: str, fields: str = SPAN_FIELDS) -> str:
    """ONE Logs Insights query: every span of a session (all its traces), selected fields only — never the raw JSON,
    which carries aws.auth.account.access_key on botocore spans."""
    return (f"fields {fields}\n| filter attributes.session.id = \"{_safe_id(session_id)}\"\n"
            "| sort startTimeUnixNano asc\n| limit 2000")


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _cw_row(r: dict) -> dict:
    start = _int(r.get("startTimeUnixNano")) or 0
    end = _int(r.get("endTimeUnixNano")) or (start + (_int(r.get("durationNano")) or 0))
    return {"trace_id": r.get("traceId", ""), "span_id": r.get("spanId", ""), "parent_id": r.get("parentSpanId") or None,
            "name": r.get("name", ""), "kind": r.get("kind", ""), "start_ns": start, "end_ns": end,
            "status": (r.get("status") or "UNSET").upper(), "status_message": r.get("error") or "",
            "tool": r.get("tool"), "tool_status": r.get("tool_status"), "in_tok": _int(r.get("in_tok")),
            "out_tok": _int(r.get("out_tok")), "model": r.get("model"), "scope": r.get("scope") or "",
            "session_id": r.get("session_id"), "source": "cloudwatch"}


class SpanRows(list):
    """A list of span rows (dicts) with .complete (the completeness gate passed) and .info (queries, seconds, …)."""
    complete: bool = False
    info: dict = {}


def trace_complete(rows: list[dict], trace_id: str) -> bool:
    """Structural completeness of one trace: exactly one root (a parent outside the set counts as root: the AgentCore
    service span, InvokeAgentRuntime per the docs, did not reach aws/spans in research: 0/2 sessions, F20), every other
    parent present, invoke_agent has >= 1 cycle and every cycle >= 1 chat."""
    spans = [r for r in rows if r["trace_id"] == trace_id]
    ids = {r["span_id"] for r in spans}
    roots = [r for r in spans if not r["parent_id"] or r["parent_id"] not in ids]
    if len(roots) != 1:
        return False
    # The root must be a TOP span: ADOT's `POST /invocations` (Runtime) or a parentless `invoke_agent` (local). Spans are
    # exported as they END, bottom-up, in batches: a first batch of [cycle 1, chat, botocore chat, execute_tool] is a
    # tidy tree rooted at a cycle (integration run 2026-09-28: 4 of 9 spans, the gate passed after 1.3 s). An
    # invoke_agent WITH a parent id is a Runtime trace whose POST span has not landed yet.
    root = roots[0]
    if not (root["name"].startswith("POST ") or (root["name"].startswith("invoke_agent") and not root["parent_id"])):
        return False
    kids: dict[str, list[dict]] = {}
    for r in spans:
        kids.setdefault(r["parent_id"], []).append(r)
    for r in spans:
        names = [c["name"] for c in kids.get(r["span_id"], [])]
        if r["name"].startswith("invoke_agent") and not any(n.startswith("execute_event_loop_cycle") for n in names):
            return False
        if r["name"].startswith("execute_event_loop_cycle") and "chat" not in names:
            return False
        if r["name"].startswith("POST ") and any(x["name"].startswith("invoke_agent") for x in spans) \
                and not any(n.startswith("invoke_agent") for n in names):
            return False
    return True


def spans_for_session(session_id: str, *, session, since: float, log_group: str | list[str] = SPANS_LOG_GROUP,
                      min_traces: int = 1, max_wait: float = 30, poll: float = 2.5, quiet: bool = False,
                      trace_ids: Iterable[str] | None = None) -> SpanRows:
    """Every span of one session from CloudWatch (Logs Insights on aws/spans by attributes.session.id), re-queried until
    the completeness gate passes: >= min_traces traces with an invoke_agent span, each structurally complete
    (trace_complete). With trace_ids= the gate checks THOSE traces (each must be present and complete) instead of every
    agent trace: on a session that already has traces (a warm-up, a re-run), the new ones are what must have landed.
    Spans were queryable 2.6-5.5 s after the answer (research runtime-traces F27). One updating status line
    (quiet=False). Never raises: .complete says whether the gate passed, .info has queries/seconds/error."""
    wanted_ids = list(trace_ids) if trace_ids is not None else None
    q = span_query(session_id)
    state: dict[str, Any] = {"rows": SpanRows(), "queries": 0, "error": None}
    t0 = time.time()

    def probe():
        try:
            got = _insights(log_group, q, since, session=session, max_wait=min(20.0, max_wait))
        except (ClientError, BotoCoreError) as e:
            state["error"] = _why(e)
            return None
        state["queries"] += 1
        rows = SpanRows(_cw_row(r) for r in got)
        state["rows"] = rows
        agent_traces = {r["trace_id"] for r in rows if r["name"].startswith("invoke_agent")}
        wanted = set(wanted_ids) if wanted_ids is not None else agent_traces   # gate on THESE traces (a reused session)
        ok = len(agent_traces) >= min_traces and all(trace_complete(rows, t) for t in wanted)
        return rows if ok else None

    def describe(_v):
        rows = state["rows"]
        return f"{len(rows)} spans, {len({r['trace_id'] for r in rows})} traces" + (f" ({state['error']})" if state["error"] else "")

    if quiet:
        done = False
        while True:
            done = probe() is not None
            if done or time.time() - t0 >= max_wait:
                break
            time.sleep(poll)
    else:
        done, _, _ = mc.wait_until(probe, max_wait=max_wait, poll=poll, describe=describe,
                                  label=f"the spans of session …{session_id[-8:]} in {log_group}")
    rows = state["rows"]
    rows.complete = bool(done)
    rows.info = {"queries": state["queries"], "seconds": round(time.time() - t0, 1), "spans": len(rows),
                 "traces": len({r["trace_id"] for r in rows}), "error": state["error"]}
    return rows


def span_documents(trace_id: str, *, session, since: float, log_group: str = SPANS_LOG_GROUP) -> list[dict]:
    """The raw span JSON of one trace (FilterLogEvents), masked: aws.auth.* dropped, account id masked. Use it to show
    ONE span in full (e.g. the ERROR span's status + exception event) or the attributes CloudWatch adds at ingestion."""
    logs, acct = session.client("logs"), _account(session)
    kw = {"logGroupName": log_group, "startTime": int((since - 60) * 1000), "filterPattern": f'"{_safe_id(trace_id)}"'}
    docs = []
    try:
        while True:
            resp = logs.filter_log_events(**kw)
            for ev in resp.get("events", []):
                try:
                    docs.append(mask_deep(json.loads(ev["message"]), acct))
                except ValueError:
                    continue
            if not resp.get("nextToken"):
                break
            kw["nextToken"] = resp["nextToken"]
    except (ClientError, BotoCoreError):
        return docs
    return sorted(docs, key=lambda d: int(d.get("startTimeUnixNano", 0)))


_STATUS = {0: "UNSET", 1: "OK", 2: "ERROR"}
_KINDS = {0: "UNSPECIFIED", 1: "INTERNAL", 2: "SERVER", 3: "CLIENT", 4: "PRODUCER", 5: "CONSUMER"}


def span_rows(spans: Iterable[Any], account: str | None = None) -> list[dict]:
    """In-memory OTel spans (ReadableSpan, e.g. LocalTelemetry.readable_spans()) -> span rows (same keys as the
    CloudWatch rows, plus masked attributes and event names)."""
    out = []
    for s in spans:
        a = dict(s.attributes or {})
        out.append({"trace_id": f"{s.context.trace_id:032x}", "span_id": f"{s.context.span_id:016x}",
                    "parent_id": f"{s.parent.span_id:016x}" if s.parent else None, "name": s.name,
                    "kind": getattr(s.kind, "name", str(s.kind)), "start_ns": s.start_time, "end_ns": s.end_time,
                    "status": s.status.status_code.name, "status_message": s.status.description or "",
                    "tool": a.get("gen_ai.tool.name"), "tool_status": a.get("gen_ai.tool.status"),
                    "in_tok": _int(a.get("gen_ai.usage.input_tokens")), "out_tok": _int(a.get("gen_ai.usage.output_tokens")),
                    "model": a.get("gen_ai.request.model"),
                    "scope": s.instrumentation_scope.name if s.instrumentation_scope else "",
                    "session_id": a.get("session.id"), "source": "in-memory",
                    "attributes": mask_attributes(a, account), "events": [e.name for e in s.events]})
    return sorted(out, key=lambda r: r["start_ns"])


def receiver_rows(spans: Iterable[dict], account: str | None = None) -> list[dict]:
    """OtlpReceiver.spans (decoded OTLP protobuf) -> span rows."""
    out = []
    for s in spans:
        a = s.get("attributes", {})
        out.append({"trace_id": s["trace_id"], "span_id": s["span_id"], "parent_id": s.get("parent_id"),
                    "name": s["name"], "kind": s.get("kind", ""), "start_ns": s["start_ns"], "end_ns": s["end_ns"],
                    "status": s["status"], "status_message": s.get("status_message", ""),
                    "tool": a.get("gen_ai.tool.name"), "tool_status": a.get("gen_ai.tool.status"),
                    "in_tok": _int(a.get("gen_ai.usage.input_tokens")), "out_tok": _int(a.get("gen_ai.usage.output_tokens")),
                    "model": a.get("gen_ai.request.model"), "scope": s.get("scope", ""),
                    "session_id": a.get("session.id"), "source": "otlp-receiver",
                    "attributes": mask_attributes(a, account), "events": s.get("events", [])})
    return sorted(out, key=lambda r: r["start_ns"])


def span_group(r: dict) -> str:
    """The span's kind for colors (viz.SPAN_COLORS): request · agent · cycle · model · bedrock · tool · other."""
    n = r.get("name", "")
    if n.startswith("POST ") or n.startswith("GET ") or r.get("kind") == "SERVER":
        return "request"
    if n.startswith("invoke_agent"):
        return "agent"
    if n.startswith("execute_event_loop_cycle"):
        return "cycle"
    if n == "chat":
        return "model"
    if n.startswith("chat "):
        return "bedrock"            # ADOT's botocore span 'chat us.amazon.nova-2-lite-v1:0' (the Bedrock API call)
    if n.startswith("execute_tool"):
        return "tool"
    return "other"


def _children(rows: list[dict]) -> tuple[dict, list[dict]]:
    ids = {r["span_id"] for r in rows}
    kids: dict[str | None, list[dict]] = {}
    for r in rows:
        p = r["parent_id"] if r["parent_id"] in ids else None
        kids.setdefault(p, []).append(r)
    for v in kids.values():
        v.sort(key=lambda x: x["start_ns"])
    return kids, kids.get(None, [])


def _dfs(rows: list[dict]) -> list[tuple[int, dict]]:
    kids, roots = _children(rows)
    out: list[tuple[int, dict]] = []

    def walk(r, d):
        out.append((d, r))
        for c in kids.get(r["span_id"], []):
            walk(c, d + 1)
    for r in roots:
        walk(r, 0)
    return out


def _traces(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["trace_id"], []).append(r)
    return sorted(by.items(), key=lambda kv: min(x["start_ns"] for x in kv[1]))


def tokens_by_trace(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Per-request tokens = the SUM of the Strands `chat` spans of each trace (never the Runtime's invoke_agent span:
    its usage attributes are cumulative for the session's Agent object, research runtime-traces F24)."""
    out: dict[str, dict[str, int]] = {}
    for tid, spans in _traces(rows):
        chats = [r for r in spans if r["name"] == "chat" and (r["scope"] or "").startswith("strands")]
        out[tid] = {"input": sum(r["in_tok"] or 0 for r in chats), "output": sum(r["out_tok"] or 0 for r in chats),
                    "chat_spans": len(chats)}
    return out


def error_spans(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("status") == "ERROR"]


def span_tree(rows: list[dict], *, max_name: int = 46) -> list[str]:
    """Session -> trace -> span as indented text lines (offsets from each trace's first span, durations, the tokens of
    each Strands chat span, ✗ on ERROR spans with their status message)."""
    lines = []
    by_session: dict[str, list[dict]] = {}
    for r in rows:
        by_session.setdefault(r.get("session_id") or "(no session.id)", []).append(r)
    for sess, srows in by_session.items():
        traces = _traces(srows)
        lines.append(f"SESSION {sess}  ({len(traces)} trace{'s' if len(traces) != 1 else ''}, {len(srows)} spans)")
        toks = tokens_by_trace(srows)
        for k, (tid, spans) in enumerate(traces, 1):
            t0 = min(r["start_ns"] for r in spans)
            wall = (max(r["end_ns"] for r in spans) - t0) / 1e9
            tok = toks.get(tid, {})
            extra = f" · tokens {tok['input']:,} in / {tok['output']:,} out (sum of chat spans)" if tok.get("chat_spans") else ""
            lines.append(f"  TRACE {k} {tid[:8]}…  {wall:.2f} s · {len(spans)} spans{extra}")
            for d, r in _dfs(spans):
                name = r["name"] if len(r["name"]) <= max_name - 2 * d else r["name"][: max_name - 2 * d - 1] + "…"
                off, dur = (r["start_ns"] - t0) / 1e9, (r["end_ns"] - r["start_ns"]) / 1e9
                note = ""
                if r["name"] == "chat" and r.get("in_tok"):
                    note = f"  in={r['in_tok']:,} out={r.get('out_tok') or 0:,}"
                if r["status"] == "ERROR":
                    note += f"  ✗ ERROR: {mc.short(r.get('status_message', ''), 70)}"
                lines.append(f"    {'  ' * d}{name:<{max_name - 2 * d}} +{off:6.2f}s {dur:6.2f}s{note}")
    return lines


def waterfall_rows(rows: list[dict], trace_ids: Iterable[str] | None = None, *, headers: bool | None = None,
                   notes: bool = True) -> list[dict]:
    """Rows for viz.waterfall: the chosen traces (default: all, in time order), each span in tree order (parent before
    its children), times in seconds since the first selected span. headers=None -> one header row per trace when more
    than one trace is drawn."""
    wanted = list(trace_ids) if trace_ids is not None else None
    traces = [(t, s) for t, s in _traces(rows) if wanted is None or t in wanted]
    if not traces:
        return []
    t0 = min(r["start_ns"] for _, s in traces for r in s)
    headers = len(traces) > 1 if headers is None else headers
    out = []
    for k, (tid, spans) in enumerate(traces, 1):
        if headers:
            wall = (max(r["end_ns"] for r in spans) - min(r["start_ns"] for r in spans)) / 1e9
            errs = sum(r["status"] == "ERROR" for r in spans)
            out.append({"header": f"trace {k} · {tid[:8]}…  ({len(spans)} spans, {wall:.2f} s"
                                  + (f", {errs} ERROR)" if errs else ")")})
        for d, r in _dfs(spans):
            note = ""
            if notes and r["name"] == "chat" and r.get("in_tok"):
                note = f"{r['in_tok']:,} in / {r.get('out_tok') or 0:,} out"
            out.append({"name": r["name"], "depth": d, "start": (r["start_ns"] - t0) / 1e9,
                        "end": (r["end_ns"] - t0) / 1e9, "group": span_group(r), "status": r["status"], "note": note})
    return out


# ======================================================================================================================
# 3. Local telemetry: one global TracerProvider, three destinations
# ======================================================================================================================
def _any_value(v) -> Any:
    kind = v.WhichOneof("value")
    if kind is None:
        return None
    if kind == "array_value":
        return [_any_value(x) for x in v.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: _any_value(kv.value) for kv in v.kvlist_value.values}
    return getattr(v, kind)


def _attrs(kvs) -> dict:
    return {kv.key: _any_value(kv.value) for kv in kvs}


class OtlpReceiver:
    """A vendor-neutral OTLP/HTTP receiver ("enterprise backend" stand-in: Datadog, Splunk or an OTel Collector would
    accept the same bytes): POST /v1/traces, application/x-protobuf, gzip/deflate. stdlib http.server + the installed
    opentelemetry-proto; binds 127.0.0.1 on an ephemeral port. Keeps every span as a dict."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        from opentelemetry.proto.collector.trace.v1 import trace_service_pb2 as pb

        self.spans: list[dict] = []
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        rx = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):          # keep the notebook quiet
                pass

            def do_POST(self):                  # noqa: N802
                if self.path.rstrip("/") != "/v1/traces":
                    self.send_error(404)
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                enc = (self.headers.get("Content-Encoding") or "").lower()
                try:
                    raw = gzip.decompress(body) if enc == "gzip" else zlib.decompress(body) if enc == "deflate" else body
                    if "protobuf" not in self.headers.get("Content-Type", ""):
                        self.send_error(415, "only application/x-protobuf in this demo receiver")
                        return
                    req = pb.ExportTraceServiceRequest()
                    req.ParseFromString(raw)
                except Exception:  # noqa: BLE001 — a bad request must not kill the server thread
                    self.send_error(400)
                    return
                got = rx._ingest(req)
                with rx._lock:
                    rx.requests.append({"wire_bytes": len(body), "raw_bytes": len(raw), "encoding": enc or "none",
                                        "spans": got, "t": time.time(), "user_agent": self.headers.get("User-Agent", "")})
                out = pb.ExportTraceServiceResponse().SerializeToString()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        self.host, self.port = self._server.server_address[:2]
        self._thread = threading.Thread(target=self._server.serve_forever, name="otlp-receiver", daemon=True)
        self.running = False

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}/v1/traces"     # the FULL signal URL (endpoint= is used verbatim)

    def start(self) -> "OtlpReceiver":
        if not self.running:
            self._thread.start()
            self.running = True
        return self

    def stop(self) -> None:
        if self.running:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)
            self.running = False

    def _ingest(self, req) -> int:
        rows = []
        for rs in req.resource_spans:
            res = _attrs(rs.resource.attributes)
            for ss in rs.scope_spans:
                for sp in ss.spans:
                    rows.append({"trace_id": sp.trace_id.hex(), "span_id": sp.span_id.hex(),
                                 "parent_id": sp.parent_span_id.hex() or None, "name": sp.name,
                                 "kind": _KINDS.get(sp.kind, str(sp.kind)),
                                 "start_ns": sp.start_time_unix_nano, "end_ns": sp.end_time_unix_nano,
                                 "status": _STATUS.get(sp.status.code, "UNSET"), "status_message": sp.status.message,
                                 "attributes": _attrs(sp.attributes), "events": [e.name for e in sp.events],
                                 "scope": ss.scope.name, "service": res.get("service.name"), "resource": res})
        with self._lock:
            self.spans.extend(rows)
        return len(rows)

    def received(self, session_id: str | None = None, account: str | None = None) -> list[dict]:
        """Span rows the receiver got (optionally one session's)."""
        with self._lock:
            spans = list(self.spans)
        if session_id:
            spans = [s for s in spans if s["attributes"].get("session.id") == session_id]
        return receiver_rows(spans, account)

    def stats(self, since: float | None = None) -> dict:
        with self._lock:
            reqs = [r for r in self.requests if since is None or r["t"] >= since]
        raw, wire = sum(r["raw_bytes"] for r in reqs), sum(r["wire_bytes"] for r in reqs)
        return {"requests": len(reqs), "spans": sum(r["spans"] for r in reqs), "raw_bytes": raw, "wire_bytes": wire,
                "gzip_saved_pct": round(100 * (1 - wire / raw), 1) if raw else None,
                "encoding": reqs[-1]["encoding"] if reqs else None, "user_agent": reqs[-1]["user_agent"] if reqs else None}


def sigv4_requests_session(boto_session, service: str = "xray"):
    """A requests.Session that SigV4-signs every request with the given boto3 session (profile + region pinned):
    the CloudWatch/X-Ray OTLP endpoint https://xray.<region>.amazonaws.com/v1/traces answers 403 without it."""
    import requests
    from botocore.auth import SigV4Auth as _BotoSigV4
    from botocore.awsrequest import AWSRequest

    creds, region = boto_session.get_credentials(), boto_session.region_name

    class SigV4(requests.auth.AuthBase):
        def __call__(self, r):
            signed = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-encoding")}
            aws_req = AWSRequest(method=r.method, url=r.url, data=r.body, headers=signed)
            _BotoSigV4(creds.get_frozen_credentials(), service, region).add_auth(aws_req)
            r.headers.update(dict(aws_req.headers.items()))
            return r

    s = requests.Session()
    s.auth = SigV4()
    return s


def _otel_sampling():
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON, Sampler
    return ALWAYS_ON, Sampler


def _make_switchable():
    ALWAYS_ON, Sampler = _otel_sampling()

    class SwitchableSampler(Sampler):
        """A root-span sampling policy you can change later: the global TracerProvider can be set only once per
        process, so the policy lives here (inner = ALWAYS_ON | TraceIdRatioBased(0.2) | ...), wrapped in ParentBased."""

        def __init__(self, inner=None):
            self.inner = inner or ALWAYS_ON

        def should_sample(self, parent_context, trace_id, name, kind=None, attributes=None, links=None, trace_state=None):
            return self.inner.should_sample(parent_context, trace_id, name, kind, attributes, links, trace_state)

        def get_description(self):
            return f"Switchable({self.inner.get_description()})"

    return SwitchableSampler


SwitchableSampler = _make_switchable()
ALWAYS_ON = _otel_sampling()[0]            # restore LOCAL_OTEL.set_sampler(obs.ALWAYS_ON) after an optional live arm


def ratio_keeps(trace_id_hex: str, ratio: float) -> bool:
    """The OTel SDK's TraceIdRatioBased rule: keep iff the low 64 bits of the trace id < ratio * 2^64 (deterministic
    per trace id, so every service in a trace makes the same head decision)."""
    return (int(trace_id_hex, 16) & 0xFFFFFFFFFFFFFFFF) < round(ratio * (1 << 64))


def tail_keep_errors(exporter, keep_ratio: float = 0.0):
    """Tail-based sampling in-process: buffer spans per trace; when the ROOT span ends, forward the whole trace if any
    span is ERROR, else only when the trace id falls under keep_ratio. (Across services this needs a collector.)"""
    from opentelemetry.sdk.trace import SpanProcessor
    from opentelemetry.trace import StatusCode

    bound = round(keep_ratio * (1 << 64))

    class TailKeepErrors(SpanProcessor):
        def __init__(self):
            self.buf: dict[int, list] = {}
            self.decisions: list[dict] = []
            self._lock = threading.Lock()

        def on_end(self, span):
            tid = span.context.trace_id
            with self._lock:
                self.buf.setdefault(tid, []).append(span)
                if span.parent is not None:
                    return
                spans = self.buf.pop(tid)
            has_error = any(s.status.status_code == StatusCode.ERROR for s in spans)
            keep = has_error or (tid & 0xFFFFFFFFFFFFFFFF) < bound
            self.decisions.append({"trace_id": f"{tid:032x}", "spans": len(spans), "error": has_error, "kept": keep})
            if keep:
                exporter.export(spans)

        def shutdown(self):
            exporter.shutdown()

        def force_flush(self, timeout_millis: int = 30000):
            return True

    return TailKeepErrors()


def head_tail(rows: list[dict], ratio: float = 0.2) -> dict:
    """Head (ratio) vs tail (every ERROR trace + the same ratio) applied to the SAME traces (like with like)."""
    traces = _traces(rows)
    errs = {t for t, s in traces if any(r["status"] == "ERROR" for r in s)}
    head = [t for t, _ in traces if ratio_keeps(t, ratio)]
    tail = [t for t, _ in traces if t in errs or ratio_keeps(t, ratio)]
    size = {t: len(s) for t, s in traces}
    return {"ratio": ratio, "traces": len(traces), "spans": len(rows), "error_traces": len(errs),
            "head_traces": len(head), "head_spans": sum(size[t] for t in head),
            "head_error_traces": len(errs & set(head)),
            "tail_traces": len(tail), "tail_spans": sum(size[t] for t in tail), "tail_error_traces": len(errs & set(tail))}


def synthetic_sampling(n_traces: int = 2000, ratio: float = 0.2, error_rate: float = 0.1, seed: int = 7) -> list[dict]:
    """Slide 30, measured without model calls: n_traces synthetic traces shaped like one agent request (6 spans:
    invoke_agent -> 2 cycles -> chat + execute_tool / chat; the tool span is ERROR in ~error_rate of them) through
    three PRIVATE TracerProviders (never the global one, so nothing reaches the receiver or CloudWatch): always on ·
    head ParentBased(TraceIdRatioBased(ratio)) · tail (keep every ERROR trace + ratio). The same seeded trace ids feed
    every arm. -> one row per strategy: traces/spans kept, error traces kept, % fewer spans."""
    import random

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.id_generator import IdGenerator
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, TraceIdRatioBased
    from opentelemetry.trace import Status, StatusCode

    class SeededIds(IdGenerator):
        def __init__(self, s):
            self.rng = random.Random(s)

        def generate_span_id(self):
            return self.rng.getrandbits(64) or 1

        def generate_trace_id(self):
            return self.rng.getrandbits(128) or 1

    def run(sampler, tail: bool) -> tuple[int, int, int, int]:
        mem = InMemorySpanExporter()
        tp = TracerProvider(sampler=sampler, id_generator=SeededIds(seed))
        proc = tail_keep_errors(mem, ratio) if tail else SimpleSpanProcessor(mem)
        tp.add_span_processor(proc)
        tracer = tp.get_tracer("mladas.m04.sampling.synthetic")
        err_rng = random.Random(seed + 1)
        n_err = 0
        for _ in range(n_traces):
            failing = err_rng.random() < error_rate
            n_err += failing
            with tracer.start_as_current_span("invoke_agent synthetic"):
                with tracer.start_as_current_span("execute_event_loop_cycle"):
                    with tracer.start_as_current_span("chat"):
                        pass
                    with tracer.start_as_current_span("execute_tool get_fraud_score") as t:
                        if failing:
                            t.set_status(Status(StatusCode.ERROR, "synthetic upstream timeout"))
                with tracer.start_as_current_span("execute_event_loop_cycle"):
                    with tracer.start_as_current_span("chat"):
                        pass
        kept = mem.get_finished_spans()
        err_traces = {s.context.trace_id for s in kept if s.status.status_code == StatusCode.ERROR}
        tp.shutdown()
        return len({s.context.trace_id for s in kept}), len(kept), len(err_traces), n_err

    total_spans = n_traces * 6
    rows = []
    for label, sampler, tail in (("always on", ALWAYS_ON, False),
                                 (f"head {ratio:.0%}", ParentBased(root=TraceIdRatioBased(ratio)), False),
                                 (f"tail (errors + {ratio:.0%})", ALWAYS_ON, True)):
        traces, spans, errs, n_err = run(sampler, tail)
        rows.append({"strategy": label, "traces kept": traces, "spans kept": spans,
                     "error traces kept": f"{errs}/{n_err}", "fewer spans": f"{1 - spans / total_spans:.0%}",
                     "_spans_total": total_spans, "_error_traces": n_err, "_errors_kept": errs})
    return rows


OTEL_ENV = {                                   # read ONCE per process: set before any agent or provider exists (§0)
    "OTEL_SERVICE_NAME": "mladas-m04-local-bank",
    "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_use_latest_invocation_tokens",   # invoke_agent usage = this request only
}
XRAY_OTLP_ENDPOINT = "https://xray.{region}.amazonaws.com/v1/traces"


def set_otel_env(run_id: str, service_name: str | None = None) -> dict[str, str]:
    """Set the OTEL_* variables the local telemetry needs (uppercase OTEL_: the deck's slide-26 `OTel_` spelling is
    silently ignored). Must run before the first Strands agent or TracerProvider in the kernel. Returns what it set."""
    out = {}
    env = dict(OTEL_ENV)
    if service_name:
        env["OTEL_SERVICE_NAME"] = service_name
    for k, v in env.items():
        if k == "OTEL_SEMCONV_STABILITY_OPT_IN":
            cur = [t for t in os.environ.get(k, "").split(",") if t]
            if v not in cur:
                os.environ[k] = ",".join(cur + [v])
        else:
            os.environ.setdefault(k, v)
        out[k] = os.environ[k]
    attrs = f"project=mladas,module=M04,mladas.run_id={run_suffix(run_id)}"
    if "mladas.run_id=" not in os.environ.get("OTEL_RESOURCE_ATTRIBUTES", ""):
        cur = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
        os.environ["OTEL_RESOURCE_ATTRIBUTES"] = f"{cur},{attrs}" if cur else attrs
    out["OTEL_RESOURCE_ATTRIBUTES"] = os.environ["OTEL_RESOURCE_ATTRIBUTES"]
    return out


_LOCAL: "LocalTelemetry | None" = None


class LocalTelemetry:
    """The local Strands agents' telemetry: ONE global TracerProvider (StrandsTelemetry(tracer_provider=tp) alone does
    NOT make it global) with ParentBased(SwitchableSampler) and three processors:
      in-memory   SimpleSpanProcessor(InMemorySpanExporter)          -> waterfalls, Evaluate on local spans
      receiver    BatchSpanProcessor(OTLP/HTTP gzip -> OtlpReceiver) -> the vendor-neutral "enterprise backend"
      cloudwatch  BatchSpanProcessor(OTLP/HTTP gzip -> X-Ray OTLP endpoint, SigV4) -> aws/spans (Transaction Search)
    setup() is idempotent per kernel (a second provider or a re-added processor would double-export)."""

    def __init__(self):
        self.provider = self.sampler = self.memory = self.receiver = None
        self.receiver_ok = self.cloudwatch_ok = False
        self.states: dict[str, str] = {}
        self.xray_exports: list[dict] = []
        self.endpoint: str | None = None
        self.closed = False
        self.t0 = time.time()

    @classmethod
    def setup(cls, session, *, run_id: str, service_name: str | None = None, receiver: bool = True,
              cloudwatch: bool = True, force_fail: str = "") -> "LocalTelemetry":
        global _LOCAL
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider as SDKProvider

        current = trace.get_tracer_provider()
        existing = getattr(current, "_mladas_local_telemetry", None) or _LOCAL
        if existing is not None and not existing.closed:
            return existing                                     # re-run of §0: nothing is added twice
        self = cls()
        set_otel_env(run_id, service_name)
        from opentelemetry.exporter.otlp.proto.http import Compression
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from opentelemetry.sdk.trace.sampling import ParentBased
        from strands.telemetry.config import get_otel_resource

        if isinstance(current, SDKProvider):                   # someone set one already (e.g. StrandsTelemetry())
            self.provider = current
            self.states["provider"] = "reused the kernel's existing TracerProvider (sampling not switchable)"
        else:
            self.sampler = SwitchableSampler()
            self.provider = SDKProvider(resource=get_otel_resource(), sampler=ParentBased(root=self.sampler))
            trace.set_tracer_provider(self.provider)
            self.states["provider"] = "global TracerProvider, ParentBased(Switchable(always on))"
        self.provider._mladas_local_telemetry = self
        self.memory = InMemorySpanExporter()
        self.provider.add_span_processor(SimpleSpanProcessor(self.memory))
        self.states["in-memory"] = "on"
        if force_fail == "otlp":
            self.states["receiver"] = self.states["cloudwatch"] = "FAILED (forced by M04_FORCE_FAIL=otlp)"
        else:
            if receiver:
                try:
                    self.receiver = OtlpReceiver().start()
                    self.provider.add_span_processor(BatchSpanProcessor(
                        OTLPSpanExporter(endpoint=self.receiver.endpoint, compression=Compression.Gzip)))
                    self.receiver_ok = True
                    self.states["receiver"] = "on (OTLP/HTTP protobuf, gzip, 127.0.0.1:<port>/v1/traces)"
                except (OSError, ImportError) as e:
                    self.states["receiver"] = f"FAILED ({type(e).__name__}: {mc.short(str(e), 80)})"
            else:
                self.states["receiver"] = "OFF"
            if cloudwatch:
                self._attach_cloudwatch(session, OTLPSpanExporter, BatchSpanProcessor, Compression)
            else:
                self.states["cloudwatch"] = "OFF"
        _LOCAL = self
        return self

    def _attach_cloudwatch(self, session, OTLPSpanExporter, BatchSpanProcessor, Compression) -> None:  # noqa: N803
        """Probe first (read-only): the X-Ray OTLP endpoint needs Transaction Search ACTIVE (a one-time account setting
        this notebook never changes). Then an OTLP exporter with a SigV4-signing requests session."""
        try:
            dest = session.client("xray").get_trace_segment_destination()
        except (ClientError, BotoCoreError) as e:
            self.states["cloudwatch"] = f"OFF (Transaction Search status unknown: {_code(e)})"
            return
        if dest.get("Destination") != "CloudWatchLogs" or dest.get("Status") != "ACTIVE":
            self.states["cloudwatch"] = (f"OFF (Transaction Search is {dest.get('Destination')}/{dest.get('Status')}; "
                                         "enabling it is an account setting — not done here)")
            return
        self.endpoint = XRAY_OTLP_ENDPOINT.format(region=session.region_name)
        s = sigv4_requests_session(session, service="xray")
        exports = self.xray_exports

        def hook(r, *a, **k):
            exports.append({"status": r.status_code, "t": time.time(), "bytes": len(r.request.body or b"")})
        s.hooks["response"].append(hook)
        self.provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=self.endpoint, session=s, compression=Compression.Gzip)))
        self.cloudwatch_ok = True
        self.states["cloudwatch"] = "on (OTLP/HTTP gzip -> X-Ray OTLP endpoint, SigV4 -> aws/spans)"

    @property
    def status(self) -> dict[str, str]:
        out = dict(self.states)
        if self.sampler is not None:
            out["sampler"] = self.sampler.get_description()
        return out

    def readable_spans(self, session_id: str | None = None) -> list:
        spans = list(self.memory.get_finished_spans()) if self.memory else []
        if session_id:
            spans = [s for s in spans if (s.attributes or {}).get("session.id") == session_id]
        return spans

    def rows(self, session_id: str | None = None) -> list[dict]:
        return span_rows(self.readable_spans(session_id))

    def clear(self) -> None:
        if self.memory:
            self.memory.clear()

    def force_flush(self) -> float:
        """Push the batch processors now (they otherwise deliver up to 5 s later). NB: returns True even when an export
        failed, so check the receiver / xray_exports, never the return value. -> seconds taken."""
        t0 = time.time()
        if self.provider is not None and not self.closed:
            self.provider.force_flush()
        return round(time.time() - t0, 2)

    def set_sampler(self, sampler) -> bool:
        if self.sampler is None:
            return False
        self.sampler.inner = sampler
        return True

    def shutdown(self) -> dict[str, str]:
        """flush -> shutdown the processors -> stop the receiver (in this order; stopping the receiver first makes the
        last flush retry for ~6.6 s). After this, spans are dropped for the rest of the kernel."""
        if self.closed:
            return {"local telemetry": "already shut down"}
        out = {}
        try:
            self.force_flush()
            self.provider.shutdown()
            out["local TracerProvider (3 processors)"] = "shut down ✓"
        except Exception as e:  # noqa: BLE001
            out["local TracerProvider (3 processors)"] = f"error: {type(e).__name__}"
        if self.receiver is not None:
            self.receiver.stop()
            out["OTLP receiver (127.0.0.1)"] = "stopped ✓" if not self.receiver.running else "error: still running"
        self.closed = True
        return out


# ======================================================================================================================
# 4. Loop detection and cost protection: custom metrics + two live high-resolution alarms
# ======================================================================================================================
WIRE_AGENT_NAME = "bank_wire_agent"
WIRE_TURN_LIMIT = 10                    # the in-agent cap (slide 28: a limit inside the agent is the strongest loop control)
LOOP_MAX_TOOL_CALLS = 5                 # loop alarm: ToolCallsInSession Maximum > 5 in a 10-s period
COST_MAX_RATIO = 2                      # cost alarm: TokensInSession / baseline > 2 (slide 28 "2x baseline")
WIRE_PROMPT = ("You are AnyCompany Bank's wire-transfer assistant. To answer a customer about a wire, call "
               "check_wire_status with the wire id. A status is final only when it is COMPLETED or RETURNED. "
               "If the status is PENDING you must call check_wire_status again before replying. "
               "Never reply to the customer while the status is PENDING. " + mc.NO_FLUFF)
WIRE_QUESTION = "Hi, this is Sofia Martinez (CUST-1001). Has my wire WIRE-4471 gone through yet?"


class WireDesk:
    """The bank's wire-status API as a Strands tool. status="COMPLETED" -> one call answers (the baseline);
    status="PENDING" -> 'pending, check again' every time, and the prompt's rule makes the agent loop until its cap."""

    def __init__(self, status: str = "COMPLETED"):
        from strands import tool

        self.status, self.calls = status, 0
        desk = self

        @tool
        def check_wire_status(wire_id: str) -> dict:
            """Look up the settlement status of an outgoing wire transfer by wire id (e.g. WIRE-0000)."""
            desk.calls += 1
            if desk.status == "COMPLETED":
                return {"wire_id": wire_id, "status": "COMPLETED", "settled_at": "2026-09-26T15:02:00Z",
                        "amount_usd": 2500.00}
            return {"wire_id": wire_id, "status": "PENDING", "message": "pending, check again"}

        self.tool = check_wire_status


def wire_agent(desk: WireDesk, *, hooks: Iterable = (), limits: dict | None = None, **agent_kwargs):
    """The wire assistant (Nova 2 Lite via mc.make_agent), capped at WIRE_TURN_LIMIT turns by default."""
    return mc.make_agent(WIRE_AGENT_NAME, WIRE_PROMPT, tools=[desk.tool], hooks=list(hooks),
                         limits=limits or {"turns": WIRE_TURN_LIMIT}, **agent_kwargs)


def loop_dimensions(run_id: str, agent_name: str = WIRE_AGENT_NAME) -> list[dict]:
    """Agent + RunId: a run-scoped dimension, so a new alarm never sees an older run's breaching datapoint."""
    return [{"Name": "Agent", "Value": agent_name}, {"Name": "RunId", "Value": run_suffix(run_id)}]


def _loop_hook_base():
    from strands.hooks import AfterToolCallEvent, BeforeInvocationEvent, HookProvider
    return AfterToolCallEvent, BeforeInvocationEvent, HookProvider


_AfterTool, _BeforeInv, _HookProvider = _loop_hook_base()


class LoopMetrics(_HookProvider):
    """Strands hook: after EVERY tool call, publish this session's running ToolCallsInSession and TokensInSession
    (PutMetricData, StorageResolution=1, dims Agent + RunId; 69-308 ms per call, median 72 ms). A failed put is
    recorded in .errors and never breaks the agent. .sent = [{t, call, tokens, tool}] for breach timing."""

    instances: list["LoopMetrics"] = []           # every hook of this kernel (the wrap-up counts their requests)

    def __init__(self, session, run_id: str, *, agent_name: str = WIRE_AGENT_NAME, namespace: str = M04_NAMESPACE,
                 enabled: bool = True):
        self.cw = session.client("cloudwatch", config=Config(connect_timeout=5, read_timeout=10,
                                                             retries={"total_max_attempts": 2, "mode": "standard"}))
        self.namespace, self.dims, self.enabled = namespace, loop_dimensions(run_id, agent_name), enabled
        self.calls: dict[int, int] = {}
        self.sent: list[dict] = []
        self.errors: list[str] = []
        self.puts = 0
        LoopMetrics.instances.append(self)

    def register_hooks(self, registry, **kwargs) -> None:
        registry.add_callback(_BeforeInv, self._before)
        registry.add_callback(_AfterTool, self._after_tool)

    def _before(self, event) -> None:
        self.calls[id(event.agent)] = 0

    def _after_tool(self, event) -> None:
        n = self.calls[id(event.agent)] = self.calls.get(id(event.agent), 0) + 1
        inv = event.agent.event_loop_metrics.latest_agent_invocation
        tokens = int(inv.usage.get("totalTokens", 0)) if inv else 0
        if self.enabled:
            try:
                self.cw.put_metric_data(Namespace=self.namespace, MetricData=[
                    {"MetricName": m, "Dimensions": self.dims, "Timestamp": datetime.now(timezone.utc), "Value": v,
                     "Unit": "Count", "StorageResolution": 1}
                    for m, v in (("ToolCallsInSession", n), ("TokensInSession", tokens))])
                self.puts += 1
            except (ClientError, BotoCoreError) as e:
                self.errors.append(_why(e))
        self.sent.append({"t": time.time(), "call": n, "tokens": tokens, "tool": event.tool_use.get("name")})

    def breaches(self, baseline_tokens: float, since_index: int = 0) -> dict[str, float | None]:
        """When the loop alarm's and the cost alarm's conditions were first crossed (epoch s) in sent[since_index:]."""
        rows = self.sent[since_index:]
        return {"loop": next((r["t"] for r in rows if r["call"] > LOOP_MAX_TOOL_CALLS), None),
                "cost": next((r["t"] for r in rows if r["tokens"] > COST_MAX_RATIO * baseline_tokens), None)}


class AlarmSet:
    """The two live alarms (10-s, high resolution, ActionsEnabled=False, TreatMissingData=notBreaching, tagged):
      loop  ToolCallsInSession Maximum > LOOP_MAX_TOOL_CALLS                        (slide 28: loop detection)
      cost  metric math TokensInSession / <baseline measured in §0> > COST_MAX_RATIO  (slide 28: cost protection)
    Measured: loop ALARM 13.0-21.4 s after the breaching datapoint (11/11), cost 16.4-26.0 s (6/6); a new alarm's first
    evaluation 6-66 s after creation; ALARM -> OK again 70-80 s later (research loop-alarms §3)."""

    KINDS = ("loop", "cost")

    def __init__(self, session, run_id: str, *, agent_name: str = WIRE_AGENT_NAME, namespace: str = M04_NAMESPACE,
                 tags: dict | None = None):
        self.session, self.run_id = session, run_id
        self.cw = session.client("cloudwatch")
        self.names = dict(resource_names(run_id)["alarms"])
        self.namespace, self.dims = namespace, loop_dimensions(run_id, agent_name)
        self.tags = dict(tags or {})
        self.baseline_tokens: float | None = None
        self.created_at: float | None = None
        self.adopted = False
        self.error: str | None = None

    def create(self, baseline_tokens: float) -> bool:
        """Put (or overwrite) both alarms for this run. Never raises; .error says why on failure."""
        self.baseline_tokens = float(baseline_tokens)
        common = dict(EvaluationPeriods=1, DatapointsToAlarm=1, ComparisonOperator="GreaterThanThreshold",
                      TreatMissingData="notBreaching", ActionsEnabled=False, Tags=_tags_list(self.tags))
        try:
            self.cw.put_metric_alarm(
                AlarmName=self.names["loop"], Namespace=self.namespace, MetricName="ToolCallsInSession",
                AlarmDescription=f"MLADAS M04 loop detection: more than {LOOP_MAX_TOOL_CALLS} tool calls in one session",
                Dimensions=self.dims, Statistic="Maximum", Period=10, Threshold=LOOP_MAX_TOOL_CALLS, **common)
            self.cw.put_metric_alarm(
                AlarmName=self.names["cost"],
                AlarmDescription=f"MLADAS M04 cost protection: session tokens > {COST_MAX_RATIO}x the measured baseline",
                Metrics=[{"Id": "m1", "ReturnData": False, "MetricStat": {"Metric": {
                              "Namespace": self.namespace, "MetricName": "TokensInSession", "Dimensions": self.dims},
                              "Period": 10, "Stat": "Maximum"}},
                         {"Id": "ratio", "Expression": f"m1 / {self.baseline_tokens:.0f}", "Label": "tokens / baseline",
                          "ReturnData": True}],
                Threshold=COST_MAX_RATIO, **common)
        except (ClientError, BotoCoreError) as e:
            self.error = _why(e)
            return False
        self.created_at, self.error = time.time(), None
        return True

    def adopt(self) -> bool:
        """Both alarms of this run exist -> reuse them (baseline parsed back from the cost alarm's expression)."""
        try:
            got = {a["AlarmName"]: a for a in self.cw.describe_alarms(AlarmNames=list(self.names.values()))["MetricAlarms"]}
        except (ClientError, BotoCoreError) as e:
            self.error = _why(e)
            return False
        if set(got) != set(self.names.values()):
            return False
        expr = next((m.get("Expression", "") for m in got[self.names["cost"]].get("Metrics", []) if m.get("Expression")), "")
        m = re.search(r"m1\s*/\s*([\d.]+)", expr)
        if not m:
            return False
        self.baseline_tokens, self.adopted = float(m.group(1)), True
        self.created_at = min(a["AlarmConfigurationUpdatedTimestamp"].timestamp() for a in got.values())
        return True

    def states(self) -> dict[str, dict]:
        try:
            got = {a["AlarmName"]: a for a in self.cw.describe_alarms(AlarmNames=list(self.names.values()))["MetricAlarms"]}
        except (ClientError, BotoCoreError) as e:
            return {k: {"state": "UNKNOWN", "reason": _why(e), "updated": None} for k in self.KINDS}
        return {k: {"state": got[n]["StateValue"], "reason": got[n].get("StateReason", ""),
                    "updated": got[n]["StateUpdatedTimestamp"].timestamp()} if n in got else
                {"state": "MISSING", "reason": "", "updated": None} for k, n in self.names.items()}

    def wait_for(self, state: str = "ALARM", *, kinds: Iterable[str] = KINDS, max_wait: float = 60, poll: float = 2,
                 since: float | None = None, quiet: bool = False) -> tuple[bool, dict, float]:
        """Wait (one updating line) until every alarm in kinds is in `state`; with since=, only a transition after that
        time counts (a stale ALARM from an earlier run doesn't). -> (ok, states, seconds). Never raises."""
        kinds = tuple(kinds)

        def probe():
            st = self.states()
            ok = all(st[k]["state"] == state and (since is None or state != "ALARM" or (st[k]["updated"] or 0) > since)
                     for k in kinds)
            return st if ok else None

        last: dict[str, Any] = {}

        def describe(_v):
            st = _v or self.states()
            last.update(st)
            return " · ".join(f"{k} {st[k]['state']}" for k in kinds)
        if quiet:
            t0 = time.time()
            while True:
                got = probe()
                if got or time.time() - t0 >= max_wait:
                    return bool(got), got or self.states(), round(time.time() - t0, 1)
                time.sleep(poll)
        ok, got, waited = mc.wait_until(probe, max_wait=max_wait, poll=poll, describe=describe,
                                        label=f"the {' and '.join(kinds)} alarm{'s' if len(kinds) > 1 else ''} to reach {state}")
        return ok, got or self.states(), waited

    def wait_armed(self, max_wait: float = 90, quiet: bool = False) -> bool:
        """True once both alarms are OK (their first evaluation takes 6-66 s after creation; after an ALARM they return
        to OK 70-80 s later)."""
        ok, _, _ = self.wait_for("OK", max_wait=max_wait, poll=3, quiet=quiet)
        return ok

    def history(self, kind: str, since: float | None = None) -> list[dict]:
        """StateUpdate history (newest first): {time, old, new, reason, datapoints}."""
        try:
            items = self.cw.describe_alarm_history(AlarmName=self.names[kind], HistoryItemType="StateUpdate",
                                                   ScanBy="TimestampDescending", MaxRecords=20)["AlarmHistoryItems"]
        except (ClientError, BotoCoreError):
            return []
        out = []
        for it in items:
            if since and it["Timestamp"].timestamp() < since:
                continue
            data = json.loads(it.get("HistoryData") or "{}")
            new = data.get("newState", {})
            out.append({"time": it["Timestamp"].timestamp(), "old": data.get("oldState", {}).get("stateValue"),
                        "new": new.get("stateValue"), "reason": new.get("stateReason", ""),
                        "datapoints": (new.get("stateReasonData") or {}).get("evaluatedDatapoints", [])})
        return out

    def alarm_seconds_after(self, kind: str, t: float) -> float | None:
        """Seconds from epoch t (the breach, or the end of the session) to this alarm's first ALARM transition after t."""
        hits = [h for h in self.history(kind, since=t) if h["new"] == "ALARM"]
        return round(min(h["time"] for h in hits) - t, 1) if hits else None

    def teardown(self) -> dict[str, str]:
        try:
            self.cw.delete_alarms(AlarmNames=list(self.names.values()))
        except (ClientError, BotoCoreError) as e:
            return {f"alarms {', '.join(self.names.values())}": f"NOT CLEANED: {_why(e)}"}
        return self.verify_gone()

    def verify_gone(self) -> dict[str, str]:
        out = {}
        for n in self.names.values():
            try:
                left = self.cw.describe_alarms(AlarmNames=[n], AlarmTypes=["MetricAlarm", "CompositeAlarm"])
                exists = bool(left.get("MetricAlarms") or left.get("CompositeAlarms"))
                out[f"CloudWatch alarm {n}"] = f"NOT CLEANED: still exists" if exists else GONE
            except (ClientError, BotoCoreError) as e:
                out[f"CloudWatch alarm {n}"] = f"NOT CLEANED: check failed ({_code(e)})"
        return out


# The alarm slide 29 describes (one session still alive after half an hour), in our own names. Its namespace and
# metric pair has no data: see slide29_check.
DECK_SLIDE29 = {"Namespace": "bedrock-agentcore", "MetricName": "Duration", "AlarmName": "SessionRunsTooLong",
                "Threshold": 30 * 60 * 1000, "Period": 300, "Statistic": "Maximum", "EvaluationPeriods": 1,
                "ComparisonOperator": "GreaterThanThreshold",
                "AlarmActions": ["arn:aws:sns:region:account:ops-pager"]}


def slide29_check(session, runtime: RuntimeObsDeployment | None = None) -> dict[str, Any]:
    """Read-only: does slide 29's metric exist? (It doesn't: namespace bedrock-agentcore holds ADOT app metrics and has
    no Duration; AWS/Bedrock-AgentCore Duration is per InvokeAgentRuntime request, in ms, not per session.)"""
    cw = session.client("cloudwatch")
    out: dict[str, Any] = {}
    try:
        out["bedrock-agentcore / Duration series"] = len(
            cw.list_metrics(Namespace="bedrock-agentcore", MetricName="Duration").get("Metrics", []))
        dims = [{"Name": "Name", "Value": f"{runtime.name}::DEFAULT"}] if runtime and runtime.runtime_id else []
        _resp = cw.list_metrics(Namespace="AWS/Bedrock-AgentCore", MetricName="Duration",
                                **({"Dimensions": dims} if dims else {}))
        vended = _resp.get("Metrics", [])
        out["AWS/Bedrock-AgentCore / Duration series" + (" (this runtime)" if dims else "")] = \
            f"{len(vended)}+ (first page)" if _resp.get("NextToken") else len(vended)
        out["dimensions"] = sorted({tuple(sorted(d["Name"] for d in m["Dimensions"])) for m in vended})[:4]
    except (ClientError, BotoCoreError) as e:
        out["error"] = _why(e)
    return out


def slide29_corrected(runtime: RuntimeObsDeployment | None = None, threshold_ms: float = 30000) -> dict:
    """The shape that has data for a Runtime agent: AWS/Bedrock-AgentCore Duration (== Latency, per request, ms).
    A per-SESSION duration needs a custom metric, a log-based alarm on spans, or lifecycleConfiguration.maxLifetime."""
    name = f"{runtime.name}::DEFAULT" if runtime else "<runtime name>::DEFAULT"
    arn = runtime.runtime_arn if runtime and runtime.runtime_arn else "<runtime ARN>"
    return {"Namespace": "AWS/Bedrock-AgentCore", "MetricName": "Duration", "Statistic": "Maximum", "Period": 60,
            "Dimensions": [{"Name": "Name", "Value": name}, {"Name": "Operation", "Value": "InvokeAgentRuntime"},
                           {"Name": "Resource", "Value": arn}],
            "EvaluationPeriods": 1, "Threshold": threshold_ms, "ComparisonOperator": "GreaterThanThreshold",
            "TreatMissingData": "notBreaching"}


# ======================================================================================================================
# 5. AgentCore Evaluations with Nova judges (derived built-ins + a custom compliance judge)
# ======================================================================================================================
DISCLAIMER = "This is general information, not personalized investment advice."
DISCLAIMER_INSTRUCTIONS = f"""You are a compliance reviewer for AnyCompany Bank.
Bank rule: a response that gives investment-related guidance (suggesting, recommending or comparing investments, funds,
stocks, bonds, crypto, asset allocation, retirement investing, or whether to move money into investments) MUST contain
the required disclaimer, word for word: "{DISCLAIMER}"
Responses that give no investment-related guidance (balances, fees, loans, card issues, refusals) are compliant
without a disclaimer.

Conversation so far:
{{context}}

Assistant response to review:
{{assistant_turn}}

First decide whether the response gives investment-related guidance. Then check whether the exact disclaimer sentence
appears in the response. Rate using the scale."""
DISCLAIMER_SCALE = {"categorical": [
    {"label": "Compliant", "definition": "No investment-related guidance, OR investment-related guidance that contains "
                                         "the exact required disclaimer."},
    {"label": "NonCompliant", "definition": "Investment-related guidance WITHOUT the exact required disclaimer."}]}
JUDGE_MODEL_CONFIG = {"bedrockEvaluatorModelConfig": {"modelId": JUDGE_MODEL_ID,
                                                      "inferenceConfig": {"maxTokens": 1024, "temperature": 0.0}}}
EVALUATOR_SPECS: dict[str, dict] = {
    "helpful": {"kind": "derived", "base": "Builtin.Helpfulness", "level": "TRACE",
                "description": "Builtin.Helpfulness logic, judged by Nova 2 Lite"},
    "goal": {"kind": "derived", "base": "Builtin.GoalSuccessRate", "level": "SESSION",
             "description": "Builtin.GoalSuccessRate logic, judged by Nova 2 Lite"},
    "toolsel": {"kind": "derived", "base": "Builtin.ToolSelectionAccuracy", "level": "TOOL_CALL",
                "description": "Builtin.ToolSelectionAccuracy logic, judged by Nova 2 Lite"},
    "disclaimer": {"kind": "custom", "level": "TRACE",
                   "description": "Investment-disclaimer compliance (deck Q2 scenario), Nova 2 Lite judge"},
}
_PASS = {"compliant", "yes", "correct", "perfectly correct", "very helpful", "above and beyond", "helpful"}
_FAIL = {"noncompliant", "no", "incorrect", "not helpful", "very unhelpful", "somewhat unhelpful"}


def has_disclaimer(text: str) -> bool:
    """The deterministic check next to the judge: the exact disclaimer sentence is in the answer."""
    return DISCLAIMER in (text or "")


def eval_state(label: str | None, value: float | None, error: str | None = None) -> str:
    """Map a result to a state for colors (viz.EVAL_LABEL_COLORS): pass · partial · fail · error. Labels differ by
    evaluator (Compliant, Yes, Very Helpful, …) and values come back normalised to 0-1: print them as returned."""
    if error:
        return "error"
    lab = (label or "").strip().lower()
    if lab in _PASS:
        return "pass"
    if lab in _FAIL:
        return "fail"
    if value is not None:
        return "pass" if value >= 0.75 else "fail" if value < 0.5 else "partial"
    return "partial"


def catalog(session) -> dict[str, Any]:
    """ListEvaluators counts by type and level (built-in + third-party; the deck says 13 built-ins)."""
    ctl = session.client("bedrock-agentcore-control")
    evs, token = [], None
    try:
        while True:
            page = ctl.list_evaluators(maxResults=100, **({"nextToken": token} if token else {}))
            evs += page.get("evaluators", [])
            token = page.get("nextToken")
            if not token:
                break
    except (ClientError, BotoCoreError) as e:
        return {"error": _why(e)}
    from collections import Counter
    by = Counter((e["evaluatorType"], e.get("level")) for e in evs if e["evaluatorType"] in ("Builtin", "ThirdParty"))
    return {"builtin": sum(v for (t, _), v in by.items() if t == "Builtin"),
            "third_party": sum(v for (t, _), v in by.items() if t == "ThirdParty"),
            "by_type_level": {f"{t} {lvl}": n for (t, lvl), n in sorted(by.items())},
            "builtin_ids": sorted(e["evaluatorId"] for e in evs if e["evaluatorType"] == "Builtin")}


def _chunks(seq: list, n: int = 10) -> list[list]:
    return [seq[i:i + n] for i in range(0, len(seq), n)] or [[]]


class EvaluatorSet:
    """Four evaluators for this run, every judge Nova 2 Lite (never an AWS-managed built-in as-is):
      helpful     derived  Builtin.Helpfulness            TRACE
      goal        derived  Builtin.GoalSuccessRate        SESSION
      toolsel     derived  Builtin.ToolSelectionAccuracy  TOOL_CALL
      disclaimer  custom   LLM judge, categorical Compliant / NonCompliant (deck Q2), TRACE
    ACTIVE in the create response (0.6-1.2 s each). Custom/derived evaluations bill $0.0015 each + the judge's Nova tokens
    in this account: evaluate() books both in the ledger (current scope, with your correlation id)."""

    def __init__(self, session, run_id: str, *, tags: dict | None = None, judge_model: str = JUDGE_MODEL_ID):
        mc.model_id(judge_model)
        self.session, self.run_id = session, run_id
        self.ctl, self.dp = session.client("bedrock-agentcore-control"), session.client(
            "bedrock-agentcore", config=Config(read_timeout=60, retries={"total_max_attempts": 3, "mode": "standard"}))
        self.names = dict(resource_names(run_id)["evaluators"])
        self.tags, self.judge_model = dict(tags or {}), judge_model
        self.judge_config = {"bedrockEvaluatorModelConfig": {"modelId": judge_model,
                                                             "inferenceConfig": {"maxTokens": 1024, "temperature": 0.0}}}
        self.ids: dict[str, str] = {}
        self.levels = {k: v["level"] for k, v in EVALUATOR_SPECS.items()}
        self.adopted: list[str] = []
        self.error: str | None = None
        self.timings: dict[str, float] = {}
        self.account = _account(session)

    @property
    def ready(self) -> bool:
        return set(self.ids) == set(EVALUATOR_KEYS)

    def _existing(self) -> dict[str, str]:
        found, token = {}, None
        while True:
            page = self.ctl.list_evaluators(maxResults=100, **({"nextToken": token} if token else {}))
            for e in page.get("evaluators", []):
                for k, n in self.names.items():
                    if e.get("evaluatorName") == n and e.get("status") != "DELETING":
                        found[k] = e["evaluatorId"]
            token = page.get("nextToken")
            if not token:
                return found

    def adopt(self) -> bool:
        try:
            got = self._existing()
        except (ClientError, BotoCoreError) as e:
            self.error = _why(e, self.account)
            return False
        self.ids.update(got)
        self.adopted = sorted(got)
        return self.ready

    def _config(self, key: str) -> dict:
        spec = EVALUATOR_SPECS[key]
        if spec["kind"] == "derived":
            return {"derived": {"baseEvaluatorId": spec["base"], "modelConfig": self.judge_config}}
        return {"llmAsAJudge": {"instructions": DISCLAIMER_INSTRUCTIONS, "ratingScale": DISCLAIMER_SCALE,
                                "modelConfig": self.judge_config}}

    def create(self) -> bool:
        """Create the missing evaluators (adopting any that exist by name). Never raises; .error on failure."""
        t0 = time.time()
        if not self.adopt() and self.error:
            return False
        for key in EVALUATOR_KEYS:
            if key in self.ids:
                continue
            spec = EVALUATOR_SPECS[key]
            t = time.time()
            try:
                r = self.ctl.create_evaluator(evaluatorName=self.names[key], level=spec["level"],
                                              description=spec["description"], evaluatorConfig=self._config(key),
                                              tags=self.tags, clientToken=f"mladas-{uuid.uuid4().hex}")
                self.ids[key] = r["evaluatorId"]
                if r.get("status") != "ACTIVE":
                    self._wait_active(r["evaluatorId"])
            except ClientError as e:
                if _code(e) == "ConflictException":
                    self.adopt()
                    if key in self.ids:
                        continue
                self.error = _why(e, self.account)
                return False
            except BotoCoreError as e:
                self.error = _why(e, self.account)
                return False
            self.timings[key] = round(time.time() - t, 2)
        self.timings["total"] = round(time.time() - t0, 2)
        self.error = None
        return self.ready

    def _wait_active(self, eid: str, timeout: float = 30) -> None:
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = self.ctl.get_evaluator(evaluatorId=eid)["status"]
            if st == "ACTIVE":
                return
            if st.endswith("FAILED"):
                raise RuntimeError(f"evaluator {eid} {st}")
            time.sleep(1)

    def describe(self, key: str) -> dict[str, Any]:
        """What the evaluator is, masked: name, id, level, kind, base evaluator, judge model, rating scale."""
        try:
            g = self.ctl.get_evaluator(evaluatorId=self.ids[key])
        except (ClientError, BotoCoreError, KeyError) as e:
            return {"key": key, "error": _why(e, self.account) if not isinstance(e, KeyError) else "not created"}
        cfg = g.get("evaluatorConfig", {})
        derived, judge = cfg.get("derived"), cfg.get("llmAsAJudge")
        model = ((derived or judge or {}).get("modelConfig", {}).get("bedrockEvaluatorModelConfig", {}) or {}).get("modelId")
        scale = (judge or {}).get("ratingScale") or {}
        return mask_deep({"key": key, "name": g.get("evaluatorName"), "id": g.get("evaluatorId"), "level": g.get("level"),
                          "type": g.get("evaluatorType"), "base": (derived or {}).get("baseEvaluatorId") or "(custom prompt)",
                          "judge model": model, "status": g.get("status"),
                          "locked": g.get("lockedForModification"),
                          "scale": ", ".join(x.get("label", "") for x in (scale.get("categorical") or scale.get("numerical") or []))
                          or "(the built-in's own scale)"}, self.account)

    def config_rows(self) -> list[dict]:
        return [self.describe(k) for k in EVALUATOR_KEYS if k in self.ids]

    # --------------------------------------------------------------------------------------------- evaluate
    def evaluate(self, key: str, docs: list[dict], target: dict | None = None, *, correlation_id: str | None = None,
                 book: bool = True) -> list[dict]:
        """One Evaluate call per <= 10 target ids. -> [{evaluator, level, session_id, trace_id, span_id, label, value,
        state, explanation, input_tokens, output_tokens, latency_s, error}]. Per-result errors (e.g.
        LogEventMissingException inside an HTTP 200) become rows with error set. Never raises."""
        rows: list[dict] = []
        if key not in self.ids:
            return [{"evaluator": key, "error": "evaluator not created", "state": "error"}]
        if not docs:
            return [{"evaluator": key, "error": "no spans to evaluate", "state": "error"}]
        targets = [None]
        if target:
            (tk, ids), = target.items()
            targets = [{tk: c} for c in _chunks(list(ids))]
        for tgt in targets:
            kw: dict[str, Any] = {"evaluatorId": self.ids[key], "evaluationInput": {"sessionSpans": docs}}
            if tgt:
                kw["evaluationTarget"] = tgt
            t0 = time.time()
            try:
                res = self.dp.evaluate(**kw)["evaluationResults"]
            except (ClientError, BotoCoreError) as e:
                rows.append({"evaluator": key, "level": self.levels[key], "error": _why(e, self.account),
                             "latency_s": round(time.time() - t0, 2), "state": "error"})
                continue
            dt = round(time.time() - t0, 2)
            for r in res:
                ctx = (r.get("context") or {}).get("spanContext") or {}
                tok = r.get("tokenUsage") or {}
                err = r.get("errorCode")
                rows.append({"evaluator": key, "level": self.levels[key], "session_id": ctx.get("sessionId"),
                             "trace_id": ctx.get("traceId"), "span_id": ctx.get("spanId"), "label": r.get("label"),
                             "value": r.get("value"), "state": eval_state(r.get("label"), r.get("value"), err),
                             "explanation": _mask(r.get("explanation") or "", self.account),
                             "input_tokens": int(tok.get("inputTokens", 0)), "output_tokens": int(tok.get("outputTokens", 0)),
                             "latency_s": dt, "error": f"{err}: {mc.short(_mask(r.get('errorMessage', ''), self.account), 140)}"
                             if err else None})
        if book:
            self.book(rows, correlation_id)
        return rows

    def book(self, rows: list[dict], correlation_id: str | None = None) -> None:
        """Ledger rows for evaluation results: the judge's Nova tokens (model rows) + the per-evaluation fee."""
        ok = [r for r in rows if not r.get("error") and (r.get("input_tokens") or r.get("output_tokens"))]
        for r in ok:
            usage = {"inputTokens": r["input_tokens"], "outputTokens": r["output_tokens"]}
            mc.LEDGER.add(agent=f"judge {r['evaluator']} (Evaluations)", model=self.judge_model,
                          input_tokens=r["input_tokens"], output_tokens=r["output_tokens"], cache_read_tokens=0,
                          cache_write_tokens=0, model_calls=1, latency_s=r.get("latency_s"),
                          cost_usd=mc.cost_usd(self.judge_model, usage), stop_reason=None, correlation_id=correlation_id)
        if ok:
            mc.LEDGER.add_service("evaluation_custom", len(ok), mc.AGENTCORE_PRICES["evaluation_custom"],
                                  correlation_id=correlation_id, note=f"{len(ok)} custom/derived evaluations",
                                  provider="AgentCore Evaluations")

    def _run_keys(self, keys: Iterable[str], docs: list[dict], trace_ids: list[str] | None,
                  correlation_id: str | None) -> list[dict]:
        tool_ids = [d["spanId"] for d in docs if "body" not in d and
                    (d.get("attributes") or {}).get("gen_ai.operation.name") == "execute_tool"]
        jobs = []
        for key in keys:
            level = self.levels.get(key)
            if level == "TOOL_CALL":
                if not tool_ids:
                    continue
                jobs.append((key, {"spanIds": tool_ids}))
            elif level == "TRACE" and trace_ids:
                jobs.append((key, {"traceIds": list(trace_ids)}))
            else:
                jobs.append((key, None))
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(jobs)))) as pool:     # <= 6 concurrent model calls
            futures = [pool.submit(self.evaluate, k, docs, t, correlation_id=correlation_id, book=False) for k, t in jobs]
            rows = [r for f in futures for r in f.result()]
        self.book(rows, correlation_id)                          # booked in the caller's thread (ledger scope)
        return rows

    def evaluate_local(self, spans: list, keys: Iterable[str] = EVALUATOR_KEYS, *, trace_ids: list[str] | None = None,
                       correlation_id: str | None = None) -> list[dict]:
        """On-demand evaluation of a LOCAL agent's in-memory spans (LocalTelemetry.readable_spans(session_id)):
        bedrock_agentcore.evaluation.convert_strands_to_adot builds the ADOT span + log documents, no CloudWatch."""
        from bedrock_agentcore.evaluation import convert_strands_to_adot
        try:
            docs = convert_strands_to_adot(list(spans))
        except Exception as e:  # noqa: BLE001
            return [{"evaluator": k, "error": f"convert failed: {type(e).__name__}", "state": "error"} for k in keys]
        return self._run_keys(keys, docs, trace_ids, correlation_id)

    def evaluate_runtime(self, session_id: str, runtime: RuntimeObsDeployment, since: float,
                         keys: Iterable[str] = EVALUATOR_KEYS, *, trace_ids: str | list[str] = "all",
                         min_traces: int = 1, correlation_id: str | None = None, max_wait: float = 60,
                         quiet: bool = False) -> list[dict]:
        """On-demand evaluation of a RUNTIME session read back from CloudWatch (spans from aws/spans + the GenAI content
        records from the runtime log group), after the completeness gate (min_traces = the requests you sent).
        trace_ids: "all" | "last" | [ids]."""
        docs, info = runtime_session_docs(session_id, runtime.log_group, session=self.session, since=since,
                                          max_wait=max_wait, min_traces=min_traces, quiet=quiet)
        if not docs:
            return [{"evaluator": k, "error": f"no spans for the session ({info.get('error') or 'not found'})",
                     "state": "error"} for k in keys]
        agent_traces = list(dict.fromkeys(d["traceId"] for d in docs if str(d.get("name", "")).startswith("invoke_agent")))
        tids = agent_traces[-1:] if trace_ids == "last" else (None if trace_ids == "all" else list(trace_ids))
        rows = self._run_keys(keys, docs, tids, correlation_id)
        for r in rows:
            r.setdefault("gate", "complete" if info.get("complete") else "INCOMPLETE")
        return rows

    # --------------------------------------------------------------------------------------------- cleanup
    def teardown(self) -> dict[str, str]:
        """Delete the evaluators (after the online config that locks them). -> by-id verification."""
        out = {}
        if not self.ids:
            self.adopt()
        for key, eid in list(self.ids.items()):
            for attempt in range(6):
                try:
                    self.ctl.delete_evaluator(evaluatorId=eid)
                    break
                except ClientError as e:
                    if _code(e) in _NOT_FOUND:
                        break
                    if "locked" in str(e).lower() and attempt < 5:         # an online config is still being deleted
                        time.sleep(5)
                        continue
                    out[f"evaluator {eid}"] = f"NOT CLEANED: {_why(e, self.account)}"
                    break
                except BotoCoreError as e:
                    out[f"evaluator {eid}"] = f"NOT CLEANED: {_why(e)}"
                    break
        for k, v in self.verify_gone().items():
            out.setdefault(k, v)
        return out

    def verify_gone(self) -> dict[str, str]:
        out = {}
        for eid in self.ids.values():
            for _ in range(10):
                label, st = _verify(f"evaluator {eid}", lambda e=eid: self.ctl.get_evaluator(evaluatorId=e))
                if st == GONE:
                    break
                time.sleep(1)
            out[label] = st
        return out


def runtime_session_docs(session_id: str, runtime_log_group: str | None, *, session, since: float, max_wait: float = 60,
                         min_traces: int = 1, spans_log_group: str = SPANS_LOG_GROUP,
                         quiet: bool = False) -> tuple[list[dict], dict]:
    """ADOT documents of one Runtime session for Evaluate: span docs (aws/spans) + the GenAI content log records
    (runtime log group, stream otel-rt-logs), re-read until every invoke_agent span has its log record (without the gate
    Evaluate returns LogEventMissingException inside an HTTP 200; one read ~2.5 min after the calls returned 0/12
    records, research evaluations F21). min_traces: the number of requests you sent in the session — a trace can be
    missing entirely (e.g. StopRuntimeSession right after an answer kills the microVM before ADOT exports its last batch:
    foundations 2026-09-28). -> (docs, {spans, events, traces, complete, queries, seconds, error})."""
    if not runtime_log_group:
        return [], {"error": "no runtime log group", "complete": False}
    q = (f'fields @timestamp, @message | filter attributes.session.id = "{_safe_id(session_id)}" '
         "| filter ispresent(scope.name) | filter ispresent(traceId) | filter ispresent(spanId) "
         "| sort @timestamp asc | limit 10000")
    state: dict[str, Any] = {"spans": [], "events": [], "queries": 0, "error": None}
    t0 = time.time()

    def read(group):
        rows = _insights(group, q, since, session=session, max_wait=20)
        out = []
        for row in rows:
            try:
                out.append(json.loads(row.get("@message", "")))
            except ValueError:
                continue
        return out

    def probe():
        try:
            state["spans"], state["events"] = read(spans_log_group), read(runtime_log_group)
        except (ClientError, BotoCoreError) as e:
            state["error"] = _why(e)
            return None
        state["queries"] += 2
        need = {d.get("spanId") for d in state["spans"] if str(d.get("name", "")).startswith("invoke_agent")}
        have = {d.get("spanId") for d in state["events"]}
        rows = [{"trace_id": d.get("traceId"), "span_id": d.get("spanId"), "parent_id": d.get("parentSpanId") or None,
                 "name": str(d.get("name", ""))} for d in state["spans"]]
        traces = {r["trace_id"] for r in rows if r["name"].startswith("invoke_agent")}
        state["traces"] = len(traces)
        ok = need and need <= have and len(traces) >= min_traces and all(trace_complete(rows, t) for t in traces)
        return True if ok else None

    describe = (lambda _v: f"{len(state['spans'])} spans, {len(state['events'])} content records")
    if quiet:
        done = False
        while True:
            done = probe() is not None
            if done or time.time() - t0 >= max_wait:
                break
            time.sleep(5)
    else:
        done, _, _ = mc.wait_until(probe, max_wait=max_wait, poll=5, describe=describe,
                                   label=f"session …{session_id[-8:]}: spans + content records in CloudWatch")
    info = {"spans": len(state["spans"]), "events": len(state["events"]), "traces": state.get("traces", 0),
            "complete": bool(done),
            "queries": state["queries"], "seconds": round(time.time() - t0, 1), "error": state["error"]}
    return state["spans"] + state["events"], info


class OnlineEvaluation:
    """Online evaluation of the Runtime agent (asynchronous, sampled live traffic): an execution role (trust
    bedrock-agentcore.amazonaws.com for this run's evaluators + online config only; reads aws/spans + this run's runtime
    log groups, writes only this run's results log group, invokes only the Nova judge), then
    CreateOnlineEvaluationConfig on the runtime log group + service name <runtime>.DEFAULT, sampling 100 %,
    sessionTimeoutMinutes 1, ENABLED (ACTIVE in ~1.5 s). The service discovers sessions every ~5 min over a window that
    ends 5 min before its query, so results arrive ~10 min after a session ends (9.6-10.6 min, 2/2 rounds).
    Side effect (research evaluations F30): the create call adds an index policy on the shared aws/spans log group as
    the caller; teardown reverts it only if it was absent before this run and no other online config exists.
    batch() is the narrated fallback: StartBatchEvaluation on chosen session ids, COMPLETED in ~60-70 s."""

    def __init__(self, session, run_id: str, *, tags: dict | None = None, judge_model: str = JUDGE_MODEL_ID):
        n = resource_names(run_id)
        self.session, self.run_id, self.names = session, run_id, n
        self.ctl = session.client("bedrock-agentcore-control")
        self.dp = session.client("bedrock-agentcore")
        self.logs, self.iam = session.client("logs"), session.client("iam")
        self.tags, self.judge_model = dict(tags or {}), judge_model
        self.role_name, self.config_name = n["online_role"], n["online_config"]
        self.role_arn = self.config_id = self.config_arn = self.results_log_group = None
        self.status = "NOT_CREATED"
        self.error: str | None = None
        self.created_at: float | None = None
        self.adopted = False
        self.index_policy_before: list | None = None
        self.batches: list[dict] = []
        self.batch_group_existed: bool | None = None
        self.account = _account(session)
        self.region = session.region_name

    @property
    def ready(self) -> bool:
        return self.status == "ACTIVE"

    def create_role(self) -> str | None:
        """The online-evaluation execution role (create or adopt). Create it early: IAM propagation ~10 s.
        Scoped to THIS run (the account may be shared): the trust accepts only this run's evaluators and online config (their ids
        start with their names: '<name>-<10 chars>'), and results may be written only to this run's results log group
        ('/aws/bedrock-agentcore/evaluations/results/<config id>'). An existing role gets the same trust again."""
        acct, reg = self.account, self.region
        rt_prefix = self.names["runtime_log_group_prefix"]
        arn = f"arn:aws:bedrock-agentcore:{reg}:{acct}"
        trust = {"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": acct, "aws:ResourceAccount": acct},
                          "ArnLike": {"aws:SourceArn": [f"{arn}:evaluator/{self.names['evaluator_prefix']}*",
                                                         f"{arn}:online-evaluation-config/{self.config_name}-*"]}}}]}
        base = self.judge_model.split(".", 1)[1] if self.judge_model.startswith(("us.", "global.")) else self.judge_model
        lg = f"arn:aws:logs:{reg}:{acct}:log-group:"
        perm = {"Version": "2012-10-17", "Statement": [
            {"Sid": "DescribeLogGroups", "Effect": "Allow", "Action": ["logs:DescribeLogGroups"], "Resource": "*"},
            {"Sid": "ReadTraces", "Effect": "Allow", "Action": ["logs:StartQuery", "logs:GetQueryResults"],
             "Resource": [f"{lg}aws/spans", f"{lg}aws/spans:*", f"{lg}{rt_prefix}*"]},
            {"Sid": "QueryResults", "Effect": "Allow", "Action": ["logs:GetQueryResults", "logs:StopQuery"], "Resource": "*"},
            {"Sid": "WriteResults", "Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": f"{lg}{self.names['results_log_group_prefix']}-*"},
            {"Sid": "DescribeIndex", "Effect": "Allow", "Action": ["logs:DescribeIndexPolicies"],
             "Resource": [f"{lg}aws/spans", f"{lg}aws/spans:*", f"{lg}{rt_prefix}*"]},
            {"Sid": "NovaJudge", "Effect": "Allow",
             "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
             "Resource": [f"arn:aws:bedrock:{reg}:{acct}:inference-profile/{self.judge_model}",
                          f"arn:aws:bedrock:*::foundation-model/{base}"]}]}
        try:
            try:
                self.role_arn = self.iam.create_role(
                    RoleName=self.role_name, AssumeRolePolicyDocument=json.dumps(trust),
                    Description="MLADAS M04 online evaluation execution role", Tags=_tags_list(self.tags))["Role"]["Arn"]
            except self.iam.exceptions.EntityAlreadyExistsException:
                self.role_arn = self.iam.get_role(RoleName=self.role_name)["Role"]["Arn"]
                self.iam.update_assume_role_policy(RoleName=self.role_name, PolicyDocument=json.dumps(trust))
            self.iam.put_role_policy(RoleName=self.role_name, PolicyName="online-evaluation",
                                     PolicyDocument=json.dumps(perm))
        except (ClientError, BotoCoreError) as e:
            self.error = _why(e, self.account)
            return None
        return self.role_arn

    def _find(self) -> dict | None:
        token = None
        while True:
            page = self.ctl.list_online_evaluation_configs(maxResults=50, **({"nextToken": token} if token else {}))
            for c in page.get("onlineEvaluationConfigs", []):
                if c.get("onlineEvaluationConfigName") == self.config_name:
                    return c
            token = page.get("nextToken")
            if not token:
                return None

    def _load(self, cfg_id: str) -> dict:
        g = self.ctl.get_online_evaluation_config(onlineEvaluationConfigId=cfg_id)
        self.config_id, self.config_arn = g["onlineEvaluationConfigId"], g["onlineEvaluationConfigArn"]
        self.results_log_group = ((g.get("outputConfig") or {}).get("cloudWatchConfig") or {}).get("logGroupName") \
            or f"{RESULTS_LOG_GROUP_ROOT}{self.config_id}"
        self.status = g["status"]
        self.role_arn = g.get("evaluationExecutionRoleArn") or self.role_arn
        return g

    def adopt(self) -> bool:
        try:
            c = self._find()
            if not c:
                return False
            self._load(c["onlineEvaluationConfigId"])
            self.adopted = True
            if not self.role_arn:
                self.role_arn = self.iam.get_role(RoleName=self.role_name)["Role"]["Arn"]
            return True
        except (ClientError, BotoCoreError) as e:
            self.error = _why(e, self.account)
            return False

    def create(self, runtime: RuntimeObsDeployment, evaluator_ids: Iterable[str], *, sampling_percent: float = 100.0,
               session_timeout_minutes: int = 1, max_wait: float = 90) -> bool:
        """Create (or adopt) the config once the runtime is READY (its log group must exist). Never raises."""
        if self.adopt() and self.status == "ACTIVE":
            return True
        if not runtime or not runtime.log_group:
            self.error = "runtime not READY (its log group must exist)"
            return False
        if not self.role_arn and not self.create_role():
            return False
        if self.index_policy_before is None:
            try:
                self.index_policy_before = self.logs.describe_index_policies(
                    logGroupIdentifiers=[SPANS_LOG_GROUP]).get("indexPolicies", [])
            except (ClientError, BotoCoreError):
                self.index_policy_before = None
        kw = dict(onlineEvaluationConfigName=self.config_name,
                  description="MLADAS M04 online evaluation (Nova judges)",
                  rule={"samplingConfig": {"samplingPercentage": float(sampling_percent)},
                        "sessionConfig": {"sessionTimeoutMinutes": int(session_timeout_minutes)}},
                  dataSourceConfig={"cloudWatchLogs": {"logGroupNames": [runtime.log_group],
                                                       "serviceNames": [runtime.service_name]}},
                  evaluators=[{"evaluatorId": e} for e in evaluator_ids],
                  evaluationExecutionRoleArn=self.role_arn, enableOnCreate=True, tags=self.tags)
        t0 = time.time()
        while True:
            try:
                r = self.ctl.create_online_evaluation_config(clientToken=f"mladas-{uuid.uuid4().hex}", **kw)
                self.config_id = r["onlineEvaluationConfigId"]
                break
            except ClientError as e:
                code, msg = _code(e), str(e).lower()
                if code == "ConflictException" and self.adopt():
                    break
                retriable = code in ("ValidationException", "AccessDeniedException") and \
                    any(w in msg for w in ("role", "assume", "log group", "permission", "access"))
                if retriable and time.time() - t0 < max_wait - 10:
                    time.sleep(8)                              # IAM propagation / the runtime log group appearing
                    continue
                self.error = _why(e, self.account)
                return False
            except BotoCoreError as e:
                self.error = _why(e, self.account)
                return False
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                g = self._load(self.config_id)
            except (ClientError, BotoCoreError) as e:
                self.error = _why(e, self.account)
                return False
            if g["status"] == "ACTIVE":
                self.created_at, self.error = time.time(), None
                return True
            if g["status"].endswith("FAILED") or g["status"] == "ERROR":
                self.error = _mask(g.get("failureReason") or g["status"], self.account)
                return False
            time.sleep(1.5)
        self.error = f"not ACTIVE after 60 s ({self.status})"
        return False

    def describe(self) -> dict[str, Any]:
        if not self.config_id:
            return {"status": self.status, "error": self.error}
        try:
            g = self.ctl.get_online_evaluation_config(onlineEvaluationConfigId=self.config_id)
        except (ClientError, BotoCoreError) as e:
            return {"status": "UNKNOWN", "error": _why(e, self.account)}
        rule = g.get("rule", {})
        src = (g.get("dataSourceConfig") or {}).get("cloudWatchLogs", {})
        return mask_deep({"name": g.get("onlineEvaluationConfigName"), "id": g.get("onlineEvaluationConfigId"),
                          "status": g.get("status"), "execution": g.get("executionStatus"),
                          "sampling %": (rule.get("samplingConfig") or {}).get("samplingPercentage"),
                          "session idle timeout (min)": (rule.get("sessionConfig") or {}).get("sessionTimeoutMinutes"),
                          "log groups": src.get("logGroupNames"), "service names": src.get("serviceNames"),
                          "evaluators": [e.get("evaluatorId") for e in g.get("evaluators", [])],
                          "results log group": self.results_log_group}, self.account)

    _RESULT_FIELDS = ("fields @timestamp, attributes.session.id as session_id, attributes.gen_ai.response.id as trace_id, "
                      "attributes.gen_ai.evaluation.name as evaluator, attributes.gen_ai.evaluation.score.label as label, "
                      "attributes.gen_ai.evaluation.score.value as value, attributes.gen_ai.evaluation.explanation as "
                      "explanation, attributes.aws.bedrock_agentcore.evaluation_level as level, "
                      "attributes.error.type as error_type")

    def _result_rows(self, raw: Iterable[dict], source: str) -> list[dict]:
        prefix = self.names["evaluator_prefix"]
        out = []
        for r in raw:
            ev = str(r.get("evaluator") or "")
            val = r.get("value")
            try:
                val = float(val) if val not in (None, "") else None
            except ValueError:
                val = None
            err = r.get("error_type") or None
            out.append({"evaluator": ev[len(prefix):] if ev.startswith(prefix) else ev, "level": r.get("level"),
                        "session_id": r.get("session_id"), "trace_id": r.get("trace_id"), "label": r.get("label"),
                        "value": val, "state": eval_state(r.get("label"), val, err),
                        "explanation": _mask(r.get("explanation") or "", self.account),
                        "error": err, "logged_at": r.get("@timestamp"), "source": source})
        return out

    def results(self, session_ids: Iterable[str] | None = None, since: float | None = None,
                max_wait: float = 20) -> list[dict]:
        """Online results so far (Logs Insights on the results log group); [] until the first results land."""
        if not self.results_log_group:
            return []
        q = self._RESULT_FIELDS + ' | filter name = "gen_ai.evaluation.result"'
        ids = [_safe_id(s) for s in (session_ids or [])]
        if ids:
            q += " | filter attributes.session.id in [" + ", ".join(f'"{s}"' for s in ids) + "]"
        q += " | sort @timestamp asc | limit 1000"
        try:
            rows = _insights(self.results_log_group, q, since or (self.created_at or time.time()) - 3600,
                             session=self.session, max_wait=max_wait)
        except (ClientError, BotoCoreError):
            return []                                         # the group has no events yet / does not exist yet
        return self._result_rows(rows, "online")

    def result_count(self, since: float | None = None) -> int:
        return len(self.results(since=since))

    BATCH_MIN_AGE_S = 300      # sessions ~1.5 min old: FAILED, LogEventMissingException (2/2); ~5-6 min old: OK (2/2)

    def batch(self, runtime: RuntimeObsDeployment, evaluator_ids: Iterable[str], session_ids: Iterable[str], *,
              max_wait: float = 120, poll: float = 5, quiet: bool = False, ended_at: float | None = None,
              min_age_s: float = BATCH_MIN_AGE_S) -> dict[str, Any]:
        """The fallback when online results are not in yet: StartBatchEvaluation over the given Runtime sessions (the
        same evaluators), polled until done (~60-70 s), results read from the batch log stream. Never raises.
        The service's own read of the content records misses them for young sessions: batches on sessions ~1.5 min old
        FAILED with LogEventMissingException (2/2 on 2026-09-28), ~5-6 min old COMPLETED (2/2, research 1/1). With
        ended_at= (epoch s of the sessions' last answer) a batch on sessions younger than min_age_s is not started:
        -> {"status": "TOO_EARLY", "wait_s": …} (at the demo's pace the warm-ups are ~15 min old by §3.4)."""
        name = f"{self.names['batch_prefix']}_{uuid.uuid4().hex[:4]}"
        sids = list(session_ids)
        if ended_at is not None and time.time() - ended_at < min_age_s:
            return {"status": "TOO_EARLY", "wait_s": round(min_age_s - (time.time() - ended_at)), "rows": [],
                    "summaries": [], "sessions": sids, "seconds": 0.0,
                    "error": f"sessions ended {time.time() - ended_at:.0f} s ago; a batch needs ~{min_age_s:.0f} s"}
        if self.batch_group_existed is None:
            try:
                self.batch_group_existed = any(g["logGroupName"] == BATCH_RESULTS_LOG_GROUP for g in self.logs.describe_log_groups(
                    logGroupNamePrefix=BATCH_RESULTS_LOG_GROUP)["logGroups"])
            except (ClientError, BotoCoreError):
                self.batch_group_existed = True                  # unknown -> never delete the shared group
        t0 = time.time()
        try:
            r = self.dp.start_batch_evaluation(
                batchEvaluationName=name, clientToken=f"mladas-{uuid.uuid4().hex}", tags=self.tags,
                description="MLADAS M04 batch evaluation (fallback for online results)",
                evaluators=[{"evaluatorId": e} for e in evaluator_ids],
                dataSourceConfig={"cloudWatchLogs": {"serviceNames": [runtime.service_name],
                                                     "logGroupNames": [runtime.log_group],
                                                     "filterConfig": {"sessionIds": sids}}})
        except (ClientError, BotoCoreError) as e:
            return {"status": "FAILED_TO_START", "error": _why(e, self.account), "rows": []}
        rec = {"id": r["batchEvaluationId"], "name": name, "status": r.get("status"), "stream": None}
        self.batches.append(rec)
        terminal = ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "STOPPED")
        last: dict[str, Any] = {}

        def probe():
            try:
                g = self.dp.get_batch_evaluation(batchEvaluationId=rec["id"])
            except (ClientError, BotoCoreError):
                return None
            last.update(g)
            rec["status"] = g["status"]
            return g if g["status"] in terminal else None
        if quiet:
            while probe() is None and time.time() - t0 < max_wait:
                time.sleep(poll)
        else:
            mc.wait_until(probe, max_wait=max_wait, poll=poll, label="the batch evaluation",
                          describe=lambda _v: rec["status"] or "…")
        out_cfg = ((last.get("outputConfig") or {}).get("cloudWatchConfig") or {})
        rec["stream"] = out_cfg.get("logStreamName")
        rows = []
        if rec["status"] in ("COMPLETED", "COMPLETED_WITH_ERRORS") and rec["stream"]:
            rows = self._batch_rows(rec["stream"], out_cfg.get("logGroupName") or BATCH_RESULTS_LOG_GROUP)
        summaries = mask_deep((last.get("evaluationResults") or {}).get("evaluatorSummaries", []), self.account)
        return {"id": rec["id"], "status": rec["status"], "seconds": round(time.time() - t0, 1), "rows": rows,
                "summaries": summaries, "sessions": sids,
                "error": mc.short(_mask(json.dumps(last.get("errorDetails"), default=str), self.account), 400)
                if last.get("errorDetails") else None}

    def _batch_rows(self, stream: str, group: str) -> list[dict]:
        raw, kw = [], {"logGroupName": group, "logStreamNames": [stream]}
        try:
            while True:
                resp = self.logs.filter_log_events(**kw)
                for ev in resp.get("events", []):
                    try:
                        d = json.loads(ev["message"])
                    except ValueError:
                        continue
                    a = d.get("attributes", {})
                    raw.append({"session_id": a.get("session.id"), "trace_id": a.get("gen_ai.response.id"),
                                "error_type": a.get("error.type"),
                                "evaluator": a.get("gen_ai.evaluation.name"), "label": a.get("gen_ai.evaluation.score.label"),
                                "value": a.get("gen_ai.evaluation.score.value"),
                                "explanation": a.get("gen_ai.evaluation.explanation"),
                                "level": a.get("aws.bedrock_agentcore.evaluation_level")})
                if not resp.get("nextToken"):
                    break
                kw["nextToken"] = resp["nextToken"]
        except (ClientError, BotoCoreError):
            pass
        return self._result_rows(raw, "batch")

    # --------------------------------------------------------------------------------------------- cleanup
    def teardown(self) -> dict[str, str]:
        """Config first (it locks its evaluators) -> its results log group (not deleted with the config) -> batch jobs
        and their streams -> the execution role -> the aws/spans index policy (reverted only when this run added it and
        no other online config exists). -> by-id verification."""
        out: dict[str, str] = {}
        if not self.config_id:
            self.adopt()
        if self.config_id:
            try:
                self.ctl.delete_online_evaluation_config(onlineEvaluationConfigId=self.config_id)
            except ClientError as e:
                if _code(e) not in _NOT_FOUND:
                    out[f"online evaluation config {self.config_id}"] = f"NOT CLEANED: {_why(e, self.account)}"
            for _ in range(30):
                if _is_gone(lambda: self.ctl.get_online_evaluation_config(onlineEvaluationConfigId=self.config_id))[0]:
                    break
                time.sleep(2)
        if self.results_log_group:
            try:
                self.logs.delete_log_group(logGroupName=self.results_log_group)
            except ClientError as e:
                if _code(e) not in _NOT_FOUND:
                    out[f"log group {self.results_log_group}"] = f"NOT CLEANED: {_why(e, self.account)}"
        for b in self.batches:
            try:
                self.dp.delete_batch_evaluation(batchEvaluationId=b["id"])
            except ClientError as e:
                if _code(e) not in _NOT_FOUND and "progress" not in str(e).lower():
                    out[f"batch evaluation {b['id']}"] = f"NOT CLEANED: {_why(e, self.account)}"
        out.update(self._clean_batch_streams())
        try:
            for p in self.iam.list_role_policies(RoleName=self.role_name)["PolicyNames"]:
                self.iam.delete_role_policy(RoleName=self.role_name, PolicyName=p)
            self.iam.delete_role(RoleName=self.role_name)
        except ClientError as e:
            if _code(e) not in _NOT_FOUND:
                out[f"IAM role {self.role_name}"] = f"NOT CLEANED: {_why(e, self.account)}"
        out.update(self.revert_index_policy())
        for k, v in self.verify_gone().items():
            out.setdefault(k, v)
        return out

    def _clean_batch_streams(self) -> dict[str, str]:
        prefix = f"run-{self.names['batch_prefix']}"
        try:
            groups = self.logs.describe_log_groups(logGroupNamePrefix=BATCH_RESULTS_LOG_GROUP)["logGroups"]
            if not any(g["logGroupName"] == BATCH_RESULTS_LOG_GROUP for g in groups):
                return {}
            streams = [s["logStreamName"] for p in self.logs.get_paginator("describe_log_streams").paginate(
                logGroupName=BATCH_RESULTS_LOG_GROUP) for s in p["logStreams"]]
            mine = [s for s in streams if s.startswith(prefix)]
            if not mine:
                return {}
            if self.batch_group_existed is False and set(streams) == set(mine):
                self.logs.delete_log_group(logGroupName=BATCH_RESULTS_LOG_GROUP)     # this run created it
                return {f"log group {BATCH_RESULTS_LOG_GROUP} (created by this run)": GONE}
            for s in mine:
                self.logs.delete_log_stream(logGroupName=BATCH_RESULTS_LOG_GROUP, logStreamName=s)
            return {f"batch result streams ({len(mine)}) in the shared batch log group": GONE}
        except (ClientError, BotoCoreError) as e:
            return {"batch result streams": f"NOT CLEANED: {_why(e, self.account)}"}

    def revert_index_policy(self) -> dict[str, str]:
        """Undo CreateOnlineEvaluationConfig's side effect on the shared aws/spans log group — only when the policy was
        absent before this run, is exactly the service's, and no online evaluation config exists any more."""
        label = "aws/spans index policy (added by CreateOnlineEvaluationConfig)"
        if self.index_policy_before is None or self.index_policy_before:
            return {label: "left as found" if self.index_policy_before else "not touched (state before unknown)"}
        try:
            others = self.ctl.list_online_evaluation_configs(maxResults=50).get("onlineEvaluationConfigs", [])
            pol = self.logs.describe_index_policies(logGroupIdentifiers=[SPANS_LOG_GROUP]).get("indexPolicies", [])
            if not pol:
                return {label: "none ✓"}
            if others:
                return {label: f"left: {len(others)} other online config(s) in the account use it"}
            if pol[0].get("policyDocument", "").replace(" ", "") != SERVICE_INDEX_POLICY:
                return {label: "left: not the service's policy"}
            self.logs.delete_index_policy(logGroupIdentifier=SPANS_LOG_GROUP)
            return {label: "reverted ✓"}
        except (ClientError, BotoCoreError) as e:
            return {label: f"left ({_code(e)})"}

    def verify_gone(self) -> dict[str, str]:
        out = {}
        if self.config_id:
            out.update([_verify(f"online evaluation config {self.config_id}",
                                lambda: self.ctl.get_online_evaluation_config(onlineEvaluationConfigId=self.config_id))])
        if self.results_log_group:
            g = self.results_log_group
            left = [x for x in self.logs.describe_log_groups(logGroupNamePrefix=g)["logGroups"] if x["logGroupName"] == g]
            out[f"log group {g}"] = GONE if not left else "NOT CLEANED: still exists"
        for b in self.batches:
            ok, detail = _is_gone(lambda i=b["id"]: self.dp.get_batch_evaluation(batchEvaluationId=i))
            out[f"batch evaluation {b['id']}"] = GONE if ok else f"NOT CLEANED: still exists ({detail})"
        out.update([_verify(f"IAM role {self.role_name}", lambda: self.iam.get_role(RoleName=self.role_name))])
        return {_mask(k, self.account): _mask(v, self.account) for k, v in out.items()}


# ======================================================================================================================
# 6. Cost of a run (everything that is not already a ledger row)
# ======================================================================================================================
SPAN_BYTES_CW = 2200                  # average span size in aws/spans (research runtime-traces F26: 2.2 KB)
RUNTIME_SESSION_MB = 0.2              # spans + otel-rt-logs + stdout per 3-request Runtime session (F36)
EVAL_JUDGE_TOKENS_EST = (1100, 150)   # judge tokens per online/batch evaluation (not reported there): research F20 ranges


def service_cost_rows(*, runtime: RuntimeObsDeployment | None = None, evaluators: EvaluatorSet | None = None,
                      online: OnlineEvaluation | None = None, alarms: AlarmSet | None = None,
                      loop_metrics: Iterable[LoopMetrics] | None = None, local: LocalTelemetry | None = None,
                      since: float | None = None, now: float | None = None) -> list[dict]:
    """Rows for the wrap-up's cost line: {service, units, unit_price, usd, note}. Book each with
    mc.LEDGER.add_service(service, units, unit_price, note=note). On-demand evaluations are NOT here (evaluate() booked
    them); the Runtime's Nova tokens are ledger rows already (invoke(book=True)). Estimates are labelled."""
    now = now or time.time()
    p = mc.AGENTCORE_PRICES
    rows: list[dict] = []

    def add(service, units, price, note):
        if units:
            rows.append({"service": service, "units": round(units, 6), "unit_price": price,
                         "usd": round(units * price, 6), "note": note})
    if runtime is not None and runtime.sessions:
        est = runtime.cost_estimate(now)
        add("runtime_vcpu_hour", est["vcpu_hours"], p["runtime_vcpu_hour"],
            f"estimate: {est['sessions']} sessions, {est['session_minutes']} session-min (research rates)")
        add("runtime_gb_hour", est["gb_hours"], p["runtime_gb_hour"], "estimate: ~1.1 GB resident per live session")
        add("cloudwatch_ingest_gb", est["sessions"] * RUNTIME_SESSION_MB / 1024, p["cloudwatch_ingest_gb"],
            "estimate: Runtime spans + content records + stdout, ~0.2 MB per session")
    if online is not None and online.results_log_group:
        n = online.result_count(since=since)
        add("evaluation_custom", n, p["evaluation_custom"], f"{n} online evaluation results (fee)")
        if n:
            j_in, j_out = EVAL_JUDGE_TOKENS_EST
            pr = mc.PRICES_PER_1M.get(online.judge_model, (0, 0, 0))
            add("online_judge_tokens_est", n, (j_in * pr[0] + j_out * pr[1]) / 1e6,
                f"estimate: Nova judge tokens ~{j_in:,} in / {j_out} out per online evaluation")
    if online is not None:
        nb = sum(len(online._batch_rows(b["stream"], BATCH_RESULTS_LOG_GROUP)) for b in online.batches if b.get("stream"))
        add("evaluation_custom", nb, p["evaluation_custom"], f"{nb} batch evaluation results (fee)")
        if nb:                                        # batch results report no tokens either (evaluations F10, F40)
            j_in, j_out = EVAL_JUDGE_TOKENS_EST
            pr = mc.PRICES_PER_1M.get(online.judge_model, (0, 0, 0))
            add("batch_judge_tokens_est", nb, (j_in * pr[0] + j_out * pr[1]) / 1e6,
                f"estimate: Nova judge tokens ~{j_in:,} in / {j_out} out per batch evaluation")
    if alarms is not None and alarms.created_at:
        hours = max(0.0, now - alarms.created_at) / 3600
        add("cloudwatch_alarm_highres_month", 2 * hours / mc.HOURS_PER_MONTH, p["cloudwatch_alarm_highres_month"],
            f"2 high-resolution alarms x {hours * 60:.0f} min (prorated)")
        add("cloudwatch_custom_metric_month", 2 * hours / mc.HOURS_PER_MONTH, p["cloudwatch_custom_metric_month"],
            "2 custom metrics (prorated by the hour; they age out, cannot be deleted)")
    puts = sum(m.puts for m in (LoopMetrics.instances if loop_metrics is None else loop_metrics))
    add("cloudwatch_api_request", puts, p["cloudwatch_api_request"], f"{puts} PutMetricData requests")
    if local is not None and local.xray_exports:
        spans = sum(1 for _ in local.readable_spans())
        add("cloudwatch_ingest_gb", spans * SPAN_BYTES_CW / 1024 ** 3, p["cloudwatch_ingest_gb"],
            f"estimate: ~{spans} local spans x 2.2 KB into aws/spans")
    if INSIGHTS_STATS["bytes_scanned"]:
        add("cloudwatch_insights_scan_gb", INSIGHTS_STATS["bytes_scanned"] / 1024 ** 3, p["cloudwatch_insights_scan_gb"],
            f"{INSIGHTS_STATS['queries']} Logs Insights queries")
    return rows


# ======================================================================================================================
# 7. Find / delete everything one run created (teardown_m04.py; the notebook uses the handles' own teardown)
# ======================================================================================================================
def find_run_resources(session, run_id: str) -> dict[str, list]:
    """Everything that carries this run's names (read-only)."""
    n = resource_names(run_id)
    ctl, dp, logs, iam, s3, cw = (session.client(c) for c in ("bedrock-agentcore-control", "bedrock-agentcore", "logs",
                                                               "iam", "s3", "cloudwatch"))
    out: dict[str, list] = {}
    rts, token = [], None
    while True:
        page = ctl.list_agent_runtimes(maxResults=100, **({"nextToken": token} if token else {}))
        rts += [r for r in page.get("agentRuntimes", []) if r["agentRuntimeName"] == n["runtime"]]
        token = page.get("nextToken")
        if not token:
            break
    out["runtimes"] = [{"name": r["agentRuntimeName"], "id": r["agentRuntimeId"], "status": r.get("status")} for r in rts]
    out["runtime_log_groups"] = [g["logGroupName"] for p in logs.get_paginator("describe_log_groups").paginate(
        logGroupNamePrefix=n["runtime_log_group_prefix"]) for g in p["logGroups"]]
    evs, token = [], None
    while True:
        page = ctl.list_evaluators(maxResults=100, **({"nextToken": token} if token else {}))
        evs += [e for e in page.get("evaluators", []) if str(e.get("evaluatorName", "")).startswith(n["evaluator_prefix"])]
        token = page.get("nextToken")
        if not token:
            break
    out["evaluators"] = [{"name": e["evaluatorName"], "id": e["evaluatorId"], "status": e.get("status")} for e in evs]
    cfgs, token = [], None
    while True:
        page = ctl.list_online_evaluation_configs(maxResults=50, **({"nextToken": token} if token else {}))
        cfgs += [c for c in page.get("onlineEvaluationConfigs", []) if c.get("onlineEvaluationConfigName") == n["online_config"]]
        token = page.get("nextToken")
        if not token:
            break
    out["online_configs"] = [{"name": c["onlineEvaluationConfigName"], "id": c["onlineEvaluationConfigId"],
                              "status": c.get("status")} for c in cfgs]
    out["results_log_groups"] = [g["logGroupName"] for p in logs.get_paginator("describe_log_groups").paginate(
        logGroupNamePrefix=n["results_log_group_prefix"]) for g in p["logGroups"]]
    bes, token = [], None
    while True:
        page = dp.list_batch_evaluations(maxResults=50, **({"nextToken": token} if token else {}))
        bes += [b for b in page.get("batchEvaluations", []) if str(b.get("batchEvaluationName", "")).startswith(n["batch_prefix"])]
        token = page.get("nextToken")
        if not token:
            break
    out["batch_evaluations"] = [{"name": b["batchEvaluationName"], "id": b["batchEvaluationId"], "status": b.get("status")}
                                for b in bes]
    try:
        out["batch_streams"] = [s["logStreamName"] for p in logs.get_paginator("describe_log_streams").paginate(
            logGroupName=BATCH_RESULTS_LOG_GROUP, logStreamNamePrefix=f"run-{n['batch_prefix']}") for s in p["logStreams"]]
    except ClientError:
        out["batch_streams"] = []
    out["alarms"] = [a["AlarmName"] for p in cw.get_paginator("describe_alarms").paginate(AlarmNamePrefix=n["alarm_prefix"])
                     for a in p["MetricAlarms"] + p.get("CompositeAlarms", [])]
    roles = []
    for r in (n["runtime_role"], n["online_role"]):
        if not _is_gone(lambda r=r: iam.get_role(RoleName=r))[0]:
            roles.append(r)
    out["iam_roles"] = roles
    out["buckets"] = [] if _is_gone(lambda: s3.head_bucket(Bucket=n["bucket"]))[0] else [n["bucket"]]
    return out


def delete_run_resources(session, run_id: str) -> dict[str, str]:
    """Delete everything find_run_resources lists, in dependency order (online config -> evaluators -> batch jobs ->
    result log groups/streams -> alarms -> runtime + its log groups/role/bucket -> online role). -> {resource: status}."""
    out: dict[str, str] = {}
    found = find_run_resources(session, run_id)
    online = OnlineEvaluation(session, run_id)
    if found["online_configs"]:
        online.index_policy_before = []          # revert_index_policy still requires: no config left + the service's doc
    for c in found["online_configs"]:
        online.config_id = c["id"]
        try:
            online._load(c["id"])
        except (ClientError, BotoCoreError):
            online.results_log_group = f"{RESULTS_LOG_GROUP_ROOT}{c['id']}"
    online.batches = [{"id": b["id"], "stream": None} for b in found["batch_evaluations"]]
    online.batch_group_existed = True                       # a script never deletes the shared batch group itself
    try:
        cfg_out = online.teardown()
    except (ClientError, BotoCoreError) as e:
        cfg_out = {"online evaluation": f"NOT CLEANED: {_why(e)}"}
    for g in found["results_log_groups"]:
        try:
            session.client("logs").delete_log_group(logGroupName=g)
        except ClientError as e:
            if _code(e) not in _NOT_FOUND:
                cfg_out[f"log group {g}"] = f"NOT CLEANED: {_why(e)}"
    out.update(cfg_out)
    ev = EvaluatorSet(session, run_id)
    ev.ids = {e["name"]: e["id"] for e in found["evaluators"]}
    out.update(ev.teardown() if ev.ids else {})
    alarms = AlarmSet(session, run_id)
    if found["alarms"]:
        extra = [a for a in found["alarms"] if a not in alarms.names.values()]
        try:
            session.client("cloudwatch").delete_alarms(AlarmNames=found["alarms"])
        except (ClientError, BotoCoreError) as e:
            out["alarms"] = f"NOT CLEANED: {_why(e)}"
        out.update(alarms.verify_gone())
        for a in extra:
            out[f"CloudWatch alarm {a}"] = GONE if not session.client("cloudwatch").describe_alarms(
                AlarmNames=[a])["MetricAlarms"] else "NOT CLEANED: still exists"
    if found["runtimes"] or found["runtime_log_groups"] or found["buckets"] or \
            resource_names(run_id)["runtime_role"] in found["iam_roles"]:
        rt = RuntimeObsDeployment(run_id, session, HERE)
        rt.adopt()
        out.update(rt.teardown(wait=True))
    return out

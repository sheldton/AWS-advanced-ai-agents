"""
agentcore_audit — turn every tool call of a Strands agent into audit evidence (MLADAS M03 §8).

    import agentcore_audit as at
    GROUP = at.resource_names(RUN_ID)["audit_log_group"]                       # /mladas/m03/<suffix>/audit
    at.ensure_audit_log_group(GROUP, SESSION, tags=TAGS)                       # §0: ~5-8 s, returns once masking is on
    audit = at.AuditLogger(RUN_ID, SESSION, GROUP)                             # a Strands HookProvider
    agent = mc.make_agent("bank_audited", PROMPT, tools=TOOLS, hooks=[audit, mc.RefundPolicyGate(500)])
    agent(text, invocation_state={"caller": {...verified JWT claims...}, "correlation_id": cid, "session_id": sid})
    audit.flush()                                                              # also automatic after every invocation
    rows = at.insights(GROUP, at.REFUNDS_OVER_QUERY.format(min_amount=100), since=T0, session=SESSION, min_rows=1)
    events = at.cloudtrail_events([GROUP], T0 - 60, SESSION, max_wait=150, expect={"CreateLogGroup"})

Public API
  resource_names(run_id) -> {"prefix": "/mladas/m03/<suffix>/", "audit_log_group": "/mladas/m03/<suffix>/audit"}
  ensure_audit_log_group(name, session, tags=None, retention_days=1, data_protection=True, *, wait_masking=True,
                         max_wait=30) -> {name, arn, ready, created, retention_days, data_protection, policy_put,
                         masking_active, masking_after_s, request_ids, seconds, error}
      Creates (or adopts) the group + a data protection policy with the MANAGED CreditCardNumber identifier and, with
      wait_masking, returns only once a canary card number written to stream "dp-canary" comes back masked. Never raises.
  AuditLogger(run_id, session, log_group, caller=None, *, session_id=None, write_tools=(), stream=None,
              batch_size=25, auto_flush=True)
      Strands HookProvider: one record per tool call (who / what / why / result / when / ids; cancelled calls too) and
      one per invocation, kept in .records and shipped to CloudWatch Logs in chronological batches.
      .flush() -> int (never raises) · .records · .tool_records() · .errors · .shipped · .stream
  new_correlation_id() -> "req-<12 hex>"      utc_ms(t=None) -> "2026-09-27T13:12:31.790Z"
  insights(log_group, query, since, until=None, *, session, max_wait=30, min_rows=0, limit=None) -> rows
      Logs Insights with a bounded wait; rows is a list of dicts (no @ptr) with .info {status, seconds, attempts, ...}.
      min_rows re-runs the query until that many rows are visible (ingestion takes 1.2-3.6 s).
  REFUNDS_OVER_QUERY ({min_amount}) · BY_TOOL_STATUS_QUERY · DENIED_QUERY · MASKED_REQUEST_QUERY
  cloudtrail_events(resource_names, since, session, include_reads=False, max_wait=0, *, expect=(), request_ids=(),
                    poll=15, on_poll=None) -> [{when, who, event, source, resource, requestID, readOnly, unmask, error}]
      CloudTrail Event history (management events only, oldest first), one LookupEvents attribute per call, <= 2
      calls/s. With max_wait > 0 it polls until every `expect` event name and every `request_ids` id is seen (or,
      with neither, every resource has an event): events arrive 41-150 s after the call. Account ids in `who` are
      masked (********1234).
  created_events([(event_name, match)], since, session, *, window_s=900) -> rows   writes with no CloudTrail
      Resources entry (CreateUserPool, CreatePolicyEngine, CreateGuardrail), found by event name in a time window
  log_events(log_group, session, *, unmask=False, stream=None, filter_pattern=None, since=None, limit=20) -> rows
  unmasked_events(log_group, session, **kw) -> rows   the same with unmask=True: the read is itself logged in
      CloudTrail (FilterLogEvents, requestParameters.unmask = true; readOnly, so include_reads=True). rows.request_id
      is that call's request id.
  delete_log_group(name, session) -> {label: "gone ✓" | "pending: …" | "error: …"}   verified by exact name
  find_run_resources(session, run_id) / delete_run_resources(session, run_id)          every group under the prefix
  Constants: SCHEMA, CARD_NUMBER_IDENTIFIER, DEMO_PAN (fictional, Luhn-valid, NOT an issuer test number: those are
             never masked), CANARY_PAN, luhn_ok(number)

Verified in a test account in us-east-1, 2026-09-27 (strands-agents 1.57.1, boto3 1.43.103). Masking is decided at INGESTION
(3.2-4.4 s after PutDataProtectionPolicy on a new group; older events stay clear) and only in CloudWatch: .records
and agent.messages still hold what the user typed. PutLogEvents and gateway tools/call are CloudTrail DATA events,
so they never appear in Event history.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from strands.hooks import (AfterInvocationEvent, AfterModelCallEvent, AfterToolCallEvent, BeforeInvocationEvent,
                           BeforeToolCallEvent, HookProvider, HookRegistry)

from agentcore_gateway import run_suffix

SCHEMA = "mladas.audit.v1"
LOG_ROOT = "/mladas/m03"
CARD_NUMBER_IDENTIFIER = "arn:aws:dataprotection::aws:data-identifier/CreditCardNumber"
DEMO_PAN = "4929 0900 0000 4417"       # Sofia's card, fictional (Visa prefix, Luhn-valid); masked 2/2 in research
CANARY_PAN = "5412 0200 0000 7731"     # fictional (Mastercard prefix, Luhn-valid): only the masking probe writes it
CANARY_STREAM = "dp-canary"
_CLIENT_CONFIG = Config(retries={"total_max_attempts": 5, "mode": "standard"}, read_timeout=60)
_ACCOUNT_ID = re.compile(r"(?<!\d)\d{8}(\d{4})(?!\d)")
_SECRET_KEY = re.compile(r"token|secret|password|authorization|assertion|api_?key", re.I)
_POLICY_DENY = re.compile(r"Tool Execution Denied|not allowed due to policy", re.I)
_WITHHELD = "[Tool output withheld"


def utc_ms(t: float | None = None) -> str:
    """UTC ISO-8601 with milliseconds: 2026-09-27T13:12:31.790Z"""
    t = time.time() if t is None else t
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_correlation_id() -> str:
    return f"req-{uuid.uuid4().hex[:12]}"


def luhn_ok(number: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", number)][::-1]
    return bool(d) and sum(x if i % 2 == 0 else (x * 2 - 9 if x * 2 > 9 else x * 2) for i, x in enumerate(d)) % 10 == 0


def _mask_ids(value: Any) -> Any:
    return _ACCOUNT_ID.sub(lambda m: "********" + m.group(1), value) if isinstance(value, str) else value


def _code(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "")


def _epoch_s(t: float | dt.datetime | None) -> float | None:
    if t is None:
        return None
    return t.timestamp() if isinstance(t, dt.datetime) else float(t)


def resource_names(run_id: str) -> dict[str, str]:
    """Log group names for this run: everything lives under /mladas/m03/<run suffix>/ (teardown finds it there)."""
    prefix = f"{LOG_ROOT}/{run_suffix(run_id)}/"
    return {"prefix": prefix, "audit_log_group": prefix + "audit"}


# ======================================================================================
# The audit log group (+ card-number masking)
# ======================================================================================
def _policy_document() -> dict:
    return {"Name": "mask-card-numbers", "Version": "2021-06-01", "Statement": [
        {"Sid": "audit", "DataIdentifier": [CARD_NUMBER_IDENTIFIER], "Operation": {"Audit": {"FindingsDestination": {}}}},
        {"Sid": "redact", "DataIdentifier": [CARD_NUMBER_IDENTIFIER], "Operation": {"Deidentify": {"MaskConfig": {}}}}]}


def _same_policy(existing: str | None) -> bool:
    if not existing:
        return False
    try:
        doc = json.loads(existing)
    except (TypeError, ValueError):
        return False
    return [s.get("DataIdentifier") for s in doc.get("Statement", [])] == \
        [s["DataIdentifier"] for s in _policy_document()["Statement"]]


def _wait_masking(logs, name: str, max_wait: float) -> tuple[bool, float | None]:
    """Write a canary card number every second to stream dp-canary and read it back (no unmask) until one comes
    back masked. -> (active, seconds from the probe start to the write of the first masked canary)."""
    try:
        logs.create_log_stream(logGroupName=name, logStreamName=CANARY_STREAM)
    except ClientError as e:
        if _code(e) != "ResourceAlreadyExistsException":
            raise
    digits = re.sub(r"\D", "", CANARY_PAN)
    t0, written, n = time.time(), {}, 0
    while True:
        n += 1
        now = time.time()
        logs.put_log_events(logGroupName=name, logStreamName=CANARY_STREAM, logEvents=[{
            "timestamp": int(now * 1000),
            "message": json.dumps({"event": "dp_canary", "probe": n, "card": CANARY_PAN, "at": utc_ms(now)})}])
        written[n] = now
        time.sleep(1.0)
        events = logs.get_log_events(logGroupName=name, logStreamName=CANARY_STREAM, startTime=int(t0 * 1000),
                                     startFromHead=True, limit=100)["events"]
        for ev in events:
            msg = ev["message"]
            if '"dp_canary"' in msg and digits not in re.sub(r"\D", "", msg) and "*" in msg:
                m = re.search(r'"probe": (\d+)', msg)
                t_write = written.get(int(m.group(1)), now) if m else now
                return True, round(t_write - t0, 1)
        if time.time() - t0 > max_wait:
            return False, None


def ensure_audit_log_group(name: str, session, tags: dict | None = None, retention_days: int = 1,
                           data_protection: bool = True, *, wait_masking: bool = True, max_wait: float = 30) -> dict:
    """Create (or adopt) the audit log group: tags, retention, and a data protection policy that masks card numbers
    with the MANAGED CreditCardNumber identifier only (a broad custom regex also masked 19-digit timeUnixNano values
    and broke the JSON, 6/6). Masking is decided at ingestion, so with wait_masking=True this returns only once a
    canary card number written after the policy comes back masked (3.2-4.4 s on a new group). Never raises.
    -> {name, arn, ready, created, retention_days, data_protection, policy_put, masking_active, masking_after_s,
        request_ids {CreateLogGroup, PutDataProtectionPolicy}, seconds, error}"""
    t0 = time.time()
    out: dict[str, Any] = {"name": name, "arn": None, "ready": False, "created": False, "retention_days": None,
                           "data_protection": None, "policy_put": False, "masking_active": None,
                           "masking_after_s": None, "request_ids": {}, "seconds": None, "error": None}
    try:
        logs = session.client("logs", config=_CLIENT_CONFIG)
        try:
            r = logs.create_log_group(logGroupName=name, tags={k: str(v) for k, v in (tags or {}).items()})
            out["created"] = True
            out["request_ids"]["CreateLogGroup"] = r["ResponseMetadata"]["RequestId"]
        except ClientError as e:
            if _code(e) != "ResourceAlreadyExistsException":
                raise
        logs.put_retention_policy(logGroupName=name, retentionInDays=retention_days)
        if data_protection:
            try:
                existing = logs.get_data_protection_policy(logGroupIdentifier=name).get("policyDocument")
            except ClientError as e:
                if _code(e) != "ResourceNotFoundException":
                    raise
                existing = None
            if not _same_policy(existing):
                r = logs.put_data_protection_policy(logGroupIdentifier=name, policyDocument=json.dumps(_policy_document()))
                out["policy_put"] = True
                out["request_ids"]["PutDataProtectionPolicy"] = r["ResponseMetadata"]["RequestId"]
            if wait_masking:
                out["masking_active"], out["masking_after_s"] = _wait_masking(logs, name, max_wait)
        g = next((g for g in logs.describe_log_groups(logGroupNamePrefix=name)["logGroups"]
                  if g["logGroupName"] == name), {})
        out.update(arn=(g.get("arn") or "").removesuffix(":*") or None, retention_days=g.get("retentionInDays"),
                   data_protection=g.get("dataProtectionStatus"))
        out["ready"] = bool(g) and (not data_protection or not wait_masking or bool(out["masking_active"]))
        if data_protection and wait_masking and not out["masking_active"]:
            out["error"] = f"card-number masking not observed within {max_wait:.0f} s"
    except (ClientError, BotoCoreError) as e:
        out["error"] = f"{type(e).__name__}: {e}"
    out["seconds"] = round(time.time() - t0, 1)
    return out


# ======================================================================================
# The hook: one audit record per tool call
# ======================================================================================
def _result_text(tool_result: dict | None) -> str:
    parts = []
    for b in (tool_result or {}).get("content", []):
        if "text" in b:
            parts.append(b["text"])
        elif "json" in b:
            parts.append(json.dumps(b["json"], default=str))
    return " ".join("".join(parts).split())


def _short(s: str | None, n: int = 200) -> str | None:
    if s is None:
        return None
    return s if len(s) <= n else s[: n - 1] + "…"


class AuditLogger(HookProvider):
    """Strands hook: one structured record per tool call (and one per invocation), kept locally in .records and
    shipped to CloudWatch Logs in chronological batches (after every invocation, every `batch_size` records, and on
    .flush()). The hook never raises: CloudWatch failures go to .errors and the records stay local.

    Record (schema mladas.audit.v1, event "tool_call"):
      who    the caller's verified claims from invocation_state["caller"] (else the ctor's `caller`) — sub,
             client_id, scope, customer_id, ... (keys that look like tokens/secrets are dropped) — + agent + model
      what   tool, input (exactly as the model wrote it), write (tool in write_tools)
      why    justification (the input's "justification" argument, if any), assistant_text / reasoning written before
             the call (Nova 2 Lite writes none: 0/50), user_request. Model-authored claims, not proof.
      result status "success" | "error" | "denied" (cancel_tool: denied_by "hook"; a Gateway Policy deny: denied_by
             "policy") | "withheld" (a tool-result screen replaced it), denied_reason, exception, preview
      when   requested / completed (UTC ISO ms), duration_ms
      ids    run_id, session_id (invocation_state["session_id"] or the ctor's), correlation_id, invocation_seq,
             tool_use_id
    Put AuditLogger FIRST in hooks=[...]: After* callbacks run in reverse order, so it then records what the model
    actually read (after a ToolResultGuard / PanMasker changed the result)."""

    def __init__(self, run_id: str, session, log_group: str | None, caller: dict | None = None, *,
                 session_id: str | None = None, write_tools: Iterable[str] = (), stream: str | None = None,
                 batch_size: int = 25, auto_flush: bool = True, local_path: str | Path | None = None):
        self.run_id, self.log_group, self.caller = run_id, log_group, dict(caller or {})
        self.session_id = session_id
        self.write_tools = set(write_tools)
        self.stream = stream or f"audit-{run_suffix(run_id)}-{uuid.uuid4().hex[:6]}"
        self.batch_size, self.auto_flush = batch_size, auto_flush
        self.local_path = Path(local_path) if local_path else None
        self.records: list[dict] = []
        self.errors: list[str] = []
        self.shipped = 0
        self._buf: list[dict] = []
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._logs = session.client("logs", config=_CLIENT_CONFIG) if (session is not None and log_group) else None
        self._stream_ready = False
        self._seq = 0
        self._inv: dict[int, dict] = {}
        self._why: dict[str, dict] = {}
        self._t0: dict[str, float] = {}

    # ------------------------------------------------------------------ registration
    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self._before_invocation)
        registry.add_callback(AfterModelCallEvent, self._after_model)
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)
        registry.add_callback(AfterInvocationEvent, self._after_invocation)

    # ------------------------------------------------------------------ views
    def tool_records(self) -> list[dict]:
        return [r for r in self.records if r.get("event") == "tool_call"]

    # ------------------------------------------------------------------ shipping
    def _emit(self, rec: dict, t: float) -> None:
        with self._lock:
            self.records.append(rec)
            self._buf.append({"timestamp": int(t * 1000), "message": json.dumps(rec, default=str)})
            full = len(self._buf) >= self.batch_size
        if self.local_path:
            with open(self.local_path, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        if full:
            self.flush()

    def flush(self) -> int:
        """Ship buffered records: one PutLogEvents per <= 500 events, sorted by timestamp (a batch out of order is
        rejected). Returns how many were shipped. Never raises: failures stay buffered and go to .errors."""
        with self._flush_lock:
            with self._lock:
                batch, self._buf = sorted(self._buf, key=lambda e: e["timestamp"]), []
            if not batch or self._logs is None:
                return 0
            try:
                if not self._stream_ready:
                    try:
                        self._logs.create_log_stream(logGroupName=self.log_group, logStreamName=self.stream)
                    except ClientError as e:
                        if _code(e) != "ResourceAlreadyExistsException":
                            raise
                    self._stream_ready = True
                for i in range(0, len(batch), 500):
                    r = self._logs.put_log_events(logGroupName=self.log_group, logStreamName=self.stream,
                                                  logEvents=batch[i:i + 500])
                    if r.get("rejectedLogEventsInfo"):
                        self.errors.append(f"rejected: {r['rejectedLogEventsInfo']}")
                    self.shipped += len(batch[i:i + 500])
                return len(batch)
            except (ClientError, BotoCoreError) as e:
                self.errors.append(f"{type(e).__name__}: {e}")
                with self._lock:                          # keep them for the next flush
                    self._buf = batch + self._buf
                return 0

    # ------------------------------------------------------------------ callbacks
    def _who(self, state: dict, agent: Any) -> dict:
        claims = dict(state.get("caller") or self.caller or {})
        who = {k: v for k, v in claims.items() if not _SECRET_KEY.search(str(k))}
        for k in ("sub", "client_id", "scope", "customer_id"):
            who.setdefault(k, None)
        who["agent"] = getattr(agent, "name", None)
        who["model"] = (getattr(getattr(agent, "model", None), "config", None) or {}).get("model_id")
        return who

    def _session_id(self, state: dict, agent: Any) -> str:
        return state.get("session_id") or self.session_id or f"{self.run_id}-{getattr(agent, 'name', 'agent')}"

    def _before_invocation(self, event: BeforeInvocationEvent) -> None:
        with self._lock:
            self._seq += 1
            seq = self._seq
        text = " ".join(b.get("text", "") for m in (event.messages or []) if m.get("role") == "user"
                        for b in m.get("content", []) if isinstance(b, dict))
        self._inv[id(event.agent)] = {"seq": seq, "t0": time.time(), "user_request": _short(text, 300)}

    def _after_model(self, event: AfterModelCallEvent) -> None:
        if not event.stop_response:
            return
        before, reasoning = [], None
        for block in event.stop_response.message.get("content", []):
            if "reasoningContent" in block:
                reasoning = (block["reasoningContent"].get("reasoningText") or {}).get("text") or "<redacted>"
            elif "text" in block:
                before.append(block["text"])
            elif "toolUse" in block:
                self._why[block["toolUse"]["toolUseId"]] = {
                    "assistant_text": _short(" ".join(" ".join(before).split()), 400) or None, "reasoning": reasoning}

    def _before_tool(self, event: BeforeToolCallEvent) -> None:
        self._t0[event.tool_use["toolUseId"]] = time.time()

    def _after_tool(self, event: AfterToolCallEvent) -> None:
        try:
            tu, state = event.tool_use, event.invocation_state or {}
            t1 = time.time()
            t0 = self._t0.pop(tu["toolUseId"], t1)
            inv = self._inv.get(id(event.agent), {})
            why = self._why.pop(tu["toolUseId"], {})
            args = dict(tu.get("input") or {})
            result = event.result if isinstance(event.result, dict) else {}
            text = _result_text(result)
            denied_by, reason = None, None
            if event.cancel_message:
                status, denied_by, reason = "denied", "hook", str(event.cancel_message)
            elif event.exception is not None:
                status = "error"
            elif result.get("status") == "error" and _POLICY_DENY.search(text):
                status, denied_by, reason = "denied", "policy", _short(text, 300)
            elif result.get("status") == "error" and text.startswith(_WITHHELD):
                status, denied_by, reason = "withheld", "tool-result screen", _short(text, 300)
            elif result.get("status") == "error":
                status = "error"
            else:
                status = "success"
            self._emit({
                "schema": SCHEMA, "event": "tool_call", "run_id": self.run_id,
                "session_id": self._session_id(state, event.agent), "correlation_id": state.get("correlation_id"),
                "invocation_seq": inv.get("seq"), "tool_use_id": tu["toolUseId"],
                "when": {"requested": utc_ms(t0), "completed": utc_ms(t1), "duration_ms": round((t1 - t0) * 1000, 1)},
                "who": self._who(state, event.agent),
                "what": {"tool": tu.get("name"), "input": args, "write": tu.get("name") in self.write_tools},
                "why": {"justification": args.get("justification"), "assistant_text": why.get("assistant_text"),
                        "reasoning": why.get("reasoning"), "user_request": inv.get("user_request")},
                "result": {"status": status, "denied_by": denied_by, "denied_reason": reason,
                           "exception": repr(event.exception) if event.exception else None,
                           "preview": _short(text, 200)}}, t1)
        except Exception as e:  # noqa: BLE001 — an audit hook must never break the agent
            self.errors.append(f"after_tool: {type(e).__name__}: {e}")

    def _after_invocation(self, event: AfterInvocationEvent) -> None:
        try:
            inv = self._inv.pop(id(event.agent), {})
            m = event.agent.event_loop_metrics.latest_agent_invocation
            usage = dict(m.usage) if m else {}
            state = event.invocation_state or {}
            t1 = time.time()
            self._emit({
                "schema": SCHEMA, "event": "invocation", "run_id": self.run_id,
                "session_id": self._session_id(state, event.agent), "correlation_id": state.get("correlation_id"),
                "invocation_seq": inv.get("seq"),
                "when": {"requested": utc_ms(inv.get("t0")), "completed": utc_ms(t1)},
                "who": self._who(state, event.agent),
                "what": {"user_request": inv.get("user_request"),
                         "stop_reason": getattr(event.result, "stop_reason", None) if event.result else None,
                         "model_calls": len(m.cycles) if m else None,
                         "input_tokens": usage.get("inputTokens"), "output_tokens": usage.get("outputTokens")}}, t1)
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"after_invocation: {type(e).__name__}: {e}")
        if self.auto_flush:
            self.flush()


# ======================================================================================
# Logs Insights (the auditor's question)
# ======================================================================================
REFUNDS_OVER_QUERY = (
    "fields when.requested as requested, session_id, who.sub as sub, who.client_id as client,"
    " who.customer_id as customer, what.tool as tool, what.input.txn_id as txn, what.input.amount as amount,"
    " result.status as status, why.justification as justification\n"
    "| filter event = \"tool_call\" and what.tool like /refund/ and what.input.amount > {min_amount}\n"
    "| sort requested asc")
BY_TOOL_STATUS_QUERY = ('filter event = "tool_call" | stats count(*) as calls by what.tool as tool, '
                        'result.status as status | sort tool, status')
DENIED_QUERY = ('fields when.requested as requested, who.sub as sub, who.customer_id as customer, what.tool as tool, '
                'result.denied_by as denied_by, result.denied_reason as reason\n'
                '| filter event = "tool_call" and result.status = "denied"\n| sort requested asc')
MASKED_REQUEST_QUERY = ('fields when.requested as requested, what.tool as tool, why.user_request as user_request\n'
                        '| filter event = "tool_call" and why.user_request like /card/\n| sort requested asc | limit 5')


class QueryRows(list):
    """A list of result rows (dicts) that also carries .info (and .request_id for log reads)."""
    info: dict
    request_id: str | None = None


def insights(log_group: str | list[str], query: str, since: float | dt.datetime, until: float | dt.datetime | None = None,
             *, session, max_wait: float = 30, min_rows: int = 0, limit: int | None = None) -> QueryRows:
    """Run a Logs Insights query with a bounded wait (a query takes 0.9-1.2 s). since/until: epoch seconds or
    datetimes (until defaults to now + 60 s). min_rows > 0 re-runs the query (1 s apart) until that many rows are
    visible or max_wait passes (events show up 1.2-3.6 s after PutLogEvents). `limit` overrides a `| limit N` in the
    query (the API parameter wins). Values come back as strings. Raises on a malformed query."""
    logs = session.client("logs", config=_CLIENT_CONFIG)
    groups = [log_group] if isinstance(log_group, str) else list(log_group)
    t0, attempts = time.time(), 0
    while True:
        attempts += 1
        kw: dict[str, Any] = {"logGroupNames": groups, "startTime": int(_epoch_s(since)),
                              "endTime": int(_epoch_s(until) or time.time() + 60), "queryString": query}
        if limit:
            kw["limit"] = limit
        qid = logs.start_query(**kw)["queryId"]
        while True:
            r = logs.get_query_results(queryId=qid)
            if r["status"] in ("Complete", "Failed", "Cancelled", "Timeout", "Unknown"):
                break
            if time.time() - t0 > max_wait:
                try:
                    logs.stop_query(queryId=qid)
                except ClientError:
                    pass
                break
            time.sleep(0.5)
        rows = QueryRows({f["field"]: f["value"] for f in row if f["field"] != "@ptr"} for row in r.get("results", []))
        if len(rows) >= min_rows or r["status"] != "Complete" or time.time() - t0 + 1 > max_wait:
            break
        time.sleep(1)
    stats = r.get("statistics", {})
    rows.info = {"status": r["status"], "seconds": round(time.time() - t0, 2), "attempts": attempts, "query_id": qid,
                 "records_scanned": stats.get("recordsScanned"), "bytes_scanned": stats.get("bytesScanned")}
    return rows


# ======================================================================================
# Reading raw events (masked by default; unmask=True is itself audited)
# ======================================================================================
def log_events(log_group: str, session, *, unmask: bool = False, stream: str | None = None,
               filter_pattern: str | None = None, since: float | dt.datetime | None = None, limit: int = 20) -> QueryRows:
    """FilterLogEvents on the group (masked unless unmask=True; the caller needs logs:Unmask).
    -> rows [{time, stream, message}] with .request_id (to find this read in CloudTrail) and .info."""
    logs = session.client("logs", config=_CLIENT_CONFIG)
    kw: dict[str, Any] = {"logGroupName": log_group, "limit": limit, "unmask": unmask}
    if stream:
        kw["logStreamNames"] = [stream]
    if filter_pattern:
        kw["filterPattern"] = filter_pattern
    if since is not None:
        kw["startTime"] = int(_epoch_s(since) * 1000)
    r = logs.filter_log_events(**kw)
    rows = QueryRows({"time": utc_ms(e["timestamp"] / 1000), "stream": e["logStreamName"], "message": e["message"]}
                     for e in r.get("events", []))
    rows.request_id = r["ResponseMetadata"]["RequestId"]
    rows.info = {"unmask": unmask, "events": len(rows), "request_id": rows.request_id}
    return rows


def unmasked_events(log_group: str, session, **kwargs: Any) -> QueryRows:
    """log_events(..., unmask=True): the clear text of masked events. The read is itself logged in CloudTrail
    (FilterLogEvents with requestParameters.unmask = true), so 'who unmasked' is auditable: look it up with
    cloudtrail_events([log_group], since, session, include_reads=True) and match rows.request_id."""
    return log_events(log_group, session, unmask=True, **kwargs)


# ======================================================================================
# CloudTrail Event history (who created / changed / unmasked)
# ======================================================================================
_CT_LOCK = threading.Lock()
_CT_LAST = [0.0]


def _lookup(ct, **kw) -> dict:
    """LookupEvents at <= 2 calls/s per account (shared), with backoff on throttling."""
    for attempt in range(5):
        with _CT_LOCK:
            wait = 0.55 - (time.time() - _CT_LAST[0])
            if wait > 0:
                time.sleep(wait)
            _CT_LAST[0] = time.time()
        try:
            return ct.lookup_events(**kw)
        except ClientError as e:
            if _code(e) not in ("ThrottlingException", "Throttling") or attempt == 4:
                raise
            time.sleep(1 + 2 ** attempt)
    return {"Events": []}


def _cloudtrail_once(ct, names: list[str], start: dt.datetime, include_reads: bool, max_pages: int) -> list[dict]:
    out, seen = [], set()
    for name in names:
        token, pages = None, 0
        while pages < max_pages:
            kw: dict[str, Any] = {"LookupAttributes": [{"AttributeKey": "ResourceName", "AttributeValue": name}],
                                  "StartTime": start, "MaxResults": 50}
            if token:
                kw["NextToken"] = token
            r = _lookup(ct, **kw)
            for e in r.get("Events", []):
                ce = json.loads(e["CloudTrailEvent"])
                if ce["eventID"] in seen or (ce.get("readOnly") and not include_reads):
                    continue
                seen.add(ce["eventID"])
                uid = ce.get("userIdentity", {})
                out.append({"when": ce["eventTime"], "who": _mask_ids(uid.get("arn") or uid.get("invokedBy") or uid.get("type")),
                            "event": ce["eventName"], "source": ce["eventSource"].split(".")[0], "resource": name,
                            "requestID": ce.get("requestID"), "readOnly": ce.get("readOnly"),
                            "unmask": (ce.get("requestParameters") or {}).get("unmask"),
                            "error": ce.get("errorCode")})
            pages += 1
            token = r.get("NextToken")
            if not token:
                break
    return sorted(out, key=lambda x: x["when"])


def cloudtrail_events(resource_names: Iterable[str], since: float | dt.datetime, session, include_reads: bool = False,
                      max_wait: float = 0, *, expect: Iterable[str] = (), request_ids: Iterable[str] = (),
                      poll: float = 15, max_pages: int = 4,
                      on_poll: Callable[[float, int], None] | None = None) -> list[dict]:
    """Management events CloudTrail Event history recorded against these resource names (log group names, gateway
    ids, role names, ...), oldest first: [{when, who, event, source, resource, requestID, readOnly, unmask, error}].
    Event history keeps 90 days of MANAGEMENT events only (no PutLogEvents, no gateway tools/call). Reads
    (Describe*/Get*/FilterLogEvents/StartQuery) are dropped unless include_reads. One lookup attribute per call and
    <= 2 calls/s. New events appear 41-123 s after the call (in batches): max_wait > 0 polls every `poll` s until
    every name in `expect` AND every id in `request_ids` (boto3 ResponseMetadata.RequestId of the calls you made) is
    present — or, with neither given, until every resource has an event. on_poll(elapsed_s, n_events) reports
    progress (use it to update one status line — never wait silently). Account ids in `who` are masked."""
    names = list(resource_names)
    expect, wanted = set(expect), {r for r in request_ids if r}
    ct = session.client("cloudtrail", config=_CLIENT_CONFIG)
    start = dt.datetime.fromtimestamp(_epoch_s(since), dt.timezone.utc)
    t0 = time.time()
    while True:
        rows = _cloudtrail_once(ct, names, start, include_reads, max_pages)
        if expect or wanted:
            done = expect <= {r["event"] for r in rows} and wanted <= {r["requestID"] for r in rows}
        else:
            done = all(any(r["resource"] == n for r in rows) for n in names)
        elapsed = time.time() - t0
        if on_poll:
            on_poll(round(elapsed, 1), len(rows))
        if done or elapsed + poll > max_wait:
            return rows
        time.sleep(poll)


def created_events(expected: Iterable[tuple[str, str]], since: float | dt.datetime, session, *, window_s: float = 900,
                   max_pages: int = 3) -> list[dict]:
    """Writes that carry NO CloudTrail `Resources` entry, which a ResourceName lookup (cloudtrail_events) misses:
    observed in us-east-1 on 2026-09-27 for CreateUserPool, CreatePolicyEngine and CreateGuardrail (CreateLogGroup,
    CreateGateway and UpdateGateway are indexed). expected = [(event_name, match)]: each pair is looked up by EVENT
    NAME inside [since, since + window_s] and matched on `match` (a resource name or id) anywhere in the event JSON;
    failed attempts (errorCode, e.g. an adopt re-run's AlreadyExists) are skipped. <= 2 lookups/s (shared lock).
    -> one row per pair, the oldest match: [{event, match, found, when, who, requestID}]; who is account-masked."""
    ct = session.client("cloudtrail", config=_CLIENT_CONFIG)
    t_start = _epoch_s(since)
    start = dt.datetime.fromtimestamp(t_start, dt.timezone.utc)
    end = dt.datetime.fromtimestamp(min(time.time(), t_start + window_s), dt.timezone.utc)
    rows = []
    for event_name, match in expected:
        hits, token = [], None
        for _ in range(max_pages):
            kw: dict[str, Any] = {"LookupAttributes": [{"AttributeKey": "EventName", "AttributeValue": event_name}],
                                  "StartTime": start, "EndTime": end, "MaxResults": 50}
            if token:
                kw["NextToken"] = token
            r = _lookup(ct, **kw)
            hits += [ce for ce in (json.loads(e["CloudTrailEvent"]) for e in r.get("Events", []))
                     if match in json.dumps(ce) and not ce.get("errorCode")]
            token = r.get("NextToken")
            if not token:
                break
        first = min(hits, key=lambda ce: ce["eventTime"]) if hits else None
        uid = (first or {}).get("userIdentity") or {}
        rows.append({"event": event_name, "match": match, "found": first is not None,
                     "when": first["eventTime"] if first else None,
                     "who": _mask_ids(uid.get("arn") or uid.get("invokedBy") or uid.get("type")) if first else None,
                     "requestID": first.get("requestID") if first else None})
    return rows


# ======================================================================================
# Teardown (by exact name) and the safety net for teardown_m03.py
# ======================================================================================
def _group_exists(logs, name: str) -> bool:
    return any(g["logGroupName"] == name for p in logs.get_paginator("describe_log_groups").paginate(
        logGroupNamePrefix=name) for g in p["logGroups"])


def delete_log_group(name: str, session) -> dict[str, str]:
    """Delete one log group and verify it is gone (exact-name lookup). -> {label: "gone ✓" | "pending: …" | "error: …"}.
    Never raises; safe to call twice."""
    label = f"log group {name}"
    try:
        logs = session.client("logs", config=_CLIENT_CONFIG)
        try:
            logs.delete_log_group(logGroupName=name)
        except ClientError as e:
            if _code(e) != "ResourceNotFoundException":
                raise
        for _ in range(10):
            if not _group_exists(logs, name):
                return {label: "gone ✓"}
            time.sleep(1)
        return {label: "pending: still listed 10 s after DeleteLogGroup"}
    except (ClientError, BotoCoreError) as e:
        return {label: f"error: {type(e).__name__}: {str(e)[:200]}"}


def find_run_resources(session, run_id: str) -> dict[str, list]:
    """Read-only: this run's log groups (every group under /mladas/m03/<run suffix>/). -> {"log_groups": [...]}"""
    logs = session.client("logs", config=_CLIENT_CONFIG)
    prefix = resource_names(run_id)["prefix"]
    return {"log_groups": [g["logGroupName"] for p in logs.get_paginator("describe_log_groups").paginate(
        logGroupNamePrefix=prefix) for g in p["logGroups"]]}


def delete_run_resources(session, run_id: str) -> dict[str, str]:
    """Delete this run's log groups (under /mladas/m03/<run suffix>/), each verified. Touches nothing else.
    -> {label: "gone ✓" | "pending: …" | "error: …"}"""
    out: dict[str, str] = {}
    try:
        for name in find_run_resources(session, run_id)["log_groups"]:
            out.update(delete_log_group(name, session))
    except (ClientError, BotoCoreError) as e:
        out["log groups (list)"] = f"error: {type(e).__name__}: {e}"
    return out

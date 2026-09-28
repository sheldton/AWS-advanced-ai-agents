"""
agentcore_gateway — an Amazon Bedrock AgentCore Gateway with semantic tool search, deployed from a notebook.

One Lambda (python3.12, arm64, inline source, no Docker) answers EVERY tool with a deterministic stub. The gateway
(inbound auth AWS_IAM = SigV4, protocol MCP, searchType SEMANTIC) puts it behind one Lambda target per catalog key,
each with inline tool schemas. With `bank_data.bank_tool_catalog()` that is slide 46: 175 / 150 / 10 = 335 tools.

    import agentcore_gateway as gwy
    gw = gwy.GatewayDeployment("mladas-m02", RUN_ID, SESSION, bank.bank_tool_catalog()).start()  # background, ~50 s
    ...                                                  # teach something else meanwhile
    gw.wait(120)                                         # True once every target is READY (indexing included)
    client = gw.mcp_client()                             # Strands MCPClient, SigV4-signed
    with client:
        tools = gwy.list_all_tools(client)               # 336 = 335 + x_amz_bedrock_agentcore_search
        top10 = gwy.search_tools(client, "block a stolen card")
        agent = mc.make_agent("bank_top10", "...", tools=gwy.agent_tools_from_search(client, top10))
        agent("My card 4417 was stolen, block it.")
    gw.teardown()                                        # {"deleted": [...], "problems": [...], "seconds": ...}

Every name carries the run id (suffix = run id lowercased, non-alphanumerics removed), so `find_run_resources` /
`delete_run_resources` (and teardown_m02.py --run-id) find exactly this run's resources and nothing else:
    gateway        <name>-gw-<suffix>          (gatewayId = <gateway name>-<10 chars>; its workload identity has the same name)
    IAM roles      <name>-gw-<suffix> (gateway service role) · <name>-gwfn-<suffix> (Lambda execution role)
    Lambda         <name>-banktools-<suffix>   (+ log group /aws/lambda/<function>, retention 1 day)
Tags on everything: {"project": "mladas", "module": "M02", "run_id": <run id>} (+ `tags`).

Verified in a test account in us-east-1, 2026-09-26: strands-agents 1.57.1, mcp 2.1.1, boto3 1.43.103.
Gotchas baked in: IAM propagation retries (CreateFunction: "role ... cannot be assumed"; CreateGatewayTarget:
"not authorized to perform AssumeRole"), target names without "-" (Nova v1 cannot call such tools) or "_" (API
pattern), tools/list pages of 30, search throttling ("Your request rate is too high") with 2/4/8 s backoff, and
teardown order targets -> NotFound -> gateway (DeleteGateway refuses while targets exist).
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

SEARCH_TOOL = "x_amz_bedrock_agentcore_search"          # the tool a SEMANTIC gateway adds to tools/list
DEFAULT_PREFIX = "mladas-m02"
_TARGET_NAME = re.compile(r"^[0-9a-zA-Z]{1,100}$")       # CamelCase: no "-" (Nova v1) and no "_" (API pattern)
_PREFIX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_CLIENT_CONFIG = Config(retries={"total_max_attempts": 5, "mode": "standard"}, read_timeout=60)

# ======================================================================================
# The Lambda behind every target (inline source, zipped in memory)
# ======================================================================================
HANDLER_SOURCE = r'''
"""AnyCompany Bank tools behind an AgentCore Gateway: ONE function answers every tool with a deterministic stub.

Gateway contract (verified 2026-09-26): event = the tool arguments; the tool name arrives in
context.client_context.custom["bedrockAgentCoreToolName"] as "<target>___<tool>". The returned dict becomes
CallToolResult.content[0].text (JSON). Stub data only: the same tool + arguments always return the same answer.
"""
import hashlib
import json


def _ref(tool, args, prefix):
    digest = hashlib.sha256((tool + json.dumps(args, sort_keys=True, default=str)).encode()).hexdigest()
    return f"{prefix}-{int(digest[:8], 16) % 900000 + 100000}"


def _payment(amount, apr_pct, months):
    r = apr_pct / 1200.0
    return round(amount * r / (1 - (1 + r) ** -months), 2) if months else None


def _num(x, default):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


SPECIAL = {
    "card_block": lambda a, ref: {"card_last4": a.get("card_last4"), "status": "blocked", "reason": a.get("reason"),
                                  "replacement_ordered": False, "next_step": "order a new card with card_replace"},
    "card_freeze": lambda a, ref: {"card_last4": a.get("card_last4"), "status": "frozen", "reversible": True},
    "card_unfreeze": lambda a, ref: {"card_last4": a.get("card_last4"), "status": "active"},
    "card_replace": lambda a, ref: {"card_last4": a.get("card_last4"), "replacement_id": ref("RPL"),
                                    "status": "ordered", "delivery_business_days": 5},
    "dispute_open": lambda a, ref: {"dispute_id": ref("DSP"), "status": "open", "card_last4": a.get("card_last4"),
                                    "provisional_credit_usd": a.get("amount"), "decision_due_days": 10},
    "fx_rate_get": lambda a, ref: {"from": a.get("from_currency", "EUR"), "to": a.get("to_currency", "USD"),
                                   "rate": 1.0842, "as_of": "2026-09-26T09:00:00Z"},
    "personal_loan_quote": lambda a, ref: {
        "amount_usd": _num(a.get("amount"), 15000.0), "term_months": int(_num(a.get("term_months"), 36)),
        "apr_pct": 9.4, "monthly_payment_usd": _payment(_num(a.get("amount"), 15000.0), 9.4,
                                                        int(_num(a.get("term_months"), 36)))},
    "customer_address_update": lambda a, ref: {"customer_id": a.get("customer_id"), "status": "pending_verification",
                                               "change_id": ref("ADR"),
                                               "note": "address changes need a one-time passcode confirmation"},
    "standing_order_create": lambda a, ref: {"order_id": ref("SO"), "customer_id": a.get("customer_id"),
                                             "status": "active", "first_payment": "2026-10-01"},
    "account_statement_export": lambda a, ref: {"statement_id": a.get("statement_id"),
                                                "format": a.get("format") or "pdf", "status": "ready",
                                                "download_ref": ref("DL"), "link_expires_in_s": 900},
    "ticket_create": lambda a, ref: {"ticket_id": ref("TCK"), "status": "open",
                                     "priority": a.get("priority") or "normal", "first_response_hours": 24},
    "callback_schedule": lambda a, ref: {"callback_id": ref("CB"), "team": a.get("team"),
                                         "scheduled_for": a.get("preferred_time"), "status": "scheduled"},
}

VERBS = {
    "get": lambda a, ref: {"status": "active", "found": True},
    "list": lambda a, ref: {"items": [ref("ID") + f"-{i}" for i in range(1, 4)], "count": 3},
    "search": lambda a, ref: {"query": a.get("query"), "matches": [ref("ID")], "count": 1},
    "create": lambda a, ref: {"id": ref("NEW"), "status": "created"},
    "update": lambda a, ref: {"status": "updated", "change_id": ref("CHG")},
    "close": lambda a, ref: {"status": "closed", "closure_id": ref("CLS")},
    "history": lambda a, ref: {"events": 3, "last_change": "2026-09-24T15:12:00Z"},
    "export": lambda a, ref: {"status": "ready", "format": a.get("format") or "pdf", "download_ref": ref("DL")},
    "quote": lambda a, ref: {"quote_id": ref("QTE"), "valid_for_s": 900},
    "status": lambda a, ref: {"status": "pending"},
    "approve": lambda a, ref: {"status": "approved"},
    "reject": lambda a, ref: {"status": "rejected"},
    "validate": lambda a, ref: {"valid": True, "errors": []},
    "summary": lambda a, ref: {"count": 4, "total_usd": 1250.0},
    "notify": lambda a, ref: {"status": "sent", "channel": a.get("channel") or "app"},
}


def lambda_handler(event, context):
    cc = getattr(context, "client_context", None)
    custom = (getattr(cc, "custom", None) or {}) if cc is not None else {}
    target, _, tool = custom.get("bedrockAgentCoreToolName", "").rpartition("___")
    args = event if isinstance(event, dict) else {}
    ref = lambda prefix: _ref(tool, args, prefix)  # noqa: E731
    build = SPECIAL.get(tool) or VERBS.get(tool.rsplit("_", 1)[-1]) or (lambda a, r: {"status": "ok"})
    result = {k: v for k, v in args.items() if k.endswith("_id") or k in ("card_last4",)}
    result.update(build(args, ref))
    print(json.dumps({"tool": f"{target}___{tool}", "args": args}))
    return {"tool": tool, "target": target, "result": result, "source": "AnyCompany Bank demo stub"}
'''


def _handler_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo("handler.py", date_time=(2026, 9, 26, 0, 0, 0))   # fixed timestamp: same zip every time
        info.external_attr = 0o644 << 16
        z.writestr(info, HANDLER_SOURCE)
    return buf.getvalue()


# ======================================================================================
# Names (shared by the deployment, find_run_resources and delete_run_resources)
# ======================================================================================
def run_suffix(run_id: str) -> str:
    """The run id as it appears in resource names: lowercase, alphanumerics only ('20260926-1745-c7e1' -> '202609261745c7e1')."""
    s = re.sub(r"[^a-z0-9]", "", str(run_id).lower())
    if not s:
        raise ValueError(f"run_id {run_id!r} has no letters or digits")
    return s


def resource_names(run_id: str, prefix: str = DEFAULT_PREFIX) -> dict[str, str]:
    """Every name a GatewayDeployment for this run id uses (validated against the service limits)."""
    if not _PREFIX.match(prefix):
        raise ValueError(f"name/prefix {prefix!r} must be lowercase letters, digits and single hyphens")
    sfx = run_suffix(run_id)
    fn = f"{prefix}-banktools-{sfx}"
    names = {"gateway": f"{prefix}-gw-{sfx}", "gateway_role": f"{prefix}-gw-{sfx}",
             "function_role": f"{prefix}-gwfn-{sfx}", "function": fn, "log_group": f"/aws/lambda/{fn}"}
    if len(names["gateway"]) > 48:
        raise ValueError(f"gateway name {names['gateway']!r} is longer than 48 characters: use a shorter run id")
    if max(len(names["function_role"]), len(fn)) > 64:
        raise ValueError("IAM role / Lambda names are limited to 64 characters: use a shorter run id")
    return names


def _code(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "")


class _Cancelled(Exception):
    pass


# ======================================================================================
# The deployment
# ======================================================================================
class GatewayDeployment:
    """IAM roles -> Lambda -> CreateGateway (AWS_IAM, MCP, SEMANTIC) -> one Lambda target per catalog key -> READY,
    in a background thread. Re-run safe: adopts a role / function / gateway / target that already has its name."""

    def __init__(self, name: str, run_id: str, session: boto3.Session, catalog: dict[str, list[dict]], *,
                 tags: dict | None = None, description: str = ""):
        """name: resource-name prefix, e.g. "mladas-m02" · run_id: the notebook's RUN_ID (goes into every name) ·
        session: mc.get_session() · catalog: {TargetName: [{name, description, inputSchema}, ...]}."""
        self.names = resource_names(run_id, name)
        for target, tools in catalog.items():
            if not _TARGET_NAME.match(target):
                raise ValueError(f"target name {target!r}: use CamelCase letters/digits only (no '-' or '_')")
            too_long = [t["name"] for t in tools if len(f"{target}___{t['name']}") > 64]
            if too_long:
                raise ValueError(f"tool names over Bedrock's 64-char limit as {target}___<tool>: {too_long[:3]}")
        self.name, self.run_id, self.session, self.catalog = name, run_id, session, catalog
        self.region = session.region_name
        self.tool_count = sum(len(v) for v in catalog.values())
        self.description = (description or f"MLADAS {name}: {self.tool_count} demo tools in "
                                           f"{len(catalog)} targets")[:200]
        self.tags = {"project": "mladas", "module": "M02", "run_id": run_id, **(tags or {})}
        self.gateway_name, self.function_name = self.names["gateway"], self.names["function"]
        self.role_names = [self.names["gateway_role"], self.names["function_role"]]
        # clients are created here, on the caller's thread (boto3 sessions are not thread-safe; clients are)
        self._iam = session.client("iam", config=_CLIENT_CONFIG)
        self._lam = session.client("lambda", config=_CLIENT_CONFIG)
        self._logs = session.client("logs", config=_CLIENT_CONFIG)
        self._ctl = session.client("bedrock-agentcore-control", config=_CLIENT_CONFIG)
        self.account = session.client("sts").get_caller_identity()["Account"]
        self.function_arn = f"arn:aws:lambda:{self.region}:{self.account}:function:{self.function_name}"
        self.gateway_id: str | None = None
        self.gateway_url: str | None = None
        self.target_ids: dict[str, str] = {}
        self.timings: dict[str, float] = {}
        self.status = "NOT_STARTED"
        self.error: str | None = None
        self.events: list[tuple[float, str, str]] = []
        self._t0 = time.time()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._done = threading.Event()

    # ---------------------------------------------------------------------------------- public API
    def start(self) -> "GatewayDeployment":
        """Deploy in a daemon thread and return immediately. Calling it again while running (or READY) is a no-op."""
        if (self._thread and self._thread.is_alive()) or self.ready:
            return self
        self._cancel.clear()
        self._done.clear()
        self._t0, self.events, self.error = time.time(), [], None
        self._log("STARTING", f"gateway {self.gateway_name}: {self.tool_count} tools in {len(self.catalog)} targets")
        self._thread = threading.Thread(target=self._run, name=f"gateway-{self.gateway_name}", daemon=True)
        self._thread.start()
        return self

    def wait(self, timeout: float = 120) -> bool:
        """Block until READY / FAILED / CANCELLED or `timeout` seconds. True when READY."""
        if self._thread is not None:
            self._done.wait(timeout)
        return self.ready

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    def progress(self) -> list[tuple[float, str, str]]:
        """[(seconds since start, step, message), ...] — the same shape as RuntimeDeployment.progress()."""
        return list(self.events)

    def mcp_client(self, **kwargs: Any):
        """A Strands MCPClient for this gateway, SigV4-signed (service 'bedrock-agentcore'). Use it as
        `with client: ...` (or pass it in `tools=[client]`). kwargs go to MCPClient (e.g. tool_filters, prefix)."""
        if not self.gateway_url:
            raise RuntimeError(f"gateway not created yet (status {self.status}): call .start() and .wait() first")
        import mladas_common as mc
        from strands.tools.mcp import MCPClient

        kwargs.setdefault("startup_timeout", 30)
        return MCPClient(url=self.gateway_url, auth_provider=mc.SigV4HttpxAuth(session=self.session, region=self.region),
                         **kwargs)

    def teardown(self) -> dict:
        """Delete everything this run created, in dependency order, verified by id. Safe to call while the deploy
        thread is still running (it is cancelled and joined first) and safe to call twice."""
        self._cancel.set()
        problems: list[str] = []
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=300)
            if self._thread.is_alive():
                problems.append("deploy thread still running after 300 s; deleting anyway")
        self.status = "DELETING"
        out = _delete_resources(self.session, self.names, [self.gateway_id] if self.gateway_id else [],
                                clients=(self._ctl, self._lam, self._logs, self._iam))
        out["problems"] = problems + out["problems"]
        self.status = "DELETED" if not out["problems"] else "DELETE_INCOMPLETE"
        self._log(self.status, f"{len(out['deleted'])} deleted, {len(out['problems'])} problems in {out['seconds']} s")
        return out

    # ---------------------------------------------------------------------------------- deploy steps
    def _log(self, step: str, msg: str) -> None:
        self.status = step
        self.events.append((round(time.time() - self._t0, 1), step, msg))

    def _mark(self, key: str) -> None:
        self.timings[key] = round(time.time() - self._t0, 1)

    def _sleep(self, seconds: float) -> None:
        if self._cancel.wait(seconds):
            raise _Cancelled()

    def _retry(self, label: str, fn, retryable, max_wait: float = 90, delay: float = 3):
        """Call fn() until it succeeds; retry ClientErrors for which retryable(e) is True (IAM propagation)."""
        deadline, n = time.time() + max_wait, 0
        while True:
            if self._cancel.is_set():
                raise _Cancelled()
            try:
                return fn(), n
            except ClientError as e:
                if not retryable(e) or time.time() + delay > deadline:
                    raise
                n += 1
                if n == 1:
                    self.events.append((round(time.time() - self._t0, 1), self.status,
                                        f"{label}: {_code(e)}, retrying every {delay:.0f} s (IAM propagation)"))
                self._sleep(delay)

    def _run(self) -> None:
        try:
            self._create_roles()
            self._create_function()
            self._create_gateway()
            self._create_targets()
            self._wait_targets()
            self._mark("ready_s")
            self._log("READY", f"{self.gateway_url} · {self.tool_count} tools + {SEARCH_TOOL}")
        except _Cancelled:
            self._log("CANCELLED", "teardown() requested; deployment stopped")
        except (ClientError, BotoCoreError, RuntimeError, TimeoutError, ValueError, KeyError) as e:
            self.error = f"{type(e).__name__}: {e}"
            self._log("FAILED", self.error)
        except BaseException as e:  # noqa: BLE001 — surface everything to the notebook, never kill the kernel
            self.error = f"{type(e).__name__}: {e}"
            self._log("FAILED", self.error)
        finally:
            self._done.set()

    def _ensure_role(self, role: str, trust: dict, policy_name: str, policy: dict, what: str) -> str:
        tag_list = [{"Key": k, "Value": str(v)} for k, v in self.tags.items()]
        try:
            arn = self._iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(trust), Tags=tag_list,
                                        Description=f"MLADAS {what} ({self.run_id})")["Role"]["Arn"]
            self.events.append((round(time.time() - self._t0, 1), self.status, f"created role {role}"))
        except ClientError as e:
            if _code(e) != "EntityAlreadyExists":
                raise
            arn = self._iam.get_role(RoleName=role)["Role"]["Arn"]
            self._iam.update_assume_role_policy(RoleName=role, PolicyDocument=json.dumps(trust))
            self.events.append((round(time.time() - self._t0, 1), self.status, f"adopted existing role {role}"))
        self._iam.put_role_policy(RoleName=role, PolicyName=policy_name, PolicyDocument=json.dumps(policy))
        return arn

    def _create_roles(self) -> None:
        # Both roles first: the gateway role then has ~15 s to propagate before CreateGatewayTarget needs it.
        self._log("CREATING_ROLES", f"{self.names['function_role']} (Lambda) · {self.names['gateway_role']} (gateway)")
        log_arn = f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.names['log_group']}"
        self._fn_role_arn = self._ensure_role(
            self.names["function_role"],
            {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                                                     "Action": "sts:AssumeRole"}]},
            "write-own-logs",
            {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Resource": [log_arn, f"{log_arn}:*"],
                                                     "Action": ["logs:CreateLogGroup", "logs:CreateLogStream",
                                                                "logs:PutLogEvents"]}]},
            "gateway tool Lambda execution role")
        self._gw_role_arn = self._ensure_role(
            self.names["gateway_role"],
            {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": self.account},
                              "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:*"}}}]},
            "invoke-tool-lambda",
            {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "lambda:InvokeFunction",
                                                     "Resource": self.function_arn}]},
            "AgentCore Gateway service role")
        self._mark("roles_s")

    def _create_function(self) -> None:
        self._log("CREATING_LAMBDA", f"{self.function_name} (python3.12, arm64, one handler for every tool)")
        try:                                              # pre-create the log group: tagged, 1-day retention
            self._logs.create_log_group(logGroupName=self.names["log_group"], tags=self.tags)
        except ClientError as e:
            if _code(e) != "ResourceAlreadyExistsException":
                raise
        self._logs.put_retention_policy(logGroupName=self.names["log_group"], retentionInDays=1)
        code = _handler_zip()
        try:
            fn, n = self._retry("CreateFunction", lambda: self._lam.create_function(
                FunctionName=self.function_name, Runtime="python3.12", Role=self._fn_role_arn,
                Handler="handler.lambda_handler", Code={"ZipFile": code}, Timeout=10, MemorySize=128,
                Architectures=["arm64"], Tags={k: str(v) for k, v in self.tags.items()},
                Description=f"MLADAS gateway tool stubs ({self.run_id})"),
                # "The role defined for the function cannot be assumed by Lambda" = the new role has not propagated
                lambda e: _code(e) == "InvalidParameterValueException" and "assume" in str(e).lower(), max_wait=120)
            self.function_arn = fn["FunctionArn"]
            self.timings["lambda_iam_retries"] = n
        except ClientError as e:
            if _code(e) != "ResourceConflictException":
                raise
            self._wait_function(lambda c: c.get("LastUpdateStatus") != "InProgress" and c.get("State") != "Pending")
            self._lam.update_function_code(FunctionName=self.function_name, ZipFile=code)
            self.events.append((round(time.time() - self._t0, 1), self.status, f"adopted existing {self.function_name}"))
        self._wait_function(lambda c: c.get("State") == "Active" and c.get("LastUpdateStatus") in (None, "Successful"))
        self._mark("lambda_active_s")

    def _wait_function(self, ok, timeout: float = 120) -> None:
        deadline = time.time() + timeout
        while True:
            c = self._lam.get_function_configuration(FunctionName=self.function_name)
            if ok(c):
                return
            if c.get("State") == "Failed" or c.get("LastUpdateStatus") == "Failed":
                raise RuntimeError(f"Lambda {self.function_name}: {c.get('StateReason') or c.get('LastUpdateStatusReason')}")
            if time.time() > deadline:
                raise TimeoutError(f"Lambda {self.function_name} not Active after {timeout:.0f} s")
            self._sleep(1)

    def _create_gateway(self) -> None:
        self._log("CREATING_GATEWAY", f"{self.gateway_name} (inbound AWS_IAM, MCP, searchType SEMANTIC)")

        def create():
            return self._ctl.create_gateway(
                name=self.gateway_name, description=self.description, roleArn=self._gw_role_arn,
                protocolType="MCP", protocolConfiguration={"mcp": {"searchType": "SEMANTIC"}},
                authorizerType="AWS_IAM", tags=self.tags, clientToken=uuid.uuid4().hex + uuid.uuid4().hex[:8])
        try:
            gw, _ = self._retry("CreateGateway", create, lambda e: _code(e) in ("ValidationException", "AccessDeniedException")
                                and "role" in str(e).lower(), max_wait=60)
            self.gateway_id, self.gateway_url = gw["gatewayId"], gw["gatewayUrl"]
        except ClientError as e:
            if _code(e) != "ConflictException":
                raise
            found = [g for g in _list_gateways(self._ctl) if g["name"] == self.gateway_name]
            if not found:
                raise
            self.gateway_id = found[0]["gatewayId"]
            self.events.append((round(time.time() - self._t0, 1), self.status, f"adopted existing gateway {self.gateway_id}"))
        deadline = time.time() + 180
        while True:
            g = self._ctl.get_gateway(gatewayIdentifier=self.gateway_id)
            self.gateway_url = g.get("gatewayUrl") or self.gateway_url
            if g["status"] == "READY":
                break
            if g["status"] not in ("CREATING", "UPDATING"):
                raise RuntimeError(f"gateway {self.gateway_id} is {g['status']}: {g.get('statusReasons')}")
            if time.time() > deadline:
                raise TimeoutError(f"gateway {self.gateway_id} not READY after 180 s")
            self._sleep(2)
        self._mark("gateway_ready_s")

    def _create_targets(self) -> None:
        self._log("CREATING_TARGETS", ", ".join(f"{k} ({len(v)} tools)" for k, v in self.catalog.items()))
        existing = {t["name"]: t["targetId"] for t in _list_targets(self._ctl, self.gateway_id)}
        retries = 0
        for target, tools in self.catalog.items():
            if target in existing:
                self.target_ids[target] = existing[target]
                self.events.append((round(time.time() - self._t0, 1), self.status, f"adopted existing target {target}"))
                continue

            def create(target=target, tools=tools):
                return self._ctl.create_gateway_target(
                    gatewayIdentifier=self.gateway_id, name=target,
                    description=f"{target}: {len(tools)} AnyCompany Bank tools"[:200],
                    targetConfiguration={"mcp": {"lambda": {"lambdaArn": self.function_arn,
                                                            "toolSchema": {"inlinePayload": tools}}}},
                    credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}])
            try:
                # "Gateway service is not authorized to perform AssumeRole on Gateway role" = IAM propagation
                r, n = self._retry(f"CreateGatewayTarget {target}", create,
                                   lambda e: "assumerole" in str(e).lower() or _code(e) == "ThrottlingException",
                                   max_wait=120)
                self.target_ids[target] = r["targetId"]
                retries += n
            except ClientError as e:
                if _code(e) != "ConflictException":
                    raise
                again = {t["name"]: t["targetId"] for t in _list_targets(self._ctl, self.gateway_id)}
                if target not in again:
                    raise
                self.target_ids[target] = again[target]
        self.timings["target_iam_retries"] = retries

    def _wait_targets(self, timeout: float = 300) -> None:
        pending, deadline = dict(self.target_ids), time.time() + timeout
        while pending:
            for target, tid in list(pending.items()):
                t = self._ctl.get_gateway_target(gatewayIdentifier=self.gateway_id, targetId=tid)
                if t["status"] == "READY":
                    pending.pop(target)
                    self._mark(f"target_{target}_ready_s")
                    self.events.append((round(time.time() - self._t0, 1), self.status,
                                        f"target {target} READY ({len(self.catalog[target])} tools indexed)"))
                elif t["status"] not in ("CREATING", "UPDATING", "SYNCHRONIZING"):
                    raise RuntimeError(f"target {target} is {t['status']}: {t.get('statusReasons')}")
            if pending:
                if time.time() > deadline:
                    raise TimeoutError(f"targets not READY after {timeout:.0f} s: {sorted(pending)}")
                self._sleep(2)


# ======================================================================================
# Using the gateway from an MCP client
# ======================================================================================
def list_all_tools(client) -> list:
    """Every tool on the gateway as MCPAgentTools. list_tools_sync() returns ONE page (30 tools): loop on the token."""
    tools, token = [], None
    for _ in range(1000):
        page = client.list_tools_sync(pagination_token=token)
        tools.extend(page)
        token = page.pagination_token
        if token is None:
            break
    return tools


@contextmanager
def _quiet_mcp_errors():
    """Strands logs a full traceback at ERROR for every failed MCP call; a throttled search is expected, so mute it."""
    log = logging.getLogger("strands.tools.mcp.mcp_client")
    old = log.level
    log.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        log.setLevel(old)


def search_tools(client, query: str, attempts: int = 4) -> list[dict]:
    """Semantic tool search: the 10 best-matching tool definitions [{name, description, inputSchema}], ranked.
    The gateway throttles search now and then ("Your request rate is too high"): back off 2, 4, 8 s."""
    for n in range(attempts):
        with _quiet_mcp_errors():
            r = client.call_tool_sync(tool_use_id=f"search-{uuid.uuid4().hex[:12]}", name=SEARCH_TOOL,
                                      arguments={"query": query})
        text = next((c.get("text", "") for c in r.get("content", []) if "text" in c), "")
        if r.get("status") == "success":
            return json.loads(text)["tools"]
        if "rate is too high" not in text or n == attempts - 1:
            raise RuntimeError(f"gateway search failed after {n + 1} attempt(s): {text[:300]}")
        time.sleep(2 * 2 ** n)
    return []


def agent_tools_from_search(client, dicts: list[dict]) -> list:
    """Search results -> MCPAgentTools an agent can call (they execute through `client`, which must be open)."""
    from mcp import types as mtypes
    from strands.tools.mcp import MCPAgentTool

    return [MCPAgentTool(mtypes.Tool.model_validate(d), client) for d in dicts]


# ======================================================================================
# Finding and deleting a run's resources (teardown script, safety net)
# ======================================================================================
def _paged(call, key: str, **kw) -> list[dict]:
    out, token = [], None
    while True:
        page = call(**kw, **({"nextToken": token} if token else {}))
        out += page.get(key, [])
        token = page.get("nextToken")
        if not token:
            return out


def _list_gateways(ctl) -> list[dict]:
    return _paged(ctl.list_gateways, "items", maxResults=100)


def _list_targets(ctl, gateway_id: str) -> list[dict]:
    return _paged(ctl.list_gateway_targets, "items", gatewayIdentifier=gateway_id, maxResults=100)


def _gateway_id_re(gateway_name: str) -> re.Pattern:
    return re.compile(re.escape(gateway_name) + r"-[a-z0-9]{10}")


def _find(session: boto3.Session, names: dict[str, str], clients=None) -> dict[str, list]:
    ctl, lam, logs, iam = clients or (session.client(s, config=_CLIENT_CONFIG)
                                      for s in ("bedrock-agentcore-control", "lambda", "logs", "iam"))
    gw_ids = _gateway_id_re(names["gateway"])
    gateways = []
    for g in _list_gateways(ctl):
        if g["name"] == names["gateway"]:
            try:
                tids = [t["targetId"] for t in _list_targets(ctl, g["gatewayId"])]
            except ClientError:
                tids = []
            gateways.append({"gatewayId": g["gatewayId"], "status": g["status"], "targets": tids})
    functions = [f["FunctionName"] for p in lam.get_paginator("list_functions").paginate() for f in p["Functions"]
                 if f["FunctionName"] == names["function"]]
    roles = [r["RoleName"] for p in iam.get_paginator("list_roles").paginate() for r in p["Roles"]
             if r["RoleName"] in (names["gateway_role"], names["function_role"])]
    log_groups = [g["logGroupName"] for p in logs.get_paginator("describe_log_groups").paginate(
        logGroupNamePrefix=names["log_group"]) for g in p["logGroups"] if g["logGroupName"] == names["log_group"]]
    log_groups += [g["logGroupName"] for p in logs.get_paginator("describe_log_groups").paginate(
        logGroupNamePattern=names["gateway"]) for g in p["logGroups"] if gw_ids.search(g["logGroupName"])]
    identities = [w["name"] for w in _paged(ctl.list_workload_identities, "workloadIdentities", maxResults=20)
                  if gw_ids.fullmatch(w["name"])]
    return {"gateways": gateways, "functions": functions, "roles": roles, "log_groups": sorted(set(log_groups)),
            "workload_identities": identities}


def find_run_resources(session: boto3.Session, run_id: str, prefix: str = DEFAULT_PREFIX) -> dict[str, list]:
    """Read-only: this run's gateway resources, found by exact name (every list call paginated).
    -> {"gateways": [{gatewayId, status, targets}], "functions", "roles", "log_groups", "workload_identities"}"""
    return _find(session, resource_names(run_id, prefix))


def delete_run_resources(session: boto3.Session, run_id: str, prefix: str = DEFAULT_PREFIX) -> dict:
    """Delete this run's gateway resources (targets -> gateway -> Lambda -> log groups -> roles), verified by id.
    Touches nothing whose name does not carry this run id. -> {"deleted": [...], "problems": [...], "seconds": s}"""
    return _delete_resources(session, resource_names(run_id, prefix), [])


def _wait_gone(check, timeout: float, poll: float = 2) -> bool:
    deadline = time.time() + timeout
    while True:
        try:
            check()
        except ClientError as e:
            if _code(e) in ("ResourceNotFoundException", "NoSuchEntity", "NotFoundException"):
                return True
            raise
        if time.time() > deadline:
            return False
        time.sleep(poll)


def _retry_delete(fn, timeout: float = 120, poll: float = 3) -> bool:
    """Call a delete until accepted. False if the resource was already gone. Retries Conflict/Validation
    (a target still CREATING, a gateway that still has targets) and throttling."""
    deadline = time.time() + timeout
    while True:
        try:
            fn()
            return True
        except ClientError as e:
            c = _code(e)
            if c in ("ResourceNotFoundException", "NoSuchEntity"):
                return False
            if c not in ("ConflictException", "ValidationException", "ThrottlingException",
                         "TooManyRequestsException") or time.time() > deadline:
                raise
            time.sleep(poll)


def _delete_resources(session: boto3.Session, names: dict[str, str], known_gateway_ids: list[str],
                      clients=None) -> dict:
    t0 = time.time()
    ctl, lam, logs, iam = clients or tuple(session.client(s, config=_CLIENT_CONFIG)
                                           for s in ("bedrock-agentcore-control", "lambda", "logs", "iam"))
    deleted: list[str] = []
    problems: list[str] = []

    def step(label: str, fn) -> None:
        try:
            fn()
        except (ClientError, BotoCoreError, RuntimeError) as e:
            problems.append(f"{label}: {type(e).__name__}: {str(e)[:200]}")

    # 1. gateways: targets first (DeleteGateway refuses while targets exist), wait NotFound, then the gateway
    gw_ids = list(dict.fromkeys([*known_gateway_ids,
                                 *(g["gatewayId"] for g in _list_gateways(ctl) if g["name"] == names["gateway"])]))
    for gid in gw_ids:
        def drop_gateway(gid=gid):
            try:
                tids = [t["targetId"] for t in _list_targets(ctl, gid)]
            except ClientError as e:
                if _code(e) == "ResourceNotFoundException":
                    return
                raise
            for tid in tids:
                _retry_delete(lambda tid=tid: ctl.delete_gateway_target(gatewayIdentifier=gid, targetId=tid))
            for tid in tids:
                if _wait_gone(lambda tid=tid: ctl.get_gateway_target(gatewayIdentifier=gid, targetId=tid), 180):
                    deleted.append(f"gateway target {tid}")
                else:
                    problems.append(f"gateway target {tid}: still there after 180 s")
            _retry_delete(lambda: ctl.delete_gateway(gatewayIdentifier=gid))
            if _wait_gone(lambda: ctl.get_gateway(gatewayIdentifier=gid), 180):
                deleted.append(f"gateway {gid}")
            else:
                problems.append(f"gateway {gid}: still there after 180 s")
        step(f"gateway {gid}", drop_gateway)

    # 2. the Lambda function
    def drop_function():
        existed = _retry_delete(lambda: lam.delete_function(FunctionName=names["function"]))
        if not _wait_gone(lambda: lam.get_function(FunctionName=names["function"]), 60):
            problems.append(f"Lambda {names['function']}: still there")
        elif existed:
            deleted.append(f"Lambda {names['function']}")
    step("Lambda", drop_function)

    # 3. log groups: sweep twice (a last invocation can still flush a batch and re-create the group)
    gw_re = _gateway_id_re(names["gateway"])

    def log_groups() -> list[str]:
        out = [g["logGroupName"] for p in logs.get_paginator("describe_log_groups").paginate(
            logGroupNamePrefix=names["log_group"]) for g in p["logGroups"] if g["logGroupName"] == names["log_group"]]
        out += [g["logGroupName"] for p in logs.get_paginator("describe_log_groups").paginate(
            logGroupNamePattern=names["gateway"]) for g in p["logGroups"] if gw_re.search(g["logGroupName"])]
        return sorted(set(out))

    def drop_logs():
        for sweep in range(2):
            for lg in log_groups():
                try:
                    logs.delete_log_group(logGroupName=lg)
                    deleted.append(f"log group {lg}")
                except ClientError as e:
                    if _code(e) != "ResourceNotFoundException":
                        raise
            if sweep == 0:
                time.sleep(3)
        left = log_groups()
        if left:
            problems.append(f"log groups still there: {left}")
    step("log groups", drop_logs)

    # 4. the gateway's workload identity (created and normally deleted with the gateway)
    def drop_identities():
        mine = lambda: [w["name"] for w in _paged(ctl.list_workload_identities, "workloadIdentities", maxResults=20)  # noqa: E731
                        if gw_re.fullmatch(w["name"])]
        deadline = time.time() + (30 if gw_ids else 0)
        left = mine()
        while left and time.time() < deadline:
            time.sleep(3)
            left = mine()
        for name in left:                              # outlived its gateway: it carries our run id, so remove it
            _retry_delete(lambda name=name: ctl.delete_workload_identity(name=name), timeout=30)
            if _wait_gone(lambda name=name: ctl.get_workload_identity(name=name), 30):
                deleted.append(f"workload identity {name}")
            else:
                problems.append(f"workload identity {name}: still there")
    step("workload identities", drop_identities)

    # 5. IAM roles (inline + attached policies first)
    for role in (names["function_role"], names["gateway_role"]):
        def drop_role(role=role):
            try:
                for p in iam.list_role_policies(RoleName=role)["PolicyNames"]:
                    iam.delete_role_policy(RoleName=role, PolicyName=p)
                for p in iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]:
                    iam.detach_role_policy(RoleName=role, PolicyArn=p["PolicyArn"])
                iam.delete_role(RoleName=role)
                existed = True
            except ClientError as e:
                if _code(e) != "NoSuchEntity":
                    raise
                existed = False
            if not _wait_gone(lambda: iam.get_role(RoleName=role), 30):
                problems.append(f"IAM role {role}: still there")
            elif existed:
                deleted.append(f"IAM role {role}")
        step(f"IAM role {role}", drop_role)

    return {"deleted": deleted, "problems": problems, "seconds": round(time.time() - t0, 1)}

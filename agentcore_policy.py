"""
agentcore_policy — Policy in AgentCore (Cedar) for the AnyCompany Bank gateways, from a notebook. Your AWS credentials, us-east-1.

Authentication (agentcore_identity) decides WHO may reach the gateway; Policy in AgentCore decides, tool call by tool
call, WHAT that caller may do. One policy engine holds the bank's Cedar policies; it attaches to a gateway in LOG_ONLY
(every call runs, decisions are only recorded) or ENFORCE (a deny becomes a JSON-RPC error at the gateway).

Public API
----------
PolicyDeployment(run_id, session, ...)
  .create_engine()                         — CreatePolicyEngine, ACTIVE in ~7 s (adopts an engine of the same run id).
  .add_policy(name, cedar, validation=, enforcement="ACTIVE", description=)
                                            — CreatePolicy; waits ACTIVE, or RETURNS the findings / status reasons on
                                              CREATE_FAILED / REJECTED (never raises — §4 shows validator errors as a lesson).
  .add_many(policy_set)                     — create a whole {name: {cedar, validation, ...}} set (concurrent, then wait).
                                              An adopted policy whose text differs from the set is brought up to date
                                              with UpdatePolicy (same policy id), so a re-run of §0 refreshes a stack.
  .set_enforcement(name, "LOG_ONLY" | "ACTIVE", validation=None)
                                            — per-policy shadow mode (UpdatePolicy enforcementMode); a forbid is
                                              re-validated with IGNORE_ALL_FINDINGS. Raises unless it ends ACTIVE in that mode.
  .attach(gateway_id, mode) / .set_mode(gateway_id, mode)
                                            — UpdateGateway policyEngineConfiguration, with the IAM-propagation retry.
                                              mode in {"LOG_ONLY", "ENFORCE", None} (None detaches). Returns seconds to READY.
  .generate(text, gateway_arn=None)         — NL2Cedar (StartPolicyGeneration) -> [assets with Cedar text + findings].
  .add_generated(name, asset)               — CreatePolicy from a generated asset.
  .remove_policy(name) / .policies()        — delete one / list all.
  .start(gateway_arn, gateway_id, policy_set, mode="ENFORCE")
                                            — engine + all policies + attach, in a background thread (§0 while class starts).
  .wait(timeout) / .status / .progress()    — the background build, never raises.
  .teardown()                               — detach, delete policies, delete engine, verified by id.

bank_policy_set(gateway_arn, target="BankOps") -> {name: {"cedar", "validation", "description"}}
    The bank's policies, validated live: staff read (slide 20 ABAC), a customer's own-record read/block (slide 23
    conditional permit), a finance refund above $0 and below $500 (slide 17), forbids on off-bank refund destinations
    and on email outside @anycompany.example or to more than one address (slide 24 forbid), and staff internal email /
    case opening / card blocks.
DECK_SLIDE17_CEDAR                          — a policy written the way slide 17 writes it (our wording; the validator rejects it: §4.2).
deck_slide17_fixed(gateway_arn, target)     — the corrected form that becomes ACTIVE.
decision_metrics(session, engine_id, since, until=None) -> {"allow", "deny"}  — AllowDecisions/DenyDecisions (CloudWatch).

find_run_resources(session, run_id) / delete_run_resources(session, run_id)   — for teardown_m03.py (names/tags only).

Verified against the research in a test account in us-east-1, 2026-09-27 (boto3 1.43.103). Cedar facts baked in: actions are
`AgentCore::Action::"<Target>___<tool>"` (3 underscores); the resource must be pinned to the gateway ARN; JWT claims are
tags (`principal.hasTag(x) && principal.getTag(x) == ...`); a JSON number is a Cedar decimal
(`context.input.amount.lessThan(decimal("500.0"))`); default deny, forbid beats permit; an unconditional permit or a
lone forbid fails FAIL_ON_ANY_FINDINGS, so those carry IGNORE_ALL_FINDINGS; policy/generation names are account-unique
(every name gets the run suffix); there is no DeletePolicyGeneration (generations expire in 7 days).
"""
from __future__ import annotations

import concurrent.futures as _cf
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from agentcore_gateway import _list_gateways, run_suffix

_CFG = Config(retries={"total_max_attempts": 5, "mode": "standard"}, read_timeout=60)
_FINDING_PREFIX = ("Overly Permissive", "Overly Restrictive")
DEFAULT_ENGINE_PREFIX = "mladas_m03"


def _code(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "")


def _token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex[:8]


# ======================================================================================
# The bank's Cedar policy set (slides 17 / 20 / 23 / 24, corrected for the live service)
# ======================================================================================
# The deck's slide-17 Cedar, verbatim (curly quotes and all). It is INVALID for the service: an unscoped `resource`
# (wildcard), the MCP:: namespace, `principal.username` (claims are tags) and `resource.amount` (input is context.input).
DECK_SLIDE17_CEDAR = (
    'permit (\n'
    '    principal,\n'
    '    action == MCP::Action::"issue_goodwill_credit",\n'
    '    resource\n'
    ')\n'
    'when {\n'
    '    principal.username == "credit-agent" &&\n'
    '    resource.amount < 250\n'
    '};'
)


def _action(tool: str, target: str) -> str:
    return f'AgentCore::Action::"{target}___{tool}"'


def _action_list(tools: list[str], target: str) -> str:
    return "[" + ", ".join(_action(t, target) for t in tools) + "]"


def deck_slide17_fixed(gateway_arn: str, target: str = "BankOps") -> str:
    """The corrected slide-17 policy: input via context.input, the finance rule on the department tag, the amount as a
    Cedar decimal, and the resource pinned to this gateway."""
    return (f'permit (\n'
            f'    principal is AgentCore::OAuthUser,\n'
            f'    action == {_action("process_refund", target)},\n'
            f'    resource == AgentCore::Gateway::"{gateway_arn}"\n'
            f')\n'
            f'when {{\n'
            f'    principal.hasTag("department") &&\n'
            f'    principal.getTag("department") == "finance" &&\n'
            f'    context.input.amount.lessThan(decimal("500.0"))\n'
            f'}};')


def bank_policy_set(gateway_arn: str, target: str = "BankOps") -> dict[str, dict]:
    """The AnyCompany Bank policy set for one gateway. Each value is {cedar, validation, description}; the description
    names the deck slide it mirrors. Permits are conditional (they pass FAIL_ON_ANY_FINDINGS); forbids carry
    IGNORE_ALL_FINDINGS (a lone forbid is otherwise flagged 'Overly Restrictive' for the IamEntity principal type)."""
    R = f'AgentCore::Gateway::"{gateway_arn}"'
    staff = ('principal.hasTag("department") && '
             '(principal.getTag("department") == "cards" || principal.getTag("department") == "finance")')
    return {
        # slide 20 — ABAC on the department tag: staff read cards, transactions and merchant dispute responses.
        "p_staff_read": {
            "validation": "FAIL_ON_ANY_FINDINGS",
            "description": "Slide 20 (ABAC): staff (department cards/finance) may read cards, transactions and disputes.",
            "cedar": (f'permit (\n  principal is AgentCore::OAuthUser,\n'
                      f'  action in {_action_list(["list_card_transactions", "get_merchant_dispute_response", "get_card_details"], target)},\n'
                      f'  resource == {R}\n)\nwhen {{ {staff} }};')},
        # slide 20 (ABAC on the customer_id tag) + slide 23 (conditional permit): a customer principal reads/blocks only
        # its OWN records. It binds the customer to the token; the BankOps tool binds the card to that customer.
        "p_customer_own": {
            "validation": "FAIL_ON_ANY_FINDINGS",
            "description": ("Slide 20 (ABAC on the customer_id tag) and slide 23 (conditional permit): a customer "
                            "principal may read/block only its own records."),
            "cedar": (f'permit (\n  principal is AgentCore::OAuthUser,\n'
                      f'  action in {_action_list(["list_card_transactions", "get_card_details", "block_card"], target)},\n'
                      f'  resource == {R}\n)\nwhen {{ principal.hasTag("customer_id") && context.input has customer_id\n'
                      f'        && principal.getTag("customer_id") == context.input.customer_id }};')},
        # slide 17 — permit with conditions: finance refunds above $0 and below $500 (the corrected slide-17 policy plus
        # a lower bound, so a $0 or negative "refund" is denied by default).
        "p_refund_finance": {
            "validation": "FAIL_ON_ANY_FINDINGS",
            "description": "Slide 17 (permit with conditions): finance may refund when the amount is above $0 and below $500.",
            "cedar": (f'permit (\n  principal is AgentCore::OAuthUser,\n'
                      f'  action == {_action("process_refund", target)},\n'
                      f'  resource == {R}\n)\nwhen {{ principal.hasTag("department") '
                      f'&& principal.getTag("department") == "finance"\n'
                      f'        && context.input.amount.greaterThan(decimal("0.0"))\n'
                      f'        && context.input.amount.lessThan(decimal("500.0")) }};')},
        # slide 20 — staff may send internal email (f_email_external forbids the rest), open cases and block cards.
        # support-agent (department cards) is therefore "cards staff: reads, internal email, cases and card blocks — no
        # refunds", not read-only (its OAuth scope bank/cards.read only gets it through the door).
        "p_staff_write": {
            "validation": "FAIL_ON_ANY_FINDINGS",
            "description": "Slide 20 (ABAC): staff may send (internal) email, open cases and block cards.",
            "cedar": (f'permit (\n  principal is AgentCore::OAuthUser,\n'
                      f'  action in {_action_list(["send_email", "open_case", "block_card"], target)},\n'
                      f'  resource == {R}\n)\nwhen {{ {staff} }};')},
        # slide 24 — forbid: a refund whose destination is not an AnyCompany account (CHK-/SAV-). Stops the injected
        # redirect even for finance (forbid beats the slide-17 permit).
        "f_refund_external_dest": {
            "validation": "IGNORE_ALL_FINDINGS",
            "description": "Slide 24 (forbid): a refund to a non-AnyCompany destination is forbidden (forbid wins).",
            "cedar": (f'forbid (\n  principal,\n  action == {_action("process_refund", target)},\n'
                      f'  resource == {R}\n)\nwhen {{ context.input has destination_account\n'
                      f'        && !(context.input.destination_account like "CHK-*" '
                      f'|| context.input.destination_account like "SAV-*") }};')},
        # slide 24 — forbid: email to any address outside @anycompany.example (stops exfiltration). A second "@" means a
        # second recipient ("x@evil.example,ops@anycompany.example" ends in the bank's domain), so that is forbidden too.
        "f_email_external": {
            "validation": "IGNORE_ALL_FINDINGS",
            "description": ("Slide 24 (forbid): email to an address outside @anycompany.example, or to more than one "
                            "address, is forbidden."),
            "cedar": (f'forbid (\n  principal,\n  action == {_action("send_email", target)},\n'
                      f'  resource == {R}\n)\nwhen {{ context.input has to_address\n'
                      f'        && (!(context.input.to_address like "*@anycompany.example")\n'
                      f'            || context.input.to_address like "*@*@*") }};')},
    }


# ======================================================================================
# PolicyDeployment
# ======================================================================================
class PolicyDeployment:
    def __init__(self, run_id: str, session: boto3.Session, *, name_prefix: str = DEFAULT_ENGINE_PREFIX,
                 tags: dict | None = None):
        self.run_id, self.session = run_id, session
        self.suffix = run_suffix(run_id)
        self.engine_name = f"{name_prefix}_{self.suffix}"[:48]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", self.engine_name):
            raise ValueError(f"engine name {self.engine_name!r}: letters, digits and '_' only, <= 48 chars")
        self.tags = {"project": "mladas", "module": "M03", "run_id": str(run_id), **(tags or {})}
        self.ctl = session.client("bedrock-agentcore-control", config=_CFG)
        self.engine_id: str | None = None
        self.engine_arn: str | None = None
        self.gateway_arn: str | None = None
        self.gateway_id: str | None = None
        self.policy_ids: dict[str, str] = {}
        self.attached: set[str] = set()
        self.timings: dict[str, float] = {}
        # background start() bookkeeping
        self.status = "NOT_STARTED"
        self.error: str | None = None
        self.events: list[tuple[float, str, str]] = []
        self._t0 = time.time()
        self._thread: threading.Thread | None = None
        self._done = threading.Event()

    # ---------------------------------------------------------------- engine
    def create_engine(self, timeout: float = 90) -> str:
        t0 = time.time()
        try:
            pe = self.ctl.create_policy_engine(name=self.engine_name, description=f"MLADAS M03 bank policies ({self.run_id})",
                                               tags=self.tags, clientToken=_token())
        except ClientError as e:
            if _code(e) != "ConflictException":
                raise
            pe = next(p for p in self._paged(self.ctl.list_policy_engines, "policyEngines")
                      if p["name"] == self.engine_name)
        self.engine_id, self.engine_arn = pe["policyEngineId"], pe["policyEngineArn"]
        while True:
            g = self.ctl.get_policy_engine(policyEngineId=self.engine_id)
            if g["status"] == "ACTIVE":
                break
            if g["status"] not in ("CREATING", "UPDATING") or time.time() - t0 > timeout:
                raise RuntimeError(f"policy engine {self.engine_id} is {g['status']}: {g.get('statusReasons')}")
            time.sleep(1)
        self.timings["engine_active_s"] = round(time.time() - t0, 1)
        for p in self._paged(self.ctl.list_policies, "policies", policyEngineId=self.engine_id):   # adopt on re-run
            self.policy_ids[p["name"].removesuffix(f"_{self.suffix}")] = p["policyId"]
        return self.engine_arn

    # ---------------------------------------------------------------- policies
    def policy_name(self, name: str) -> str:
        full = f"{name}_{self.suffix}"
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", full):
            raise ValueError(f"policy name {full!r}: letters, digits and '_' only, <= 48 chars (shorten {name!r})")
        return full

    def add_policy(self, name: str, cedar: str | None = None, *, validation: str = "FAIL_ON_ANY_FINDINGS",
                   enforcement: str = "ACTIVE", description: str | None = None, kind: str = "cedar",
                   generated: dict | None = None, timeout: float = 90) -> dict:
        """Create one policy and wait for a terminal status. Returns {name, status, policyId, reasons, seconds}. Does
        NOT raise on CREATE_FAILED / REJECTED — the validator's findings are teaching material (§4.2). status is one of
        ACTIVE | CREATE_FAILED | REJECTED (synchronous ValidationException) | adopted."""
        if name in self.policy_ids:
            return {"name": name, "status": "ACTIVE", "policyId": self.policy_ids[name], "reasons": [], "adopted": True}
        definition = {"policyGeneration": generated} if generated else {kind: {"statement": cedar}}
        t0 = time.time()
        kw = dict(policyEngineId=self.engine_id, name=self.policy_name(name), definition=definition,
                  validationMode=validation, enforcementMode=enforcement, clientToken=_token())
        if description:
            kw["description"] = description[:200]
        try:
            p = self.ctl.create_policy(**kw)
        except ClientError as e:
            return {"name": name, "status": "REJECTED", "policyId": None,
                    "reasons": [e.response["Error"].get("Message", str(e))], "seconds": round(time.time() - t0, 1)}
        pid = p["policyId"]
        self.policy_ids[name] = pid
        while True:
            g = self.ctl.get_policy(policyEngineId=self.engine_id, policyId=pid)
            if g["status"] not in ("CREATING", "UPDATING") or time.time() - t0 > timeout:
                break
            time.sleep(0.5)
        if g["status"] != "ACTIVE":
            self._delete_policy(name)                       # a CREATE_FAILED policy still occupies the name
        return {"name": name, "status": g["status"], "policyId": pid if g["status"] == "ACTIVE" else None,
                "reasons": g.get("statusReasons") or [], "seconds": round(time.time() - t0, 1)}

    @staticmethod
    def _norm(statement: str | None) -> str:
        return " ".join(str(statement or "").split())

    def _sync_adopted(self, name: str, spec: dict, timeout: float = 90) -> dict:
        """An adopted policy (a re-run, or a shared dev stack) whose statement differs from `spec` gets UpdatePolicy
        with the new statement: the policy id stays the same (other kernels' cached ids keep working) and there is no
        window without the policy, unlike delete + create. -> {} when unchanged, else {updated, status, reasons}.
        Never raises: a rejected update keeps the live policy and returns its reasons."""
        kind, statement = spec.get("kind", "cedar"), spec.get("cedar")
        if not statement or not self.engine_id:
            return {}
        pid = self.policy_ids[name]
        try:
            live = self.ctl.get_policy(policyEngineId=self.engine_id, policyId=pid)
            if self._norm(((live.get("definition") or {}).get(kind) or {}).get("statement")) == self._norm(statement):
                return {}
            if live.get("status") in ("CREATING", "UPDATING"):   # another kernel is updating it right now
                t0 = time.time()
                while live.get("status") in ("CREATING", "UPDATING") and time.time() - t0 < timeout:
                    time.sleep(1)
                    live = self.ctl.get_policy(policyEngineId=self.engine_id, policyId=pid)
                if self._norm(((live.get("definition") or {}).get(kind) or {}).get("statement")) == self._norm(statement):
                    return {"updated": True, "status": live["status"], "reasons": live.get("statusReasons") or []}
            kw = dict(policyEngineId=self.engine_id, policyId=pid, definition={kind: {"statement": statement}},
                      validationMode=spec.get("validation", "FAIL_ON_ANY_FINDINGS"))
            if spec.get("description"):
                kw["description"] = {"optionalValue": spec["description"][:200]}   # UpdatePolicy: a structure
            self.ctl.update_policy(**kw)
            t0 = time.time()
            while True:
                g = self.ctl.get_policy(policyEngineId=self.engine_id, policyId=pid)
                if g["status"] not in ("CREATING", "UPDATING") or time.time() - t0 > timeout:
                    break
                time.sleep(0.5)
            return {"updated": True, "status": g["status"], "reasons": g.get("statusReasons") or []}
        except (ClientError, BotoCoreError) as e:
            why = e.response["Error"].get("Message", str(e)) if isinstance(e, ClientError) else str(e)
            return {"updated": False, "status": "ACTIVE", "reasons": [f"update of the adopted policy failed: {why}"]}

    def add_generated(self, name: str, asset: dict, **kw) -> dict:
        return self.add_policy(name, generated={"policyGenerationId": asset["generationId"],
                                                "policyGenerationAssetId": asset["assetId"]}, **kw)

    def add_many(self, policy_set: dict[str, dict], *, timeout: float = 120) -> dict[str, dict]:
        """Create a whole policy set. CreatePolicy is async, so the calls are submitted concurrently and then each is
        polled to a terminal status. -> {name: {status, policyId, reasons, seconds}}."""
        t0 = time.time()
        submitted: dict[str, dict] = {}                     # name -> {policyId} or {rejected}

        def submit(name: str, spec: dict):
            if name in self.policy_ids:
                return name, {"adopted": True, "policyId": self.policy_ids[name], **self._sync_adopted(name, spec)}
            definition = {spec.get("kind", "cedar"): {"statement": spec["cedar"]}}
            kw = dict(policyEngineId=self.engine_id, name=self.policy_name(name), definition=definition,
                      validationMode=spec.get("validation", "FAIL_ON_ANY_FINDINGS"),
                      enforcementMode=spec.get("enforcement", "ACTIVE"), clientToken=_token())
            if spec.get("description"):
                kw["description"] = spec["description"][:200]
            try:
                p = self.ctl.create_policy(**kw)
                return name, {"policyId": p["policyId"]}
            except ClientError as e:
                return name, {"rejected": e.response["Error"].get("Message", str(e))}

        with _cf.ThreadPoolExecutor(max_workers=min(8, len(policy_set) or 1)) as pool:
            for name, sub in pool.map(lambda kv: submit(*kv), policy_set.items()):
                submitted[name] = sub
                if sub.get("policyId"):
                    self.policy_ids[name] = sub["policyId"]

        out: dict[str, dict] = {}
        for name, sub in submitted.items():
            if sub.get("adopted"):
                out[name] = {"name": name, "status": sub.get("status", "ACTIVE"), "policyId": sub["policyId"],
                             "reasons": sub.get("reasons", []), "adopted": True, "updated": sub.get("updated", False)}
                continue
            if sub.get("rejected"):
                out[name] = {"name": name, "status": "REJECTED", "policyId": None, "reasons": [sub["rejected"]]}
                continue
            pid, t1 = sub["policyId"], time.time()
            while True:
                g = self.ctl.get_policy(policyEngineId=self.engine_id, policyId=pid)
                if g["status"] not in ("CREATING", "UPDATING") or time.time() - t1 > timeout:
                    break
                time.sleep(0.5)
            if g["status"] != "ACTIVE":
                self._delete_policy(name)
            out[name] = {"name": name, "status": g["status"],
                         "policyId": pid if g["status"] == "ACTIVE" else None,
                         "reasons": g.get("statusReasons") or [], "seconds": round(time.time() - t1, 1)}
        self.timings["add_many_s"] = round(time.time() - t0, 1)
        return out

    def set_enforcement(self, name: str, enforcement: str, *, validation: str | None = None,
                        timeout: float = 90) -> float:
        """Per-policy ACTIVE | LOG_ONLY (shadow mode) without deleting it. UpdatePolicy re-runs validation, so a forbid
        needs validationMode=IGNORE_ALL_FINDINGS (otherwise UPDATE_FAILED 'Overly Restrictive' and the mode silently
        stays; observed in us-east-1 on 2026-09-27). validation=None picks IGNORE_ALL_FINDINGS for a forbid and
        FAIL_ON_ANY_FINDINGS otherwise. -> seconds; raises RuntimeError unless the policy ends ACTIVE in `enforcement`."""
        t0, pid = time.time(), self.policy_ids[name]
        if validation is None:
            live = self.ctl.get_policy(policyEngineId=self.engine_id, policyId=pid)
            stmt = " ".join(str(d.get("statement") or "") for d in (live.get("definition") or {}).values()
                            if isinstance(d, dict))
            validation = "IGNORE_ALL_FINDINGS" if stmt.lstrip().startswith("forbid") else "FAIL_ON_ANY_FINDINGS"
        self.ctl.update_policy(policyEngineId=self.engine_id, policyId=pid, enforcementMode=enforcement,
                               validationMode=validation)
        while (g := self.ctl.get_policy(policyEngineId=self.engine_id, policyId=pid))["status"] in ("CREATING",
                                                                                                  "UPDATING"):
            if time.time() - t0 > timeout:
                break
            time.sleep(0.5)
        if g["status"] != "ACTIVE" or g.get("enforcementMode") != enforcement:
            why = "; ".join(str(r).split(":")[0] for r in g.get("statusReasons") or [])   # no ARNs (account id)
            raise RuntimeError(f"UpdatePolicy {name}: {g['status']}, enforcementMode {g.get('enforcementMode')} "
                               f"({why or validation})")
        return round(time.time() - t0, 1)

    def remove_policy(self, name: str) -> bool:
        return self._delete_policy(name)

    def _delete_policy(self, name: str, timeout: float = 90) -> bool:
        pid = self.policy_ids.pop(name, None)
        if not pid:
            return True
        try:
            self.ctl.delete_policy(policyEngineId=self.engine_id, policyId=pid)
        except ClientError as e:
            if _code(e) != "ResourceNotFoundException":
                raise
        return self._gone(lambda: self.ctl.get_policy(policyEngineId=self.engine_id, policyId=pid), timeout)

    def policies(self) -> list[dict]:
        return self._paged(self.ctl.list_policies, "policies", policyEngineId=self.engine_id)

    # ---------------------------------------------------------------- attach / mode
    def set_mode(self, gateway_id: str, mode: str | None, *, max_wait: float = 90) -> float:
        """Attach/detach the engine to a gateway. mode in {"LOG_ONLY","ENFORCE",None}. Re-sends the whole gateway
        config (UpdateGateway requires it) and retries the first attach through IAM propagation (the gateway role's
        GetPolicyEngine grant can be seconds behind). -> seconds until the gateway is READY again."""
        t0, deadline = time.time(), time.time() + max_wait
        first_reason = None
        g = self._wait_gateway(gateway_id)
        have = g.get("policyEngineConfiguration") or {}
        if g["status"] == "READY" and ((not mode and not have) or
                                       (mode and have.get("arn") == self.engine_arn and have.get("mode") == mode)):
            # Already in the requested state (e.g. §0 adopting a dev stack): no UpdateGateway, nothing to race.
            self.gateway_id, self.gateway_arn = gateway_id, g.get("gatewayArn", self.gateway_arn)
            (self.attached.add if mode else self.attached.discard)(gateway_id)
            return round(time.time() - t0, 1)
        while True:
            g = self.ctl.get_gateway(gatewayIdentifier=gateway_id)
            self.gateway_id, self.gateway_arn = gateway_id, g.get("gatewayArn", self.gateway_arn)
            kw = {k: g[k] for k in ("name", "roleArn", "authorizerType", "protocolType", "authorizerConfiguration",
                                    "protocolConfiguration", "exceptionLevel", "description",
                                    "interceptorConfigurations", "kmsKeyArn") if g.get(k)}
            if mode:
                kw["policyEngineConfiguration"] = {"arn": self.engine_arn, "mode": mode}
            self.ctl.update_gateway(gatewayIdentifier=gateway_id, **kw)
            g = self._wait_gateway(gateway_id)
            if g["status"] == "READY":
                if mode:
                    self.attached.add(gateway_id)
                else:
                    self.attached.discard(gateway_id)
                return round(time.time() - t0, 1)
            reason = " ".join(g.get("statusReasons") or [])
            first_reason = first_reason or reason
            if ("access" in reason.lower() or "getpolicyengine" in reason.lower()) and time.time() < deadline:
                time.sleep(10)                              # IAM propagation of the gateway role's GetPolicyEngine grant
                continue
            raise RuntimeError(f"attach {gateway_id} -> {g['status']}: {reason or first_reason}")

    def attach(self, gateway_id: str, mode: str = "ENFORCE", **kw) -> float:
        return self.set_mode(gateway_id, mode, **kw)

    def _wait_gateway(self, gateway_id: str, timeout: float = 60) -> dict:
        deadline = time.time() + timeout
        while True:
            g = self.ctl.get_gateway(gatewayIdentifier=gateway_id)
            if g["status"] != "UPDATING":
                return g
            if time.time() > deadline:
                raise TimeoutError(f"gateway {gateway_id} still UPDATING after {timeout:.0f} s")
            time.sleep(0.5)

    # ---------------------------------------------------------------- NL2Cedar
    def generate(self, text: str, name: str | None = None, *, gateway_arn: str | None = None,
                 timeout: float = 90) -> list[dict]:
        """StartPolicyGeneration -> [{generationId, assetId, fragment, kind, statement, findings}]. 12-22 s. An empty
        statement with an INVALID finding means the text was not translatable. Names get the run suffix (account-unique;
        there is no DeletePolicyGeneration, so a name stays taken until the asset expires in 7 days)."""
        arn = gateway_arn or self.gateway_arn
        if not arn:
            raise ValueError("generate() needs a gateway ARN (pass gateway_arn= or attach()/start() first)")
        gen_name = f"{name or 'gen_' + uuid.uuid4().hex[:8]}_{self.suffix}"[:48]
        g = self.ctl.start_policy_generation(policyEngineId=self.engine_id, name=gen_name, resource={"arn": arn},
                                             content={"rawText": text}, clientToken=_token())
        gid, t0 = g["policyGenerationId"], time.time()
        while True:
            g = self.ctl.get_policy_generation(policyEngineId=self.engine_id, policyGenerationId=gid)
            if g["status"] != "GENERATING" or time.time() - t0 > timeout:
                break
            time.sleep(1)
        self.timings[f"generate:{gen_name}"] = round(time.time() - t0, 1)
        if g["status"] != "GENERATED":
            raise RuntimeError(f"policy generation {gid} is {g['status']}: {g.get('statusReasons')}")
        out = []
        for a in self._paged(self.ctl.list_policy_generation_assets, "policyGenerationAssets",
                             policyEngineId=self.engine_id, policyGenerationId=gid):
            d = a.get("definition") or {}
            k = next(iter(d), None)
            out.append({"generationId": gid, "assetId": a["policyGenerationAssetId"],
                        "fragment": a.get("rawTextFragment"), "kind": k,
                        "statement": (d.get(k) or {}).get("statement", "") if k else "",
                        "findings": [(f.get("type"), f.get("description")) for f in a.get("findings") or []]})
        return out

    # ---------------------------------------------------------------- background start
    def start(self, gateway_arn: str, gateway_id: str, policy_set: dict[str, dict], mode: str = "ENFORCE"
              ) -> "PolicyDeployment":
        """Create the engine, add every policy in `policy_set`, and attach to `gateway_id` in `mode` — in a background
        thread, so §0 can build the whole control plane while class starts. Never raises from the thread."""
        if self._thread and self._thread.is_alive():
            return self
        self.gateway_arn, self.gateway_id = gateway_arn, gateway_id
        self._t0, self.events, self.error = time.time(), [], None
        self._done.clear()
        self._policy_set, self._mode = policy_set, mode
        self._log("STARTING", f"engine {self.engine_name} + {len(policy_set)} policies -> {gateway_id} ({mode})")
        self._thread = threading.Thread(target=self._run, name=f"policy-{self.engine_name}", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            self.create_engine()
            self._log("ADDING_POLICIES", f"{len(self._policy_set)} policies")
            self._add_results = self.add_many(self._policy_set)
            failed = {n: r["reasons"] for n, r in self._add_results.items() if r["status"] not in ("ACTIVE",)}
            if failed:
                self._log("POLICY_FINDINGS", f"{len(failed)} policy(ies) not ACTIVE: {sorted(failed)}")
            self._log("ATTACHING", f"{self.gateway_id} ({self._mode})")
            self.set_mode(self.gateway_id, self._mode)
            self._log("READY", f"engine {self.engine_id} ({self._mode}) on {self.gateway_id}")
        except (ClientError, BotoCoreError, RuntimeError, TimeoutError, ValueError, KeyError) as e:
            self.error = f"{type(e).__name__}: {e}"
            self._log("FAILED", self.error)
        except BaseException as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self._log("FAILED", self.error)
        finally:
            self._done.set()

    def wait(self, timeout: float = 120) -> bool:
        if self._thread is not None:
            self._done.wait(timeout)
        return self.status == "READY"

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    def progress(self) -> list[tuple[float, str, str]]:
        return list(self.events)

    def _log(self, step: str, msg: str) -> None:
        self.status = step
        self.events.append((round(time.time() - self._t0, 1), step, msg))

    # ---------------------------------------------------------------- teardown
    def teardown(self) -> dict:
        """Detach from every gateway this engine is attached to, delete all policies, then delete the engine. By id."""
        t0, deleted, problems = time.time(), [], []
        for gid in list(self.attached):
            try:
                self.set_mode(gid, None)
                deleted.append(f"detach {gid}")
            except (ClientError, RuntimeError) as e:
                if not (isinstance(e, ClientError) and _code(e) == "ResourceNotFoundException"):
                    problems.append(f"detach {gid}: {e}")
        if self.engine_id:
            try:
                for p in self.policies():
                    self.policy_ids.setdefault(p["name"].removesuffix(f"_{self.suffix}"), p["policyId"])
            except ClientError as e:
                if _code(e) != "ResourceNotFoundException":
                    problems.append(f"list policies: {_code(e)}")
            for name in list(self.policy_ids):
                try:
                    (deleted if self._delete_policy(name) else problems).append(f"policy {name}")
                except ClientError as e:
                    problems.append(f"policy {name}: {_code(e)}")
            try:
                self.ctl.delete_policy_engine(policyEngineId=self.engine_id)
            except ClientError as e:
                if _code(e) != "ResourceNotFoundException":
                    problems.append(f"engine: {_code(e)}: {e}")
            if self._gone(lambda: self.ctl.get_policy_engine(policyEngineId=self.engine_id), 120):
                deleted.append(f"policy engine {self.engine_id}")
            else:
                problems.append(f"policy engine {self.engine_id}: still there after 120 s")
        return {"deleted": deleted, "problems": problems, "seconds": round(time.time() - t0, 1)}

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _paged(fn, key: str, **kw) -> list[dict]:
        out, tok = [], None
        while True:
            r = fn(**kw, **({"nextToken": tok} if tok else {}))
            out += r.get(key, [])
            tok = r.get("nextToken")
            if not tok:
                return out

    @staticmethod
    def _gone(check, timeout: float, poll: float = 1) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                check()
            except ClientError as e:
                if _code(e) == "ResourceNotFoundException":
                    return True
                raise
            time.sleep(poll)
        return False


# ======================================================================================
# Decision metrics (CloudWatch, on by default — no Transaction Search needed)
# ======================================================================================
def decision_metrics(session: boto3.Session, engine_id: str, since: datetime, until: datetime | None = None, *,
                     max_wait: float = 0, poll: float = 6, mode: str | None = None) -> dict:
    """Allow/Deny decision counts for one policy engine from AWS/Bedrock-AgentCore (AllowDecisions / DenyDecisions,
    OperationName=AuthorizeAction). May be empty for ~12 s-2 min after the calls, so max_wait polls until non-zero.
    mode="LOG_ONLY" | "ENFORCE" reads the per-mode series (dimension set PolicyEngine + OperationName + Mode, observed
    in us-east-1 on 2026-09-27): it proves LOG_ONLY evaluated calls it did not block. -> {"allow": int, "deny": int}."""
    cw = session.client("cloudwatch", config=_CFG)
    end = until or (datetime.now(timezone.utc) + timedelta(minutes=1))
    dims = [{"Name": "PolicyEngine", "Value": engine_id}, {"Name": "OperationName", "Value": "AuthorizeAction"}]
    if mode:
        dims.append({"Name": "Mode", "Value": mode})

    def read() -> dict:
        out = {}
        for metric, key in (("AllowDecisions", "allow"), ("DenyDecisions", "deny")):
            r = cw.get_metric_statistics(
                Namespace="AWS/Bedrock-AgentCore", MetricName=metric,
                Dimensions=dims,
                StartTime=since, EndTime=end, Period=60, Statistics=["Sum"])
            out[key] = int(sum(p["Sum"] for p in r["Datapoints"]))
        return out

    deadline = time.time() + max_wait
    while True:
        m = read()
        if (m["allow"] + m["deny"]) > 0 or time.time() >= deadline:
            return m
        time.sleep(poll)


# ======================================================================================
# Finding and deleting a run's policy resources (teardown_m03.py, safety net)
# ======================================================================================
def engine_name(run_id: str, prefix: str = DEFAULT_ENGINE_PREFIX) -> str:
    return f"{prefix}_{run_suffix(run_id)}"[:48]


def _paged(fn, key: str, **kw) -> list[dict]:
    out, tok = [], None
    while True:
        r = fn(**kw, **({"nextToken": tok} if tok else {}))
        out += r.get(key, [])
        tok = r.get("nextToken")
        if not tok:
            return out


def find_run_resources(session: boto3.Session, run_id: str, prefix: str = DEFAULT_ENGINE_PREFIX) -> dict[str, list]:
    """Read-only: this run's policy engine(s) and their policy counts, by exact name."""
    ctl = session.client("bedrock-agentcore-control", config=_CFG)
    name = engine_name(run_id, prefix)
    engines = []
    for e in _paged(ctl.list_policy_engines, "policyEngines"):
        if e["name"] == name:
            try:
                n = len(_paged(ctl.list_policies, "policies", policyEngineId=e["policyEngineId"]))
            except ClientError:
                n = 0
            engines.append({"policyEngineId": e["policyEngineId"], "status": e["status"], "policies": n,
                            "arn": e.get("policyEngineArn")})
    return {"policy_engines": engines}


def delete_run_resources(session: boto3.Session, run_id: str, prefix: str = DEFAULT_ENGINE_PREFIX) -> dict:
    """Delete this run's policy engine(s): detach from any gateway that references the engine, delete every policy, then
    the engine, verified by id. Touches nothing whose name does not carry this run id (generation records cannot be
    deleted and expire in 7 days)."""
    t0, deleted, problems = time.time(), [], []
    ctl = session.client("bedrock-agentcore-control", config=_CFG)
    found = find_run_resources(session, run_id, prefix)["policy_engines"]
    engine_arns = {e["arn"] for e in found if e.get("arn")}
    # detach from any gateway that has one of these engines attached
    if engine_arns:
        for g in _list_gateways(ctl):
            try:
                gg = ctl.get_gateway(gatewayIdentifier=g["gatewayId"])
            except ClientError:
                continue
            if (gg.get("policyEngineConfiguration") or {}).get("arn") in engine_arns:
                kw = {k: gg[k] for k in ("name", "roleArn", "authorizerType", "protocolType",
                                         "authorizerConfiguration", "protocolConfiguration", "exceptionLevel",
                                         "description", "interceptorConfigurations", "kmsKeyArn") if gg.get(k)}
                try:
                    ctl.update_gateway(gatewayIdentifier=g["gatewayId"], **kw)
                    deleted.append(f"detach gateway {g['gatewayId']}")
                except ClientError as e:
                    problems.append(f"detach {g['gatewayId']}: {_code(e)}")
    for e in found:
        eid = e["policyEngineId"]
        for p in _paged(ctl.list_policies, "policies", policyEngineId=eid):
            try:
                ctl.delete_policy(policyEngineId=eid, policyId=p["policyId"])
            except ClientError as ex:
                if _code(ex) != "ResourceNotFoundException":
                    problems.append(f"policy {p['name']}: {_code(ex)}")
        # wait for policies to clear, then delete the engine (DeletePolicyEngine refuses while any remain)
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                ctl.delete_policy_engine(policyEngineId=eid)
                break
            except ClientError as ex:
                if _code(ex) == "ResourceNotFoundException":
                    break
                if _code(ex) != "ConflictException" or time.time() > deadline:
                    problems.append(f"engine {eid}: {_code(ex)}")
                    break
                time.sleep(3)
        try:
            ctl.get_policy_engine(policyEngineId=eid)
            problems.append(f"policy engine {eid}: still there")
        except ClientError as ex:
            if _code(ex) == "ResourceNotFoundException":
                deleted.append(f"policy engine {eid}")
    return {"deleted": deleted, "problems": problems, "seconds": round(time.time() - t0, 1)}

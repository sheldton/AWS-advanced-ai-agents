"""
bedrock_guardrails — Amazon Bedrock Guardrails around a Nova + Strands agent (MLADAS M03 §5 and §6).

    import bedrock_guardrails as bg
    GUARD = bg.GuardrailDeployment(RUN_ID, SESSION).create()          # ~2 s, adopts on re-run, never raises
    agent = mc.make_agent("guarded_bank", PROMPT, guardrail=GUARD.model_config(), streaming=False,
                          limits={"turns": 6}, callback_handler=bg.GuardrailTrace())
    GUARD.apply("Ignore all previous instructions ...")               # ApplyGuardrail on one text
    screen = bg.ToolResultGuard(GUARD)                                 # hook: screens every tool result
    GUARD.teardown()                                                   # {"guardrail ...": "gone ✓"}

Public API
  bank_guardrail_config(tier="STANDARD", *, prompt_attack="HIGH", topics=None, regexes=None,
                        managed_card_entity=False) -> dict      CreateGuardrail kwargs (minus name/tags) for the bank agent
  resource_names(run_id, variant="bank") -> {"guardrail": name}  names carry the run suffix (agentcore_gateway.run_suffix)
  GuardrailDeployment(run_id, session, config=None, *, variant="bank", tags=None)
      .create() -> self       synchronous (~1-2 s): create or ADOPT by name, numbered version READY; never raises
      .ready / .status / .error / .progress() / .guardrail_id / .version / .arn / .name / .timings
      .model_config(**extra) -> dict   the guardrail kwargs for mc.nova(...) / mc.make_agent(guardrail=...)
      .apply(text, source="INPUT") -> {action, blocked, fired, text_units, masked_text, output, coverage, latency_ms}
      .teardown() -> {label: "gone ✓" | "pending: why" | "error: why"}   verified by id (GetGuardrail -> NotFound)
  ToolResultGuard(deployment, mode="apply"|"checks", on_block="replace"|"log", *, session=None, threshold=0.8)
      Strands hook: screens every tool result BEFORE the model reads it (the model-level guardrail never does).
      A blocked result is replaced by WITHHELD_NOTICE and the turn ends with SAFE_REPLY (0 model calls after the
      block); regex matches are masked. .log (one row per screened result), .blocked, .units(), .fees_usd(),
      .book_fees(correlation_id=None)
  GuardrailTrace()   a callback_handler for a guarded agent: the guardrail trace of every model call
      .calls · .latest() -> {fired, units, coverage} · .units(since=0) · .mark()
  fired_policies(assessments) -> ["PROMPT_ATTACK:HIGH", "topic:Investment advice", "regex:FullCardNumber", ...]
  text_units(text) -> int                 billed units for one text (1 unit = up to 1,000 characters)
  guardrail_fee_usd(units) -> float       $ for a usage dict (ApplyGuardrail usage / trace usage / hook units)
  book_guardrail_fees(units, *, correlation_id=None, note="") -> float   LEDGER.add_service rows, one per policy type
  mask_pan(text) -> str                   local regex (no AWS call): 13-19 digit card numbers -> ****-****-****-4417
  ToolResultFilter(detector=None)         LOCAL stand-in for the prompt-attack screen (regex; ends the turn too)
  PanMasker()                             LOCAL stand-in for PAN anonymization on tool results
  find_run_resources(session, run_id) / delete_run_resources(session, run_id)   by name only (teardown_m03.py)
  Constants: BLOCKED_INPUT_MESSAGE, BLOCKED_OUTPUT_MESSAGE, INVESTMENT_TOPIC, PAN_REGEX, ACCOUNT_REGEX,
             WITHHELD_NOTICE, SAFE_REPLY, GUARDRAIL_UNIT_PRICES

Verified in a test account in us-east-1, 2026-09-27 (boto3 1.43.103, strands-agents 1.57.1, Nova 2 Lite). Gotchas baked in:
STANDARD tier needs crossRegionConfig us.guardrail.v1:0 · PROMPT_ATTACK needs outputStrength NONE · PII entries need
explicit inputAction/outputAction/inputEnabled/outputEnabled (legacy `action` alone skipped INPUT) · the managed
CREDIT_DEBIT_CARD_NUMBER entity also fires on "card ending 4417", so regexes are the default · a withheld tool result
WITHOUT end_turn made Nova 2 Lite re-call the tool 319 times, so the hook ends the turn.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import uuid
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from strands.hooks import AfterToolCallEvent, AfterToolsEvent, HookProvider, HookRegistry

import mladas_common as mc
from agentcore_gateway import run_suffix

PREFIX = "mladas-m03-gr"
_CLIENT_CONFIG = Config(retries={"total_max_attempts": 5, "mode": "standard"}, read_timeout=60)
_VARIANT = re.compile(r"^[a-z0-9]{1,10}$")

# ======================================================================================
# The bank guardrail
# ======================================================================================
# One message per direction for ALL policies (a card-specific text would also show for jailbreaks and topics).
BLOCKED_INPUT_MESSAGE = ("I'm sorry, I can't help with that request. I can help with your AnyCompany Bank accounts, "
                         "cards and payments.")
BLOCKED_OUTPUT_MESSAGE = ("I'm sorry, I can't share that here. An AnyCompany Bank specialist can help you with "
                          "this request.")

INVESTMENT_TOPIC = {
    "name": "Investment advice",
    "definition": ("Recommendations or opinions about buying, selling or holding stocks, bonds, funds, crypto or "
                   "other investments, or about how to invest savings for returns."),
    "examples": ["Should I put my savings into Octank stock?", "Which ETF will give me the best return this year?",
                 "Is now a good time to buy Bitcoin?", "How should I invest my 401k?",
                 "Would you buy shares of AnyCompany Bank?"],
    "type": "DENY",
}
# Regexes, not the managed CREDIT_DEBIT_CARD_NUMBER entity: the managed entity also masks/blocks LAST-4 mentions
# ("card ending 4417", "card_last4": "4417"), which breaks a bank support agent. Regex-only PII is billed at $0.
PAN_REGEX = {"name": "FullCardNumber", "description": "13-19 digit card number (PAN), spaces or dashes allowed",
             "pattern": r"\b(?:\d[ -]?){12,18}\d\b"}
ACCOUNT_REGEX = {"name": "AnyCompanyAccountNumber", "description": "AnyCompany deposit account id, e.g. CHK-0000-00",
                 "pattern": r"\b(?:CHK|SAV)-\d{4}-\d{2}\b"}
MANAGED_CARD_ENTITY = {"type": "CREDIT_DEBIT_CARD_NUMBER"}      # §5.4 only: shows the last-4 trap


def _both(entry: dict, action: str) -> dict:
    """Set input AND output explicitly: with only the legacy `action`, INPUT was not evaluated (0/8, observed)."""
    return {**entry, "action": action, "inputAction": action, "outputAction": action,
            "inputEnabled": True, "outputEnabled": True}


def bank_guardrail_config(tier: str = "STANDARD", *, prompt_attack: str = "HIGH", topics: list | None = None,
                          regexes: list | None = None, managed_card_entity: bool = False) -> dict:
    """CreateGuardrail kwargs (minus name and tags) for the AnyCompany Bank support agent:
    PROMPT_ATTACK on input (output must be NONE), the "Investment advice" denied topic, and PAN + account-id regexes
    with ANONYMIZE in and out. managed_card_entity=True adds the managed CREDIT_DEBIT_CARD_NUMBER entity (ANONYMIZE)
    for §5.4's demonstration that it also masks "card ending 4417"."""
    if tier not in ("STANDARD", "CLASSIC"):
        raise ValueError("tier must be 'STANDARD' or 'CLASSIC'")
    sensitive: dict[str, Any] = {"regexesConfig": [_both(r, "ANONYMIZE") for r in (regexes or [PAN_REGEX, ACCOUNT_REGEX])]}
    if managed_card_entity:
        sensitive["piiEntitiesConfig"] = [_both(MANAGED_CARD_ENTITY, "ANONYMIZE")]
    cfg: dict[str, Any] = dict(
        description="MLADAS M03: AnyCompany Bank support agent guardrail",
        contentPolicyConfig={"filtersConfig": [{"type": "PROMPT_ATTACK", "inputStrength": prompt_attack,
                                                "outputStrength": "NONE"}],
                             "tierConfig": {"tierName": tier}},
        topicPolicyConfig={"topicsConfig": list(topics or [INVESTMENT_TOPIC]), "tierConfig": {"tierName": tier}},
        sensitiveInformationPolicyConfig=sensitive,
        blockedInputMessaging=BLOCKED_INPUT_MESSAGE, blockedOutputsMessaging=BLOCKED_OUTPUT_MESSAGE,
    )
    if tier == "STANDARD":                   # STANDARD tier fails without a cross-Region guardrail profile
        cfg["crossRegionConfig"] = {"guardrailProfileIdentifier": "us.guardrail.v1:0"}
    return cfg


def _config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:8]


def resource_names(run_id: str, variant: str = "bank") -> dict[str, str]:
    """{"guardrail": "mladas-m03-gr-<variant>-<run suffix>"} (guardrail names: [0-9a-zA-Z-_]{1,50})."""
    if not _VARIANT.match(variant):
        raise ValueError("variant must be 1-10 lowercase letters/digits, e.g. 'bank' or 'card'")
    name = f"{PREFIX}-{variant}-{run_suffix(run_id)}"
    if len(name) > 50:
        raise ValueError(f"guardrail name {name!r} is longer than 50 characters: use a shorter run id")
    return {"guardrail": name}


def _code(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "")


def _tags(run_id: str, extra: dict | None) -> list[dict]:
    tags = {"project": "mladas", "module": "M03", "run_id": run_id, **(extra or {})}
    return [{"key": k, "value": str(v)} for k, v in tags.items()]


# ======================================================================================
# Deployment
# ======================================================================================
class GuardrailDeployment:
    """Create (or adopt) one Bedrock guardrail for a notebook run, publish a numbered version, tear it down.

    create() is synchronous (1.1-2.2 s measured) and never raises: check .ready, .status and .error.
    Re-run safe: a guardrail with this run's name is adopted; if its latest version was built from a different
    config (a builder changed bank_guardrail_config), the DRAFT is updated and a new version published."""

    def __init__(self, run_id: str, session, config: dict | None = None, *, variant: str = "bank",
                 tags: dict | None = None):
        self.run_id, self.session, self.variant = run_id, session, variant
        self.name = resource_names(run_id, variant)["guardrail"]
        self.config = dict(config or bank_guardrail_config())
        self.config_hash = _config_hash(self.config)
        self.tags = _tags(run_id, tags)
        self.bedrock = session.client("bedrock", config=_CLIENT_CONFIG)
        self.runtime = session.client("bedrock-runtime", config=_CLIENT_CONFIG)
        self.guardrail_id: str | None = None
        self.arn: str | None = None
        self.version: str | None = None
        self.adopted = False
        self.status = "NOT_STARTED"
        self.error: str | None = None
        self.timings: dict[str, float] = {}
        self.events: list[tuple[float, str, str]] = []
        self._t0 = time.time()

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    def progress(self) -> list[tuple[float, str, str]]:
        """[(seconds since create() started, step, message), ...]"""
        return list(self.events)

    def _log(self, step: str, msg: str) -> None:
        self.status = step
        self.events.append((round(time.time() - self._t0, 2), step, msg))

    # ---------------------------------------------------------------------------------- create / adopt
    def create(self) -> "GuardrailDeployment":
        """Create or adopt the guardrail and make a numbered version READY. Never raises."""
        if self.ready:
            return self
        self._t0, self.events, self.error = time.time(), [], None
        try:
            self._log("CREATING", f"guardrail {self.name} (config {self.config_hash})")
            try:
                r = self.bedrock.create_guardrail(name=self.name, tags=self.tags,
                                                  clientRequestToken=str(uuid.uuid4()), **self.config)
                self.guardrail_id, self.arn = r["guardrailId"], r["guardrailArn"]
                self._log("CREATING", f"created {self.guardrail_id}")
            except ClientError as e:
                if _code(e) != "ConflictException" or not self._adopt():
                    raise
                self.adopted = True
                self._log("CREATING", f"adopted existing guardrail {self.guardrail_id}")
            self._wait_ready(None, "DRAFT")
            self.timings["draft_ready_s"] = round(time.time() - self._t0, 2)
            self.version = self._matching_version()
            if self.version is None:
                if self.adopted:                    # DRAFT may hold an older config: make it match, then publish
                    self.bedrock.update_guardrail(guardrailIdentifier=self.guardrail_id, name=self.name,
                                                  **self.config)
                    self._wait_ready(None, "DRAFT (updated)")
                self.version = self.bedrock.create_guardrail_version(
                    guardrailIdentifier=self.guardrail_id, description=f"run {self.run_id} cfg {self.config_hash}",
                    clientRequestToken=str(uuid.uuid4()))["version"]
                self._log("CREATING", f"published version {self.version}")
            self._wait_ready(self.version, f"version {self.version}")
            self.timings["version_ready_s"] = round(time.time() - self._t0, 2)
            self._log("READY", f"{self.guardrail_id} version {self.version} in {self.timings['version_ready_s']} s")
        except (ClientError, BotoCoreError, RuntimeError, TimeoutError) as e:
            self.error = f"{type(e).__name__}: {e}"
            self._log("FAILED", self.error)
        except Exception as e:  # noqa: BLE001 — never kill the notebook from setup
            self.error = f"{type(e).__name__}: {e}"
            self._log("FAILED", self.error)
        return self

    def _adopt(self) -> bool:
        for page in self.bedrock.get_paginator("list_guardrails").paginate():
            for g in page["guardrails"]:
                if g["name"] == self.name:
                    self.guardrail_id, self.arn = g["id"], g["arn"]
                    return True
        return False

    def _versions(self) -> list[dict]:
        return [g for p in self.bedrock.get_paginator("list_guardrails").paginate(guardrailIdentifier=self.guardrail_id)
                for g in p["guardrails"] if g.get("version") not in (None, "DRAFT")]

    def _matching_version(self) -> str | None:
        """The newest numbered version published from THIS config (its description carries the config hash)."""
        ok = [v for v in self._versions() if f"cfg {self.config_hash}" in (v.get("description") or "")]
        return max((v["version"] for v in ok), key=int) if ok else None

    def _wait_ready(self, version: str | None, what: str, max_wait: float = 30) -> None:
        t0 = time.time()
        while True:
            kw = {"guardrailIdentifier": self.guardrail_id, **({"guardrailVersion": version} if version else {})}
            g = self.bedrock.get_guardrail(**kw)
            if g["status"] == "READY":
                self.arn = g.get("guardrailArn", self.arn)
                return
            if g["status"] == "FAILED":
                raise RuntimeError(f"guardrail {what} FAILED: {g.get('failureRecommendations') or g.get('statusReasons')}")
            if time.time() - t0 > max_wait:
                raise TimeoutError(f"guardrail {what} is {g['status']} after {max_wait:.0f} s")
            time.sleep(0.5)

    # ---------------------------------------------------------------------------------- use
    def model_config(self, **extra: Any) -> dict:
        """The guardrail kwargs for mc.nova(...) or mc.make_agent(guardrail=...): the guardrail then screens every
        Converse call (user and assistant turns, NOT the system prompt or tool results). Raises if not READY, so a
        "guarded" agent can never silently run unguarded. Useful extras: guardrail_latest_message=True."""
        if not self.ready:
            raise RuntimeError(f"guardrail {self.name} is not READY ({self.status}: {self.error})")
        return {"guardrail_id": self.guardrail_id, "guardrail_version": self.version,
                "guardrail_trace": "enabled", **extra}

    def apply(self, text: str, source: str = "INPUT") -> dict:
        """ApplyGuardrail on one text (no model call). source "INPUT" screens as a user prompt, "OUTPUT" as a reply.
        -> {action ("NONE" | "GUARDRAIL_INTERVENED"), blocked (a BLOCK fired, not just masking), fired, text_units
        (non-zero usage), masked_text (the anonymized text when masking was the only intervention, else None),
        output (the text the guardrail returns: blocked message or masked text), coverage {guarded, total},
        latency_ms}. Raises RuntimeError if the guardrail is not READY."""
        if not self.ready:
            raise RuntimeError(f"guardrail {self.name} is not READY ({self.status}: {self.error})")
        t0 = time.perf_counter()
        r = self.runtime.apply_guardrail(guardrailIdentifier=self.guardrail_id, guardrailVersion=self.version,
                                         source=source, content=[{"text": {"text": text}}], outputScope="INTERVENTIONS")
        fired = fired_policies(r.get("assessments", []))
        output = r["outputs"][0]["text"] if r.get("outputs") else None
        sip = [x for a in r.get("assessments", []) for k in ("piiEntities", "regexes")
               for x in (a.get("sensitiveInformationPolicy") or {}).get(k, [])]
        masking_only = bool(fired) and all(f.startswith(("regex:", "pii:")) for f in fired) \
            and not any(x.get("action") == "BLOCKED" for x in sip)     # a PII/regex entity set to BLOCK blocks
        intervened = r["action"] == "GUARDRAIL_INTERVENED"
        return {"action": r["action"], "blocked": intervened and not masking_only, "fired": fired,
                "text_units": {k: v for k, v in (r.get("usage") or {}).items() if v},
                "masked_text": output if (intervened and masking_only) else None, "output": output,
                "coverage": (r.get("guardrailCoverage") or {}).get("textCharacters"),
                "latency_ms": round((time.perf_counter() - t0) * 1000)}

    # ---------------------------------------------------------------------------------- teardown
    def teardown(self) -> dict[str, str]:
        """Delete the guardrail (all versions) and verify by id. -> {label: "gone ✓" | "pending: …" | "error: …"}.
        Safe to call twice; never raises."""
        if not self.guardrail_id:
            try:
                if not self._adopt():
                    return {f"guardrail {self.name}": "gone ✓ (never created)"}
            except (ClientError, BotoCoreError) as e:
                return {f"guardrail {self.name}": f"error: {type(e).__name__}: {e}"}
        out = _delete_guardrail(self.bedrock, self.guardrail_id, self.name)
        self.status = "DELETED" if all(v.startswith("gone") for v in out.values()) else "DELETE_INCOMPLETE"
        return out


def _delete_guardrail(bedrock, guardrail_id: str, name: str, max_wait: float = 30) -> dict[str, str]:
    label = f"guardrail {name} ({guardrail_id})"
    try:
        try:
            bedrock.delete_guardrail(guardrailIdentifier=guardrail_id)
        except ClientError as e:
            if _code(e) != "ResourceNotFoundException":
                raise
        deadline = time.time() + max_wait
        while True:
            try:
                g = bedrock.get_guardrail(guardrailIdentifier=guardrail_id)
            except ClientError as e:
                if _code(e) == "ResourceNotFoundException":
                    return {label: "gone ✓"}
                raise
            if time.time() > deadline:
                return {label: f"pending: still {g.get('status')} {max_wait:.0f} s after DeleteGuardrail"}
            time.sleep(1)
    except (ClientError, BotoCoreError) as e:
        return {label: f"error: {type(e).__name__}: {str(e)[:200]}"}


# ======================================================================================
# Reading what fired, and what it costs
# ======================================================================================
def fired_policies(assessments: list | dict | None) -> list[str]:
    """Compact 'what fired' list from ApplyGuardrail `assessments` or Converse-trace assessment dicts:
    "PROMPT_ATTACK:HIGH", "topic:Investment advice", "regex:FullCardNumber", "pii:CREDIT_DEBIT_CARD_NUMBER", "word:…"."""
    items = list(assessments.values()) if isinstance(assessments, dict) else list(assessments or [])
    out: list[str] = []
    for a in items:
        for f in a.get("contentPolicy", {}).get("filters", []):
            if f.get("detected", f.get("action") != "NONE"):
                out.append(f"{f['type']}:{f.get('confidence', '?')}")
        for t in a.get("topicPolicy", {}).get("topics", []):
            if t.get("detected", t.get("action") != "NONE"):
                out.append(f"topic:{t['name']}")
        for w in a.get("wordPolicy", {}).get("customWords", []) + a.get("wordPolicy", {}).get("managedWordLists", []):
            if w.get("detected", w.get("action") != "NONE"):
                out.append(f"word:{w.get('match')}")
        sip = a.get("sensitiveInformationPolicy", {})
        out += [f"pii:{p['type']}" for p in sip.get("piiEntities", []) if p.get("detected", True)]
        out += [f"regex:{p['name']}" for p in sip.get("regexes", []) if p.get("detected", True)]
    return out


def text_units(text: str) -> int:
    """Billed text units for one text: 1 unit = up to 1,000 characters (0 for an empty text)."""
    return math.ceil(len(text or "") / 1000)


# Price per text unit, by the usage key the APIs return (ApplyGuardrail `usage`, the Converse trace's
# invocationMetrics.usage, InvokeGuardrailChecks usage.*.textUnits). Values from mc.AGENTCORE_PRICES (Price List API,
# AmazonBedrock, us-east-1, 2026-09-27); image and grounding/AR units are not used in M03.
GUARDRAIL_UNIT_PRICES: dict[str, float] = {
    "contentPolicyUnits": mc.AGENTCORE_PRICES["guardrail_content_unit"],          # includes PROMPT_ATTACK
    "topicPolicyUnits": mc.AGENTCORE_PRICES["guardrail_topic_unit"],
    "sensitiveInformationPolicyUnits": mc.AGENTCORE_PRICES["guardrail_pii_unit"],  # managed PII entities
    "sensitiveInformationPolicyFreeUnits": mc.AGENTCORE_PRICES["guardrail_regex_unit"],   # regex only: $0
    "wordPolicyUnits": 0.0,
    "contextualGroundingPolicyUnits": 0.0001,
    "automatedReasoningPolicyUnits": 0.00017,
    "promptAttackCheckUnits": mc.AGENTCORE_PRICES["guardrail_checks_prompt_attack_unit"],  # InvokeGuardrailChecks
}


def guardrail_fee_usd(units: dict | None) -> float:
    """$ for a usage dict such as {"contentPolicyUnits": 1, "topicPolicyUnits": 2}. Unknown keys count $0."""
    return round(sum(n * GUARDRAIL_UNIT_PRICES.get(k, 0.0) for k, n in (units or {}).items()
                     if isinstance(n, (int, float))), 8)


def book_guardrail_fees(units: dict | None, *, correlation_id: str | None = None, note: str = "") -> float:
    """Put guardrail fees in mc.LEDGER (they are NOT in the model's token usage): one add_service row per policy
    type, service "guardrail_text_units", with the request's correlation_id. Returns the $ booked."""
    total = 0.0
    for key, n in (units or {}).items():
        if not isinstance(n, (int, float)) or n <= 0:
            continue
        price = GUARDRAIL_UNIT_PRICES.get(key, 0.0)
        mc.LEDGER.add_service("guardrail_text_units", n, price, correlation_id=correlation_id,
                              note=" ".join(x for x in (key, note) if x), provider="Bedrock Guardrails")
        total += n * price
    return round(total, 8)


def _sum_units(dicts) -> dict:
    tot: dict[str, int] = {}
    for d in dicts:
        for k, v in (d or {}).items():
            if isinstance(v, (int, float)) and v:
                tot[k] = tot.get(k, 0) + v
    return tot


class GuardrailTrace:
    """A Strands callback_handler that keeps the guardrail trace of every model call of a guarded agent.
    (Hooks do not carry the trace; the raw `event` passed to the callback handler does, streaming or not.)

        trace = bg.GuardrailTrace()
        agent = mc.make_agent(..., guardrail=GUARD.model_config(), streaming=False, callback_handler=trace)
        n = trace.mark(); agent("..."); trace.latest(), trace.units(since=n)"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, **kwargs: Any) -> None:
        ev = kwargs.get("event")
        if isinstance(ev, dict):
            g = (ev.get("metadata") or {}).get("trace", {}).get("guardrail")
            if g is not None:
                with self._lock:
                    self.calls.append(g)

    def mark(self) -> int:
        """Index to pass as since= later (the number of model calls traced so far)."""
        return len(self.calls)

    @staticmethod
    def _assessments(g: dict) -> list[dict]:
        return list((g.get("inputAssessment") or {}).values()) + \
            [a for v in (g.get("outputAssessments") or {}).values() for a in v]

    def summary(self, g: dict) -> dict:
        """{fired, units, coverage} of one model call's trace."""
        assess = self._assessments(g)
        cov = [(a.get("invocationMetrics") or {}).get("guardrailCoverage", {}).get("textCharacters")
               for a in (g.get("inputAssessment") or {}).values()]
        return {"fired": fired_policies(assess),
                "units": _sum_units((a.get("invocationMetrics") or {}).get("usage") for a in assess),
                "coverage": next((c for c in cov if c), None)}

    def latest(self) -> dict:
        """Summary of the most recent model call ({} if none was traced)."""
        return self.summary(self.calls[-1]) if self.calls else {}

    def units(self, since: int = 0) -> dict:
        """Summed text units of the model calls traced since `since` (use with book_guardrail_fees)."""
        return _sum_units(self.summary(g)["units"] for g in self.calls[since:])

    def fired(self, since: int = 0) -> list[str]:
        return [f for g in self.calls[since:] for f in self.summary(g)["fired"]]


# ======================================================================================
# Screening tool results (the model-level guardrail never reads them)
# ======================================================================================
WITHHELD_NOTICE = ("[Tool output withheld by the security screen: it contained instructions aimed at the assistant. "
                   "Do not call this tool again for this request.]")
SAFE_REPLY = ("I'm sorry, I can't complete this request automatically: information from one of our systems failed a "
              "security check. I've flagged it for an AnyCompany Bank specialist to review.")
_CHECK_CATEGORIES = ("JAILBREAK", "PROMPT_INJECTION")        # score both: some injections score only one of them
_BLOCK_FLAG = "mladas_tool_screen_blocked"


def _result_text(result: dict) -> str:
    parts = []
    for c in (result or {}).get("content", []):
        if "text" in c:
            parts.append(c["text"])
        elif "json" in c:
            parts.append(json.dumps(c["json"], ensure_ascii=False, default=str))
    return "\n".join(parts)


def _local_regex_mask(text: str) -> str:
    """Same regexes as the guardrail, applied locally (used when a topic block hid the guardrail's masked text)."""
    for r in (PAN_REGEX, ACCOUNT_REGEX):
        text = re.sub(r["pattern"], "{" + r["name"] + "}", text)
    return text


class ToolResultGuard(HookProvider):
    """Strands hook that screens every tool result BEFORE the model reads it.

    mode="apply"  : ApplyGuardrail with the deployed guardrail (source INPUT): PROMPT_ATTACK blocks; regex
                    matches (card numbers, account ids) are masked in place.
    mode="checks" : InvokeGuardrailChecks (2026, no guardrail resource): blocks when JAILBREAK or PROMPT_INJECTION
                    scores >= threshold. No masking.
    on_block="replace" : the result is replaced by WITHHELD_NOTICE (status "error") and the agent loop ends after
                    the tool batch with SAFE_REPLY (AfterToolsEvent.end_turn) — 0 model calls after the block.
                    Without end_turn Nova 2 Lite re-called the tool 319 times in one request (observed).
    on_block="log" : screen and record only (like Policy LOG_ONLY): nothing is replaced or masked.
    A screening error fails CLOSED (the result counts as blocked). Cancelled calls are not screened.
    .log rows: {tool, tool_use_id, correlation_id, action ("blocked" | "masked" | "passed" | "would_block"),
    fired, units, fee_usd, ms}. Put this hook AFTER an AuditLogger in hooks=[...] so the audit records what the
    model actually read (After* callbacks run in reverse order)."""

    def __init__(self, deployment: GuardrailDeployment | None, mode: str = "apply", on_block: str = "replace", *,
                 session=None, threshold: float = 0.8, safe_reply: str = SAFE_REPLY, notice: str = WITHHELD_NOTICE):
        if mode not in ("apply", "checks"):
            raise ValueError("mode must be 'apply' or 'checks'")
        if on_block not in ("replace", "log"):
            raise ValueError("on_block must be 'replace' or 'log'")
        if mode == "apply" and deployment is None:
            raise ValueError("mode='apply' needs the GuardrailDeployment")
        if deployment is None and session is None:
            raise ValueError("pass a GuardrailDeployment or session= (mode='checks')")
        self.deployment, self.mode, self.on_block = deployment, mode, on_block
        self.threshold, self.safe_reply, self.notice = threshold, safe_reply, notice
        self._runtime = deployment.runtime if deployment is not None else \
            session.client("bedrock-runtime", config=_CLIENT_CONFIG)
        self.log: list[dict] = []
        self._booked = 0
        self._lock = threading.Lock()

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self._screen)
        registry.add_callback(AfterToolsEvent, self._after_batch)

    # ------------------------------------------------------------------ results
    @property
    def blocked(self) -> int:
        """How many tool results were blocked (or would have been, with on_block="log")."""
        return sum(r["action"] in ("blocked", "would_block") for r in self.log)

    def units(self, since: int = 0) -> dict:
        return _sum_units(r["units"] for r in self.log[since:])

    def fees_usd(self, since: int = 0) -> float:
        return round(sum(r["fee_usd"] for r in self.log[since:]), 8)

    def book_fees(self, correlation_id: str | None = None) -> float:
        """Book the fees of screens not booked yet (LEDGER.add_service). correlation_id defaults to each row's own."""
        total = 0.0
        with self._lock:
            rows, self._booked = self.log[self._booked:], len(self.log)
        for r in rows:
            total += book_guardrail_fees(r["units"], correlation_id=correlation_id or r["correlation_id"],
                                         note=f"tool-result screen ({self.mode})")
        return round(total, 8)

    # ------------------------------------------------------------------ callbacks
    def _after_batch(self, event: AfterToolsEvent) -> None:
        if event.invocation_state.pop(_BLOCK_FLAG, False) and self.on_block == "replace":
            event.end_turn = self.safe_reply

    def _check(self, text: str) -> tuple[bool, str | None, list[str], dict]:
        """-> (blocked, masked_text or None, fired, units)"""
        if self.mode == "checks":
            r = self._runtime.invoke_guardrail_checks(
                messages=[{"role": "user", "content": [{"text": text}]}],
                checks={"promptAttack": {"categories": [{"category": c} for c in _CHECK_CATEGORIES]}})
            scores = {x["category"]: float(x.get("severityScore", x.get("confidenceScore", 0)) or 0)
                      for x in r["results"]["promptAttack"]["results"]}
            fired = [f"{k}:{v:.1f}" for k, v in scores.items() if v]
            units = {"promptAttackCheckUnits": (r.get("usage", {}).get("promptAttack") or {}).get("textUnits", 0)}
            return max(scores.values(), default=0.0) >= self.threshold, None, fired, units
        a = self.deployment.apply(text, source="INPUT")
        blocked = any(f.startswith("PROMPT_ATTACK") for f in a["fired"])
        masked = None
        if not blocked and any(f.startswith(("regex:", "pii:")) for f in a["fired"]):
            masked = a["masked_text"] if a["masked_text"] is not None else _local_regex_mask(text)
        return blocked, masked, a["fired"], a["text_units"]

    def _screen(self, event: AfterToolCallEvent) -> None:
        if event.cancel_message:                         # denied before it ran: nothing came back to screen
            return
        res = event.result or {}
        text = _result_text(res)
        if not text.strip():
            return
        t0 = time.perf_counter()
        try:
            blocked, masked, fired, units = self._check(text)
        except (ClientError, BotoCoreError, RuntimeError) as e:   # fail CLOSED: unscreened text never reaches the model
            blocked, masked, fired, units = True, None, [f"screen error {type(e).__name__}"], {}
        state = event.invocation_state or {}
        if self.on_block == "log":
            action = "would_block" if blocked else "passed"
        else:
            action = "blocked" if blocked else ("masked" if masked is not None else "passed")
        with self._lock:
            self.log.append({"tool": event.tool_use.get("name"), "tool_use_id": event.tool_use.get("toolUseId"),
                             "correlation_id": state.get("correlation_id"), "action": action, "fired": fired,
                             "units": units, "fee_usd": guardrail_fee_usd(units),
                             "ms": round((time.perf_counter() - t0) * 1000)})
        if self.on_block == "log":
            return
        if blocked:
            event.result = {"toolUseId": res.get("toolUseId", event.tool_use.get("toolUseId")), "status": "error",
                            "content": [{"text": self.notice}]}
            if event.invocation_state is not None:
                event.invocation_state[_BLOCK_FLAG] = True
        elif masked is not None:
            event.result = {**res, "content": [{"text": masked}]}


# ======================================================================================
# Local stand-ins (no AWS call) — for §6 before/after arms and fallbacks when M03_CREATE_GUARDRAIL=0
# ======================================================================================
_PAN_LOCAL = re.compile(PAN_REGEX["pattern"])


def mask_pan(text: str) -> str:
    """Mask every 13-19 digit card number (spaces/dashes allowed) locally, keeping the last 4:
    "4929 0900 0000 4417" -> "****-****-****-4417". The same pattern as the guardrail's PAN regex (no Luhn check),
    so it also masks any other 13-19 digit run."""
    return _PAN_LOCAL.sub(lambda m: "****-****-****-" + re.sub(r"\D", "", m.group())[-4:], text or "")


INSTRUCTION_PATTERNS = re.compile(
    r"(?i)\b(call|invoke|use)\s+(the\s+)?[a-z_]+\s*(\(|tool|with)|\bprocess_refund\b|\bsend_email\b|"
    r"note to (the )?(anycompany|bank|agent|assistant)|ignore (all|previous|prior)|do not (tell|mention|inform)|"
    r"pre-?authori[sz]ed|without (customer )?confirmation")


class ToolResultFilter(HookProvider):
    """LOCAL stand-in for the guardrail prompt-attack screen on tool results: a regex for instructions aimed at the
    agent. A match replaces the result with WITHHELD_NOTICE and ends the turn with SAFE_REPLY (like ToolResultGuard).
    detector(text) -> bool overrides the regex. .blocked counts the blocks."""

    def __init__(self, detector=None, *, end_turn: bool = True):
        self.detector = detector or (lambda text: bool(INSTRUCTION_PATTERNS.search(text)))
        self.end_turn = end_turn
        self.blocked = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self._after)
        registry.add_callback(AfterToolsEvent, self._after_batch)

    def _after(self, e: AfterToolCallEvent) -> None:
        if e.cancel_message:
            return
        if self.detector(_result_text(e.result)):
            self.blocked += 1
            e.result = {**e.result, "status": "error", "content": [{"text": WITHHELD_NOTICE}]}
            if e.invocation_state is not None:
                e.invocation_state[_BLOCK_FLAG] = True

    def _after_batch(self, e: AfterToolsEvent) -> None:
        if e.invocation_state.pop(_BLOCK_FLAG, False) and self.end_turn:
            e.end_turn = SAFE_REPLY


class PanMasker(HookProvider):
    """LOCAL stand-in for PAN anonymization on tool results: the model never sees a full card number
    (mask_pan keeps the last 4). .masked counts the results changed."""

    def __init__(self) -> None:
        self.masked = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self._after)

    def _after(self, e: AfterToolCallEvent) -> None:
        if e.cancel_message:
            return
        text = _result_text(e.result)
        new = mask_pan(text)
        if new != text:
            self.masked += 1
            e.result = {**e.result, "content": [{"text": new}]}


# ======================================================================================
# Finding and deleting a run's guardrails (teardown_m03.py, safety net)
# ======================================================================================
def _run_name_re(run_id: str) -> re.Pattern:
    return re.compile(rf"^{re.escape(PREFIX)}-[a-z0-9]{{1,10}}-{re.escape(run_suffix(run_id))}$")


def find_run_resources(session, run_id: str) -> dict[str, list]:
    """Read-only: this run's guardrails, by exact name pattern mladas-m03-gr-<variant>-<run suffix>.
    -> {"guardrails": [{"name", "id", "status"}]}"""
    bedrock = session.client("bedrock", config=_CLIENT_CONFIG)
    pat = _run_name_re(run_id)
    found = [{"name": g["name"], "id": g["id"], "status": g["status"]}
             for p in bedrock.get_paginator("list_guardrails").paginate() for g in p["guardrails"]
             if pat.match(g["name"])]
    return {"guardrails": found}


def delete_run_resources(session, run_id: str) -> dict[str, str]:
    """Delete this run's guardrails (all versions), verified by id. Touches nothing without this run's suffix.
    -> {label: "gone ✓" | "pending: …" | "error: …"}"""
    bedrock = session.client("bedrock", config=_CLIENT_CONFIG)
    out: dict[str, str] = {}
    try:
        for g in find_run_resources(session, run_id)["guardrails"]:
            out.update(_delete_guardrail(bedrock, g["id"], g["name"]))
    except (ClientError, BotoCoreError) as e:
        out["guardrails (list)"] = f"error: {type(e).__name__}: {e}"
    return out

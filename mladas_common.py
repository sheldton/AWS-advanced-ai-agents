"""
mladas_common — shared plumbing for the MLADAS demo notebooks (Building Advanced Agentic Systems on AWS).

What lives here (so the notebooks can focus on the agent patterns):
  * AWS session: MLADAS_AWS_PROFILE if set, else boto3's standard credential chain; region MLADAS_AWS_REGION
    (default us-east-1)
  * nova()        -> a Strands BedrockModel for an Amazon Nova model (never the Strands default!)
  * make_agent()  -> a Strands Agent wired to the cost/latency ledger
  * LEDGER        -> every agent invocation's tokens, $ and latency (for the dashboards)
  * text helpers  -> text_of(), strip_thinking(), tool_calls()
  * A2A helpers   -> A2AServerThread (serve an A2A app from a notebook), SigV4HttpxAuth,
                     runtime_a2a_agent() (call an A2A agent hosted on AgentCore Runtime)
  * tokens        -> count_tokens() (measured, Nova has no CountTokens), NOVA_BASE_OVERHEAD
  * M02 context   -> stream_timed()/converse_once() (raw Converse + TTFT), cache_tag()/cache_hits(), to_toon()/
                     md_table()/csv_text(), patch_nova_overflow(), pin_message(), meter_hidden(), tool_trace(),
                     context_rows()
  * M03 security  -> make_agent(..., guardrail=, streaming=, limits=, callback_handler=) · AGENTCORE_PRICES for
                     Policy, Identity, Cognito M2M, VPC endpoints and Guardrails text units ·
                     LEDGER.add_service(..., provider=) · tool_outcomes(agent, latest=) -> [{tool, input, status, text,
                     denied, tool_use_id}] (local and Gateway tools) · principal_of(invocation_state) ·
                     RefundPolicyGate(limit, tool=) / OwnershipGate() (local cancel_tool stand-ins for Cedar/identity)
  * M04 observability -> AGENTCORE_PRICES for Runtime vCPU/GB-hours, Evaluations (custom/derived fee; built-in token
                     prices for reference), CloudWatch high-resolution alarms, custom metrics, API requests, indexed
                     spans · HOURS_PER_MONTH (730, CloudWatch proration). The M04 helpers live in agentcore_observability.

Tested with strands-agents 1.57.1, bedrock-agentcore 1.23.1, a2a-sdk 0.3.26, boto3 1.43.x (Sept 2026).
"""

from __future__ import annotations

import contextvars
import importlib.metadata as _md
import json
import os
import re
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterable

import boto3
from botocore.config import Config

# ======================================================================================
# 1. AWS session & model catalog
# ======================================================================================
AWS_PROFILE = os.getenv("MLADAS_AWS_PROFILE") or None   # None = boto3's standard credential chain
#                                                          (env vars, AWS_PROFILE, default profile, SSO)
AWS_REGION = os.getenv("MLADAS_AWS_REGION", "us-east-1")

# Amazon models only (course rule). IDs are US cross-Region inference profiles, except the one in-Region id below.
MODELS: dict[str, str] = {
    "nova-micro":  "us.amazon.nova-micro-v1:0",    # text-only, fastest/cheapest  -> routing, classification
    "nova-lite":   "us.amazon.nova-lite-v1:0",     # multimodal v1
    "nova-pro":    "us.amazon.nova-pro-v1:0",      # strongest Nova v1
    "nova-2-lite": "us.amazon.nova-2-lite-v1:0",   # Nova 2 (GA Dec 2025): 1M ctx, optional extended reasoning
    # In-Region id: prompt-cache hits are deterministic (36/36 on 2026-09-26) because every call lands in one Region,
    # while us.* profiles hit best-effort (24/32). Use it for caching demos that must not flake. (Nova 2 Lite has no
    # in-Region on-demand option in us-east-1.)
    "nova-micro-inregion": "amazon.nova-micro-v1:0",
}

# On-demand price per 1M tokens (input, output, cache-read), us-east-1.
# Source: AWS Price List API (service AmazonBedrock), retrieved 2026-09-26. Cache writes are free for Nova.
# In-Region ids bill the same USE1 usage types as the us.* profiles (without these keys their cost was silently $0).
PRICES_PER_1M: dict[str, tuple[float, float, float]] = {
    "us.amazon.nova-micro-v1:0":      (0.035, 0.14, 0.00875),
    "us.amazon.nova-lite-v1:0":       (0.06,  0.24, 0.015),
    "us.amazon.nova-pro-v1:0":        (0.80,  3.20, 0.20),
    "us.amazon.nova-2-lite-v1:0":     (0.33,  2.75, 0.0825),
    "global.amazon.nova-2-lite-v1:0": (0.30,  2.50, 0.075),
    "amazon.nova-micro-v1:0":         (0.035, 0.14, 0.00875),
    "amazon.nova-lite-v1:0":          (0.06,  0.24, 0.015),
    "amazon.nova-pro-v1:0":           (0.80,  3.20, 0.20),
}

# AgentCore on-demand prices, us-east-1 (AWS Price List API, service AmazonBedrockAgentCore, retrieved 2026-09-26).
# Memory retrieval: the pricing page says "$0.50 per 1,000 memory record retrievals" without saying whether one
# RetrieveMemoryRecords call counts once or once per record returned; we bill per CALL (the lower bound) and say so.
AGENTCORE_PRICES: dict[str, float] = {
    "memory_retrieval": 0.0005,        # per RetrieveMemoryRecords call (per-record reading: x topK)
    "memory_event": 0.00025,           # per CreateEvent (short-term memory)
    "memory_record_month": 0.00075,    # per long-term record stored per month (built-in strategies)
    "gateway_search": 0.000025,        # per x_amz_bedrock_agentcore_search call
    "gateway_invoke": 0.000005,        # per ListTools / CallTool / Ping
    "code_interpreter_vcpu_hour": 0.0895,
    "code_interpreter_gb_hour": 0.00945,
    # --- M03 additions. Source: AWS Price List API (AmazonBedrockAgentCore, AmazonBedrock, AmazonCognito, AmazonVPC)
    # and the service pricing pages, us-east-1, retrieved 2026-09-27 (M03 research: agentcore-policy, identity-outbound,
    # cognito-jwt-gateway, guardrails, vpc-privatelink). gateway_invoke above also bills each Gateway tools/call.
    "policy_authorization": 0.000025,  # Policy in AgentCore: per authorization request (one per tools/call)
    "identity_token": 0.00001,         # AgentCore Identity: per token / API-key request ($0.010 per 1,000)
    "cognito_m2m_token": 0.00225,      # Amazon Cognito M2M (client credentials): per access token issued
    "vpc_endpoint_az_hour": 0.01,      # interface VPC endpoint (PrivateLink): per endpoint per AZ-hour
    # Amazon Bedrock Guardrails, per TEXT UNIT (1 unit = up to 1,000 characters); bedrock_guardrails maps usage keys
    "guardrail_content_unit": 0.00015,               # content filters incl. PROMPT_ATTACK ($0.15 per 1,000 units)
    "guardrail_topic_unit": 0.00015,                 # denied topics
    "guardrail_pii_unit": 0.0001,                    # managed sensitive-information (PII) entities
    "guardrail_regex_unit": 0.0,                     # regex-only sensitive information: free units
    "guardrail_checks_prompt_attack_unit": 0.00008,  # InvokeGuardrailChecks prompt attack (no guardrail resource)
    "policy_nl_authoring_token": 0.00013,            # Policy in AgentCore NL2Cedar: $0.13 per 1K user input tokens (pricing page)
    # CloudWatch Logs, us-east-1 (Price List API 2026-09-27, M03 audit-trail research): per GB
    "cloudwatch_ingest_gb": 0.50,                     # custom log ingestion (Standard)
    "cloudwatch_data_protection_gb": 0.12,           # data protection policy scan
    "cloudwatch_insights_scan_gb": 0.005,            # Logs Insights data scanned
    # AWS Secrets Manager, us-east-1 (Price List API AWSSecretsManager, 2026-09-27): each AgentCore Identity credential
    # provider keeps one service-managed secret in the account (research identity-outbound_facts l.28, l.77)
    "secrets_manager_secret_month": 0.40,            # per secret per month, prorated by the hour
    # --- M04 additions (observability + evaluations). Sources: AWS Price List API (AmazonBedrockAgentCore,
    # AmazonCloudWatch, AWSXRay) + the pricing pages, us-east-1, retrieved 2026-09-28 (M04 research: runtime-traces F35,
    # evaluations F39-F40, loop-alarms §3). Runtime V1 (default platform); V2 is $0.1276 / $0.0169.
    "runtime_vcpu_hour": 0.0895,                     # AgentCore Runtime, consumption-based vCPU-hour
    "runtime_gb_hour": 0.00945,                      # AgentCore Runtime, consumption-based memory GB-hour
    # Evaluations: custom AND derived evaluators bill a per-evaluation fee; their judge (Nova) tokens are ordinary
    # Bedrock usage in the account (evaluations F10, F40). Built-in evaluators as-is bill tokens on an AWS-side judge.
    "evaluation_custom": 0.0015,                     # per custom/derived evaluation ($1.50 per 1,000), judge tokens extra
    "evaluation_builtin_input_token": 0.0000024,     # built-in evaluator, per input token ($2.40 per 1M) - never used here
    "evaluation_builtin_output_token": 0.000012,     # built-in evaluator, per output token ($12.00 per 1M) - never used
    # CloudWatch metrics/alarms: per metric or alarm per month, prorated by the hour (730 h/month)
    "cloudwatch_alarm_highres_month": 0.30,          # high-resolution (10 s) metric alarm, per alarm-metric-month
    "cloudwatch_alarm_standard_month": 0.10,         # standard (60 s) metric alarm
    "cloudwatch_custom_metric_month": 0.30,          # custom metric (first 10k), per metric-month
    "cloudwatch_api_request": 0.00001,               # PutMetricData / GetMetricData etc. ($0.01 per 1,000 requests)
    "xray_span_indexed": 0.00000075,                 # Transaction Search: per indexed span (only the indexing % counts)
}
HOURS_PER_MONTH = 730                                # CloudWatch prorates monthly metric/alarm prices by the hour

_SESSION: boto3.Session | None = None


def get_session() -> boto3.Session:
    """The one boto3 session every notebook uses: MLADAS_AWS_PROFILE if set, else boto3's standard credential
    chain (env vars, AWS_PROFILE, default profile, SSO); the region is pinned (MLADAS_AWS_REGION, us-east-1)."""
    global _SESSION
    if _SESSION is None:
        _SESSION = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION) if AWS_PROFILE \
            else boto3.Session(region_name=AWS_REGION)
    return _SESSION


def whoami() -> dict[str, str]:
    ident = get_session().client("sts").get_caller_identity()
    return {"account": ident["Account"], "arn": ident["Arn"], "region": AWS_REGION, "profile": AWS_PROFILE or "<default chain>"}


def stack_versions() -> dict[str, str]:
    out = {}
    for pkg in ("strands-agents", "strands-agents-tools", "bedrock-agentcore", "a2a-sdk", "boto3"):
        try:
            out[pkg] = _md.version(pkg)
        except _md.PackageNotFoundError:
            out[pkg] = "not installed"
    return out


def model_id(model: str) -> str:
    """Accept a catalog key ('nova-2-lite') or a full Amazon model id; refuse anything that is not Amazon Nova."""
    mid = MODELS.get(model, model)
    if "amazon.nova" not in mid:
        raise ValueError(f"{mid!r} is not an Amazon Nova model — this course uses Amazon models only.")
    return mid


def nova(model: str = "nova-2-lite", *, max_tokens: int = 1200, temperature: float = 0.2,
         reasoning: str | None = None, **model_config):
    """Build a Strands BedrockModel for an Amazon Nova model, pinned to the notebook's AWS session.

    reasoning: None | "low" | "medium"  (Nova 2 Lite extended thinking). "high" is intentionally not offered:
               Bedrock requires temperature/maxTokens to be unset for it and it can burn >10k output tokens.
    """
    from strands.models import BedrockModel

    mid = model_id(model)
    if reasoning is not None:
        if "nova-2" not in mid:
            raise ValueError("Extended reasoning (reasoningConfig) is a Nova 2 feature.")
        if reasoning not in ("low", "medium"):
            raise ValueError("Use reasoning='low' or 'medium' in class ('high' needs temperature/max_tokens unset).")
        extra = dict(model_config.pop("additional_request_fields", {}) or {})
        extra["reasoningConfig"] = {"type": "enabled", "maxReasoningEffort": reasoning}
        model_config["additional_request_fields"] = extra
    return BedrockModel(
        model_id=mid,
        boto_session=get_session(),
        # bounded retries for a live class (botocore layer); Strands adds its own ModelRetryStrategy in make_agent()
        boto_client_config=Config(retries={"total_max_attempts": 3, "mode": "standard"}, read_timeout=120),
        max_tokens=max_tokens,
        temperature=temperature,
        **model_config,
    )


# ======================================================================================
# 2. Cost & latency ledger (feeds the dashboards)
# ======================================================================================
def cost_usd(model: str, usage: dict[str, int]) -> float:
    """$ for one call. A 4th price (cache write per 1M) is optional: Nova bills cache writes at 0."""
    p = PRICES_PER_1M.get(model, (0.0, 0.0, 0.0))
    p_in, p_out, p_cache = p[:3]
    p_write = p[3] if len(p) > 3 else 0.0
    return (usage.get("inputTokens", 0) * p_in
            + usage.get("outputTokens", 0) * p_out
            + usage.get("cacheReadInputTokens", 0) * p_cache
            + usage.get("cacheWriteInputTokens", 0) * p_write) / 1_000_000


_LOCAL_SCOPE: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("mladas_ledger_local_scope", default=None)


class Ledger:
    """Collects one row per agent invocation: scope (section/pattern), agent, model, tokens, $ and latency."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.scope_name = "default"
        self._lock = threading.Lock()

    @contextmanager
    def scope(self, name: str):
        """`with LEDGER.scope("agents-as-tools"):` tags every invocation made inside the block.
        (Process-wide on purpose: agent calls hop across threads/event loops, so a contextvar would lose it.)"""
        previous, self.scope_name = self.scope_name, name
        try:
            yield self
        finally:
            self.scope_name = previous

    @contextmanager
    def local_scope(self, name: str):
        """Like scope(), but only for calls started in THIS thread (parallel arms that each need their own scope).
        Strands copies the caller's contextvars into its worker thread (strands._async.run_async), so the recorder
        hook, summarizer agents and mc.meter_hidden rows all see it. Enter it inside the worker thread."""
        token = _LOCAL_SCOPE.set(name)
        try:
            yield self
        finally:
            _LOCAL_SCOPE.reset(token)

    def add(self, **row: Any) -> None:
        row.setdefault("scope", _LOCAL_SCOPE.get() or self.scope_name)
        row.setdefault("ts", time.time())
        with self._lock:
            self.rows.append(row)

    def add_remote(self, agent: str, latency_s: float, stop_reason: str | None = None) -> None:
        """Record a call to a REMOTE (A2A) agent: latency only — A2A does not propagate the partner's token usage."""
        self.add(agent=agent, model="remote (A2A)", input_tokens=0, output_tokens=0, cache_read_tokens=0,
                 cache_write_tokens=0, model_calls=0, latency_s=round(latency_s, 3), cost_usd=0.0, stop_reason=stop_reason)

    def df(self, scope: str | Iterable[str] | None = None):
        import pandas as pd
        d = pd.DataFrame(self.rows)
        if d.empty or scope is None:
            return d
        scopes = [scope] if isinstance(scope, str) else list(scope)
        return d[d["scope"].isin(scopes)].reset_index(drop=True)

    def summary(self, by: str | list[str] = "scope"):
        d = self.df()
        if d.empty:
            return d
        cols = ["input_tokens", "output_tokens", "cost_usd", "latency_s"]
        out = d.groupby(by)[cols].sum()
        out["calls"] = d.groupby(by).size()
        return out.sort_values("cost_usd", ascending=False)

    def add_service(self, service: str, units: float, unit_price: float, *, correlation_id: str | None = None,
                    note: str = "", provider: str = "AgentCore") -> None:
        """Record a non-model AgentCore charge (a memory retrieval, a gateway search, sandbox time) as a ledger row, so
        per-request costs include what the engineered arm pays besides tokens. Use the request's correlation_id and
        a price from AGENTCORE_PRICES. model="AgentCore" keeps these rows out of model charts.
        provider (M03) only changes the agent label, e.g. "guardrail_text_units (Bedrock Guardrails)" or
        "cognito_m2m_token (Amazon Cognito)"; model stays "AgentCore" so every service row is filtered the same way."""
        self.add(agent=f"{service} ({provider})", model="AgentCore", input_tokens=0, output_tokens=0,
                 cache_read_tokens=0, cache_write_tokens=0, model_calls=0, latency_s=0.0,
                 cost_usd=round(units * unit_price, 8), stop_reason=note or None, correlation_id=correlation_id,
                 service_units=units)

    def per_request(self, scope: str | Iterable[str]):
        """One row per logical request (correlation_id) in EXACT scope(s): tokens, $, model calls, wall clock.
        Rows without a correlation_id each count as their own request."""
        import pandas as pd
        d = self.df(scope)
        if d.empty:
            return d
        d = d.copy()
        cid = d["correlation_id"] if "correlation_id" in d else pd.Series([None] * len(d), index=d.index)
        d["request"] = [c if isinstance(c, str) and c else f"row{i}" for i, c in enumerate(cid)]
        d["t_start"] = d["ts"] - d["latency_s"].fillna(0)
        g = d.groupby("request", sort=False)
        out = g[["input_tokens", "output_tokens", "cost_usd"]].sum()
        for col in ("cache_read_tokens", "cache_write_tokens", "model_calls"):
            if col in d:
                out[col] = g[col].sum()
        out["agents"] = g["agent"].nunique()
        out["wall_s"] = (g["ts"].max() - g["t_start"].min()).round(2)
        return out.reset_index()

    def wall_clock(self, scope: str | Iterable[str]) -> float:
        """Seconds from the first call's start to the last call's end in EXACT scope(s) (never a sum of latencies)."""
        d = self.df(scope)
        if d.empty:
            return 0.0
        return round(float(d["ts"].max() - (d["ts"] - d["latency_s"].fillna(0)).min()), 2)

    def clear_section(self, prefix: str) -> None:
        """Drop every row whose scope starts with e.g. '§2 ' — call it first in a section so re-runs don't double count."""
        with self._lock:
            self.rows = [r for r in self.rows if not str(r.get("scope", "")).startswith(prefix.rstrip() + " ")]

    def clear(self, scope: str | None = None) -> None:
        with self._lock:
            self.rows = [r for r in self.rows if scope is not None and r.get("scope") != scope]


LEDGER = Ledger()


def _make_recorder():
    """A Strands HookProvider that writes every agent invocation to LEDGER."""
    from strands.hooks import AfterInvocationEvent, BeforeInvocationEvent, HookProvider, HookRegistry

    class UsageRecorder(HookProvider):
        def __init__(self) -> None:
            self._t0: dict[int, float] = {}

        def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
            registry.add_callback(BeforeInvocationEvent, self._before)
            registry.add_callback(AfterInvocationEvent, self._after)

        def _before(self, event: BeforeInvocationEvent) -> None:
            self._t0[id(event.agent)] = time.perf_counter()

        def _after(self, event: AfterInvocationEvent) -> None:
            agent = event.agent
            t0 = self._t0.pop(id(agent), None)
            inv = agent.event_loop_metrics.latest_agent_invocation
            usage = dict(inv.usage) if inv else {}
            mid = getattr(agent.model, "config", {}).get("model_id", "?")
            LEDGER.add(
                agent=agent.name, model=mid,
                input_tokens=usage.get("inputTokens", 0), output_tokens=usage.get("outputTokens", 0),
                cache_read_tokens=usage.get("cacheReadInputTokens", 0),
                cache_write_tokens=usage.get("cacheWriteInputTokens", 0),
                model_calls=len(inv.cycles) if inv else 0,
                latency_s=round(time.perf_counter() - t0, 3) if t0 else None,
                cost_usd=cost_usd(mid, usage),
                stop_reason=getattr(event.result, "stop_reason", None) if event.result else None,
                correlation_id=(getattr(event, "invocation_state", None) or {}).get("correlation_id"),
            )

    return UsageRecorder()


_RECORDER = None


def recorder():
    global _RECORDER
    if _RECORDER is None:
        _RECORDER = _make_recorder()
    return _RECORDER


_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _set_default_limits(agent: Any, limits: dict) -> None:
    """Make `limits` the default of every invocation of this agent (Strands 1.57 takes limits per call only:
    agent(prompt, limits=...)). Every entry point (agent(), invoke_async, Graph/Swarm nodes, agent-as-tool) goes
    through stream_async, so the default is injected there; an explicit limits= on a call still wins."""
    from strands import Agent

    Agent._validate_limits(limits)                 # TypeError now, not on the first call
    original = agent.stream_async
    defaults = dict(limits)

    def stream_async(*args: Any, **kwargs: Any):
        if kwargs.get("limits") is None:
            kwargs["limits"] = dict(defaults)
        return original(*args, **kwargs)

    agent.stream_async = stream_async
    agent.default_limits = defaults


def make_agent(name: str, system_prompt: str | list, *, model: str = "nova-2-lite", tools: list | None = None,
               description: str | None = None, max_tokens: int = 1200, temperature: float = 0.2,
               reasoning: str | None = None, hooks: list | None = None, guardrail: dict | None = None,
               streaming: bool | None = None, limits: dict | None = None, **agent_kwargs):
    """Create a Strands Agent on an Amazon Nova model, silent (no stdout streaming), recorded in LEDGER.

    `name` doubles as the tool name when the agent is used as a tool -> must match [a-zA-Z0-9_-]+ (Bedrock rule).

    M03 additions (all optional; omitted = the M01/M02 behaviour):
      guardrail  the guardrail kwargs for BedrockModel, e.g. bedrock_guardrails.GuardrailDeployment.model_config()
                 ({"guardrail_id", "guardrail_version", "guardrail_trace", ...}); only guardrail_* keys are accepted.
      streaming  False = Converse instead of ConverseStream. Use it with a guardrail that masks OUTPUT: streamed
                 masking leaked digits ("{FullCardNumber}0008 4") 3/3; non-streaming was clean 3/3.
      limits     default per-invocation caps for every call, e.g. {"turns": 6} -> stop_reason "limit_turns"
                 (red-team agents: a withheld tool result once made Nova loop 319 times).
      callback_handler (in **agent_kwargs) replaces the default None, e.g. bedrock_guardrails.GuardrailTrace().
    """
    from strands import Agent, ModelRetryStrategy

    if not _NAME_RE.match(name):
        raise ValueError(f"Agent name {name!r} must match [a-zA-Z0-9_-]+ (it becomes a Bedrock tool name).")
    model_kwargs: dict[str, Any] = {}
    if guardrail is not None:
        bad = [k for k in guardrail if not str(k).startswith("guardrail_")]
        if bad:
            raise ValueError(f"guardrail= takes only guardrail_* model kwargs, got {bad}")
        if not guardrail.get("guardrail_id") or not guardrail.get("guardrail_version"):
            # Strands sends guardrailConfig only when BOTH are set: without them the agent would silently run unguarded
            raise ValueError("guardrail= needs guardrail_id and guardrail_version (is the guardrail READY?)")
        model_kwargs.update(guardrail)
    if streaming is not None:
        model_kwargs["streaming"] = bool(streaming)
    agent = Agent(
        name=name,
        description=description,
        system_prompt=system_prompt,
        model=nova(model, max_tokens=max_tokens, temperature=temperature, reasoning=reasoning, **model_kwargs),
        tools=list(tools or []),
        # no token streaming to stdout by default; print results explicitly
        callback_handler=agent_kwargs.pop("callback_handler", None),
        hooks=[recorder(), *(hooks or [])],
        # throttling retries: 3 attempts, 2 s -> 10 s (the Strands default can wait minutes with no output)
        retry_strategy=agent_kwargs.pop("retry_strategy", ModelRetryStrategy(max_attempts=3, initial_delay=2, max_delay=10)),
        **agent_kwargs,
    )
    if limits:
        _set_default_limits(agent, limits)
    return agent


# ======================================================================================
# 3. Text helpers
# ======================================================================================
_THINKING = re.compile(r"<thinking>.*?</thinking>\s*", flags=re.S)


def strip_thinking(text: str) -> str:
    """Nova v1 models (Micro/Lite/Pro) may wrap reasoning in <thinking> tags inside normal text. Remove it."""
    return _THINKING.sub("", text or "").strip()


def text_of(result: Any) -> str:
    """Final text of an AgentResult (joins streamed chunks without inserting newlines; drops <thinking>)."""
    if result is None:
        return ""
    msg = getattr(result, "message", None)
    if isinstance(msg, dict):
        return strip_thinking("".join(b.get("text", "") for b in msg.get("content", []) if isinstance(b, dict)))
    return strip_thinking(str(result))


def short(text: str, n: int = 90) -> str:
    """One-line preview: collapse whitespace and cut at n characters."""
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def middle(text: str, head: int = 52, tail: int = 26) -> str:
    """Keep the start and the end of a long string (ids, URLs): 'arn:aws:bedrock-agentcore:…:runtime/abc'."""
    t = str(text or "")
    return t if len(t) <= head + tail + 1 else f"{t[:head]}…{t[-tail:]}"


def mask_account(text: str, account: str | None = None) -> str:
    """Hide the AWS account id on a projector: <12-digit account id> -> ********<last 4>."""
    acct = account or whoami()["account"]
    return str(text).replace(acct, "********" + acct[-4:])


def parse_json(text: str) -> Any:
    """Parse the first JSON object in a model reply (tolerates ```json fences and surrounding prose)."""
    import json

    t = strip_thinking(text)
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            return json.loads(t[start:end + 1])
        raise


def tool_calls(agent: Any) -> list[str]:
    """Names of the tools an agent called, in order (read from its conversation history)."""
    return [c["toolUse"]["name"] for m in agent.messages if m["role"] == "assistant"
            for c in m["content"] if "toolUse" in c]


def run_async(coro: Any) -> Any:
    """Run a coroutine from a notebook cell OR a plain script.
    Jupyter already runs an event loop (so asyncio.run() fails there); in that case run on a worker thread."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ======================================================================================
# 4. A2A helpers
# ======================================================================================
class A2AServerThread:
    """Serve an ASGI app (e.g. A2AServer(...).to_starlette_app() or build_a2a_app(...)) from a daemon thread,
    so notebook cells stay interactive. Re-run safe: refuses a busy port instead of hanging."""

    def __init__(self, app: Any, host: str = "127.0.0.1", port: int = 9101):
        import uvicorn

        self.host, self.port, self.url = host, port, f"http://{host}:{port}/"
        self.server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, name=f"a2a-{port}", daemon=True)

    @staticmethod
    def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
        with socket.socket() as s:
            return s.connect_ex((host, port)) == 0

    def start(self, timeout: float = 15) -> "A2AServerThread":
        if self.port_in_use(self.port, self.host):
            raise RuntimeError(f"Port {self.port} is busy — stop the previous server first (srv.stop()).")
        self.thread.start()
        deadline = time.time() + timeout
        while not self.server.started:
            if not self.thread.is_alive() or time.time() > deadline:
                raise RuntimeError("A2A server failed to start")
            time.sleep(0.05)
        return self

    def stop(self, timeout: float = 10) -> bool:
        self.server.should_exit = True
        self.thread.join(timeout)
        return not self.thread.is_alive()


def free_port(preferred: int | None = None) -> int:
    """`preferred` if it is free on localhost, otherwise any free port chosen by the OS."""
    if preferred and not A2AServerThread.port_in_use(preferred):
        return preferred
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sigv4_auth_class():
    import httpx
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    class SigV4HttpxAuth(httpx.Auth):
        """Sign each httpx request with AWS SigV4 (service 'bedrock-agentcore') using the notebook's AWS session."""
        requires_request_body = True

        def __init__(self, session: boto3.Session | None = None, region: str = AWS_REGION,
                     service: str = "bedrock-agentcore"):
            self.session, self.region, self.service = session or get_session(), region, service

        def auth_flow(self, request):
            headers = {k: v for k, v in request.headers.items() if k.lower() not in ("connection", "accept-encoding")}
            aws_req = AWSRequest(method=request.method, url=str(request.url), data=request.content, headers=headers)
            creds = self.session.get_credentials().get_frozen_credentials()
            SigV4Auth(creds, self.service, self.region).add_auth(aws_req)
            request.headers.update(dict(aws_req.headers.items()))
            yield request

    return SigV4HttpxAuth


def SigV4HttpxAuth(*args: Any, **kwargs: Any):  # noqa: N802 (factory that looks like a class)
    return _sigv4_auth_class()(*args, **kwargs)


def runtime_session_id(prefix: str = "mladas") -> str:
    """AgentCore Runtime session ids must be >= 33 characters."""
    return f"{prefix}-{uuid.uuid4().hex}"


def runtime_a2a_url(runtime_arn: str) -> str:
    from bedrock_agentcore.runtime import build_runtime_url
    return build_runtime_url(runtime_arn) + "/"


def _runtime_a2a_agent_class():
    from contextlib import asynccontextmanager

    import httpx
    from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
    from strands.agent.a2a_agent import A2AAgent

    class RuntimeA2AAgent(A2AAgent):
        """Strands A2AAgent for an A2A server on AgentCore Runtime, authenticated with IAM SigV4.

        Why a subclass: an httpx.AsyncClient is bound to the event loop that created it, and every sync call
        (agent("...") or graph("...")) runs on a fresh loop. So we open a new signed client per call instead of
        sharing one via ClientConfig (which fails on the 2nd call with 'Event loop is closed')."""

        def __init__(self, runtime_arn: str, *, name: str | None = None, session_id: str | None = None,
                     timeout: int = 300):
            super().__init__(runtime_a2a_url(runtime_arn), name=name, timeout=timeout)
            self.runtime_arn = runtime_arn
            # AgentCore routes every request with the same session id to the same microVM session
            self.session_headers = {"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id or runtime_session_id()}

        def _http(self) -> httpx.AsyncClient:
            return httpx.AsyncClient(auth=SigV4HttpxAuth(), headers=self.session_headers, timeout=self.timeout)

        async def get_agent_card(self):
            if self._agent_card is None:
                async with self._http() as client:
                    self._agent_card = await A2ACardResolver(httpx_client=client, base_url=self.endpoint).get_agent_card()
                self.name = self.name or self._agent_card.name
                self.description = self.description or self._agent_card.description
            return self._agent_card

        @asynccontextmanager
        async def _get_a2a_client(self):
            card = await self.get_agent_card()
            async with self._http() as client:
                yield ClientFactory(ClientConfig(httpx_client=client, streaming=True)).create(card)

    return RuntimeA2AAgent


def runtime_a2a_agent(runtime_arn: str, *, name: str | None = None, session_id: str | None = None,
                      timeout: int = 300):
    """A Strands A2AAgent that talks to an A2A server hosted on AgentCore Runtime (IAM SigV4 inbound auth)."""
    return _runtime_a2a_agent_class()(runtime_arn, name=name, session_id=session_id, timeout=timeout)


# ======================================================================================
# 5. Classroom helpers (promoted from M01 section code, Sept 2026)
# ======================================================================================
# Nova output recipe: role + explicit output format + explicit negatives. Nova 2 Lite ignores soft length limits
# ("max 80 words") but follows explicit negatives. Append NO_FLUFF to any customer-facing prompt.
NO_FLUFF = ("No headings, no lists, no markdown, no greetings, no offers of further help. "
            "Never promise emails, calls, texts or timelines that no tool returned.")

FOLLOW_UP_PROMISE = re.compile(
    r"\b(?:e-?mails?|text messages?|(?:by|via) (?:sms|text)|call you|contact you|notify you|"
    r"within \d+ (?:business )?(?:days?|hours?))\b", re.IGNORECASE)


def invented_promises(answer: str) -> list[str]:
    """Follow-ups promised in an answer (email, text, call, 'within N days'). If no tool returned such a
    commitment, every hit is invented. Deterministic — use it as a check column, never trust the prose."""
    return sorted({m.lower() for m in FOLLOW_UP_PROMISE.findall(answer or "")})


def leaked_rules(answer: str, foreign_rules: dict[str, str]) -> list[str]:
    """Names of rules from ANOTHER domain whose regex appears in the answer (rule bleed / context clash).
    foreign_rules: {"dispute callback": r"specialist will call", ...}"""
    return [name for name, pattern in foreign_rules.items() if re.search(pattern, answer or "", re.IGNORECASE)]


_NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?%?")


def _norm_number(tok: str) -> str:
    t = tok.replace("$", "").replace(",", "").rstrip("%")
    t = t[:-3] if t.endswith(".00") else t
    return (t.lstrip("0") or "0") if "." not in t else t


def unsupported_numbers(answer: str, sources: Iterable[str], ignore: Iterable[str] = ()) -> list[str]:
    """Numbers in `answer` that appear in none of `sources` (after normalising $ , % and .00).
    A non-empty list means the model computed, rounded or invented a figure — show it next to the answer."""
    seen = {_norm_number(t) for s in sources for t in _NUMBER.findall(str(s))}
    skip = {_norm_number(t) for t in ignore}
    return sorted({t for t in _NUMBER.findall(answer or "") if _norm_number(t) not in seen | skip})


def wait_until(predicate, *, max_wait: float, label: str, poll: float = 5.0, describe=None):
    """Poll `predicate()` (truthy = done) on ONE status line that updates in place; never wait silently.

    Prints 'Waiting up to N s for <label> ...' first. `describe(value)` renders the status text for the latest
    value. Returns (done, value, elapsed_s); never raises on timeout — the caller decides what to do."""
    from IPython.display import Pretty, display

    print(f"Waiting up to {max_wait:.0f} s for {label} ...")
    t0, handle, value = time.time(), None, None
    while True:
        try:
            value = predicate()
        except Exception as e:  # noqa: BLE001 - a transient error is a status, not a crash
            value = None
            text = f"{type(e).__name__}: {short(str(e), 80)}"
        else:
            text = describe(value) if describe else ("ready" if value else "not yet")
        elapsed = time.time() - t0
        line = Pretty(f"{elapsed:5.0f}s  {label}: {text}")
        handle = handle.update(line) or handle if handle else display(line, display_id=True)
        if value:
            return True, value, round(elapsed, 1)
        if elapsed >= max_wait:
            print(f"{label}: not ready after {max_wait:.0f} s — continuing without it.")
            return False, value, round(elapsed, 1)
        time.sleep(min(poll, max(0.5, max_wait - elapsed)))   # never overshoot max_wait by a whole poll


class Cleanup:
    """Isolated cleanup steps for a notebook's wrap-up: each step records what it deleted or why it failed,
    and never raises, so one failure cannot strand the other resources. Run it in a background thread."""

    def __init__(self) -> None:
        self.done: list[str] = []
        self.problems: list[str] = []
        self.thread: threading.Thread | None = None

    def step(self, label: str, fn) -> None:
        try:
            out = fn()
            self.done.extend(out if isinstance(out, list) else [label])
        except Exception as e:  # noqa: BLE001
            self.problems.append(f"{label}: {type(e).__name__}: {e}")

    def start(self, steps: list[tuple[str, Any]]) -> "Cleanup":
        """steps: [(label, fn), ...] run in order on a daemon thread."""
        self.thread = threading.Thread(target=lambda: [self.step(lbl, fn) for lbl, fn in steps],
                                       name="mladas-cleanup", daemon=True)
        self.thread.start()
        return self

    def join(self, timeout: float | None = None) -> bool:
        if self.thread:
            self.thread.join(timeout)
        return not (self.thread and self.thread.is_alive())


def gone(check) -> bool:
    """True if `check()` raises a not-found error (verify deletion BY ID, not by list scans)."""
    from botocore.exceptions import ClientError
    try:
        check()
        return False
    except ClientError as e:
        code = e.response["Error"]["Code"]
        return code in ("ResourceNotFoundException", "NoSuchEntity", "404", "NoSuchBucket", "NotFoundException") \
            or code.endswith(".NotFound")          # EC2: InvalidVpcID.NotFound, InvalidGroup.NotFound, ...


def all_done(*node_ids: str):
    """Strands Graph AND-join: put this condition on EVERY incoming edge of a join node (joins are OR by default)."""
    def condition(state: Any) -> bool:
        return all(nid in {n.node_id for n in state.completed_nodes} for nid in node_ids)
    return condition


# ======================================================================================
# 6. Token measurement (Nova has no CountTokens API — verified 2026-09-26)
# ======================================================================================
# Hidden input tokens Nova adds to EVERY request, before your first character (measured 2026-09-26 with a one-word
# user message: "Hi" bills 47 tokens on Nova 2 Lite and 1 token on Micro/Lite/Pro). count_tokens() removes it (plus
# the 1-token "." placeholder) by subtracting a measured baseline; the constant is here so a notebook can show it.
NOVA_BASE_OVERHEAD: dict[str, int] = {
    "nova-2-lite": 46, "nova-micro": 0, "nova-lite": 0, "nova-pro": 0, "nova-micro-inregion": 0,
}

_TOKEN_CACHE: dict[tuple, int] = {}
_RUNTIME = None
_RUNTIME_BIG = None
_BIG_REQUEST_CHARS = 400_000     # ~100k+ tokens: never re-send such a request on a timeout/retry


def _bedrock_runtime():
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = get_session().client("bedrock-runtime", config=Config(   # a shared account: ride out throttling bursts
            retries={"total_max_attempts": 6, "mode": "adaptive"}, read_timeout=120))
    return _RUNTIME


def _bedrock_runtime_big():
    """For very large prompts: ONE attempt (a retry re-sends and re-bills the whole prompt) and a long read timeout
    (a 1M-token prompt took 92 s to the first token)."""
    global _RUNTIME_BIG
    if _RUNTIME_BIG is None:
        _RUNTIME_BIG = get_session().client("bedrock-runtime", config=Config(
            retries={"total_max_attempts": 1, "mode": "standard"}, read_timeout=900, connect_timeout=30))
    return _RUNTIME_BIG


_RUNTIME_PATIENT = None


def _bedrock_runtime_patient():
    """Token probes only (cheap, idempotent, never timed): up to 8 attempts with adaptive backoff, so a throttling burst
    on a shared account (every notebook draws on the same Nova requests-per-minute quota) does not stop §0.5 or a measuring cell."""
    global _RUNTIME_PATIENT
    if _RUNTIME_PATIENT is None:
        _RUNTIME_PATIENT = get_session().client("bedrock-runtime", config=Config(
            retries={"total_max_attempts": 8, "mode": "adaptive"}, read_timeout=120))
    return _RUNTIME_PATIENT


def _tool_config(tools: list | None) -> dict | None:
    if not tools:
        return None
    specs = []
    for t in tools:
        spec = getattr(t, "tool_spec", None) or (t.get("toolSpec") if isinstance(t, dict) and "toolSpec" in t else t)
        specs.append({"toolSpec": spec})
    return {"tools": specs}


def _api_messages(messages: Iterable[dict]) -> list[dict]:
    """Converse accepts only role + content: Strands messages also carry metadata/tracking_id (ParamValidationError)."""
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def _has_tool_blocks(messages: list[dict]) -> bool:
    return any(isinstance(b, dict) and ("toolUse" in b or "toolResult" in b) for m in messages for b in m["content"])


def _prompt_tokens(usage: dict) -> int:
    """Everything the model read: inputTokens EXCLUDES tokens read from or written to the prompt cache."""
    return (usage.get("inputTokens", 0) + usage.get("cacheReadInputTokens", 0)
            + usage.get("cacheWriteInputTokens", 0))


def _ledger_call(agent: str, mid: str, usage: dict, latency_s: float | None, stop_reason: str | None,
                 **extra: Any) -> None:
    """One LEDGER row for a raw Bedrock call (same columns as the agent recorder)."""
    LEDGER.add(agent=agent, model=mid, input_tokens=usage.get("inputTokens", 0),
               output_tokens=usage.get("outputTokens", 0), cache_read_tokens=usage.get("cacheReadInputTokens", 0),
               cache_write_tokens=usage.get("cacheWriteInputTokens", 0), model_calls=1,
               latency_s=round(latency_s, 3) if latency_s is not None else None, cost_usd=cost_usd(mid, usage),
               stop_reason=stop_reason, **extra)


def _probe(model_id_: str, system: list | None, tools: list | None, messages: list[dict] | str) -> int:
    """Total prompt tokens of one request (cached per content). With tools, maxTokens=1 can end in
    stopReason=malformed_tool_use with ALL usage 0 (5/5 on 2026-09-26), so a zero reading is retried with 512."""
    import hashlib

    if isinstance(messages, str):
        messages = [{"role": "user", "content": [{"text": messages}]}]
    key = (model_id_, hashlib.sha256(json.dumps([system, _tool_config(tools), messages], sort_keys=True,
                                                default=str).encode()).hexdigest())
    if key not in _TOKEN_CACHE:
        req: dict[str, Any] = {"modelId": model_id_, "messages": messages}
        if system:
            req["system"] = system
        if tools:
            req["toolConfig"] = _tool_config(tools)
        for max_tokens in (1, 512):
            req["inferenceConfig"] = {"maxTokens": max_tokens}
            t0 = time.perf_counter()
            resp = _bedrock_runtime_patient().converse(**req)
            usage = resp["usage"]
            total = _prompt_tokens(usage)
            _ledger_call("token_probe", model_id_, usage, time.perf_counter() - t0,
                         "probe" if total else f"{resp.get('stopReason')} (usage 0 -> retry maxTokens=512)")
            if total:
                break
        _TOKEN_CACHE[key] = total
    return _TOKEN_CACHE[key]


def count_tokens(text: str | None = None, *, system: str | list | None = None, tools: list | None = None,
                 model: str = "nova-2-lite", messages: list | None = None, agent: Any = None,
                 include_overhead: bool = False) -> int:
    """How many input tokens a piece of context costs on an Amazon Nova model, MEASURED, not estimated.

    Bedrock's CountTokens API does not support Nova ("The provided model doesn't support counting tokens"), and
    chars/4 estimates are 18-43% off for Nova 2 Lite. So we send one Converse request with maxTokens=1 and read
    usage (inputTokens + cache read + cache write), minus a measured baseline: a one-character message, i.e.
    NOVA_BASE_OVERHEAD[model] + 1. Cost: input tokens only (a 10k-token text costs about $0.003 on Nova 2 Lite);
    results are cached per content, and each probe is a LEDGER row (agent="token_probe").

      count_tokens("some text")                 -> tokens of the text as a user message
      count_tokens(system="You are ...")        -> tokens the system prompt adds
      count_tokens(tools=[tool_a, tool_b])      -> tokens the tool definitions add (incl. Nova's tool preamble)
      count_tokens(messages=agent.messages, system=..., tools=...)  -> a whole conversation (toolUse/toolResult kept)
      count_tokens(agent=agent)                 -> the agent's current context: its messages, system prompt, tools, model
      count_tokens("next question", agent=agent) -> ... plus one more user turn

    include_overhead=True returns the full request as Bedrock bills it (no baseline subtracted); it equals the
    agent's own per-call usage for the same messages (963 = 963, 1,281 = 1,281 in the research runs).
    """
    if agent is not None:
        messages = agent.messages if messages is None else messages
        system = system or getattr(agent, "system_prompt_content", None) or agent.system_prompt
        if tools is None:
            tools = [{"toolSpec": s} for s in agent.tool_registry.get_all_tool_specs()]
        model = agent.model.config["model_id"]
    mid = model_id(model)
    sys_blocks = ([{"text": system}] if system else None) if isinstance(system, str) else system
    msgs = _api_messages(messages or [])
    if text is not None:
        msgs.append({"role": "user", "content": [{"text": text}]})
    if not msgs:
        msgs = [{"role": "user", "content": [{"text": "."}]}]
    if _has_tool_blocks(msgs) and not tools:
        raise ValueError("These messages contain toolUse/toolResult blocks, and Bedrock rejects them without a "
                         "toolConfig: pass tools= (the agent's tools) or use count_tokens(agent=agent).")
    total = _probe(mid, sys_blocks or None, tools, msgs)
    if include_overhead:
        return total
    return max(0, total - _probe(mid, None, None, "."))


# ======================================================================================
# 7. Context-engineering helpers (M02, research-verified 2026-09-26)
# ======================================================================================
NOVA_OVERFLOW_MESSAGE = "Input Tokens Exceeded"      # Nova's wording when the prompt is larger than the window


def patch_nova_overflow() -> bool:
    """Teach Strands Nova's context-overflow wording. Returns True if it changed anything (idempotent).

    Strands 1.57 maps an overflow to ContextWindowOverflowException only for known phrases, and Nova's
    "Input Tokens Exceeded: Number of input tokens exceeds maximum length" is not one of them. Unpatched, a Nova overflow
    surfaces as EventLoopException and NO conversation manager gets the chance to trim; patched, SlidingWindow
    recovered in 3.7 s. Process-wide, so notebooks call it explicitly in §0 (never at import)."""
    import strands.models.bedrock as sb

    if NOVA_OVERFLOW_MESSAGE in sb.BEDROCK_CONTEXT_WINDOW_OVERFLOW_MESSAGES:
        return False
    sb.BEDROCK_CONTEXT_WINDOW_OVERFLOW_MESSAGES.append(NOVA_OVERFLOW_MESSAGE)
    return True


def _converse_request(user: str | list | None, system: str | list | None, model: str, max_tokens: int,
                      messages: list | None, tools: list | None, temperature: float | None) -> tuple[str, dict]:
    mid = model_id(model)
    msgs = _api_messages(messages or [])
    if user is not None:
        msgs.append({"role": "user", "content": [{"text": user}] if isinstance(user, str) else list(user)})
    if not msgs:
        raise ValueError("Nothing to send: pass user= and/or messages=.")
    req: dict[str, Any] = {"modelId": mid, "messages": msgs, "inferenceConfig": {"maxTokens": max_tokens}}
    if temperature is not None:
        req["inferenceConfig"]["temperature"] = temperature
    if system:
        req["system"] = [{"text": system}] if isinstance(system, str) else list(system)
    if tools:
        req["toolConfig"] = _tool_config(tools)
    return mid, req


def _client_for(req: dict):
    big = len(json.dumps(req, default=str)) > _BIG_REQUEST_CHARS
    return _bedrock_runtime_big() if big else _bedrock_runtime()


def stream_timed(user: str | list | None, *, system: str | list | None = None, model: str = "nova-2-lite",
                 max_tokens: int = 64, messages: list | None = None, tools: list | None = None,
                 temperature: float | None = 0.0, label: str = "converse_stream") -> dict:
    """One raw ConverseStream call, timed the way a user feels it. Returns
    {text, usage, ttft_s, wall_s, latency_ms, stop_reason, model}.

    ttft_s = wall clock from the request to the first TEXT delta (None if the model only called a tool); latency_ms is
    Bedrock's own metadata figure. `user` is a string or a list of content blocks (e.g. text + cachePoint) appended
    after `messages`; `system` is a string or a list of blocks. Requests over ~400k characters use a single-attempt
    client with a 900 s read timeout (never re-send a 1M-token prompt). Every call, failed ones too, is a LEDGER row
    (agent=label) with cache read/write tokens and the correct cost."""
    mid, req = _converse_request(user, system, model, max_tokens, messages, tools, temperature)
    t0 = time.perf_counter()
    ttft, parts, usage, latency_ms, stop = None, [], {}, None, None
    retries = 0
    try:
        response = _client_for(req).converse_stream(**req)
        retries = response.get("ResponseMetadata", {}).get("RetryAttempts", 0)   # silent botocore throttle retries
        for ev in response["stream"]:
            if "contentBlockDelta" in ev:
                delta = ev["contentBlockDelta"]["delta"]
                if "text" in delta:
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    parts.append(delta["text"])
            elif "messageStop" in ev:
                stop = ev["messageStop"].get("stopReason")
            elif "metadata" in ev:
                usage = ev["metadata"].get("usage", {})
                latency_ms = ev["metadata"].get("metrics", {}).get("latencyMs")
    except Exception as e:
        _ledger_call(label, mid, usage, time.perf_counter() - t0, f"error: {type(e).__name__}")
        raise
    wall = time.perf_counter() - t0
    ttft = round(ttft, 3) if ttft is not None else None
    _ledger_call(label, mid, usage, wall, stop, ttft_s=ttft, retries=retries)
    return {"text": strip_thinking("".join(parts)), "usage": usage, "ttft_s": ttft, "wall_s": round(wall, 3),
            "latency_ms": latency_ms, "stop_reason": stop, "model": mid, "retries": retries}


def converse_once(user: str | list | None, *, system: str | list | None = None, model: str = "nova-2-lite",
                  max_tokens: int = 64, messages: list | None = None, tools: list | None = None,
                  temperature: float | None = 0.0, label: str = "converse") -> dict:
    """One raw (non-streaming) Converse call for API-level demos, e.g. the 5 usage fields of a cache write vs a hit.
    Returns {text, usage, wall_s, latency_ms, stop_reason, model}; same inputs and LEDGER row as stream_timed()."""
    mid, req = _converse_request(user, system, model, max_tokens, messages, tools, temperature)
    t0 = time.perf_counter()
    try:
        resp = _client_for(req).converse(**req)
    except Exception as e:
        _ledger_call(label, mid, {}, time.perf_counter() - t0, f"error: {type(e).__name__}")
        raise
    wall = time.perf_counter() - t0
    usage, stop = resp.get("usage", {}), resp.get("stopReason")
    _ledger_call(label, mid, usage, wall, stop)
    text = "".join(b.get("text", "") for b in resp.get("output", {}).get("message", {}).get("content", []))
    return {"text": strip_thinking(text), "usage": usage, "wall_s": round(wall, 3),
            "latency_ms": resp.get("metrics", {}).get("latencyMs"), "stop_reason": stop, "model": mid}


def _num(v: Any) -> int:
    return 0 if v is None or v != v else int(v)          # None / NaN (pandas) -> 0


def _cache_usage(obj: Any) -> dict:
    if hasattr(obj, "to_dict") and not isinstance(obj, dict):          # a pandas row
        obj = obj.to_dict()
    if isinstance(obj.get("usage"), dict):                               # stream_timed()/converse_once() result
        return obj["usage"]
    if "cache_read_tokens" in obj or "cache_write_tokens" in obj:      # a LEDGER row
        return {"cacheReadInputTokens": _num(obj.get("cache_read_tokens")),
                "cacheWriteInputTokens": _num(obj.get("cache_write_tokens"))}
    return obj                                                           # a Bedrock/Strands usage dict


def cache_tag(usage: Any) -> str:
    """'hit' (read from the prompt cache), 'write' (cache entry created) or 'none' for one call.
    Accepts a usage dict, a stream_timed()/converse_once() result, or a LEDGER row. A call that reads a shared
    preamble and writes its own suffix counts as a hit."""
    u = _cache_usage(usage)
    if _num(u.get("cacheReadInputTokens")):
        return "hit"
    if _num(u.get("cacheWriteInputTokens")):
        return "write"
    return "none"


def cache_hits(rows: Any, *, after_first: bool = False) -> str:
    """'x/N' cache hits over calls (list of usages/results/LEDGER rows, or LEDGER.df(...)). Print the tally next to
    every caching cell: on us.* profiles hits are best-effort (24/32), so never claim a hit you did not count.
    after_first=True skips the first call, which is always a write on a fresh prefix."""
    if hasattr(rows, "columns"):                                   # a DataFrame, e.g. LEDGER.df(...)
        rows = rows.to_dict("records")
    elif hasattr(rows, "tolist"):                                  # a pandas Series, e.g. inside groupby().agg()
        rows = rows.tolist()
    rows = list(rows)[1:] if after_first else list(rows)
    return f"{sum(cache_tag(r) == 'hit' for r in rows)}/{len(rows)}"


# --- TOON (Token-Oriented Object Notation, spec v4.1) -----------------------------------------------------------
# `pip install toon-format` gives 0.1.0, a stub whose encode() raises NotImplementedError; the 0.9.0b1 pre-release
# follows an older spec (emits 1249.0). This small encoder follows v4.1 for objects, primitive arrays, tabular arrays of
# flat objects and list items. Not implemented (valid but non-canonical output): keyed tabular objects (§9.5) and
# nested field groups (§9.3) — such data is written in the plain nested / list form, which decoders read back fine.
_TOON_NUM = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?$", re.I)      # spec §7.2 (also "05", "+1")
_TOON_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")                                # spec §7.3
_TOON_ESC = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _toon_q(s: str) -> str:
    return '"' + "".join(_TOON_ESC.get(c) or (f"\\u{ord(c):04x}" if ord(c) < 32 else c) for c in s) + '"'


def _toon_val(v: Any) -> str:
    if v is None or isinstance(v, bool):
        return {None: "null", True: "true", False: "false"}[v]
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return "null"                                    # spec §3
        if v.is_integer() and abs(v) < 1e21:
            return str(int(v))                               # 1249.0 -> 1249, -0.0 -> 0
        s = repr(v)
        if "e" in s and abs(v) >= 1e-6:                      # canonical range: no exponent (1e-05 -> 0.00001)
            import decimal
            s = format(decimal.Decimal(s), "f")
        return s
    s = str(v)
    quote = (not s or s != s.strip(" \t") or s in ("true", "false", "null") or _TOON_NUM.match(s) or s[0] in "-#"
             or any(c in s for c in ':"\\[]{},') or any(ord(c) < 32 for c in s))
    return _toon_q(s) if quote else s


def _toon_key(k: Any) -> str:
    k = str(k)
    return k if _TOON_KEY.match(k) else _toon_q(k)


def _toon_obj(d: dict, ind: int) -> list[str]:
    pad, out = "  " * ind, []
    for k, v in d.items():
        if isinstance(v, dict):
            out += [f"{pad}{_toon_key(k)}:", *_toon_obj(v, ind + 1)]
        elif isinstance(v, list):
            out += _toon_arr(_toon_key(k), v, ind)
        else:
            out.append(f"{pad}{_toon_key(k)}: {_toon_val(v)}")
    return out


def _toon_arr(head: str, v: list, ind: int, item: bool = False) -> list[str]:
    pad = "  " * ind
    if not v:
        return [f"{pad}[0]:"] if item else [f"{pad}{head}: []" if head else f"{pad}[]"]
    if all(not isinstance(x, (dict, list)) for x in v):
        return [f"{pad}{head}[{len(v)}]: " + ",".join(map(_toon_val, v))]
    if not item and all(isinstance(x, dict) and x and all(not isinstance(y, (dict, list)) for y in x.values())
                        for x in v) and len({frozenset(x) for x in v}) == 1:
        cols = list(v[0])
        return [f"{pad}{head}[{len(v)}]{{{','.join(map(_toon_key, cols))}}}:",
                *(f"{pad}  " + ",".join(_toon_val(x[c]) for c in cols) for x in v)]
    out = [f"{pad}{head}[{len(v)}]:"]
    for x in v:
        if isinstance(x, (dict, list)):
            body = _toon_obj(x, ind + 2) if isinstance(x, dict) else _toon_arr("", x, ind + 1, item=True)
            out += [f"{pad}  - " + body[0].lstrip(), *body[1:]] if body else [f"{pad}  -"]
        else:
            out.append(f"{pad}  - {_toon_val(x)}")
    return out


def to_toon(obj: Any) -> str:
    """Encode a dict / list / primitive as TOON: indentation instead of braces, and ONE header line with the field
    names for a uniform list of flat records (`rows[60]{txn_id,date,amount}:` then one CSV-like line per record).
    On Nova 2 Lite: -57% tokens vs pretty JSON, -32% vs minified JSON, +4% vs CSV on 60 transactions — but it did not
    change answer accuracy, and minified JSON was smaller on nested data. Prune fields first (-85%)."""
    if isinstance(obj, dict):
        return "\n".join(_toon_obj(obj, 0))
    if isinstance(obj, list):
        return "\n".join(_toon_arr("", obj, 0))
    return _toon_val(obj)


def _columns(rows: list[dict]) -> list[str]:
    return list(dict.fromkeys(k for r in rows for k in r))


def md_table(rows: list[dict]) -> str:
    """A Markdown table from records, for the format comparison (pandas.to_markdown needs 'tabulate', not installed)."""
    if not rows:
        return ""
    cols = _columns(rows)
    cell = lambda v: "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(cell(r.get(c)) for c in cols) + " |" for r in rows]
    return "\n".join(lines)


def csv_text(rows: list[dict]) -> str:
    """CSV text (header + rows, '\\n' line ends) from records: the smallest format for flat tables on Nova."""
    import csv
    import io

    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_columns(rows), restval="", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().rstrip("\n")


# --- Strands context inspection ----------------------------------------------------------------------------------
def pin_message(agent: Any, predicate: Any) -> int:
    """Protect matching messages from context reduction; returns how many messages matched (and are now pinned).

    `predicate` is a function(message) -> bool, or a string that must appear in a text block. Strands has no public
    pin API; it honours msg["metadata"]["custom"]["pinned"] = True in SlidingWindow and Summarizing managers and in
    context_manager="auto" (research: the pinned case note survived auto's summaries verbatim, 2/2 vs 2/4). Metadata
    never reaches the model. Trap: with SummarizingConversationManager, pin a WHOLE turn (user through the assistant
    reply) — if the unpinned rest starts with an assistant message, Bedrock rejects the summary request and Strands
    swallows the error (the pin_first=1|2 bug)."""
    if isinstance(predicate, str):
        needle = predicate
        predicate = lambda m: any(needle in b.get("text", "") for b in m.get("content", [])  # noqa: E731
                                  if isinstance(b, dict))
    count = 0
    for msg in getattr(agent, "messages", agent):
        if predicate(msg):
            meta = msg.get("metadata") or {}
            custom = meta.get("custom") or {}
            custom["pinned"] = True
            meta["custom"] = custom
            msg["metadata"] = meta
            count += 1
    return count


def meter_hidden(model: Any, label: str) -> Any:
    """Make model calls made OUTSIDE the agent loop visible in LEDGER as agent=f"{label} (summarizer)".

    SummarizingConversationManager without summarization_agent, and context_manager="auto", call model.stream()
    directly, so their tokens appear in neither latest_agent_invocation nor LEDGER. The agent loop always passes
    invocation_state= to model.stream() and those hidden calls never do, so only the hidden calls are recorded (no
    double counting). Wraps the model instance in place; calling it twice is a no-op."""
    if getattr(model, "_mladas_meter_label", None):
        return model
    original = model.stream

    async def stream(*args: Any, **kwargs: Any):
        if "invocation_state" in kwargs:                 # agent-loop call: recorder() already bills it
            async for event in original(*args, **kwargs):
                yield event
            return
        t0, usage, stop = time.perf_counter(), {}, None
        try:
            async for event in original(*args, **kwargs):
                if isinstance(event, dict):
                    if "metadata" in event:
                        usage = event["metadata"].get("usage") or usage
                    elif "messageStop" in event:
                        stop = event["messageStop"].get("stopReason")
                yield event
        except Exception as e:
            stop = f"error: {type(e).__name__}"
            raise
        finally:
            _ledger_call(f"{label} (summarizer)", model.config.get("model_id", "?"), usage,
                         time.perf_counter() - t0, stop)

    model.stream = stream
    model._mladas_meter_label = label
    return model


def _result_text(tool_result: dict) -> str:
    parts = []
    for block in tool_result.get("content", []):
        if "text" in block:
            parts.append(block["text"])
        elif "json" in block:
            parts.append(json.dumps(block["json"], ensure_ascii=False, default=str))
    return "".join(parts)


def tool_trace(agent: Any) -> list[dict]:
    """One row per tool call in the agent's history, in call order: {tool, input_chars, output_chars, status}.
    input_chars = the JSON arguments the model wrote; output_chars = what the tool put back into the context (the
    number that decides whether a tool bloats the window). status is the toolResult status ('no result' if missing)."""
    rows: dict[str, dict] = {}
    for msg in agent.messages:
        for block in msg.get("content", []):
            if "toolUse" in block:
                tu = block["toolUse"]
                rows[tu["toolUseId"]] = {"tool": tu.get("name", "?"),
                                         "input_chars": len(json.dumps(tu.get("input", {}), ensure_ascii=False)),
                                         "output_chars": 0, "status": "no result"}
            elif "toolResult" in block:
                tr = block["toolResult"]
                row = rows.setdefault(tr["toolUseId"], {"tool": "?", "input_chars": 0})
                row.update(output_chars=len(_result_text(tr)), status=tr.get("status", "success"))
    return list(rows.values())


def context_rows(agent: Any) -> dict:
    """One row describing the latest turn: {messages, turn_input_tokens, per_call_input, context_size}.
    turn_input_tokens sums the turn's model calls (latest_agent_invocation — never the lifetime accumulated_usage,
    which was 6.7x too high by turn 6); per_call_input lists them; context_size is the last call's full prompt
    (cached tokens included), i.e. how full the window is. Append one per turn to chart context growth."""
    metrics = agent.event_loop_metrics
    inv = metrics.latest_agent_invocation
    return {"messages": len(agent.messages),
            "turn_input_tokens": inv.usage.get("inputTokens", 0) if inv else 0,
            "per_call_input": [c.usage.get("inputTokens", 0) for c in inv.cycles] if inv else [],
            "context_size": metrics.latest_context_size}


# ======================================================================================
# 8. Security helpers (M03, research-verified 2026-09-27)
# ======================================================================================
# A Policy in AgentCore deny comes back through the Gateway as HTTP 200 + JSON-RPC error -32002, which Strands turns
# into a tool result with status "error" and text "Tool Execution Denied: ... [Policy evaluation denied due to ...]".
# A local BeforeToolCallEvent gate (cancel_tool) produces status "error" with its own text ("AccessDenied ...").
_DENIED_TEXT = re.compile(r"Tool Execution Denied|AccessDenied|not allowed due to policy|denied by policy", re.I)


def _latest_turn_start(messages: list[dict]) -> int:
    """Index of the last user message that is a real prompt (not a toolResult carrier)."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") == "user" and not any(isinstance(b, dict) and "toolResult" in b for b in m.get("content", [])):
            return i
    return 0


def tool_outcomes(agent: Any, *, latest: bool = False, n: int = 160) -> list[dict]:
    """One row per tool call, in call order: {tool, input, status, text, denied, tool_use_id}.

    Works the same for local @tools and MCP Gateway tools (their names are "<Target>___<tool>"), because both end
    up as toolUse/toolResult blocks in agent.messages. status is the toolResult status ("success" | "error";
    "no result" if the loop stopped before the tool answered); text is the result, shortened to n characters;
    denied = status "error" AND the text is a deny message (Policy "Tool Execution Denied ...", a local gate's
    "AccessDenied ..."). latest=True keeps only the calls of the most recent invocation. `agent` may also be a list
    of messages. (tool_trace() is the size view of the same calls.)"""
    messages = list(getattr(agent, "messages", agent) or [])
    if latest:
        messages = messages[_latest_turn_start(messages):]
    rows: dict[str, dict] = {}
    for msg in messages:
        for block in msg.get("content", []):
            if not isinstance(block, dict):
                continue
            if "toolUse" in block:
                tu = block["toolUse"]
                rows[tu["toolUseId"]] = {"tool": tu.get("name", "?"), "input": dict(tu.get("input") or {}),
                                         "status": "no result", "text": "", "denied": False,
                                         "tool_use_id": tu["toolUseId"]}
            elif "toolResult" in block:
                tr = block["toolResult"]
                text = _result_text(tr)
                status = tr.get("status", "success")
                row = rows.setdefault(tr["toolUseId"], {"tool": "?", "input": {}, "tool_use_id": tr["toolUseId"]})
                row.update(status=status, text=short(text, n),
                           denied=status == "error" and bool(_DENIED_TEXT.search(text)))
    return list(rows.values())


def principal_of(invocation_state: dict | None) -> str | None:
    """The signed-in customer of a request, taken from what the APP verified (never from the prompt):
    invocation_state["caller"]["customer_id"] (the AuditLogger's caller claims), else
    invocation_state["principal_customer_id"]."""
    state = invocation_state or {}
    return (state.get("caller") or {}).get("customer_id") or state.get("principal_customer_id")


def _matches_tool(name: str, tool: str) -> bool:
    return name == tool or name.endswith("___" + tool)           # local @tool or "<Target>___<tool>" on a Gateway


def _gate_classes():
    from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

    class RefundPolicyGate(HookProvider):
        """LOCAL stand-in for a Cedar policy on the refund tool (default deny + permit when amount < limit and the
        destination is an account of the signed-in customer). Deterministic: sets cancel_tool, so the tool body never
        runs and the model reads the deny text as the tool result. .denied lists {tool, input, reason}."""

        def __init__(self, limit: float = 500, *, tool: str = "process_refund", amount_field: str = "amount",
                     destination_field: str = "destination_account", accounts: dict | None = None):
            self.limit, self.tool = float(limit), tool
            self.amount_field, self.destination_field = amount_field, destination_field
            if accounts is None:
                import bank_data
                accounts = bank_data.ACCOUNTS
            self.accounts = {c: {a["account_id"] for a in accts} for c, accts in accounts.items()}
            self.denied: list[dict] = []

        def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
            registry.add_callback(BeforeToolCallEvent, self._before)

        def _before(self, e: BeforeToolCallEvent) -> None:
            name = e.tool_use.get("name", "")
            if not _matches_tool(name, self.tool):
                return
            inp = dict(e.tool_use.get("input") or {})
            principal = principal_of(e.invocation_state)
            reasons = []
            try:
                amount = float(inp.get(self.amount_field) or 0)
            except (TypeError, ValueError):
                amount = float("inf")
            if amount >= self.limit:
                reasons.append(f"amount {amount:.2f} is not below the {self.limit:.0f} limit")
            if self.destination_field in inp and str(inp[self.destination_field]) not in self.accounts.get(principal, set()):
                reasons.append(f"destination {inp[self.destination_field]} is not an account of {principal}")
            if reasons:
                reason = "; ".join(reasons)
                self.denied.append({"tool": name, "input": inp, "reason": reason})
                e.cancel_tool = f"AccessDenied by policy: {reason}."

    class OwnershipGate(HookProvider):
        """LOCAL stand-in for identity scoping: the principal comes from the VERIFIED caller in invocation_state
        (principal_of), never from the prompt; a customer_id or card_last4 argument must belong to that principal.
        Sets cancel_tool on a mismatch (or when no principal was verified). .denied lists {tool, input, reason}."""

        def __init__(self, customers: dict | None = None):
            if customers is None:
                import bank_data
                customers = bank_data.CUSTOMERS
            self.cards = {cid: c.get("card_last4") for cid, c in customers.items()}
            self.denied: list[dict] = []

        def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
            registry.add_callback(BeforeToolCallEvent, self._before)

        def _before(self, e: BeforeToolCallEvent) -> None:
            inp = dict(e.tool_use.get("input") or {})
            if "customer_id" not in inp and "card_last4" not in inp:
                return
            principal = principal_of(e.invocation_state)
            bad = []
            if principal is None:
                bad.append("no verified caller")
            else:
                if "customer_id" in inp and inp["customer_id"] != principal:
                    bad.append(f"customer_id {inp['customer_id']}")
                if "card_last4" in inp and str(inp["card_last4"]) != self.cards.get(principal):
                    bad.append(f"card {inp['card_last4']}")
            if bad:
                reason = f"{', '.join(bad)} is not owned by the signed-in customer {principal}"
                self.denied.append({"tool": e.tool_use.get("name"), "input": inp, "reason": reason})
                e.cancel_tool = f"AccessDenied: {reason}."

    return RefundPolicyGate, OwnershipGate


def RefundPolicyGate(limit: float = 500, **kwargs: Any):  # noqa: N802 (factory that looks like a class)
    """RefundPolicyGate(limit=500, *, tool="process_refund", amount_field="amount",
    destination_field="destination_account", accounts=bank_data.ACCOUNTS) — see the class docstring."""
    return _gate_classes()[0](limit, **kwargs)


def OwnershipGate(customers: dict | None = None):  # noqa: N802
    """OwnershipGate(customers=bank_data.CUSTOMERS) — see the class docstring."""
    return _gate_classes()[1](customers)

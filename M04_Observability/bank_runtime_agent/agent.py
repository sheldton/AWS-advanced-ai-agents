"""AnyCompany Bank assistant on Amazon Bedrock AgentCore Runtime (MLADAS Module 4: observability and evaluation).

Deployed by agentcore_observability.RuntimeObsDeployment: direct code (no Docker), HTTP protocol (BedrockAgentCoreApp,
POST /invocations and GET /ping on 0.0.0.0:8080), entryPoint ["opentelemetry-instrument", "agent.py"] with
aws-opentelemetry-distro in requirements.txt. ADOT auto-instruments Strands, botocore (Bedrock) and Starlette, and
AgentCore injects every OTEL_* variable, so THIS FILE CONTAINS NO TRACING CODE. bank_data.py (the course's fictional
systems of record) is copied next to it at packaging time.

Payload (JSON)
  prompt           the customer's message
  customer_id      the signed-in customer, e.g. "CUST-1002"
  persona          "assistant" (default: balances, card transactions, fraud score)
                   "coach"      (a savings coach that gives general investment guidance; no disclaimer rule)
                   "coach_rule" (the same coach + the bank's disclaimer rule, deck knowledge-check Q2 scenario)
  simulate_outage  false | "timeout" (the card-risk service hangs 1.2 s, then TimeoutError) | "error" (HTTP 503 at once)
                   | true (= "timeout"): a deterministic TOOL failure; the agent recovers gracefully (slide 14)
  warmup           true -> no model call: starts the session's microVM so the first real request never waits for a cold start
Response: {answer, stop_reason, session_id, persona, trace_id, tools, tool_errors, model_calls, usage, latency_s,
           turns_in_session}  (usage = THIS request only: latest_agent_invocation, never the cumulative total)

One runtimeSessionId = one microVM = one conversation (the Agent objects live in AGENTS for the session's lifetime).
Nothing random, time-based, network- or credential-related happens at import (Runtime V1 starts it in ~1 s).
"""
from __future__ import annotations

import logging
import os
import re
import time

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from opentelemetry import trace
from strands import Agent, tool
from strands.models import BedrockModel

import bank_data as bank

MODEL_ID = os.getenv("BANK_MODEL_ID", "us.amazon.nova-2-lite-v1:0")
assert "amazon.nova" in MODEL_ID, f"Amazon Nova only (got {MODEL_ID})"      # course rule: never the Strands default
DISCLAIMER = "This is general information, not personalized investment advice."
FRAUD_TIMEOUT_S = 1.2
BOTO_SESSION = None            # a local harness may set a boto3 session; on the Runtime the execution role is used

app = BedrockAgentCoreApp()
log = logging.getLogger("bank_assistant")
log.setLevel(logging.INFO)
OUTAGE = {"mode": None}        # set per request from the payload (requests of one session never overlap)


@tool
def get_account_balances(customer_id: str) -> dict:
    """Current balances of all deposit accounts (checking, savings) of a customer.

    Args:
        customer_id: Bank customer id: "CUST-" followed by 4 digits, e.g. "CUST-0000".
    """
    return bank.get_account_balances(customer_id)


@tool
def list_card_transactions(card_last4: str) -> dict:
    """Recent transactions and current status (active/blocked) of a card.

    Args:
        card_last4: Last four digits of the card (4 digits, e.g. "0000").
    """
    return bank.list_card_transactions(card_last4)


@tool
def get_fraud_score(card_last4: str) -> dict:
    """Real-time fraud risk score (0-100) of a card from the card-risk service.

    Args:
        card_last4: Last four digits of the card (4 digits, e.g. "0000").
    """
    if OUTAGE["mode"] == "timeout":
        time.sleep(FRAUD_TIMEOUT_S)                       # the upstream hangs, then the client gives up
        raise TimeoutError(f"card-risk service did not answer within {FRAUD_TIMEOUT_S} s (upstream 504)")
    if OUTAGE["mode"] == "error":
        raise ConnectionError("card-risk service returned HTTP 503 (service unavailable)")
    txns = bank.CARD_TRANSACTIONS.get(card_last4, [])
    foreign = sum(1 for t in txns if t.get("country") not in (None, "US"))
    return {"card_last4": card_last4, "fraud_score": min(95, 12 + 20 * foreign), "model": "card-risk v7"}


RULES = ("Use the tools for every fact about balances, card transactions or fraud risk; never guess numbers. "
         "Answer in at most 4 short sentences, plain text, no markdown, no greetings. "
         "Never promise calls, emails, texts or actions that no tool performed.")
PROMPTS = {
    "assistant": ("You are AnyCompany Bank's customer-service assistant. The signed-in customer id is given in the "
                  "request. If a tool fails, say plainly which check is temporarily unavailable, answer with what the "
                  "other tools returned, and suggest trying again later. " + RULES),
    "coach": ("You are AnyCompany Bank's savings coach. The signed-in customer id is given in the request. When a "
              "customer asks about investing, give practical general guidance (emergency fund first, diversified "
              "low-cost index funds, time horizon). " + RULES),
}
PROMPTS["coach_rule"] = (PROMPTS["coach"] + " If you give ANY investment-related guidance, end your answer with this "
                         f'exact sentence: "{DISCLAIMER}"')
AGENTS: dict[tuple[str, str], Agent] = {}


def bank_agent(session_id: str, persona: str) -> Agent:
    """One Strands Agent (conversation) per (runtime session, persona)."""
    key = (session_id, persona)
    if key not in AGENTS:
        kw = {"model_id": MODEL_ID, "temperature": 0.2, "max_tokens": 1200}
        model = BedrockModel(boto_session=BOTO_SESSION, **kw) if BOTO_SESSION else BedrockModel(**kw)
        AGENTS[key] = Agent(name="bank_assistant", model=model, system_prompt=PROMPTS[persona], callback_handler=None,
                            tools=[get_account_balances, list_card_transactions, get_fraud_score],
                            trace_attributes={"mladas.module": "M04", "bank.persona": persona})   # on every Strands span
    return AGENTS[key]


def _strip_thinking(text: str) -> str:
    return re.sub(r"<thinking>.*?</thinking>", "", text or "", flags=re.S).strip()


def _trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()           # the POST /invocations server span (ADOT)
    return f"{ctx.trace_id:032x}" if ctx.is_valid else None


@app.entrypoint
def invoke(payload, context):
    session_id = getattr(context, "session_id", None) or "local"
    if payload.get("warmup"):
        return {"warm": True, "session_id": session_id, "trace_id": _trace_id(), "model_calls": 0, "usage": {}}
    persona = payload.get("persona", "assistant")
    persona = persona if persona in PROMPTS else "assistant"
    mode = payload.get("simulate_outage")
    OUTAGE["mode"] = "timeout" if mode is True else (mode if mode in ("timeout", "error") else None)
    agent = bank_agent(session_id, persona)
    n_before = len(agent.messages)
    t0 = time.time()
    try:
        result = agent(f"[customer_id={payload.get('customer_id', 'CUST-1001')}] {payload.get('prompt', 'Hello')}")
    finally:
        OUTAGE["mode"] = None
    answer = _strip_thinking("".join(b.get("text", "") for b in result.message["content"]))
    inv = result.metrics.latest_agent_invocation                  # THIS request (accumulated_usage = lifetime)
    usage = dict(inv.usage) if inv else {}
    new = agent.messages[n_before:]
    tools = [b["toolUse"]["name"] for m in new if m["role"] == "assistant" for b in m["content"] if "toolUse" in b]
    tool_errors = [b["toolResult"].get("toolUseId") for m in new if m["role"] == "user" for b in m["content"]
                   if "toolResult" in b and b["toolResult"].get("status") == "error"]
    latency = time.time() - t0
    log.info("answered session=%s persona=%s tools=%s tool_errors=%d latency_s=%.2f", session_id, persona, tools,
             len(tool_errors), latency)
    return {"answer": answer, "stop_reason": result.stop_reason, "session_id": session_id, "persona": persona,
            "trace_id": _trace_id(), "tools": tools, "tool_errors": len(tool_errors),
            "model_calls": len(inv.cycles) if inv else 0, "latency_s": round(latency, 2),
            "turns_in_session": sum(1 for m in agent.messages if m["role"] == "user"
                                    and any("text" in b for b in m["content"])),
            "usage": {k: int(usage.get(k, 0)) for k in ("inputTokens", "outputTokens", "totalTokens",
                                                         "cacheReadInputTokens")}}


if __name__ == "__main__":
    app.run()

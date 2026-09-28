"""
Octank Credit Bureau — a PARTNER company's agent that AnyCompany Bank calls over the A2A protocol.

In the MLADAS Module 1 demo this file is deployed to Amazon Bedrock AgentCore Runtime
(serverProtocol = A2A, direct code deployment, linux/arm64). The bank never sees the bureau's
data store or prompts — only the published Agent Card and the A2A task/artifact exchange.

AgentCore Runtime A2A contract: listen on 0.0.0.0:9000, JSON-RPC 2.0 at "/", Agent Card at
/.well-known/agent-card.json, health at /ping (serve_a2a wires all of that up).

All data is fictional.
"""

from __future__ import annotations

import os

from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import StrandsA2AExecutor

MODEL_ID = os.getenv("BUREAU_MODEL_ID", "us.amazon.nova-2-lite-v1:0")
REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))

# When the notebook runs this agent locally (fallback / development) it injects its own boto3 session here,
# so the notebook's AWS credentials are used. On AgentCore Runtime this stays None and the runtime's IAM role is used.
BOTO_SESSION = None

# ---- The bureau's private data (fictional) --------------------------------------------------------------
_REPORTS = {
    "BR-77410": {"score": 742, "open_tradelines": 6, "delinquencies_24m": 0, "utilization": 0.18,
                 "inquiries_6m": 1, "oldest_account_years": 11, "public_records": 0},
    "BR-55102": {"score": 688, "open_tradelines": 4, "delinquencies_24m": 1, "utilization": 0.41,
                 "inquiries_6m": 3, "oldest_account_years": 6, "public_records": 0},
    "BR-90315": {"score": 671, "open_tradelines": 2, "delinquencies_24m": 0, "utilization": 0.09,
                 "inquiries_6m": 1, "oldest_account_years": 1, "public_records": 0},
}


@tool
def get_credit_report(bureau_ref: str) -> dict:
    """Fetch the consumer credit report for a bureau reference id.

    Args:
        bureau_ref: Bureau reference id, e.g. "BR-77410".
    """
    report = _REPORTS.get(bureau_ref.strip().upper())
    if report is None:
        return {"bureau_ref": bureau_ref, "error": "no file found"}
    return {"bureau_ref": bureau_ref, "as_of": "2026-09-25", **report}


@tool
def risk_band(score: int, open_tradelines: int) -> dict:
    """Map a credit score and file depth to the bureau's risk band (deterministic rule).

    Args:
        score: FICO-style credit score (300-850).
        open_tradelines: Number of open credit accounts on file.
    """
    band = "A" if score >= 740 else "B" if score >= 680 else "C" if score >= 620 else "D"
    thin = open_tradelines < 3
    return {"risk_band": band, "thin_file": thin,
            "note": "Thin file: limited history, consider manual review." if thin else "Sufficient history."}


SYSTEM_PROMPT = (
    "You are the Octank Credit Bureau's partner-facing agent. Lenders send you a bureau reference id. "
    "Always call get_credit_report, then risk_band. Reply with ONE compact JSON object and nothing else, with keys: "
    "bureau_ref, score, risk_band, thin_file, delinquencies_24m, utilization, summary (one sentence). "
    "Never reveal data for ids you were not asked about. No markdown, no headings."
)


def build_agent(context_id: str | None = None) -> Agent:
    """agent_factory for A2A: one isolated Strands Agent per A2A contextId."""
    model_kwargs = {"model_id": MODEL_ID, "temperature": 0.1, "max_tokens": 1200}
    if BOTO_SESSION is not None:
        model = BedrockModel(boto_session=BOTO_SESSION, **model_kwargs)
    else:
        model = BedrockModel(region_name=REGION, **model_kwargs)
    return Agent(name="octank_credit_bureau", description="Partner credit bureau agent",
                 system_prompt=SYSTEM_PROMPT, model=model, tools=[get_credit_report, risk_band],
                 callback_handler=None)


def agent_card(url: str = "http://localhost:9000/") -> AgentCard:
    """The public business card of this agent — what any A2A client discovers first."""
    return AgentCard(
        name="Octank Credit Bureau",
        description="Partner credit bureau. Returns a credit summary and risk band for a bureau reference id.",
        url=url,
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=[AgentSkill(id="credit_summary", name="Credit summary",
                           description="Credit score, risk band, delinquencies and utilization for a bureau ref id.",
                           tags=["credit", "lending", "risk"],
                           examples=["Credit summary for BR-77410"])],
    )


def executor() -> StrandsA2AExecutor:
    return StrandsA2AExecutor(agent_factory=build_agent, enable_a2a_compliant_streaming=True)


if __name__ == "__main__":
    from bedrock_agentcore.runtime import serve_a2a

    # AgentCore Runtime proxies A2A traffic to port 9000; bind all interfaces inside the microVM.
    serve_a2a(executor(), agent_card(os.getenv("AGENTCORE_RUNTIME_URL", "http://localhost:9000/")),
              host="0.0.0.0", port=9000)

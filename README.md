# Building Advanced Agentic Systems on AWS: companion demos

Four short demos for the first four modules of the course **Building Advanced Agentic Systems on AWS** (MLADAS).
Each demo is a Jupyter notebook you can walk through in about 15 minutes. The notebooks build agents with
[Strands Agents](https://strandsagents.com) and [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/),
use Amazon Nova models only, and run live against an AWS account: they create real resources, measure what happens,
and delete what they created.

There are two ways to use this repository:

- **Read.** Each demo has an `_executed` copy that shows the outputs of one of our verified runs. You don't need an
  AWS account for that.
- **Run.** Run the clean notebook in your own AWS account. One run costs about $0.05–0.12 (see [Cost](#cost-per-run)).
  M04 also needs one account-level setting, CloudWatch Transaction Search (see [below](#cloudwatch-transaction-search-m04)).

> **Please note**
> - This is **not an official AWS training product**, and AWS does not maintain it. It is companion material for the
>   course. Slide numbers in the notebooks and READMEs (for example "slides 19–24") refer to the course deck of that
>   module.
> - **All data is fictional**: AnyCompany Bank, the Octank Credit Bureau, their customers, accounts, card numbers
>   (Luhn-valid test numbers, not real cards), policies and chat transcripts. Nothing here is financial advice.
> - Service behavior, quotas and prices are as observed in **September and October 2026** in us-east-1. Where a service behaved
>   differently from what the course deck describes, the notebook points it out.

## The demos

| Demo | What you'll see | Walk-through | Cost per run |
|---|---|---|---|
| **[M01 · Multi-agent architecture and communication patterns](M01_MultiAgent/)** | AnyCompany Bank splits one overloaded support agent into a team: an orchestrator that holds no back-end API and specialists that each hold only the APIs they need, measured against a single agent on accuracy and cost. The team shares one AgentCore Memory, so a specialist remembers a customer across days. A partner's credit-bureau agent runs on AgentCore Runtime behind the A2A protocol, and a Strands Graph makes a loan decision with code nodes, the remote agent as a node and a review loop. | ~15 min + ~5 min setup lead | ≈ $0.11–0.12 |
| **[M02 · Context engineering and performance optimization](M02_ContextEngineering/)** | Three ways to send the model less, each measured with a ledger of every model call: prompt caching on Nova, writing to and selecting from AgentCore Memory (including a case where selecting costs more than it saves), and AgentCore Gateway semantic tool search over 335 tools. | ~15 min + ~5 min setup lead | ≈ $0.05 |
| **[M03 · Security and compliance implementation](M03_Security/)** | Who may call the bank's agent and what it may do: JWT inbound auth on AgentCore Gateway, Cedar policies in Policy in AgentCore (LOG_ONLY, then ENFORCE), and a red-team before/after matrix that compares a guardrail hook with Cedar, with boundary probes at the refund limit. | ~15 min + ~3 min setup | ≈ $0.12 |
| **[M04 · Production monitoring, observability and evaluation](M04_Observability/)** | The bank's agent on AgentCore Runtime is traced with no tracing code (ADOT) and read back from CloudWatch as session → trace → span, including a tool that fails inside a request that succeeds. A local agent sends the same spans to three destinations over OTLP, a looping agent trips two live CloudWatch alarms (loop detection and cost protection), and AgentCore Evaluations grades the agent with Amazon Nova judges, on demand and online. | ~15 min + 5–10 min setup lead | ≈ $0.05–0.09 |

The demos are independent: run them in any order. Some cell text mentions longer versions of each module notebook
(a 60-minute "core" build and a "full" build with deep dives). Those are used in class and are not part of this
repository. An earlier, 20-minute version of these four demos is in this repository's git history.

## What you need

### An AWS account

- An AWS account in which you may create and delete resources. Use a **sandbox or test account**, not a production
  account.
- Region **us-east-1**. The demos were tested only there.
- **Amazon Bedrock** access to **Amazon Nova 2 Lite** (all four demos) and **Amazon Nova Micro** (M01 and M02) in
  us-east-1. The notebooks call them through the US cross-Region inference profiles `us.amazon.nova-2-lite-v1:0` and
  `us.amazon.nova-micro-v1:0`. Amazon models are normally available without a request; if your account still shows a
  *Model access* page in the Bedrock console, enable both models there. A `us.` profile can send a request to
  us-east-1, us-east-2 or us-west-2, so a service control policy that blocks those Regions breaks the calls.
- **Amazon Bedrock AgentCore** in us-east-1: Runtime and Memory (M01), Memory and Gateway (M02), Gateway and Policy
  (M03), Runtime and Evaluations (M04).
- **M04 only: CloudWatch Transaction Search turned on** in us-east-1 (see
  [below](#cloudwatch-transaction-search-m04)).
- Credentials with the permissions below.

### Permissions per demo

Every demo calls `sts:GetCallerIdentity` and invokes the Nova models (`bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream`, which also cover the Converse API). Beyond that, each demo creates, tags,
reads and deletes the resources in this table. Every resource carries the run's `RUN_ID` in its name (some names
write it without hyphens or with underscores), and most names start with `mladas` or `MLADAS`.

| Demo | What the setup creates | Services your credentials must manage |
|---|---|---|
| M01 | an AgentCore Runtime agent (A2A protocol) with its IAM execution role, an S3 bucket for its code package and its CloudWatch log groups; one AgentCore Memory | Bedrock AgentCore (Runtime, Memory), IAM roles, S3, CloudWatch Logs |
| M02 | one AgentCore Memory; one AgentCore Gateway with 3 targets, one Lambda function, 2 IAM roles and a log group | Bedrock AgentCore (Memory, Gateway), Lambda, IAM roles, CloudWatch Logs |
| M03 | an Amazon Cognito user pool with a domain and 4 app clients (the agent identities), 2 Lambda functions (a pre-token-generation trigger and the bank tools), 2 AgentCore Gateways with JWT auth, a policy engine with 6 Cedar policies, a Bedrock guardrail, a CloudWatch log group with a data protection policy, IAM roles | Bedrock AgentCore (Gateway, Policy), Amazon Cognito, Lambda, Bedrock Guardrails, CloudWatch Logs, IAM roles |
| M04 | an AgentCore Runtime agent (HTTP protocol, with ADOT) with its IAM execution role, an S3 bucket for its code package and its CloudWatch log groups; 4 AgentCore evaluators with a Nova judge; an online evaluation configuration with its IAM role and results log group (and a field index policy on the `aws/spans` log group); 2 high-resolution CloudWatch alarms on custom metrics | Bedrock AgentCore (Runtime, Evaluations), IAM roles, S3, CloudWatch Logs (including Logs Insights queries and index policies), CloudWatch metrics and alarms, X-Ray (`GetTraceSegmentDestination` to read the Transaction Search status, `PutSpans` to export spans) |

The simplest setup is an administrator role in a sandbox account. With a narrower role, allow `iam:PassRole` for the
roles the demos create (names starting with `mladas`), and `iam:CreateServiceLinkedRole`, which some services need the
first time they are used in an account. The shared modules also contain helpers for CloudTrail lookups and IAM Access
Analyzer that these four demos don't call.

### CloudWatch Transaction Search (M04)

M04 reads the bank agent's spans from the CloudWatch Logs log group `aws/spans`, which only receives spans when
**CloudWatch Transaction Search** is on. It is an **account-level setting per Region**: once it is on, X-Ray stores
every trace in that Region, from any application in the account, as spans in `aws/spans`, billed as CloudWatch Logs
ingestion. The notebook only reads its status and never changes it. Turn it on yourself (console or three AWS CLI
calls) and wait until it reports `ACTIVE`; the [M04 README](M04_Observability/README.md#turn-on-cloudwatch-transaction-search)
has the steps, the cost and how to turn it off again. M01–M03 don't need it.

### Account quotas

- **Amazon Bedrock requests and tokens per minute** for Nova 2 Lite and Nova Micro: new accounts can have low
  defaults. Throttling makes cells slower or makes them retry; M02 sends about 0.4 million input tokens in a run. If
  you see throttling errors, check these quotas in Service Quotas.
- The default AgentCore, Cognito, Lambda and CloudWatch quotas are enough for one run of each demo.

### Your computer

- macOS or Linux (we tested on macOS).
- [uv](https://docs.astral.sh/uv/), which creates the Python 3.12 environment (it can download Python 3.12 for you).
  M01 and M04 also use uv to package an agent for AgentCore Runtime (linux/arm64 wheels), so you need internet
  access to PyPI, and uv must be on the `PATH` that the Jupyter kernel sees.
- A Jupyter front end: JupyterLab, Jupyter Notebook, or VS Code with the Jupyter extension.
- The AWS CLI v2 (optional): the setup script uses it to check your credentials, and it is the easiest way to sign in
  with IAM Identity Center (`aws sso login`).

## Setup

```bash
git clone https://github.com/sheldton/AWS-advanced-ai-agents.git
cd AWS-advanced-ai-agents
./setup_env.sh
```

`setup_env.sh` creates `.venv` with Python 3.12, installs the pinned packages from `requirements.txt`, registers the
Jupyter kernel **MLADAS (Python 3.12)** and checks that your AWS credentials work.

**AWS credentials.** The notebooks use the standard AWS credential chain: environment variables, `AWS_PROFILE`,
IAM Identity Center (SSO), or an instance role. To use a named profile only for these demos, set
`MLADAS_AWS_PROFILE`. The Region defaults to us-east-1; keep it (`MLADAS_AWS_REGION` exists, but no other Region was
tested). Set these variables in the shell you start Jupyter from, because the kernel inherits them:

```bash
export MLADAS_AWS_PROFILE=my-sandbox      # optional; leave unset to use the standard chain
aws sts get-caller-identity --profile my-sandbox   # check the account (without a profile: drop --profile)
uvx --from jupyterlab jupyter-lab         # or any Jupyter front end; start it from the repository root
```

## Run a demo

1. Open the **clean** notebook, for example `M02_ContextEngineering/M02_context_engineering_demo15.ipynb` (not the
   `_executed` copy), and select the kernel **MLADAS (Python 3.12)**.
2. **Run the §0 setup cells first.** They print a `RUN_ID` (write it down) and start the slow resources in the
   background: AgentCore Memory, Runtime and Gateway; in M03, the identity provider, the gateways and the policy
   engine; in M04, the Runtime agent, the evaluators and the alarms. Give them about **5 minutes** (M03: about 3; M04:
   5–10, because its online evaluation needs about 10). You can also just carry on: the first cell that needs a slow
   resource waits for it on one status line.
3. Run the remaining cells in order, top to bottom. Read each section's text, and compare the outputs with *Expected
   results* in the module's README. Each demo has one discussion question marked **Think about it**: pause and
   answer it.
4. **Run the last cells.** They delete everything the run created and check each resource by id.

*Run All* works too; in M04 it reaches the online evaluation too early, and the
[M04 README](M04_Observability/README.md#run-it) says what to do. Don't restart the kernel in the middle of a run: the
module READMEs say how to pick a run up again if you must.

**The `_executed` copy** shows one of our verified runs (Oct 2026), with account identifiers masked. Your numbers
will differ within the ranges in the module READMEs. Model output varies from run to run, so each cell also prints a
deterministic check (✓/✗ or x/N); compare those.

## Cost per run

Measured in our test runs (October 2026), at us-east-1 on-demand list prices, before any free tier:

| Demo | Per run | Main items |
|---|---|---|
| M01 | ≈ $0.11–0.12 | Nova ≈ $0.07 · AgentCore Memory ≈ $0.032–0.041 · AgentCore Runtime < $0.01 |
| M02 | ≈ $0.05 | Nova ≈ $0.037–0.040 · AgentCore Memory and Gateway ≈ $0.01 |
| M03 | ≈ $0.12 | Nova and per-request service fees (Cognito tokens, Policy authorizations, Gateway calls, guardrail units) ≥ $0.115 |
| M04 | ≈ $0.05–0.09 | AgentCore Evaluations fees and Nova judge tokens ≈ $0.02–0.05 · Nova tokens of the agents ≈ $0.02 · Runtime and CloudWatch < $0.01 |

Each notebook prints its own bill at the end, from a ledger of every model call and service fee. What keeps costing
money if you skip the cleanup: mainly M04's two high-resolution alarms ($0.30 each per month) and its online
evaluation configuration, which keeps scoring any new session of the Runtime agent. The other resources cost little
while idle, but delete them too. Transaction Search (M04) stays on until you turn it off; see the M04 README.

## Cleanup

- The **last cells of each notebook** start the cleanup and check every resource by id (`gone ✓`). Don't skip them.
- If the kernel died, or you stopped early, run the module's **teardown script** from its folder, with the same
  `MLADAS_AWS_PROFILE` as the notebook:

  ```bash
  cd M01_MultiAgent
  ../.venv/bin/python teardown_m01.py --list                   # read-only: M01 resources in the account
  ../.venv/bin/python teardown_m01.py --run-id <RUN_ID>        # delete one run (asks first; --yes skips the prompt)
  ../.venv/bin/python teardown_m01.py --last                   # the most recent RUN_ID started from this folder
  ```

  M02, M03 and M04 work the same way (`teardown_m02.py`, `teardown_m03.py`, `teardown_m04.py`).
- The scripts only touch resources whose names or tags contain the given `RUN_ID`; there is no "delete everything"
  mode. **Never delete anything else**: don't run account-wide cleanups, because other work may be running in the
  account.

## Repository layout

```text
.
├── README.md
├── LICENSE                  MIT-0 (code)
├── LICENSE-CONTENT          CC BY-SA 4.0 (text)
├── .gitignore               keeps local run state and the venv out of commits
├── setup_env.sh             Python 3.12 venv + Jupyter kernel "MLADAS (Python 3.12)"
├── requirements.txt         the package versions the demos were verified with
├── mladas_common.py         AWS session, Nova-only agent factory, cost ledger, token counting, A2A helpers
├── mladas_viz.py            chart helpers (one colorblind-safe palette)
├── bank_data.py             AnyCompany Bank's fictional systems of record, tools and evaluation sets
├── bank_docs/               the bank's service handbook (M02 caches it)
├── agentcore_deploy.py      deploy an agent to AgentCore Runtime without Docker (M01)
├── agentcore_gateway.py     AgentCore Gateway with 335 bank tools and semantic search (M02)
├── agentcore_identity.py    the bank's Cognito identity provider and two JWT-protected gateways (M03)
├── agentcore_outbound.py    the Octank partner and the AgentCore Identity token vault (imported by M03; not built in this demo)
├── agentcore_policy.py      Policy in AgentCore: policy engine and Cedar policies (M03)
├── bedrock_guardrails.py    the bank's Bedrock guardrail and a hook that screens tool results (M03)
├── agentcore_audit.py       an audit log group that masks card numbers (M03)
├── agentcore_vpc.py         a private VPC, an interface endpoint and a probe inside it (imported by M03; not built in this demo)
├── agentcore_observability.py  traces, spans, local OpenTelemetry, alarms and AgentCore Evaluations (M04)
├── M01_MultiAgent/
│   ├── README.md
│   ├── M01_multi_agent_demo15.ipynb            run this
│   ├── M01_multi_agent_demo15_executed.ipynb   read this: the outputs of a verified run
│   ├── partner_credit_bureau/                  the partner's agent that M01 deploys to AgentCore Runtime
│   └── teardown_m01.py
├── M02_ContextEngineering/
│   ├── README.md
│   ├── M02_context_engineering_demo15.ipynb
│   ├── M02_context_engineering_demo15_executed.ipynb
│   └── teardown_m02.py
├── M03_Security/
│   ├── README.md
│   ├── M03_security_demo15.ipynb
│   ├── M03_security_demo15_executed.ipynb
│   └── teardown_m03.py
└── M04_Observability/
    ├── README.md
    ├── M04_observability_demo15.ipynb
    ├── M04_observability_demo15_executed.ipynb
    ├── bank_runtime_agent/                     the bank agent that M04 deploys to AgentCore Runtime (with ADOT)
    └── teardown_m04.py
```

The notebooks import the shared modules from the parent folder, so keep this layout. A run also writes local files
into its module folder: `.m01_run_ids` (and the M02–M04 equivalents), which `--last` reads. Keep them out of any
commit.

## Safety

- Every resource carries the `RUN_ID` in its name, and in a tag where the service supports tags, so you can always
  tell what a run created.
- Read a cell before you run it. The setup sections list what they create.
- M03 creates two internet-facing AgentCore Gateways for the length of a run. They require a valid token from the
  run's own Cognito pool, and the cleanup deletes them.
- M04 runs a small OTLP receiver inside the kernel that listens on `127.0.0.1` only (a random port). Its spans carry
  the demo's prompts and answers; the notebook drops the `aws.auth.*` span attributes (they hold the temporary access
  key id) and masks the account id before it prints a span.
- The notebooks don't print secrets: tokens are shortened and account ids are masked.
- Delete everything after use, with the last cells or the teardown script.

## Versions

Verified in September and October 2026 with Python 3.12, strands-agents 1.57.1, strands-agents-tools 0.8.9, bedrock-agentcore
1.23.1, a2a-sdk 0.3.x (Strands 1.57 speaks A2A 0.3), boto3 1.43.95 or later, opentelemetry-sdk 1.45.0 and, inside M04's
Runtime package, aws-opentelemetry-distro 0.20.0. §0 of each notebook checks the
versions. Newer releases of these packages or of the services may behave differently.

## License

- **Code** (the Python files, `setup_env.sh`, the requirements files, the notebooks' code cells and their recorded
  outputs) is licensed under the MIT No Attribution license (MIT-0). See [LICENSE](LICENSE).
- **Text** (the READMEs and the notebooks' prose, that is their markdown cells) is licensed under the Creative Commons
  Attribution-ShareAlike 4.0 International license (CC BY-SA 4.0). See [LICENSE-CONTENT](LICENSE-CONTENT).

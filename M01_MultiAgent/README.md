# M01 demo: multi-agent architecture and communication patterns

*AnyCompany Bank: from one overloaded agent to a team of specialists*

| | |
|---|---|
| Notebook | [`M01_multi_agent_demo15.ipynb`](M01_multi_agent_demo15.ipynb) (run this) · [`M01_multi_agent_demo15_executed.ipynb`](M01_multi_agent_demo15_executed.ipynb) (the outputs of a verified run) |
| Time | about 15 minutes to walk through, plus about 5 minutes of setup lead |
| Models | Amazon Nova 2 Lite and Amazon Nova Micro, us-east-1 |
| Cost | ≈ $0.11–0.12 per run |

**The story.** AnyCompany Bank (fictional) splits one overloaded customer-service agent into a team: specialists
behind an orchestrator, a partner credit bureau reached over A2A, and a loan-decision graph. Sofia Martinez
(`CUST-1001`) is remembered across days, Aisha Patel (`CUST-1003`) applies for a loan, and the Octank Credit Bureau is
another company's agent. All people and companies are fictional.

Prerequisites, setup and licenses are in the [repository README](../README.md).

## What you'll see

1. **An orchestrator with no back-end access, measured against one agent** (§2 · slides 7, 10, 13, 15, 17).
   Three specialists each get only the bank APIs they need, and the orchestrator gets none: its tools are the
   specialists. A *who may call what* matrix shows it, a routing trace follows four customer questions, and a dashboard
   compares the team with a single agent that holds all 13 APIs on the same 8 labelled questions. Least privilege
   holds by construction, and the team costs more per answer, not less.
2. **One AgentCore Memory for the whole team, Day 1** (§8 part 1 · slides 31–33). One memory with four built-in
   long-term strategies and per-customer namespaces. Every agent uses the same memory and session but its own actor
   id, so the lending specialist sees what Sofia told the orchestrator a moment earlier.
3. **A2A in one minute** (slides 18, 25). The Octank credit-bureau agent runs on AgentCore Runtime with IAM (SigV4)
   inbound auth. You fetch its Agent Card signed (cold, then warm), see an unsigned request refused with 403, and read
   the same card through boto3.
4. **A Graph for the loan decision** (§5 · slides 12, 19, 21). Code nodes, the remote A2A partner as a node, an
   AND-join and a capped review loop that ends with a person. A live trace and a Gantt chart show the route and where
   the time goes.
5. **Days 3 and 7: a specialist that never met Sofia remembers her** (§8 part 2 · slides 31, 34). Long-term records
   extracted from Day 1 reach a different specialist, and Sofia doesn't repeat herself.

The wrap-up (§9–§10 · slides 38, 44) asks one discussion question, shows what the run cost (Nova and Memory), and
deletes every resource, checking each by id.

## Before you run

The §0 setup cells return in about 2 seconds and start two slow resources in the background:

| Resource | Name | Ready after (our runs) |
|---|---|---|
| AgentCore Runtime agent: the partner credit bureau (A2A protocol, IAM inbound auth), with an IAM execution role, an S3 bucket for the code package and CloudWatch log groups | `mladas_m01_bureau_<RUN_ID>…`, `mladas-…-<RUN_ID>` | under a minute |
| AgentCore Memory with four built-in strategies (preferences, facts, session summaries, episodes); raw events expire after 7 days | `MLADAS_M01_<RUN_ID>…` | ~2.5 min |

To package the partner agent, setup uses `uv` to download linux/arm64 wheels from PyPI. If the runtime is not ready or
not answering, the notebook serves the same partner code on `localhost` and says so in a banner.

## Run it

1. Open `M01_multi_agent_demo15.ipynb` with the kernel **MLADAS (Python 3.12)**.
2. Run the §0 setup cells, everything above **▶ Demo starts here**, and write down the printed `RUN_ID`.
3. Wait about 5 minutes, or carry on at once: the **ready check** (the first cell after setup) waits on one status line
   (at most 4 minutes) until the runtime is READY and the memory is ACTIVE.
4. Run the rest in order. In our automated runs, with no head start, the whole notebook took 7–8 minutes of execution
   (435–469 s), including the waits: about 2.5 minutes in the ready check, up to a minute while §8 part 2 waits for
   the long-term records, and about 3 minutes for the cleanup.
5. Run §9 and §10 to the end. §10 waits for the cleanup and prints `gone ✓` for each resource.

**Optional flags.** Set them in the shell you start Jupyter from (the kernel inherits them), then restart the kernel:

| Flag | Effect |
|---|---|
| `M01_DEPLOY_RUNTIME=0` | no AgentCore Runtime: the A2A step and §5 use the partner code on `localhost` (with a banner). The discovery table then shows two plain GET rows, with no 403 row and no boto3 row; everything else runs |
| `M01_CREATE_MEMORY=0` | setup creates no memory; §8 creates it when it needs it and waits about 2.5 minutes |

## Expected results

In our test runs (Oct 2026: 3 runs, 2 of them on AgentCore Runtime); "x/N" counts runs.

| Section | What you should see |
|---|---|
| Ready check | `partner runtime: READY · AgentCore Memory: ACTIVE`; without a head start it waited 142–152 s |
| §2 matrix and tool specs | the orchestrator holds 0 of 13 APIs, and only the cards specialist can call `block_card`. Each specialist appears as a tool of type `agent` with one string input: its description is the routing logic |
| §2 routing trace | question X-1 fans out to `cards_specialist` and `lending_specialist`; every hop shows `header added by hook`; "Invented follow-up promises: none in 4 answers" (3/3) |
| §2.4 dashboard | the same accuracy for both designs (100% vs 100%, 3/3); the team made 1.9–2.0× the model calls at **1.23–1.34× the cost**. The ⚠ line: the single agent promised an email nobody will send in 3/3 runs, the team in 0/3 |
| §8 Day 1 | `lending_specialist · STM: 1 earlier turn(s) in this session from bank_orchestrator`, and `LTM: 0` for both agents: long-term extraction is still running. The reply quotes 7.99–12.49% and $506.79 and mentions `APP-2001` |
| A2A discovery | signed cold GET 5.1–6.5 s (a microVM starts), warm 0.19–0.23 s, **unsigned GET → 403** `Missing Authentication Token` in 0.11–0.14 s, boto3 `get_agent_card` `same card ✓`. The card says `protocolVersion 0.3.0` and advertises no security scheme, yet the endpoint enforces auth |
| §5 route | the same in every run: `intake → income_check + credit_bureau → affordability → underwriter (APPROVE) → reviewer (sent back: 2 triggers) → underwriter (REFER) → reviewer → decision_letter` |
| §5 Gantt | `affordability` needs only `income_check`, yet it starts when `credit_bureau` finishes: Strands starts the next batch of nodes only when the whole batch is done. On Runtime the Graph opens a new partner session, so `credit_bureau` is a cold call (7.2–9.9 s; 2.8 s on localhost). The letter checks are all ✓ (3/3) |
| §8 records | long-term facts and preferences such as a kitchen remodel starting in November and "email only", plus a Day-1 session summary (3/3) |
| §8 Days 3 and 7 | on Day 3 the cards specialist, which never met Sofia, loads 5–7 long-term records and uses the remodel and the email preference (3/3); Day 7 mentions the remodel in 2/3 runs. Sofia repeats nothing (3/3) |
| §9 cost | Nova ≈ $0.07 for the run. The Memory line: 12 events, 8–9 records stored and 46–63 records retrieved by 26 searches ≈ $0.032–0.041, 3.4–4.3× §8's own Nova cost: retrieval is the largest memory cost in this design |
| §10 | `gone ✓` for the runtime, the IAM role, the S3 bucket, the log groups and the memory (on localhost: the memory only) |

Why does §8 promise an email when §2 flagged exactly that? In §8 Sofia asked for email, and the prompts tell the team
to use her channel. No tool in this team sends email, so a notification tool or a person has to keep that promise.

## Cost

| Item | Per run |
|---|---|
| Amazon Nova inference (≈ 160–174k input and 6.7–7.1k output tokens, 104–107 model calls) | ≈ $0.07 |
| AgentCore Memory (events, records, retrievals) | ≈ $0.032–0.041 |
| AgentCore Runtime (active CPU and memory seconds; estimate) | < $0.01 |
| **Total** | **≈ $0.11–0.12** |

## Cleanup

§9's second code cell starts the cleanup in the background: it deletes the partner runtime (and waits for it), its log
groups, the IAM role, the S3 code bucket and the memory, and stops the local A2A server. §10 checks each resource by id.
If a line says `STILL THERE ✗`, or the kernel died, run from this folder:

```bash
../.venv/bin/python teardown_m01.py --list                    # read-only: M01 resources in the account
../.venv/bin/python teardown_m01.py --run-id <RUN_ID> --yes   # only this run's resources
```

`--last` picks the most recent `RUN_ID` started from this folder. The script only touches names that contain the
`RUN_ID`.

## Troubleshooting

- **The ready check waits.** Normal if setup ran less than about 3 minutes ago: the memory needs ~2.5 minutes.
- **"AgentCore Runtime is READY but not answering -> using the local backup instance"**, or a FALLBACK banner in the
  A2A step and §5: the partner runs on `localhost` instead. The demo continues; the Runtime-only rows are missing.
  Check that `uv` is on your `PATH` and that the runtime deployed without an error in §0.
- **CreateMemory failed** in setup (for example a permission error): §8 tries again when it needs the memory.
- **The long-term memory poll in §8 part 2 waits.** Extraction runs asynchronously, 1.5–2.5 minutes after Day 1. If
  you go straight from Day 1 to part 2, it waits on one status line.
- **Throttling** (`ThrottlingException`, slow cells): your Bedrock requests-per-minute quota is low or shared. Wait a
  minute and re-run the cell, or check the Nova quotas in Service Quotas.
- **The kernel died.** Run the teardown for that `RUN_ID`, then start again from §0 on a fresh kernel.
- **Some text mentions §3, §4 or other sections that this notebook doesn't have.** They are in the longer module
  notebook, which is not in this repository. For example, the A2A step's retry hint says "restart §3 on the
  fallback": here §3 is the A2A step.

## Known variance

- The team/single cost ratio moves between 1.23× and 1.34× (the number of model calls varies by one or two per
  question). The dashboard title states this run's number.
- Day 7 mentions the remodel in most runs, not all. The memory lines show it was loaded: retrieval makes context
  available, and the prompt decides whether the model uses it.
- The first Agent Card request and the Graph's partner call are slow because each new runtime session starts a
  microVM.
- The loan letter's wording varies. If the `no internal terms` check ever shows ✗, that is the point of it: code, not
  the prompt, guards the customer text.
- Episodic records don't appear within the demo's time frame; the demo doesn't rely on them.

# M01 demo: multi-agent architecture and communication patterns

*AnyCompany Bank: from one overloaded agent to a team of specialists*

| | |
|---|---|
| Notebook | [`M01_multi_agent_demo20.ipynb`](M01_multi_agent_demo20.ipynb) (run this) · [`M01_multi_agent_demo20_executed.ipynb`](M01_multi_agent_demo20_executed.ipynb) (the outputs of a verified run) |
| Time | about 20 minutes to walk through, plus about 5 minutes of setup lead |
| Models | Amazon Nova 2 Lite and Amazon Nova Micro, us-east-1 |
| Cost | ≈ $0.10–0.12 per run |

**The story.** AnyCompany Bank (fictional) splits one overloaded customer-service agent into a team: specialists
behind an orchestrator, a partner credit bureau reached over A2A, and a loan-decision graph. Sofia Martinez
(`CUST-1001`) is remembered across days, Aisha Patel (`CUST-1003`) applies for a loan, and the Octank Credit Bureau is
another company's agent. All people and companies are fictional.

Prerequisites, setup and licenses are in the [repository README](../README.md).

## What you'll see

1. **An orchestrator with no back-end access, measured against one agent** (§2 · slides 7, 8, 10, 13, 15, 17).
   Three specialists each get only the bank APIs they need, and the orchestrator gets none: its tools are the
   specialists. A *who may call what* matrix shows it, a routing trace follows four customer questions, and a dashboard
   compares the team with a single agent that holds every API. The output proves that least privilege holds by
   construction, and that the team costs more per answer, not less.
2. **One AgentCore Memory for the whole team, Day 1** (§8 part 1 · slides 31–33, 39). One memory with four built-in
   long-term strategies and per-customer namespaces. Every agent in the team uses the same memory and session but its
   own actor id, so the lending specialist sees what Sofia told the orchestrator a moment earlier.
3. **A partner's agent over A2A on AgentCore Runtime** (§3 · slides 9, 18, 25). Octank's credit-bureau agent is
   deployed from source to AgentCore Runtime (no Docker) with IAM (SigV4) inbound auth. You fetch its Agent Card,
   see an unsigned request refused, and call it with the Strands `A2AAgent`, cold and warm.
4. **A Graph for the loan decision** (§5 · slides 16, 21). Code nodes, the remote A2A partner as a node, and a review
   loop that can send a draft back. A live trace and a Gantt chart show the route and where the time goes.
5. **Days 3 and 7: a specialist that never met Sofia remembers her** (§8 part 2 · slides 26–34, 39). Long-term
   records extracted from Day 1 reach a different specialist, and Sofia doesn't repeat herself.

The wrap-up (§9–§10 · slides 38, 39, 45) asks one discussion question, shows what the run cost (Nova and Memory), and
deletes every resource, checking each by id.

## Before you run

The §0 setup cells return in about 2 seconds and start two slow resources in the background:

| Resource | Name | Ready after (our runs) |
|---|---|---|
| AgentCore Runtime agent: the partner credit bureau (A2A protocol, IAM inbound auth), with an IAM execution role, an S3 bucket for the code package and CloudWatch log groups | `mladas_m01_bureau_<RUN_ID>…`, `mladas-…-<RUN_ID>` | 37–38 s |
| AgentCore Memory with four built-in strategies (preferences, facts, session summaries, episodes); raw events expire after 7 days | `MLADAS_M01_<RUN_ID>…` | ~2.5 min |

To package the partner agent, setup uses `uv` to download linux/arm64 wheels from PyPI. If the runtime is not ready or
not answering, the notebook serves the same partner code on `localhost` and says so in a banner.

## Run it

1. Open `M01_multi_agent_demo20.ipynb` with the kernel **MLADAS (Python 3.12)**.
2. Run the §0 setup cells (everything above the ready check) and write down the printed `RUN_ID`.
3. Wait about 5 minutes, or carry on at once: the **ready check** (the first cell after setup) waits on one status line
   (at most 4 minutes) until the runtime is READY and the memory is ACTIVE.
4. Run the rest in order. In our automated runs, with no head start, the whole notebook took 8–9 minutes of execution
   (470–532 s), including those waits.
5. Run §9 and §10 to the end. §10 waits for the cleanup and prints `gone ✓` for each resource.

**Optional flags.** Set them in the shell you start Jupyter from (the kernel inherits them), then restart the kernel:

| Flag | Effect |
|---|---|
| `M01_DEPLOY_RUNTIME=0` | no AgentCore Runtime: §3 and §5 use the partner code on `localhost` (with a banner). The 403 row, the cold/warm timings and the deploy facts disappear; everything else runs. |
| `M01_CREATE_MEMORY=0` | setup creates no memory; §8 creates it when it needs it and waits about 2.5 minutes |

## Expected results

In our test runs (Sept 2026); "x/N" counts runs.

| Section | What you should see |
|---|---|
| Ready check | `partner runtime: READY · AgentCore Memory: ACTIVE`; without a head start it waited 141–152 s |
| §2 matrix and tool specs | the orchestrator holds 0 of the 13 bank APIs; only the cards specialist can call `block_card`. Each specialist appears as a tool with a single string input; its description is what the orchestrator routes on |
| §2 routing trace | one question fans out to both the cards and the lending specialist (6/6); every hop carries a header added by a hook; no invented follow-up promises in the team's 4 answers (6/6) |
| §2 dashboard | the team made 1.9–2.1× the model calls at **1.26–1.46× the cost** of the single agent, at equal accuracy (100% vs 100%). The single agent promised an email it has no tool to send in 6/6 runs; the team in 0/6 |
| §8 Day 1 | the lending specialist sees 1 earlier turn from the orchestrator in the same session (short-term memory) and 0 long-term records yet (6/6); the reply uses the bank's rate range and mentions application `APP-2001` |
| §3 deploy and discovery | deployed in 36.7–37.9 s. First signed Agent Card GET 5.1–6.7 s (a microVM starts), warm 0.33–0.36 s; an **unsigned GET gets 403**; the card reports protocol version 0.3.0 |
| §3 A2A calls | first call 8.0–9.6 s, warm 2.2–2.7 s (on localhost 2.3–2.6 s). The partner wrapped its JSON in code fences on every call (12/12): parse partner output defensively |
| §5 route | the same in every run: `intake → income_check + credit_bureau → affordability → underwriter → reviewer → underwriter → reviewer → decision_letter`; the reviewer sends the first draft back once |
| §5 Gantt | `affordability` needs only `income_check`, yet it starts when `credit_bureau` finishes: Strands starts the next batch of nodes only when the whole batch is done. On Runtime the Graph opens a new partner session, so `credit_bureau` is a cold call (8.3–9.4 s) |
| §5 tokens and letter | the Graph's `accumulated_usage` reported 17.2–19.0k tokens, while the per-node sum and the ledger showed 13.2–13.9k (6/6). Code checks the customer letter: all ✓ in 5/6 runs |
| §8 records | long-term facts and preferences such as a kitchen remodel starting in November 2026 and "email only", plus a Day-1 session summary in 5/6 runs; ready 91–146 s after the Day-1 session |
| §8 Days 3 and 7 | on Day 3 the cards specialist loads the remodel and the email preference from long-term memory and uses them (6/6); Day 7 mentions the remodel in 5/6 runs. Sofia repeats nothing (6/6) |
| §9 cost | Nova ≈ $0.07–0.08 for the run (§3 shows $0 because A2A returns no token usage). A Memory line such as 12 events, 5 records stored and 45 records retrieved by 26 searches ≈ $0.03, 2.5–3× §8's Nova cost: retrieval is the largest memory cost in this design. Per unit: agent-as-tool 1.3–1.5× one single-agent answer; the Graph 3.6–4.4× per loan application |
| §10 | `gone ✓` for the runtime, the IAM role, the S3 bucket, the log groups and the memory; the cleanup took about 3 minutes (161–184 s) |

Why does §8 promise an email when §2 flagged exactly that? In §8 Sofia asked for email, and the prompts tell the team
to use her channel. No tool in this team sends email, so a notification tool or a person has to keep that promise;
the Day-1 cell says so.

## Cost

| Item | Per run |
|---|---|
| Amazon Nova inference (≈ 170–176k input and 6.8–7.6k output tokens, 105–108 model calls) | ≈ $0.07–0.08 |
| AgentCore Memory (events, records, retrievals) | ≈ $0.025–0.03 |
| AgentCore Runtime (active CPU and memory seconds; estimate) | < $0.01 |
| **Total** | **≈ $0.10–0.12** |

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
- **"AgentCore Runtime is READY but not answering -> using the local backup instance"**, or a FALLBACK banner in §3
  and §5: the partner runs on `localhost` instead. The demo continues; the Runtime-only rows are missing. Check that
  `uv` is on your `PATH` and that the runtime deployed without an error in §0.
- **CreateMemory failed** in setup (for example a permission error): §8 tries again when it needs the memory.
- **The long-term memory poll in §8 part 2 waits.** Extraction runs asynchronously, 1.5–2.5 minutes after Day 1. If
  you go straight from Day 1 to part 2, it waits on one status line.
- **Throttling** (`ThrottlingException`, slow cells): your Bedrock requests-per-minute quota is low or shared. Wait a
  minute and re-run the cell, or check the Nova quotas in Service Quotas.
- **The kernel died.** Run the teardown for that `RUN_ID`, then start again from §0 on a fresh kernel.
- **Some text mentions §1, §4, §6 or §7.** Those sections are in the longer module notebook, which is not in this
  repository.

## Known variance

- The team/single cost ratio moves between 1.26× and 1.46× (the number of model calls varies by one or two per
  question). The chart title states this run's number.
- The loan letter's wording varies. Once in 6 runs it mentioned the credit score, and the `no internal terms` check
  caught it: code, not the prompt, guards the customer text.
- Day 7 mentions the remodel in most runs, not all. The memory lines show it was loaded: retrieval makes context
  available, and the prompt decides whether the model uses it.
- The first Agent Card request and the first A2A call are slow because a microVM starts; the Graph opens its own
  session, so its partner call is cold too.
- Episodic records don't appear within the demo's time frame; the demo doesn't rely on them.

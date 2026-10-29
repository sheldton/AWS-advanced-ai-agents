# M02 demo: context engineering and performance optimization

*AnyCompany Bank: what the model reads, what it costs, and how to send less of it*

| | |
|---|---|
| Notebook | [`M02_context_engineering_demo15.ipynb`](M02_context_engineering_demo15.ipynb) (run this) · [`M02_context_engineering_demo15_executed.ipynb`](M02_context_engineering_demo15_executed.ipynb) (the outputs of a verified run) |
| Time | about 15 minutes to walk through, plus about 5 minutes of setup lead |
| Models | Amazon Nova 2 Lite and Amazon Nova Micro, us-east-1 |
| Cost | ≈ $0.05 per run |

**The story.** AnyCompany Bank's support agent (fictional) answers from a 6,400-token service handbook, remembers
Sofia Martinez from last week and can reach 335 tools, all through one context window. Three changes send the model
less, and a ledger of every model call shows what each one saved, or cost, per request.

Prerequisites, setup and licenses are in the [repository README](../README.md).

## What you'll see

1. **Prompt caching on Nova** (§5.2 · slides 19–21; the discussion question is on slide 20). The static handbook goes
   first, followed by a cache checkpoint; four customers' questions run with and without it. The output shows cache
   writes and reads per call and what a hit costs compared with a plain call.
2. **Write to and select from AgentCore Memory** (§6.2–6.4 · slides 25–29). Records extracted from last week's chat
   next to records the application wrote directly; relevance scores and a threshold calibrated on them; then three
   arms: replay last week's transcript, select a few records, or use the deck's much stricter cut-off. The output
   shows which arm answers correctly, and that fewer prompt tokens is not always cheaper.
3. **Semantic tool search on AgentCore Gateway** (§9.3–9.4 · slides 44–46). One Gateway serves the bank's 335 tools
   to every agent; semantic search hands the model the 10 best-matching tools instead of all of them. The output
   measures the catalog in tokens and compares the first tool the model picks, with and without search.

The wrap-up (slide 53) puts the three changes side by side as cost per request, then deletes every resource and
checks each by id.

## Before you run

The §0 setup cells take about 4 seconds and start two slow resources in the background:

| Resource | Name | Ready after (our runs) |
|---|---|---|
| AgentCore Memory with long-term strategies, seeded with last week's chats and records the application writes directly | `MLADAS_M02_<RUN_ID>-…` | `SEEDED` about 2.5–3 min after setup; last week's records searchable about 2 min 50 s to 3 min 10 s after setup started |
| AgentCore Gateway with semantic search and IAM (SigV4) inbound auth; 3 targets on one Lambda function that answers all 335 tools; its log group; 2 IAM roles | `mladas-m02-gw-<RUN_ID>-…`, `mladas-m02-banktools-…`, `mladas-m02-gwfn-…` | READY after ~1 minute |

§0.5 also shows that the Bedrock CountTokens API refuses Nova model ids (as of Sept 2026), so the notebook measures
tokens with a one-token Converse call instead. The error line it prints is expected.

## Run it

1. Open `M02_context_engineering_demo15.ipynb` with the kernel **MLADAS (Python 3.12)**.
2. Run the §0 cells, down to **Setup ends here**, and write down the printed `RUN_ID`.
3. Wait about 5 minutes. The **ready check** should then show Memory `SEEDED` and Gateway `READY`. If you carry on
   sooner, §6's first cell waits for the memory on one status line (up to 5 minutes).
4. Run the rest in order. In our automated runs the whole notebook took 231–246 s, most of it §6 waiting for the
   memory (145–177 s); with a 5-minute head start, the cells after setup execute in about 65–70 s.
5. Run the wrap-up to the end: its first code cell starts the cleanup, and the last cell (10.4) checks each resource.

**Don't restart the kernel during a run.** A new kernel loses the handle to the memory, and re-running setup would
create a second one. If you must restart: copy the memory id from the ready check's `CREATING` line, set both
`M02_RUN_ID=<RUN_ID>` and `M02_MEMORY_ID=<memory id>` in the shell that starts Jupyter, and re-run setup. The memory
and the Gateway are adopted. The final cleanup leaves an adopted memory in place, so run the teardown script afterwards
(see [Cleanup](#cleanup)).

**Optional flags** (set them in the shell you start Jupyter from):

| Flag | Effect |
|---|---|
| `M02_CREATE_MEMORY=0` | no Memory: §6 prints one ⚠ line per cell and skips; the wrap-up chart loses the §6.4 bar |
| `M02_CREATE_GATEWAY=0` | no Gateway: §9.3 counts the local catalog (the same ~29k tokens) and §9.4 is skipped; the wrap-up chart loses the §9.4 bar |

With both off, the notebook runs in about 22 s and costs about $0.025.

## Expected results

In our test runs (Oct 2026, 5 end-to-end runs); "x/N" counts runs.

| Section | What you should see |
|---|---|
| Ready check | Memory `STARTING → CREATING → ACTIVE → SEEDED`; Gateway through roles, Lambda, gateway and targets to `READY`. Right after setup it shows the early states; 5 minutes later, `SEEDED` and `READY` |
| §5.2 caching | the cached arm hit on 2, 3 or 4 of 4 calls (the `us.` inference profiles cache on a best-effort basis; which call misses varies). A hit cost **52–71% less** than the same question without a checkpoint. A cache write is the cheapest row of all, because Nova bills cache writes at $0. The handbook fact is correct 4/4 in both arms |
| §6.2 inventory | extracted facts 6–17, preferences 3–6, session summaries 1–3, direct writes 2–3: the counts depend on how far the asynchronous extraction has got |
| §6.3 scores | unrelated questions scored up to 0.360–0.364 and the weakest record holding an answer 0.405–0.441, so a threshold calibrated at 0.38 separates them. A cut-off of 0.8 kept **0 of 25** records in every run |
| §6.4 replay vs select | correct answers: replay 2/3, select 3/3, the 0.8 cut-off 0/3, in every run. Select sent 24–38% of replay's prompt tokens, yet cost **26–32% more** per question ($0.00065–0.00071 vs $0.00050–0.00055): at $0.0005 a retrieval pays off only above ~1,515 saved input tokens, and select saved 889–1,087 |
| §9.3 catalog | `tools/list` returns 336 tools (335 plus the search tool) in ~2.2–2.5 s; two clients get an identical catalog (same SHA-256). All definitions cost **29,058 tokens** on every call, ~84 per tool |
| §9.4 search | per request 31,797 tokens with all tools vs 1,420 with search (**22× fewer**), every run. First pick correct: all tools 6–7/10, search 8–10/10 |
| Wrap-up | cost per request: caching 52–71% cheaper · memory select 26–32% *more expensive* · search 92% cheaper; the chart title "2 of 3 changes cut the cost per request" |
| 10.4 cleanup | `gone ✓` for the Memory, the Gateway, the Lambda function and both IAM roles, then "All 5 resources of RUN_ID … are gone" (every run). The first line, about Code Interpreter sessions, belongs to a part of the full module and is a no-op here |

## Cost

| Item | Per run |
|---|---|
| Amazon Nova inference (about 397–404k input tokens, mostly the all-tools arm of §9.4 on Nova Micro and the §9.3 catalog probe) | $0.037–0.040 |
| AgentCore fees in the ledger (Memory events, 10–11 retrievals, Gateway searches and calls) | ≈ $0.010–0.011 |
| Memory storage, Lambda and Gateway time (not in the ledger) | < $0.001 |
| **Total** | **≈ $0.05** |

## Cleanup

The wrap-up's first code cell starts the cleanup in the background: Gateway targets, gateway, Lambda function, log
group, IAM roles, and the memory. The last cell checks each resource by id. The memory shows `DELETING` for a few
minutes and finishes on its own; the check accepts that.

If 10.4 prints **NOT CLEANED**, you stopped before the wrap-up, or you adopted a memory after a restart, run from this
folder:

```bash
../.venv/bin/python teardown_m02.py --list                    # read-only: what each M02 run id still has
../.venv/bin/python teardown_m02.py --run-id <RUN_ID> --yes   # delete this run's resources; ends with "All gone ✓"
```

Prefer `--run-id` over `--last` if you ran the notebook several times. The script only touches names that contain the
`RUN_ID`.

## Troubleshooting

- **No cache hit in §5.2.** Re-run the cell. Every one of our runs had at least 2 hits out of 4.
- **The first call of the cached arm is already a hit.** The prompt cache is shared across your account and Region
  for about 5 minutes, so a re-run (or a colleague's run) within that time finds the handbook cached. The cell text
  explains it.
- **§6's first cell waits.** Setup ran less than about 3 minutes ago; long-term extraction is still running.
- **§6.3 prints ✗ "calibrated".** On a fresh memory the weakest correct record scored under 0.38, so the select arm
  gets no record for that question. It didn't happen in our runs, and it shows why a threshold needs calibration.
- **§9.4: search is behind all-tools.** Nova Micro answered in text ("no call") although the right tool ranked near
  the top. The token saving holds every time.
- **Throttling** (slow cells, retries): §5.2 and §9.4 take longer; the status lines keep counting. The notebook never
  runs more than 5 model calls at once. Check your Nova quotas in Service Quotas if it persists.
- **Some text mentions §1.2, §9.5 or other missing subsections.** They are in the longer module notebook, which is not
  in this repository.

## Known variance

- **Cache hits:** 2/4 to 4/4, and which call misses changes from run to run. A smaller saving (about half rather than
  two thirds) happened in one of our runs; the headline always states this run's number.
- **Replay's wrong answer** is the dispute question: it denies a dispute "from last week", sometimes while quoting the
  case id. Replayed turns carry no dates, and the system prompt says it's a new session today. The correctness rule
  counts that hedge as wrong.
- **Memory inventory counts** depend on how far extraction has got when you reach §6.2.
- **§9.4's first picks** vary by a request or two between runs; most misses are "no call" rather than a wrong tool.

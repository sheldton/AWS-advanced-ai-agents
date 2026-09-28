# M02 demo: context engineering and performance optimization

*AnyCompany Bank: what the model reads, what it costs, and how to send less of it*

| | |
|---|---|
| Notebook | [`M02_context_engineering_demo20.ipynb`](M02_context_engineering_demo20.ipynb) (run this) · [`M02_context_engineering_demo20_executed.ipynb`](M02_context_engineering_demo20_executed.ipynb) (the outputs of a verified run) |
| Time | about 20 minutes to walk through, plus about 5 minutes of setup lead |
| Models | Amazon Nova 2 Lite and Amazon Nova Micro, us-east-1 |
| Cost | ≈ $0.08–0.09 per run |

**The story.** AnyCompany Bank's support agent (fictional) answers from a 6,400-token service handbook, remembers
Sofia Martinez from last week, reads James Chen's fraud-incident export and can reach 335 tools, all in one context
window. Four techniques send the model less, and a ledger of every model call shows what each one saved, or cost, per
request.

Prerequisites, setup and licenses are in the [repository README](../README.md).

## What you'll see

1. **Prompt caching on Nova** (§5.2 · slides 19–24; discussion question on slide 20). The static handbook goes first,
   followed by a cache checkpoint; the same questions run with and without it. The output shows cache writes and reads
   per call and what a hit costs compared with a plain call.
2. **Write to and select from AgentCore Memory** (§6.2–6.4 · slides 18, 25–29). Records extracted from last week's
   chat next to records the application wrote directly; relevance scores and a threshold calibrated on them; then
   three arms: replay last week's transcript, select a few records, or use the deck's much stricter cut-off. The
   output proves which arm answers correctly, and shows that fewer prompt tokens is not always cheaper.
3. **Offloading a large tool result** (§7.4 · slides 30–33). One tool returns a ~17,500-token export. Three arms run
   in parallel: keep it in context, Strands' automatic context manager (a preview plus a retrieve tool), and the
   `ContextOffloader` plugin. The output compares input tokens and correctness.
4. **Semantic tool search on AgentCore Gateway** (§9.3–9.4 · slides 38–47). One Gateway serves the bank's 335 tools
   to every agent; semantic search hands the model the 10 best-matching tools instead of all of them. The output
   measures the catalog in tokens and compares the first tool the model picks, with and without search.

The wrap-up (slides 48–54) puts the four changes side by side as cost per request, then deletes every resource and
checks each by id.

## Before you run

The §0 setup cells take about 5 seconds and start two slow resources in the background:

| Resource | Name | Ready after (our runs) |
|---|---|---|
| AgentCore Memory with long-term strategies, seeded with last week's chats (19 events) and 4 records the application writes directly | `MLADAS_M02_<RUN_ID>-…` | ACTIVE after ~2.5 min; last week's records searchable 3 min 42 s to 4 min 25 s after setup started |
| AgentCore Gateway with semantic search and IAM (SigV4) inbound auth; 3 targets on one Lambda function that answers all 335 tools; its log group; 2 IAM roles | `mladas-m02-gw-<RUN_ID>-…`, `mladas-m02-banktools-…`, `mladas-m02-gwfn-…` | READY after ~1 minute |

§0.5 also shows that the Bedrock CountTokens API refuses Nova model ids (as of Sept 2026), so the notebook measures
tokens with a one-token Converse call instead. The error line it prints is expected.

## Run it

1. Open `M02_context_engineering_demo20.ipynb` with the kernel **MLADAS (Python 3.12)**.
2. Run the §0 cells and write down the printed `RUN_ID`.
3. Wait about 5 minutes. The **ready check** (the first cell after setup) should then show Memory `SEEDED` and Gateway
   `READY`. If you carry on sooner, §6's first cell waits for the memory on one status line (up to 5 minutes).
4. Run the rest in order. In our automated runs the whole notebook took 288–329 s, most of it §6 waiting for the
   memory; with a 5-minute head start, the cells after setup execute in about 80 s.
5. Run the wrap-up to the end: its first code cell starts the cleanup, and the last cell (10.4) checks each resource.

**Don't restart the kernel during a run.** A new kernel loses the handle to the memory, and re-running setup would
create a second one. If you must restart: copy the memory id from the ready check's `CREATING` line, set both
`M02_RUN_ID=<RUN_ID>` and `M02_MEMORY_ID=<memory id>` in the shell that starts Jupyter, and re-run setup. The memory
and the Gateway are adopted. The final cleanup leaves an adopted memory in place, so run the teardown script afterwards
(see [Cleanup](#cleanup)).

**Optional flags** (set them in the shell you start Jupyter from):

| Flag | Effect |
|---|---|
| `M02_CREATE_MEMORY=0` | no Memory: §6 prints one ⚠ line per cell and skips; the wrap-up chart shows 3 bars |
| `M02_CREATE_GATEWAY=0` | no Gateway: §9.3 counts the local catalog (the same ~29k tokens) and §9.4 is skipped |

With both off, the notebook runs in about 30 s and costs about $0.05.

## Expected results

In our test runs (Sept 2026, 10 end-to-end runs); "x/N" counts runs.

| Section | What you should see |
|---|---|
| Ready check | Memory `STARTING → CREATING → ACTIVE → SEEDED` ("19 events, 4 direct records"); Gateway through roles, Lambda, gateway and targets to `READY` |
| §5.2 caching | the cached arm hit on 2 or 3 of 4 calls (the `us.` inference profiles cache on a best-effort basis; which call misses varies). A hit cost **68–71% less** than the same question without a checkpoint. A cache write is the cheapest row of all, because Nova bills cache writes at $0. The handbook fact is correct 4/4 in both arms |
| §6.2 inventory | extracted facts 7–23, preferences 3–8, session summaries 1–3, direct writes 1–3: the counts depend on how far the asynchronous extraction has got |
| §6.3 scores | unrelated questions scored 0.353–0.367 and the weakest correct record 0.384–0.494, so a threshold calibrated at 0.38 separates them (by a margin of 0.004 to 0.114). A cut-off of 0.8 kept **0 of 25** records in every run |
| §6.4 replay vs select | correct answers: replay 2/3, select 3/3, the 0.8 cut-off 0/3, in 10/10 runs. Select sent 19–29% of replay's prompt tokens, yet cost **22–31% more** per question ($0.00062–0.00067 vs $0.00050–0.00054): at $0.0005 a retrieval pays off only above ~1,515 saved input tokens, and select saved 1,015–1,157 |
| §7.4 offloading | the export is ~17,564 tokens (50,675 characters). The automatic context manager used 0.46–0.55× the input tokens of keeping the export in context in 7/10 runs, and 0.94× in 3/10 (see *Known variance*); the offloader plugin 0.41–0.76×; answers 6/6 in every run |
| §9.3 catalog | `tools/list` returns 336 tools (335 plus the search tool) in ~2.5 s; two clients get an identical catalog (same SHA-256). All definitions cost **29,058 tokens** on every call, ~84 per tool |
| §9.4 search | per request 31,797 tokens with all tools vs 1,420 with search (**22× fewer**), every run. First pick correct: all tools 5–8/10, search 5–10/10; search was ahead in 9/10 runs |
| Wrap-up | cost per request: caching 68–71% cheaper · memory select 22–31% *more expensive* · offloading 43–53% cheaper (about 5% in the 0.94× runs) · search 91–92% cheaper |
| 10.4 cleanup | `gone ✓` for the Memory, the Gateway, the Lambda function and both IAM roles, then "All 5 resources of RUN_ID … are gone" (10/10). The first line, about Code Interpreter sessions, belongs to a part of the full module and is a no-op here |

## Cost

| Item | Per run |
|---|---|
| Amazon Nova inference (about 475–507k input tokens, mostly the all-tools arm of §9.4 on Nova Micro and the §9.3 catalog probes) | $0.063–0.075 |
| AgentCore fees in the ledger (19 Memory events, 11–25 retrievals, Gateway searches and calls) | $0.011–0.018 |
| Memory storage, Lambda and Gateway time (not in the ledger) | < $0.001 |
| **Total** | **≈ $0.08–0.09** |

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
- **§6's first cell waits.** Setup ran less than about 4.5 minutes ago; long-term extraction is still running.
- **§6.3 prints ✗ "calibrated".** On a fresh memory the weakest correct record scored under 0.38, so the select arm
  gets no record for that question. It didn't happen in our runs (the smallest margin was 0.004), and it shows why a
  threshold needs calibration.
- **§9.4: search is behind all-tools.** Nova Micro answered in text ("no call") although the right tool ranked in
  the top 3. The token saving holds every time.
- **Throttling** (slow cells, retries): §5.2 and §9.4 take longer; the status lines keep counting. The notebook never
  runs more than 5 model calls at once. Check your Nova quotas in Service Quotas if it persists.
- **Some text mentions §5.4, §9.5 or other missing subsections.** They are in the longer module notebook, which is not
  in this repository.

## Known variance

- **Cache hits:** 2/4 or 3/4, and which call misses changes from run to run.
- **Offloading at 0.94×:** in a third of our runs, the agent retrieved without a search pattern and pulled the whole
  export back ("chars retrieved" ≈ 40,000), so it saved almost nothing. That is why the prompt asks for a narrow
  pattern. The wrap-up bar for §7.4 then drops to about 5%.
- **Replay's wrong answer** usually quotes last week's case id but treats the dispute as opened today: replayed turns
  carry no dates, and the system prompt says it's a new session today. The correctness rule counts a
  self-contradicting answer as wrong.
- **Memory inventory counts** depend on how far extraction has got when you reach §6.2.

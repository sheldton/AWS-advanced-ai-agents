# M04 demo: production monitoring, observability and evaluation

*AnyCompany Bank: see inside the bank agent, alarm on it, and grade it*

| | |
|---|---|
| Notebook | [`M04_observability_demo20.ipynb`](M04_observability_demo20.ipynb) (run this) · [`M04_observability_demo20_executed.ipynb`](M04_observability_demo20_executed.ipynb) (the outputs of a verified run) |
| Time | about 20 minutes to walk through, plus 5–12 minutes of setup lead |
| Models | Amazon Nova 2 Lite (the agents and every judge), us-east-1 |
| Cost | ≈ $0.05–0.09 per run |
| Account setting | **CloudWatch Transaction Search must be on** in us-east-1 (see [below](#turn-on-cloudwatch-transaction-search)); the notebook only reads it |

**The story.** AnyCompany Bank's Nova 2 Lite assistant runs on Amazon Bedrock AgentCore Runtime. James Chen
(`CUST-1002`) asks it about his card while the card-risk service is down, Sofia Martinez (`CUST-1001`) asks about a
wire that never leaves `PENDING` and whether she should invest her savings. The demo reads what the agent did from its
traces, alarms on a runaway session, and has Nova judges grade the answers. All people, companies and card numbers are
fictional.

Prerequisites, setup and licenses are in the [repository README](../README.md).

## What you'll see

1. **Session → trace → span, and a tool that fails inside a request that succeeds** (§1 · slides 4, 8–15, 18,
   21–26, 30, 31). The bank agent on AgentCore Runtime contains no tracing code: the AWS Distro for OpenTelemetry
   (ADOT) adds the spans. The notebook reads James's three requests back from CloudWatch as a span tree and a waterfall.
   His fraud check times out, yet the request answers with HTTP 200: the Runtime's `Errors` metric stays at 0, and
   only the log and the trace show the failure. A local agent then sends the same kind of spans to three places at
   once (memory, an OTLP receiver that stands in for another vendor's backend, and CloudWatch), and 2,000 synthetic
   traces show what head sampling throws away.
2. **A looping agent trips two live CloudWatch alarms** (§2 · slides 5, 7, 27–29). A wire assistant polls a wire
   that stays `PENDING`. Nothing fails, and it burns about 6× the tokens of a normal session. A Strands hook publishes
   per-session metrics; a loop-detection alarm and a cost-protection alarm (10-second periods, actions off) fire
   within seconds. The deck's session-duration alarm is checked against what the service really publishes.
3. **AgentCore Evaluations with Nova judges, on demand and online** (§3 · slides 17, 33–48, 53, 54). Built-in
   evaluators re-created on a Nova 2 Lite judge, plus a custom judge for the bank's investment-disclaimer rule. The
   savings coach is graded with and without the rule, locally and on AgentCore Runtime; then the notebook reads the
   results that an online evaluation wrote for the setup's warm-up conversations, with no call from the notebook.
4. **What was measured, what it cost, cleanup verified by id** (§4 · slides 6, 49, 55, 56).

## Before you run

### Turn on CloudWatch Transaction Search

**What it is.** Transaction Search is how CloudWatch stores and searches trace data: X-Ray writes every span it
receives as a structured log event into the CloudWatch Logs log group `aws/spans`, and indexes a percentage of them as
trace summaries. AgentCore Runtime (through ADOT) and the X-Ray OTLP endpoint that this demo's local agent exports to
both need it. With it off, spans have nowhere to go.

**It is an account-level setting.** It applies to the whole account in one Region (here us-east-1), not to this demo:
once it is on, **every** trace sent to X-Ray in that Region, from any application in the account, is stored as spans
in `aws/spans` and billed as CloudWatch Logs ingestion. The X-Ray trace views and APIs then only see the indexed share
of traces. Turn it on only in an account where that is acceptable (a sandbox account is ideal), or ask whoever owns the
account. **The notebook never turns it on or off**: §0 and §1.1 only read its status.

**Check it** (read-only):

```bash
aws xray get-trace-segment-destination --region us-east-1   # add --profile <name> if you use MLADAS_AWS_PROFILE
```

`"Destination": "CloudWatchLogs"` with `"Status": "ACTIVE"` means you are ready. `"XRay"`, or `"PENDING"`, means not
yet.

**Turn it on in the console.** CloudWatch console, Region us-east-1 → **Application Signals** → **Transaction
search** → **Enable Transaction Search**, select the option to **ingest spans as structured logs**, keep or set the
indexing percentage (1 % is enough for this demo) → **Save**. Console layouts change; in some versions the same switch
is under **Settings** → **X-Ray traces** → **Transaction Search**.

**Or with the AWS CLI** (the same steps as the console). Replace `<ACCOUNT_ID>` with your 12-digit account id:

```bash
# 1. Let X-Ray write spans into CloudWatch Logs (a Logs resource policy for the service principal xray.amazonaws.com)
aws logs put-resource-policy --region us-east-1 --policy-name TransactionSearchXRayAccess --policy-document '{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "TransactionSearchXRayAccess",
    "Effect": "Allow",
    "Principal": {"Service": "xray.amazonaws.com"},
    "Action": "logs:PutLogEvents",
    "Resource": ["arn:aws:logs:us-east-1:<ACCOUNT_ID>:log-group:aws/spans:*",
                 "arn:aws:logs:us-east-1:<ACCOUNT_ID>:log-group:/aws/application-signals/data:*"],
    "Condition": {"ArnLike": {"aws:SourceArn": "arn:aws:xray:us-east-1:<ACCOUNT_ID>:*"},
                  "StringEquals": {"aws:SourceAccount": "<ACCOUNT_ID>"}}
  }]
}'

# 2. Send trace segments to CloudWatch Logs instead of X-Ray's own store
aws xray update-trace-segment-destination --region us-east-1 --destination CloudWatchLogs

# 3. Optional: the share of spans indexed as X-Ray trace summaries (the demo works with 1 %)
aws xray update-indexing-rule --region us-east-1 --name Default --rule '{"Probabilistic": {"DesiredSamplingPercentage": 1}}'
```

The status goes from `PENDING` to `ACTIVE` within minutes (about 7 in our test account). Wait for `ACTIVE` before you
run §0. You need `logs:PutResourcePolicy`, `xray:UpdateTraceSegmentDestination` and `xray:UpdateIndexingRule` for these
steps; the notebook itself only needs `xray:GetTraceSegmentDestination` (to read the status) and `xray:PutSpans` (to
export the local agent's spans).

**What it costs.** Spans in `aws/spans` are billed as CloudWatch Logs data: ingestion ($0.50 per GB in us-east-1,
September 2026) and storage ($0.03 per GB-month; check the log group's retention). Indexed spans cost $0.75 per million
($0.00000075 each), and only the indexing percentage counts. One run of this demo ingests well under 1 MB of spans:
fractions of a cent, included in the CloudWatch line of §4's bill. The standing cost is whatever **other** traced
applications in the account send once the setting is on.

**If it is off,** the notebook still runs, but with little to show in §1 and §3: §0's `Telemetry:` line reports
CloudWatch as `OFF (Transaction Search is …)`, the Runtime's spans don't arrive in `aws/spans`, so §1.1 finds no
traces and §1.2 shows recorded results instead (labelled); §1.3 marks the CloudWatch destination with ⚠, and the
Runtime evaluations in §3.3 and §3.4 have no spans to score.

**Turning it back off** (optional, after the demo): `aws xray update-trace-segment-destination --region us-east-1
--destination XRay`. Spans already ingested stay in `aws/spans` until its retention removes them. Check the AWS
documentation first if other applications in the account use X-Ray.

### What setup creates

The §0 cells start four parts in the background:

| Part | What setup creates | Ready after (our runs) |
|---|---|---|
| runtime → warm-up | the bank agent on AgentCore Runtime (HTTP protocol, direct code deployment with ADOT, entry point `opentelemetry-instrument agent.py`): an S3 bucket for its code package, an IAM execution role, the runtime and its CloudWatch log groups. Then two warm-up conversations with the savings coach (without and with the disclaimer rule, 2 turns each) | ~1–1.5 min |
| evaluators → online | 4 evaluators with a Nova 2 Lite judge (3 derived from built-ins: Helpfulness, GoalSuccessRate, ToolSelectionAccuracy; 1 custom disclaimer judge), then an online evaluation configuration on the runtime's log group with its own IAM role and results log group (100 % sampling) | ~5 s, then ~2 s after the runtime |
| alarms | one normal wire-agent session (the token baseline), then 2 high-resolution CloudWatch alarms with actions disabled, on custom metrics in the namespace `MLADAS/M04` | ~15 s |
| telemetry | one OpenTelemetry pipeline in the kernel for the local agents: in memory, a small OTLP receiver on `127.0.0.1` (random port), and CloudWatch through the X-Ray OTLP endpoint | instant |

Every name contains the `RUN_ID` with its hyphens removed (`mladas-m04-…` or `mladas_m04_…`). The readiness check
(0.5) returned 64–70 s after setup started in our runs.

Two more things change in the account while a run lasts:
- Creating the online evaluation adds a field index policy (`resource.attributes.service.name`) to the `aws/spans`
  log group. The cleanup removes it only if this run added it and no other online evaluation configuration remains.
- Spans written to `aws/spans` and datapoints of the `MLADAS/M04` custom metrics can't be deleted. Both cost nothing
  once idle; the 1-second metric data ages out after 3 hours.

**Packaging the Runtime agent** runs `uv` on your machine to download linux/arm64 wheels (Strands, bedrock-agentcore,
`aws-opentelemetry-distro`) from PyPI, so you need internet access and `uv` on the `PATH` of the Jupyter kernel.

## Run it

1. Check that Transaction Search is `ACTIVE` (above).
2. Open `M04_observability_demo20.ipynb` with the kernel **MLADAS (Python 3.12)**.
3. **Run §0, down to and including 0.5 "Ready check", 5–12 minutes before you go on to §1.** Write down the printed
   `RUN_ID`. The last line should read `Ready: 5/5 parts READY · … · online results expected in ~10 min`. Two timers
   matter:
   - The **online evaluation** scores a session about 10 minutes after it ends (6–11 minutes in our runs). §3.4 reads
     the results for the warm-up conversations, so it needs §0 to have finished about 10 minutes earlier.
   - §1's Runtime session was warmed by §0 and **idles out after 15 minutes**. If you wait longer, §1's first request
     includes a cold start (5–8 s more); nothing else changes.
4. Run §1, §2 and §3 in order. Each cell prints its evidence and a ✓/✗ check. §3 ends with one question marked
   **Think about it**.
5. Run §4 to the end. Its first cell books the charges that bill by time or volume and starts the cleanup in the
   background; the last cell waits up to 90 s and checks every resource by id.

With *Run All*, §3.4 comes about a minute after the warm-ups ended, too early for results: it prints
`Batch evaluation: TOO_EARLY` and a ⚠ line with the minutes to wait. Re-run that cell later: once the warm-ups are
about 5 minutes old it runs a batch evaluation of the same sessions (~60–70 s), and after about 10 minutes the online
results are there. Re-running §3.4 before §4 is fine; after §4 the resources are gone.

**Optional flags** (set them in the shell you start Jupyter from; each affected cell prints one ⚠ line and shows
recorded or local results, labelled as such):

| Flag | Effect |
|---|---|
| `M04_DEPLOY_RUNTIME=0` | no Runtime agent: §1 shows a recorded Runtime trace plus the live local agent; §3.3 and §3.4 have nothing to score |
| `M04_CREATE_EVALUATORS=0` | no evaluators: §3 shows the code check plus judge labels recorded in a test account, marked "(rec.)" |
| `M04_CREATE_ALARMS=0` | no alarms: §2's loop still runs, the alarm part shows recorded timings |
| `M04_ONLINE_EVAL=0` | no online evaluation configuration: §3.4 goes straight to the batch evaluation |
| `M04_WARMUP=0` | no warm-up conversations: §1's first request is a cold start, §3.3 and §3.4 have no session to score |
| `M04_FORCE_FAIL=runtime`, `evaluations`, `alarms` or `otlp` | makes that part fail on purpose, so you can see the fallback |
| `M04_RUN_ID=<RUN_ID>` | adopt an existing run's resources (after a kernel restart) |

## Expected results

In our test runs (Sept 2026: 8 fresh runs, 4 with every cell back to back and 4 with a pause after setup as described
above); "x/N" counts checks within a run.

| Section | What you should see |
|---|---|
| §1.1 traces | the entry point read from the live runtime, `['opentelemetry-instrument', 'agent.py']`, and 0 calls that create spans in `agent.py`; Transaction Search `CloudWatchLogs / ACTIVE`. James's 3 requests: 3 traces of 9 spans each (27 in all), warm requests 1.2–1.6 s, the one with the outage 2.4–3.1 s. `✓ Tokens … 3/3` (the `invoke_agent` counters grow about 2,500 → 5,600 → 9,300 because they are cumulative), `✓ session.id is on the spans Strands never created too` (SERVER 3/3, CLIENT 6/6), `✓ Every trace complete … after 1.3–8.8 s` |
| §1.2 failed tool | waterfall title "get_fraud_score failed after 1.2 s, and the request still answered after 2.2–2.8 s". Metric `Errors 0` over 3–8 invocations, log `tool_errors=1`, the trace names the span; the span record shows status `ERROR` and an `exception` event `TimeoutError`, with three ✓ lines (no ERROR above it, HTTP 200 answer, graceful wording) |
| §1.3 three places | 15 spans, `✓ 15/15` at the OTLP receiver and in CloudWatch; gzip 78–84 % smaller; CloudWatch adds 6 attributes at ingestion; `✓ get_card_status spans in order: ERROR → OK` (failed once, retried) |
| §1.4 sampling | 2,000 synthetic traces: head sampling at 20 % stores 79 % fewer spans but keeps only 39 of 190 error traces; tail sampling keeps 190/190 and still stores 72 % fewer. §1's own 5 traces are too few to show a rate: which ones head sampling keeps changes every run |
| §2.1 loop | loop: about 14,800 tokens, 10 tool calls, stop reason `limit_turns`, an empty reply to Sofia and 0 ERROR spans of 31; normal session about 2,300 tokens → **6.4×** |
| §2.2 alarms | both alarms went to ALARM in every run: 10–24 s after their condition was met and 7–22 s after the 10-turn cap had already stopped the agent (the cap stopped it after 6.7–9.0 s) |
| §2.3 slide 29 | the deck's alarm (retyped with our own names) fails with `SyntaxError: unterminated string literal`; `'region' is not a Region`; `bedrock-agentcore / Duration series: 0`; the runtime's `lifecycleConfiguration` is 900 s idle / 28,800 s maximum |
| §3.1 evaluators | "AWS built-ins today: 18 (SESSION 4, TOOL_CALL 4, TRACE 10) + 13 third-party"; the deck's `agentcore_evaluation` package: not installed (it does not exist); judge = Amazon Nova on 4/4 evaluators |
| §3.2 local coaches | the S&P 500 question: **NonCompliant without the rule, Compliant with it**, and the code check agrees 2/2; the balance replies: Compliant 2/2 although the sentence is in 0/2 (the judge also decides whether a reply is investment guidance) |
| §3.3 Runtime session | 7/7 results from 4 evaluators: GoalSuccessRate Yes, Helpfulness "Very Helpful 0.83" on both turns, disclaimer Compliant (balance) and NonCompliant (S&P 500), ToolSelectionAccuracy Yes on both turns. 11 on-demand evaluations: $0.0165 in fees plus about $0.007 of Nova judge tokens |
| §3.4 online | with the setup lead: the configuration table, "online results for them so far: 10 of 10", a chart "Online evaluation: 10 results, logged 10–11 min after the warm-ups ended" and the same label as §3.3 on 5/5. Back to back: `TOO_EARLY` and a ⚠ line (see [Run it](#run-it)) |
| §4 wrap-up | 4.1: one line per highlight with the numbers above; 4.2: the bill (see [Cost](#cost)); 4.3: `14 gone ✓ · 0 NOT CLEANED` about 36–37 s after the cleanup started, then "All resources of RUN_ID … are gone, each checked by id." |

The `_executed` copy comes from a run that paused 12 minutes after §0, as described in [Run it](#run-it), so its §3.4
shows the online results. A run that executes every cell back to back reaches §3.4 about a minute after the warm-ups
and shows the `TOO_EARLY` path instead.

## Cost

§4.2 prints this run's bill from a ledger of every model call plus the time- and volume-based charges.

| Item | Back to back | With the setup lead |
|---|---|---|
| Nova 2 Lite tokens, the agents (warm-ups, baseline, §1, the loop, §3's coaches) | $0.021–0.022 | $0.021 |
| Nova judge tokens: on demand (measured) + online (estimated) | $0.007 | $0.015–0.020 |
| AgentCore Evaluations, $0.0015 per evaluation | $0.0165 (11) | $0.032–0.042 (21–28) |
| AgentCore Runtime hosting (estimated from vCPU- and GB-hours) | $0.001 | $0.003 |
| CloudWatch: alarms, custom metrics, `PutMetricData`, span ingestion, Logs Insights | $0.0006 | $0.0009 |
| **Total (§4's bill line)** | **≈ $0.05** | **≈ $0.07–0.09** |

With the setup lead the online evaluation also scores §1's session if it has gone idle before §4 runs, which adds
evaluations. Not in the ledger, each under $0.001: S3 storage of the code package, X-Ray span indexing, other
CloudWatch reads, IAM (free). If you skip the cleanup, the two high-resolution alarms keep costing $0.30 each per
month, the online evaluation configuration keeps scoring (and billing) any new session of the Runtime agent, and the
rest costs little while unused. Delete everything anyway.

## Cleanup

§4's first cell starts the cleanup in dependency order: the online evaluation configuration (with its results log
group, batch jobs and role, and the `aws/spans` index policy if this run added it), the evaluators, the alarms, the
Runtime agent (live sessions stopped first; its log groups, role and bucket), then the local telemetry. The last cell
checks every resource by id. If §4 was skipped or failed, or the kernel died, run from this folder:

```bash
../.venv/bin/python teardown_m04.py --list                    # read-only: what each M04 run id still has
../.venv/bin/python teardown_m04.py --run-id <RUN_ID> --yes   # delete one run; ends with "All gone ✓"
../.venv/bin/python teardown_m04.py --last                    # the most recent RUN_ID started from this folder
```

Add `--profile <name>` if you use a named profile. The script only touches names that contain the `RUN_ID`; spans in
`aws/spans` and the custom metrics stay (they can't be deleted and cost nothing once idle). The cleanup does not change
Transaction Search (see [turning it back off](#turn-on-cloudwatch-transaction-search)).

## Troubleshooting

- **`Telemetry: … cloudwatch OFF (Transaction Search is XRay/…)`** or §1.1 finds no traces. Transaction Search is off
  or still `PENDING`: see [Turn on CloudWatch Transaction Search](#turn-on-cloudwatch-transaction-search) and wait
  for `ACTIVE`. The local telemetry is set up once per kernel, so restart the kernel with `M04_RUN_ID=<RUN_ID>` set
  (see *The kernel restarted* below) and run §0 again.
- **`OFF or FAILED: <part>`** in the readiness line. That part is switched off or failed, often for lack of a
  permission (Bedrock AgentCore Runtime or Evaluations, IAM roles, CloudWatch alarms) or of model access to Nova 2 Lite.
  Run `OBS_STACK.errors` in a new cell to see why; to retry, fix the cause and re-run the §0 cells (a failed part
  retries and adopts what this `RUN_ID` already has).
- **The runtime fails with "packaging failed" or "uv not found".** Install [uv](https://docs.astral.sh/uv/) where the
  kernel can find it and make sure PyPI is reachable.
- **The runtime fails with "… a launcher AgentCore cannot run" or "Runtime initialization time exceeded".** The real
  path of the kernel's Python contains a space, which breaks the ADOT launcher inside the package. Use a Python
  installation (or a clone location) whose path has no spaces, then re-run §0.
- **§0 stops with `opentelemetry-sdk … found, 1.45.x expected`.** Re-run `../setup_env.sh`: `requirements.txt` pins
  the OpenTelemetry versions the demo was verified with.
- **§1.2 says the metric has counted only some requests.** CloudWatch publishes the Runtime's metrics 10–90 s after a
  call; re-run the cell.
- **§2 says the alarms are not back to OK.** After an ALARM they return to OK 70–80 s later; this only happens when you
  re-run §2 within that time. Wait a minute and re-run §2.
- **§3.4 prints `TOO_EARLY`.** The warm-ups are less than ~5 minutes old: re-run the cell later (see [Run it](#run-it)).
- **The kernel restarted.** Set `M04_RUN_ID=<RUN_ID>` in the shell that starts Jupyter (or run
  `%env M04_RUN_ID=<RUN_ID>` in a new first cell) and run §0 again: it adopts every resource in about 30 s, but it also
  sends new warm-ups, so the online results come about 10 minutes after that.
- **Throttling** shows as slower requests and retries: check your Bedrock quotas for Nova 2 Lite.

## Known variance

- **Alarm timing:** 10–24 s after the condition in these runs (11–28 s in other tests). Sometimes the cost alarm fires
  first, sometimes the loop alarm.
- **Online results** arrived 10–11 minutes after the warm-ups in all four runs with a setup lead (6–11 minutes in other
  tests). The service can write `ValidationException` rows for §1's session (its warm-up call has no agent span); §3.4
  reads only the warm-up sessions.
- **Judges vary:** ToolSelectionAccuracy said "No" once in 18 of our section runs. Read the judge's explanation, not
  only its label.
- **Model wording** changes from run to run: compare the ✓/✗ checks and the x/N counts, not the prose.
- **Your account's traffic:** §1.2's invocation count includes the warm-up calls made in the same minutes (3–8), and
  other applications that write to `aws/spans` don't affect the demo (it filters by session id).

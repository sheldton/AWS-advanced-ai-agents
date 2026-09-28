"""
Safety net for Module 4: find and delete what ONE notebook run created, by its RUN_ID. Run by a human, never
automatically (the account may be shared with other work: this script only touches names that carry the run id).

    python teardown_m04.py --list                     # every M04 run id found in the account (+ what each still has)
    python teardown_m04.py --last                     # the most recent run id started from this folder (.m04_run_ids)
    python teardown_m04.py --run-id 20260928-...      # a specific run
    add --profile <name> for a named AWS profile (default: $MLADAS_AWS_PROFILE, else boto3's standard chain),
    --yes to skip the confirmation prompt, --allow-dev to delete a shared builder dev stack (run ids
    YYYYMMDD-000000-dev?, in either spelling: 20260928000000devm names the same resources)

What a run creates (§0.4; every name carries the run suffix = the RUN_ID's lowercase letters and digits, <sfx>):
  * AgentCore Runtime mladas_m04_bank_<sfx> (+ its log group /aws/bedrock-agentcore/runtimes/mladas_m04_bank_<sfx>-…,
    never-expire retention), IAM role mladas-m04-bank-<sfx>, S3 bucket mladas-m04-<sfx>
  * evaluators mladas_m04_<sfx>_{helpful,goal,toolsel,disclaimer} (Nova 2 Lite judges)
  * online evaluation config mladas_m04_<sfx>_online, its results log group /aws/bedrock-agentcore/evaluations/results/
    mladas_m04_<sfx>_online-…, IAM role mladas-m04-oe-<sfx>; batch evaluations mladas_m04_<sfx>_batch_* and their streams
    in the account's shared batch results log group (only our streams are deleted)
  * CloudWatch alarms mladas-m04-<sfx>-loop-detection and mladas-m04-<sfx>-cost-protection
Not deletable (and $0 once idle): spans in the shared aws/spans log group (30-day retention) and the custom metrics in
MLADAS/M04 (1-s data ages out after 3 h). The aws/spans index policy that CreateOnlineEvaluationConfig adds is reverted
only when no online evaluation config is left in the account and it is exactly the service's policy.
There is no delete-all mode.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
try:                                                   # python.org builds on macOS ship without trust roots
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass

import agentcore_observability as obs  # noqa: E402
import mladas_common as mc  # noqa: E402
from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402

RUNS_FILE = HERE / ".m04_run_ids"
SFX_RE = re.compile(r"(\d{14}[a-z0-9]{4})")             # YYYYMMDDHHMMSS + 4 (hex or 'dev?')


def known_run_ids() -> list[str]:
    return RUNS_FILE.read_text().split() if RUNS_FILE.exists() else []


def run_id_for(sfx: str, known: dict[str, str]) -> str:
    """Names carry the run suffix. Map it back: this laptop's .m04_run_ids first, then the notebook's own format."""
    if sfx in known:
        return known[sfx]
    if len(sfx) == 18 and sfx[:14].isdigit():
        return f"{sfx[:8]}-{sfx[8:14]}-{sfx[14:]}"
    return sfx


def run_ids_in_account(session) -> list[str]:
    known = {obs.run_suffix(r): r for r in known_run_ids()}
    sfx: set[str] = set()

    def take(name: str, prefix: str) -> None:
        if name.startswith(prefix):
            m = SFX_RE.match(name[len(prefix):])
            if m:
                sfx.add(m.group(1))

    ctl, dp, logs, iam, cw = (session.client(c) for c in ("bedrock-agentcore-control", "bedrock-agentcore", "logs",
                                                           "iam", "cloudwatch"))
    for page in ctl.get_paginator("list_agent_runtimes").paginate():
        for r in page.get("agentRuntimes", []):
            take(r["agentRuntimeName"], "mladas_m04_bank_")
    token = None
    while True:
        page = ctl.list_evaluators(maxResults=100, **({"nextToken": token} if token else {}))
        for e in page.get("evaluators", []):
            take(str(e.get("evaluatorName", "")), "mladas_m04_")
        token = page.get("nextToken")
        if not token:
            break
    token = None
    while True:
        page = ctl.list_online_evaluation_configs(maxResults=50, **({"nextToken": token} if token else {}))
        for c in page.get("onlineEvaluationConfigs", []):
            take(str(c.get("onlineEvaluationConfigName", "")), "mladas_m04_")
        token = page.get("nextToken")
        if not token:
            break
    for page in cw.get_paginator("describe_alarms").paginate(AlarmNamePrefix="mladas-m04-"):
        for a in page["MetricAlarms"]:
            take(a["AlarmName"], "mladas-m04-")
    for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix="/aws/bedrock-agentcore/runtimes/mladas_m04_bank_"):
        for g in page["logGroups"]:
            take(g["logGroupName"], "/aws/bedrock-agentcore/runtimes/mladas_m04_bank_")
    for page in logs.get_paginator("describe_log_groups").paginate(
            logGroupNamePrefix=f"{obs.RESULTS_LOG_GROUP_ROOT}mladas_m04_"):
        for g in page["logGroups"]:
            take(g["logGroupName"], f"{obs.RESULTS_LOG_GROUP_ROOT}mladas_m04_")
    for page in iam.get_paginator("list_roles").paginate():
        for r in page["Roles"]:
            take(r["RoleName"], "mladas-m04-bank-")
            take(r["RoleName"], "mladas-m04-oe-")
    for page in session.client("s3").get_paginator("list_buckets").paginate():
        for b in page.get("Buckets", []):
            take(b["Name"], "mladas-m04-")
    return sorted(run_id_for(s, known) for s in sfx)


def summary(found: dict[str, list]) -> tuple[bool, str]:
    parts = []
    for kind, items in found.items():
        items = [i for i in items or [] if not (isinstance(i, dict) and i.get("status") == "DELETING")]
        if items:
            parts.append(f"{kind}={len(items)}")
    return bool(parts), (", ".join(parts) or "-")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--last", action="store_true")
    g.add_argument("--run-id")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--profile", default=mc.AWS_PROFILE,
                    help="AWS profile (default: $MLADAS_AWS_PROFILE, else boto3's standard credential chain)")
    ap.add_argument("--allow-dev", action="store_true", help="allow deleting a shared builder dev stack")
    args = ap.parse_args()
    mc.AWS_PROFILE = args.profile                  # before the first get_session()

    session = mc.get_session()
    account = mc.whoami()["account"]

    def say(text: str) -> None:
        print(mc.mask_account(str(text), account), flush=True)

    if args.list:
        ids = run_ids_in_account(session)
        if not ids:
            say("No M04 resources found.")
        for rid in ids:
            try:
                _, text = summary(obs.find_run_resources(session, rid))
            except (ClientError, BotoCoreError, ValueError) as e:
                text = f"error: {type(e).__name__}"
            say(f"{rid}{'  (dev stack)' if obs.is_dev_stack(rid) else ''}: {text}")
        return 0

    run_id = args.run_id
    if args.last:
        if not known_run_ids():
            say("No .m04_run_ids file here — pass --run-id.")
            return 1
        run_id = known_run_ids()[-1]
    if obs.is_dev_stack(run_id) and not args.allow_dev:
        say(f"{run_id} is a shared builder dev stack. Add --allow-dev to delete it.")
        return 1
    found = obs.find_run_resources(session, run_id)
    any_left, text = summary(found)
    say(f"RUN_ID {run_id}: {text}")
    for kind, items in found.items():
        for i in items:
            say(f"  {kind}: {i['name'] if isinstance(i, dict) else i}")
    if not any_left:
        say("Nothing to delete.")
        return 0
    if not args.yes and input("Delete these? [y/N] ").strip().lower() != "y":
        say("Aborted.")
        return 1
    t0 = time.time()
    report = obs.delete_run_resources(session, run_id)
    for k, v in report.items():
        say(f"  {k}: {v}")
    left, text = summary(obs.find_run_resources(session, run_id))
    say(f"Finished in {time.time() - t0:.0f} s.")
    if not left and not any(str(v).startswith("NOT CLEANED") for v in report.values()):
        say("All gone ✓")
        return 0
    say(f"Still present: {text}")
    return 2


if __name__ == "__main__":
    sys.exit(main())

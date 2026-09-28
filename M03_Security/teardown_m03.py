"""
Safety net for Module 3: find and delete what ONE notebook run created, by its RUN_ID. Run by a human, never
automatically (the account may be shared with other work: this script only touches names and tags that carry the run id).

    python teardown_m03.py --list                     # every M03 run id found in the account (+ what each still has)
    python teardown_m03.py --last                     # the most recent run id this laptop started (.m03_run_ids)
    python teardown_m03.py --run-id 20260927-...      # a specific run
    add --profile <name> for a named AWS profile (default: $MLADAS_AWS_PROFILE, else boto3's standard chain),
    --yes to skip the confirmation prompt, --wait-enis <s> (default 1500) to bound the wait for the VPC shell,
    --allow-dev to delete a shared builder dev stack (run ids 20260927-000000-dev?, in either form: the suffix form
    20260927000000dev? names the same resources)

What a run creates (§0.4, §0.5; every name carries the run suffix = the RUN_ID's lowercase letters and digits):
  * agentcore_identity : the bank's Cognito pool mladas-m03-<sfx> (+ pre-token Lambda/role), the BankOps Lambda + 2 roles,
                         AgentCore Gateways mladas-m03-open-<sfx> and mladas-m03-guarded-<sfx> (+ their workload identities)
  * agentcore_policy   : policy engine mladas_m03_<sfx> and its Cedar policies (detached from the gateway first)
  * agentcore_outbound : Octank's Cognito pool + domain, HTTP API, Lambda, role; workload identity + 3 credential providers
                         (their service-managed secrets go with them); the optional Octank gateway; .m03_3lo_login.txt
  * bedrock_guardrails : guardrails mladas-m03-gr-<variant>-<sfx>
  * agentcore_audit    : log groups under /mladas/m03/<sfx>/
  * agentcore_vpc      : VPC mladas-m03-vpc-<sfx> (2 subnets, 2 SGs), the bedrock-agentcore interface endpoint, the probe
                         Lambda + role. After the Lambda is deleted, Lambda keeps its network interfaces for ~19 min;
                         the VPC, subnets, SGs and role ($0) can only go after that, so this script waits (--wait-enis).
Local state removed: .sessions/<run id>/, .m03_3lo_login.txt (if it belongs to the run), .agentcore.json.
There is no delete-all mode.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
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

import agentcore_audit as at  # noqa: E402
import agentcore_identity as aci  # noqa: E402
import agentcore_outbound as aco  # noqa: E402
import agentcore_policy as acp  # noqa: E402
import agentcore_vpc as avpc  # noqa: E402
import bedrock_guardrails as bg  # noqa: E402
import mladas_common as mc  # noqa: E402
from agentcore_gateway import run_suffix  # noqa: E402
from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402

RUNS_FILE = HERE / ".m03_run_ids"
DEV_STACK = re.compile(r"\d{14}dev[a-z]")               # matched against run_suffix(run_id), never the raw string


def is_dev_stack(run_id: str) -> bool:
    """True for a shared builder dev stack in ANY spelling: every resource name is built from run_suffix(run_id), so
    '20260927-000000-deva' and '20260927000000deva' (or upper case) address the same resources."""
    try:
        return bool(DEV_STACK.fullmatch(run_suffix(run_id)))
    except ValueError:
        return False


def lambda_log_groups(run_id: str) -> list[str]:
    """The exact /aws/lambda/... log groups of this run's four Lambdas (pre-token trigger, BankOps, Octank, VPC probe)."""
    out = set()
    for name in (lambda: f"/aws/lambda/{aci.identity_names(run_id)['pretoken_fn']}",
                 lambda: aci.gateway_names(run_id)["log_group"], lambda: aco.resource_names(run_id)["log_group"],
                 lambda: avpc.resource_names(run_id)["log_group"]):
        try:
            out.add(name())
        except ValueError:                                  # a run id too long for that module's names: nothing to sweep
            pass
    return sorted(out)


MODULES = {   # order = deletion order: detach + delete Cedar before the gateways, gateways before the IdP, VPC last
    "policy": acp, "identity": aci, "partner": aco, "guardrails": bg, "audit": at, "vpc": avpc}


def known_run_ids() -> list[str]:
    return RUNS_FILE.read_text().split() if RUNS_FILE.exists() else []


def run_id_for(sfx: str, known: dict[str, str]) -> str:
    """Names carry the run suffix (RUN_ID without hyphens). Map it back: this laptop's .m03_run_ids first, then the
    notebook's own format (YYYYMMDD-HHMMSS-xxxx). Otherwise the suffix itself works as a run id for every name-based
    lookup (the VPC's are tag-based and listed separately)."""
    if sfx in known:
        return known[sfx]
    if len(sfx) == 18 and sfx[:14].isdigit():
        return f"{sfx[:8]}-{sfx[8:14]}-{sfx[14:]}"
    return sfx


def run_ids_in_account(session) -> list[str]:
    known = {run_suffix(r): r for r in known_run_ids()}
    sfx: set[str] = set()

    def take(name: str) -> None:
        if name.startswith("mladas-m03-vpc-"):
            return                                                    # VPC probe: tag-based, see list_run_ids
        if name.startswith(("mladas-m03-", "mladas_m03_")):
            sfx.add(re.split(r"[-_]", name)[-1])

    idp = session.client("cognito-idp")
    for page in idp.get_paginator("list_user_pools").paginate(MaxResults=60):
        for p in page["UserPools"]:
            take(p["Name"])
    ctl = session.client("bedrock-agentcore-control")
    for page in ctl.get_paginator("list_gateways").paginate():
        for g in page.get("items", []):
            take(g["name"])
    for page in ctl.get_paginator("list_policy_engines").paginate():
        for e in page.get("policyEngines", []):
            take(e["name"])
    for page in session.client("bedrock").get_paginator("list_guardrails").paginate():
        for g in page["guardrails"]:
            take(g["name"])
    for page in session.client("lambda").get_paginator("list_functions").paginate():
        for f in page["Functions"]:
            take(f["FunctionName"])
    logs = session.client("logs")
    for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix="/mladas/m03/"):
        for g in page["logGroups"]:
            sfx.add(g["logGroupName"].split("/")[3])
    ids = {run_id_for(s, known) for s in sfx if s}
    ids |= set(avpc.list_run_ids(session))
    return sorted(ids)


def find_all(session, run_id: str) -> dict[str, dict]:
    found = {}
    for name, mod in MODULES.items():
        try:
            if mod is aco:
                found[name] = mod.find_run_resources(session, run_id, state_dir=HERE)
            else:
                found[name] = mod.find_run_resources(session, run_id)
        except (ClientError, BotoCoreError, ValueError) as e:
            found[name] = {"error": [f"{type(e).__name__}: {e}"]}
    return found


def summary(found: dict[str, dict]) -> tuple[bool, str]:
    parts = []
    for mod, kinds in found.items():
        for kind, items in kinds.items():
            items = [i for i in items or [] if not (isinstance(i, dict) and i.get("status") == "DELETING")]
            if items:
                parts.append(f"{mod}.{kind}={len(items)}")
    return bool(parts), (", ".join(parts) or "-")


def local_state(run_id: str) -> list[Path]:
    paths = [HERE / ".sessions" / run_id]
    login = HERE / aco.LOGIN_FILE
    if login.exists() and f"run_id: {run_id}" in login.read_text():
        paths.append(login)
    paths.append(HERE / ".agentcore.json")              # only the SDK decorators write it; the notebook never does
    return [p for p in paths if p.exists()]


def sweep_log_groups(session, run_id: str) -> list[str]:
    """Delete any log group left with this run's names (a Lambda's last flush can re-create its group). Twice.
    Exact names only: /mladas/m03/<sfx>/... and this run's four /aws/lambda/<function> groups, never a name that merely
    ends with the suffix."""
    logs, sfx, gone = session.client("logs"), run_suffix(run_id), []
    exact = set(lambda_log_groups(run_id))
    for sweep in range(2):
        names = []
        for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=f"/mladas/m03/{sfx}/"):
            names += [g["logGroupName"] for g in page["logGroups"]]
        for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix="/aws/lambda/mladas-m03-"):
            names += [g["logGroupName"] for g in page["logGroups"] if g["logGroupName"] in exact]
        for n in names:
            try:
                logs.delete_log_group(logGroupName=n)
                gone.append(n)
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise
        if sweep == 0:
            time.sleep(3)
    return gone


def delete_all(session, run_id: str, wait_enis: float, say) -> dict[str, object]:
    report: dict[str, object] = {}
    for name, mod in MODULES.items():
        say(f"-- {name} ...")
        t0 = time.time()
        try:
            if mod is aco:
                out = mod.delete_run_resources(session, run_id, state_dir=HERE)
            elif mod is avpc:
                out = mod.delete_run_resources(session, run_id, wait_enis=wait_enis, log=say)
            else:
                out = mod.delete_run_resources(session, run_id)
        except (ClientError, BotoCoreError, RuntimeError, ValueError) as e:
            out = {"error": f"{type(e).__name__}: {e}"}
        report[name] = out
        say(f"   {name}: {out}  ({time.time() - t0:.0f} s)")
    try:
        report["log group sweep"] = sweep_log_groups(session, run_id)
    except (ClientError, BotoCoreError) as e:
        report["log group sweep"] = f"error: {type(e).__name__}: {e}"
    say(f"   log group sweep: {report['log group sweep'] or '-'}")
    removed = []
    for p in local_state(run_id):
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        removed.append(str(p.relative_to(HERE)))
    report["local state"] = removed or "-"
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--last", action="store_true")
    g.add_argument("--run-id")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--profile", default=mc.AWS_PROFILE,
                    help="AWS profile (default: $MLADAS_AWS_PROFILE, else boto3's standard credential chain)")
    ap.add_argument("--wait-enis", type=float, default=1500, metavar="S",
                    help="seconds to wait for Lambda to release the probe's network interfaces (default 1500)")
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
            say("No M03 resources found.")
        for rid in ids:
            _, text = summary(find_all(session, rid))
            say(f"{rid}{'  (dev stack)' if is_dev_stack(rid) else ''}: {text}")
        return 0

    run_id = args.run_id
    if args.last:
        if not known_run_ids():
            say("No .m03_run_ids file here — pass --run-id.")
            return 1
        run_id = known_run_ids()[-1]
    if is_dev_stack(run_id) and not args.allow_dev:
        say(f"{run_id} is a shared builder dev stack. Add --allow-dev to delete it.")
        return 1
    found = find_all(session, run_id)
    any_left, text = summary(found)
    say(f"RUN_ID {run_id}: {text}")
    for p in local_state(run_id):
        say(f"  local: {p.relative_to(HERE)}")
    if not any_left and not local_state(run_id):
        say("Nothing to delete.")
        return 0
    if not args.yes and input("Delete these? [y/N] ").strip().lower() != "y":
        say("Aborted.")
        return 1
    t0 = time.time()
    delete_all(session, run_id, args.wait_enis, say)
    left, text = summary(find_all(session, run_id))
    say(f"Finished in {time.time() - t0:.0f} s.")
    if not left:
        say("All gone ✓")
        return 0
    say(f"Still present: {text}")
    if "vpc." in text:
        say("The VPC shell waits for Lambda to release its network interfaces (~19 min after the probe Lambda was "
            f"deleted). Run again later:  python teardown_m03.py --run-id {run_id} --yes")
    return 2


if __name__ == "__main__":
    sys.exit(main())

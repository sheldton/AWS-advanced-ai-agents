#!/usr/bin/env python3
"""
Safety-net teardown for the MLADAS Module 1 notebook — use it if the kernel died before the §10 cleanup cell.

    python teardown_m01.py --list                         # show M01 resources in the account (read-only)
    python teardown_m01.py --run-id 20260926-174532-c7e1  # delete ONE run's resources (asks for confirmation)
    python teardown_m01.py --last                         # the most recent RUN_ID started from this folder
    python teardown_m01.py --run-id <RUN_ID> --yes        # ... without the prompt
    add --profile <name> for a named AWS profile (default: $MLADAS_AWS_PROFILE, else boto3's standard chain)

It only ever touches resources whose names contain the given RUN_ID:
  AgentCore runtime  mladas_m01_bureau_<runid>-*      (+ its /aws/bedrock-agentcore/runtimes/... log groups)
  IAM role           mladas-mladas-m01-bureau-<runid>
  S3 bucket          mladas-<account>-<runid>
  AgentCore memory   MLADAS_M01_<run_id with underscores>-*
  local              .workflows/ scratch folder, $TMPDIR/mladas-pkg-* packaging leftovers
There is deliberately NO "delete everything" mode: other notebooks may be running in the same account.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

PROFILE = os.getenv("MLADAS_AWS_PROFILE") or None        # None = boto3's standard credential chain
REGION = os.getenv("MLADAS_AWS_REGION", "us-east-1")
HERE = Path(__file__).resolve().parent


def _paged(call, key: str, **kw) -> list[dict]:
    out, token = [], None
    while True:
        page = call(maxResults=100, **kw, **({"nextToken": token} if token else {}))
        out += page.get(key, [])
        token = page.get("nextToken")
        if not token:
            return out


def find(session: boto3.Session, run_tag: str | None) -> dict[str, list]:
    ctl = session.client("bedrock-agentcore-control")
    acct = session.client("sts").get_caller_identity()["Account"]
    norm = lambda s: s.lower().replace("-", "").replace("_", "")
    match = (lambda s: run_tag in norm(s)) if run_tag else (lambda s: "mladas" in s.lower() and "m01" in s.lower())
    runtimes = [r for r in _paged(ctl.list_agent_runtimes, "agentRuntimes") if match(r["agentRuntimeName"])]
    memories = [m for m in _paged(ctl.list_memories, "memories") if match(m["id"])]
    iam = session.client("iam")
    roles = [r["RoleName"] for page in iam.get_paginator("list_roles").paginate()
             for r in page["Roles"] if r["RoleName"].startswith("mladas-") and match(r["RoleName"])]
    buckets = [b["Name"] for b in session.client("s3").list_buckets()["Buckets"]
               if b["Name"].startswith(f"mladas-{acct}-") and (run_tag is None or run_tag in norm(b["Name"]))]
    logs = session.client("logs")
    log_groups = [g["logGroupName"] for p in logs.get_paginator("describe_log_groups").paginate(
        logGroupNamePrefix="/aws/bedrock-agentcore/runtimes/mladas_m01") for g in p["logGroups"]
        if match(g["logGroupName"])]
    return {"runtimes": runtimes, "memories": memories, "roles": roles, "buckets": buckets, "log_groups": log_groups}


def _try(label: str, fn) -> None:
    try:
        fn()
        print("  ✓", label)
    except ClientError as e:
        print("  ✗", label, "->", e.response["Error"]["Code"])


def delete(session: boto3.Session, found: dict[str, list], run_tag: str) -> None:
    ctl = session.client("bedrock-agentcore-control")
    logs = session.client("logs")
    for r in found["runtimes"]:
        _try(f"delete runtime {r['agentRuntimeId']}", lambda r=r: ctl.delete_agent_runtime(agentRuntimeId=r["agentRuntimeId"]))
    for r in found["runtimes"]:                     # wait, because a deleting runtime can re-create its log group
        for _ in range(60):
            try:
                ctl.get_agent_runtime(agentRuntimeId=r["agentRuntimeId"])
                time.sleep(5)
            except ClientError:
                break
    for m in found["memories"]:
        _try(f"delete memory {m['id']}", lambda m=m: ctl.delete_memory(memoryId=m["id"]))
    iam = session.client("iam")
    for role in found["roles"]:
        def _drop(role=role):
            for p in iam.list_role_policies(RoleName=role)["PolicyNames"]:
                iam.delete_role_policy(RoleName=role, PolicyName=p)
            for p in iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]:
                iam.detach_role_policy(RoleName=role, PolicyArn=p["PolicyArn"])
            iam.delete_role(RoleName=role)
        _try(f"delete IAM role {role}", _drop)
    s3 = session.resource("s3")
    for name in found["buckets"]:
        def _empty(name=name):
            b = s3.Bucket(name)
            b.object_versions.delete()
            b.objects.delete()
            b.delete()
        _try(f"delete bucket {name}", _empty)
    time.sleep(10)
    for lg in find(session, run_tag)["log_groups"]:  # second sweep after the runtimes are gone
        _try(f"delete log group {lg}", lambda lg=lg: logs.delete_log_group(logGroupName=lg))
    shutil.rmtree(HERE / ".workflows", ignore_errors=True)
    for d in Path(tempfile.gettempdir()).glob("mladas-pkg-*"):
        shutil.rmtree(d, ignore_errors=True)
    print("  ✓ local scratch folders")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="list M01 resources (read-only)")
    g.add_argument("--run-id", help="RUN_ID printed by the notebook's §0 cell")
    g.add_argument("--last", action="store_true", help="the most recent RUN_ID recorded in .m01_run_ids")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--profile", default=PROFILE,
                    help="AWS profile (default: $MLADAS_AWS_PROFILE, else boto3's standard credential chain)")
    args = ap.parse_args()

    run_id = args.run_id
    if args.last:
        ids = (HERE / ".m01_run_ids").read_text().split() if (HERE / ".m01_run_ids").exists() else []
        if not ids:
            print("No RUN_ID recorded yet in .m01_run_ids")
            return 1
        run_id = ids[-1]
    session = boto3.Session(profile_name=args.profile, region_name=REGION)
    tag = run_id.replace("-", "").replace("_", "").lower() if run_id else None
    found = find(session, tag)
    acct = session.client("sts").get_caller_identity()["Account"]
    print(f"Account ********{acct[-4:]} / {REGION} — "
          f"{'resources for run ' + run_id if tag else 'all MLADAS M01 resources'}:")
    for kind, items in found.items():
        names = [i.get("agentRuntimeName", i.get("id")) if isinstance(i, dict) else i for i in items]
        print(f"  {kind:<10} {[n.replace(acct, '********' + acct[-4:]) for n in names] or '-'}")
    if args.list or not any(found.values()):
        return 0
    if not args.yes and input("Delete the resources above? [y/N] ").strip().lower() != "y":
        print("aborted")
        return 1
    delete(session, found, tag)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())

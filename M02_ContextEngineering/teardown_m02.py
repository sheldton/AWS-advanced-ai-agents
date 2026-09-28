"""
Safety net for Module 2: find and delete what ONE notebook run created, by its RUN_ID. Run by a human, never
automatically (the account may be shared with other work: this script only touches names that contain the run id).

    python teardown_m02.py --list                 # every M02 run id found in the account (+ what each still has)
    python teardown_m02.py --last                 # the most recent run id this laptop started (.m02_run_ids)
    python teardown_m02.py --run-id 20260926-...  # a specific run
    add --yes to skip the confirmation prompt, --profile <name> for a named AWS profile (default:
    $MLADAS_AWS_PROFILE, else boto3's standard credential chain)

What a run creates (§0.6, §0.7): one AgentCore Memory named MLADAS_M02_<run id>, and one AgentCore Gateway with its
targets, one Lambda function, two IAM roles and their log groups (agentcore_gateway.py names them with the run id).
Code Interpreter sessions (§8) use the AWS-managed sandbox and stop themselves after 15 minutes at most.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import mladas_common as mc  # noqa: E402
from agentcore_gateway import delete_run_resources, find_run_resources  # noqa: E402

MEMORY_PREFIX = "MLADAS_M02_"
GATEWAY_PREFIX = "mladas-m02-gw-"


def memory_name(run_id: str) -> str:
    return MEMORY_PREFIX + run_id.replace("-", "_")


def list_memories(ctl) -> list[dict]:
    out, token = [], None
    while True:
        page = ctl.list_memories(maxResults=100, **({"nextToken": token} if token else {}))
        out += page.get("memories", [])
        token = page.get("nextToken")
        if not token:
            return out


def memories_for(ctl, run_id: str | None) -> list[dict]:
    """Memories whose id starts with this run's memory name (AgentCore memory ids are '<name>-<suffix>')."""
    found = [m for m in list_memories(ctl) if m["id"].startswith(MEMORY_PREFIX)]
    return found if run_id is None else [m for m in found if m["id"].startswith(memory_name(run_id) + "-")]


def _run_id_from_suffix(sfx: str) -> str:
    """Gateway names carry the run id without hyphens (20260926163000ab12); M02 run ids are 8-6-4 characters."""
    return f"{sfx[:8]}-{sfx[8:14]}-{sfx[14:]}" if len(sfx) == 18 and sfx[:14].isdigit() else sfx


def run_ids_in_account(ctl, session) -> list[str]:
    ids = set()
    for m in memories_for(ctl, None):
        stem = m["id"][len(MEMORY_PREFIX):].rsplit("-", 1)[0]          # 20260926_163000_ab12
        ids.add(stem.replace("_", "-"))
    token = None
    while True:
        page = ctl.list_gateways(maxResults=100, **({"nextToken": token} if token else {}))
        for g in page.get("items", []):
            if g["name"].startswith(GATEWAY_PREFIX):
                ids.add(_run_id_from_suffix(g["name"][len(GATEWAY_PREFIX):]))
        token = page.get("nextToken")
        if not token:
            return sorted(ids)


def gateway_summary(found: dict) -> tuple[bool, str]:
    parts = [f"{k}={len(v)}" for k, v in found.items() if v]
    return bool(parts), (", ".join(parts) or "-")


def delete_memories(ctl, run_id: str) -> list[str]:
    done = []
    for m in memories_for(ctl, run_id):
        if m.get("status") == "DELETING":
            done.append(f"memory {m['id']} (already DELETING)")
            continue
        ctl.delete_memory(memoryId=m["id"])
        for _ in range(36):
            try:
                if ctl.get_memory(memoryId=m["id"])["memory"]["status"] != "DELETING":
                    break
            except ctl.exceptions.ResourceNotFoundException:
                break
            time.sleep(5)
        done.append(f"memory {m['id']}")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--last", action="store_true")
    g.add_argument("--run-id")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--profile", default=mc.AWS_PROFILE,
                    help="AWS profile (default: $MLADAS_AWS_PROFILE, else boto3's standard credential chain)")
    args = ap.parse_args()
    mc.AWS_PROFILE = args.profile                  # before the first get_session()

    session = mc.get_session()
    account = mc.whoami()["account"]
    ctl = session.client("bedrock-agentcore-control")

    if args.list:
        ids = run_ids_in_account(ctl, session)
        if not ids:
            print("No M02 resources found.")
        for rid in ids:
            mems = [m["id"] for m in memories_for(ctl, rid)]
            _, gw = gateway_summary(find_run_resources(session, rid))
            print(f"{rid}: memories={mems or '-'} gateway resources: {gw}")
        return 0

    run_id = args.run_id
    if args.last:
        runs_file = HERE / ".m02_run_ids"
        if not runs_file.exists():
            print("No .m02_run_ids file here — pass --run-id.")
            return 1
        run_id = runs_file.read_text().split()[-1]
    found_mem = memories_for(ctl, run_id)
    any_gw, gw_text = gateway_summary(find_run_resources(session, run_id))
    print(f"RUN_ID {run_id}:")
    print(f"  memories: {[m['id'] for m in found_mem] or '-'}")
    print(f"  gateway : {gw_text}")
    if not found_mem and not any_gw:
        print("Nothing to delete.")
        return 0
    if not args.yes and input("Delete these? [y/N] ").strip().lower() != "y":
        print("Aborted.")
        return 1
    report = {"memory": delete_memories(ctl, run_id), "gateway": delete_run_resources(session, run_id)}
    print(mc.mask_account(str(report), account))
    left_mem = [m["id"] for m in memories_for(ctl, run_id) if m.get("status") != "DELETING"]
    left_any, left_gw = gateway_summary(find_run_resources(session, run_id))
    ok = not left_mem and not left_any
    print("All gone ✓" if ok else f"Still present: memories={left_mem} gateway={left_gw}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())

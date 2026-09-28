"""
agentcore_vpc — a minimal private VPC that proves the AgentCore PrivateLink controls from INSIDE the VPC (MLADAS M03 §7).

The notebook runs on a laptop, outside any VPC, so a Lambda function inside the VPC makes the calls:

    VPC 10.83.0.0/16 (DNS support + DNS hostnames ON) · 2 private subnets in 2 AZs · NO internet gateway, NO NAT
    SG <base>-ep   443 from the VPC CIDR -> interface endpoint com.amazonaws.<region>.bedrock-agentcore
                                           (private DNS ON; the slide-32 endpoint policy with VALID ARNs, set AT CREATION)
    SG <base>-fn   no ingress, default egress -> Lambda <base>-probe (python3.12, arm64) in both subnets
    IAM role <base>-probe   AWSLambdaVPCAccessExecutionRole + ListEvents / ListMemoryExtractionJobs on memory/*,
                            so the ENDPOINT POLICY is the only thing that can deny a probe call

    import agentcore_vpc as avpc
    VPC = avpc.VpcProbeDeployment(RUN_ID, SESSION).start()     # §0: background thread, READY in ~4 min
    VPC.wait(90)                                                # §7: True once a real call went through the endpoint
    pd.DataFrame(VPC.probe()["rows"])                           # allowed / denied / unreachable, with the DNS column
    VPC.network_layers()                                        # route tables, NACL entries, SG rules (read-only)
    VPC.release_network()                                       # §7's LAST cell: the Lambda ENIs drain during §8
    VPC.teardown(wait_enis=0)                                   # wrap-up: {label: "gone ✓" | "pending: …" | "error: …"}
    avpc.delete_run_resources(SESSION, RUN_ID, wait_enis=1800)  # teardown_m03.py --run-id: finishes the $0 shell

Public API
  DEFAULT_PREFIX, SERVICE, MEMORY_ACTIONS, PRICES          constants (prefix "mladas-m03", the 13 slide-32 actions, $)
  run_suffix(run_id)                                       the run id as it appears in names (same rule as agentcore_gateway)
  resource_names(run_id, prefix=DEFAULT_PREFIX)            every name this run uses {base, vpc, ep_sg, fn_sg, endpoint, role,
                                                           function, log_group, ...}
  fake_memory_ids(run_id)                                  (allowed, other): valid-looking memory ids that do not exist
  slide32_policy(account, region, memory_id, as_printed=False)
                                                           slide 32's endpoint policy; as_printed=True = the deck's broken
                                                           version (arn:aws::…, no Version) for the validator demo
  validate_policy(policy, session)                         IAM Access Analyzer ValidatePolicy(RESOURCE_POLICY) -> [rows]
  endpoint_services(session, companions=True)              read-only: which AgentCore (+ slide-33) endpoint services exist
  runtime_vpc_config_shape(subnets=None, security_groups=None)
                                                           read-only (local botocore model): Runtime / Code Interpreter /
                                                           Browser network modes + Gateway target privateEndpoint
  VpcProbeDeployment(run_id, session, prefix=DEFAULT_PREFIX, *, cidr=, tags=, policy="slide32", azs=None)
    .start()                  deploy (or ADOPT this run id's resources) in a daemon thread; never raises
    .wait(timeout=90) -> bool  block until READY / FAILED / CANCELLED or timeout; True when READY
    .ready / .status / .error / .progress() -> [(t_s, step, message)] / .timings / .ids / .names / .policy
    .probe(only=None) -> {"rows": [...], "ok", "dns", "invoke_s", ...}   one Lambda invoke, table-ready rows
    .network_layers() -> {"vpc", "route_tables", "internet_gateways", "nat_gateways", "network_acls",
                          "security_groups", "endpoints", "summary"}          read-only describe calls
    .runtime_network_config() the Runtime networkConfiguration you WOULD pass for this VPC (not called)
    .release_network() -> {"function", "result", "seconds", "note"}         delete the Lambda now; keep the role
    .teardown(wait_enis=0) -> TeardownResult {label: "gone ✓" | "gone ✓ (deleting)" | "pending: why" | "error: why"}
    .drain_in_background(wait_enis=1800) / .drain_done / .drain_result     finish the ENI-bound shell in a thread
    .cost_estimate() -> {"endpoint_az_hours", "endpoint_usd", "lambda_usd", "total_usd", ...}
  adopt(run_id, session, prefix=DEFAULT_PREFIX) -> VpcProbeDeployment   attach to existing resources, no creation
  find_run_resources(session, run_id, prefix=DEFAULT_PREFIX) -> {vpcs, subnets, security_groups, endpoints, enis,
                                                                  functions, roles, log_groups}   read-only
  delete_run_resources(session, run_id, prefix=DEFAULT_PREFIX, *, wait_enis=0, poll=15, log=None) -> TeardownResult
  verify_gone(session, run_id, ids=None, prefix=DEFAULT_PREFIX) -> TeardownResult   by id, deletes nothing
  list_run_ids(session, prefix=DEFAULT_PREFIX) -> [run ids that still own VPC-probe resources]   read-only
  TeardownResult (dict)       .all_gone · .pending · .errors · .timeline · .seconds

Verified in a test account in us-east-1, 2026-09-27 (boto3 1.43.103 locally; the Lambda python3.12 runtime ships boto3 1.42.97,
which already has bedrock-agentcore — nothing to bundle):
  * READY in 243-245 s: Lambda (in a VPC) create -> Active ~220 s; endpoint create -> available 38-239 s; CreateFunction
    needs 2-3 IAM-propagation retries. 'available' is not 'reachable' (once ~2.5 min of timeouts), so READY = 2 fast
    answers through the endpoint in a row.
  * An endpoint policy given AT CREATION is enforced from the first probe; a LIVE ModifyVpcEndpoint change took 2-4.5 min
    with mixed allow/deny results — never live-edit it in class.
  * After DeleteFunction the Lambda's hyperplane ENIs stay in-use 18.2-19.0 min (4/5 runs; once 24 s). Until then the
    Lambda SG, the subnets and the VPC fail with DependencyViolation, and the ENIs cannot be deleted or detached by hand.
    Once an ENI turns 'available' it can be deleted. Keep the execution role until every Lambda ENI is gone (Lambda
    deletes them with it). Only the endpoint bills ($0.01 per AZ-hour), and it is deleted first.
  * Lambda's last log flush can RE-CREATE /aws/lambda/<function> a few seconds after DeleteFunction (seen when teardown
    ran 4 s after release_network). In class, release_network() ends §7 and teardown() runs minutes later, so it is gone
    by then; the ENI drain / delete_run_resources sweeps the log group a second time anyway.
Every call uses the boto3 session you pass (mc.get_session(): your AWS credentials). Every string this module returns has the
12-digit account id masked like mc.mask_account ("********1234").
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import threading
import time
import uuid
import zipfile
from collections.abc import Callable
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

DEFAULT_PREFIX = "mladas-m03"
SERVICE = "bedrock-agentcore"                              # the data-plane interface endpoint this demo creates
MEMORY_ACTIONS = ["CreateEvent", "DeleteEvent", "GetEvent", "ListEvents", "DeleteMemoryRecord", "GetMemoryRecord",
                  "ListMemoryRecords", "RetrieveMemoryRecords", "ListActors", "ListSessions",
                  "BatchCreateMemoryRecords", "BatchDeleteMemoryRecords", "BatchUpdateMemoryRecords"]   # slide 32
PRICES = {                                                 # Price List API, us-east-1, 2026-09-27
    "endpoint_az_hour": 0.01,                              # interface endpoint, per endpoint per AZ-hour
    "endpoint_gb": 0.01,                                   # data processed by the endpoint
    "lambda_request": 0.0000002,
    "lambda_gb_second_arm": 0.0000133334,
    "nat_gateway_hour": 0.045,                             # for contrast: the "just add a NAT" alternative
}
ENI_DRAIN_MINUTES = (18.2, 19.0)                           # Lambda hyperplane ENI release after DeleteFunction (4/5 runs)
LAMBDA_UNSUPPORTED_AZ_IDS = {"use1-az3"}                   # Lambda cannot place VPC functions there
PROBES = ("allowed", "denied_resource", "denied_action", "no_endpoint", "control_plane", "gateway_dns")
DEFAULT_PROBES = ("allowed", "denied_resource", "denied_action", "no_endpoint")   # ~1-6.5 s per invoke

_CFG = Config(retries={"total_max_attempts": 5, "mode": "standard"}, read_timeout=60)
_INVOKE_CFG = Config(retries={"total_max_attempts": 1}, read_timeout=60, connect_timeout=10)
_PREFIX_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_GONE = "gone ✓"
_TIMEOUTS = ("ConnectTimeoutError", "EndpointConnectionError", "ConnectionError", "ConnectTimeout")


# ======================================================================================
# Names, policies, validation
# ======================================================================================
def run_suffix(run_id: str) -> str:
    """The run id as it appears in names: lowercase, alphanumerics only ('20260927-083000-ab12' -> '20260927083000ab12').
    Same rule as agentcore_gateway.run_suffix."""
    s = re.sub(r"[^a-z0-9]", "", str(run_id).lower())
    if not s:
        raise ValueError(f"run_id {run_id!r} has no letters or digits")
    return s


def resource_names(run_id: str, prefix: str = DEFAULT_PREFIX) -> dict[str, str]:
    """Every name this run's VPC probe uses. Lambda and IAM names are limited to 64 characters."""
    if not _PREFIX_RE.match(prefix):
        raise ValueError(f"prefix {prefix!r} must be lowercase letters, digits and single hyphens")
    base = f"{prefix}-vpc-{run_suffix(run_id)}"
    names = {"base": base, "vpc": base, "subnet": f"{base}-private", "ep_sg": f"{base}-ep", "fn_sg": f"{base}-fn",
             "endpoint": f"{base}-agentcore", "role": f"{base}-probe", "function": f"{base}-probe",
             "route_table": f"{base}-main-rt", "nacl": f"{base}-default-nacl", "default_sg": f"{base}-default-sg"}
    names["log_group"] = f"/aws/lambda/{names['function']}"
    if len(names["function"]) > 64:
        raise ValueError(f"{names['function']!r}: Lambda / IAM names are limited to 64 characters "
                         "(use a shorter prefix or run id)")
    return names


def fake_memory_ids(run_id: str) -> tuple[str, str]:
    """(allowed, other): memory ids with the valid shape [a-zA-Z][a-zA-Z0-9_]{0,47}-[a-zA-Z0-9]{10} that don't exist.
    The endpoint policy names only `allowed`, so a call on `other` must be denied by the endpoint."""
    sfx = run_suffix(run_id)[:20]
    return f"mladas_vpcprobe_{sfx}-ALLOWED000", f"mladas_vpcprobe_{sfx}-OTHER00000"


def slide32_policy(account: str, region: str, memory_id: str, as_printed: bool = False) -> dict:
    """Slide 32's endpoint policy: the account root may call the 13 Memory data-plane actions on ONE memory.

    as_printed=False (default): valid ARNs + "Version" — what the demo endpoint gets at creation (0 validator findings).
    as_printed=True: the deck's text — 'arn:aws::iam::…' and 'arn:aws::bedrock-agentcore:…' (one ':' too many) and
    no Version. ModifyVpcEndpoint rejects it (InvalidPolicyDocument); IAM Access Analyzer reports 2 × ERROR
    INVALID_SERVICE + 1 × WARNING MISSING_VERSION (verified 2026-09-27)."""
    part = "aws:" if as_printed else "aws"                   # "arn:aws::iam" (deck) vs "arn:aws:iam" (valid)
    statement = {"Effect": "Allow", "Principal": {"AWS": f"arn:{part}:iam::{account}:root"},
                 "Action": [f"bedrock-agentcore:{a}" for a in MEMORY_ACTIONS],
                 "Resource": f"arn:{part}:bedrock-agentcore:{region}:{account}:memory/{memory_id}"}
    return {"Statement": [statement]} if as_printed else {"Version": "2012-10-17", "Statement": [statement]}


def _mask(text: Any, account: str | None) -> str:
    """Same format as mc.mask_account (<12-digit account id> -> ********<last 4>), plus any account id inside an ARN
    (":<12 digits>:"), so AWS error messages are safe on a projector even before the account id is known."""
    s = str(text)
    if account:
        s = s.replace(account, "********" + account[-4:])
    return re.sub(r":\d{8}(\d{4}):", r":********\1:", s)


def _mask12(text: str) -> str:
    return re.sub(r"(?<!\d)\d{8}(\d{4})(?!\d)", r"********\1", text)


def _fmt_path(path: list[dict]) -> str:
    out = ""
    for p in path:
        if "index" in p:
            out += f"[{p['index']}]"
        elif "value" in p or "key" in p:
            out += ("." if out else "") + str(p.get("value", p.get("key")))
        elif "substring" in p:
            out += f"[chars {p['substring'].get('start')}+{p['substring'].get('length')}]"
    return out or "(whole policy)"


def validate_policy(policy: dict | str, session: boto3.Session, *, policy_type: str = "RESOURCE_POLICY") -> list[dict]:
    """IAM Access Analyzer ValidatePolicy (0.1-0.5 s, needs no VPC). An endpoint policy is a RESOURCE_POLICY
    (validatePolicyResourceType has no VPC-endpoint value). Returns one row per finding:
    {"type": ERROR|SECURITY_WARNING|WARNING|SUGGESTION, "code", "detail", "path", "snippet", "learn_more"}.
    An empty list = no findings. Raises ClientError if Access Analyzer itself is unavailable."""
    aa = session.client("accessanalyzer", config=_CFG)
    doc = policy if isinstance(policy, str) else json.dumps(policy)
    rows: list[dict] = []
    token = None
    while True:
        r = aa.validate_policy(policyDocument=doc, policyType=policy_type, **({"nextToken": token} if token else {}))
        for f in r.get("findings", []):
            loc = (f.get("locations") or [{}])[0]
            span = loc.get("span") or {}
            start, end = span.get("start", {}).get("offset"), span.get("end", {}).get("offset")
            rows.append({"type": f["findingType"], "code": f["issueCode"], "detail": f["findingDetails"],
                         "path": _fmt_path(loc.get("path", [])),
                         "snippet": _mask12(doc[start:end]) if start is not None and end is not None else "",
                         "learn_more": f.get("learnMoreLink", "")})
        token = r.get("nextToken")
        if not token:
            return rows


# ======================================================================================
# Read-only views for §7.4: endpoint services and the Runtime / Gateway network shapes
# ======================================================================================
_AGENTCORE_SERVICES = {
    "bedrock-agentcore": "data plane: Runtime invoke, Memory, Identity (token vault), Code Interpreter, Browser",
    "bedrock-agentcore-control": "control plane: create / get / list / delete every AgentCore resource",
    "bedrock-agentcore.gateway": "AgentCore Gateway MCP URLs (<gateway id>.gateway.bedrock-agentcore…)",
}
_COMPANION_SERVICES = {                                     # slide 33: a fully private (container) Runtime also needs
    "ecr.api": "ECR API: image metadata and auth tokens",
    "ecr.dkr": "ECR registry: docker pull of the agent image",
    "s3": "S3: ECR keeps image layers in S3 (the Gateway type has no hourly charge)",
    "logs": "CloudWatch Logs: the agent's logs",
}


def _pages(call: Callable, key: str, **kw) -> list:
    """Every item of a NextToken-paginated EC2 describe call."""
    out, token = [], None
    while True:
        page = call(**kw, **({"NextToken": token} if token else {}))
        out += page.get(key, [])
        token = page.get("NextToken")
        if not token:
            return out


def endpoint_services(session: boto3.Session, companions: bool = True) -> list[dict]:
    """Read-only DescribeVpcEndpointServices: the AgentCore interface endpoint services in this Region (slide 31 shows
    two; us-east-1 has three), plus the slide-33 companions (ECR api/dkr, S3, CloudWatch Logs) when companions=True.
    Rows: {service, type, private_dns, azs, policy_supported, acceptance_required, serves, slide, in_this_demo}."""
    region = session.region_name
    wanted = {**{s: (31, d) for s, d in _AGENTCORE_SERVICES.items()},
              **({s: (33, d) for s, d in _COMPANION_SERVICES.items()} if companions else {})}
    full = {f"com.amazonaws.{region}.{s}": s for s in wanted}
    ec2 = session.client("ec2", config=_CFG)
    try:                                      # by name: 0.2 s (a service-name filter scans every service: 3.4-3.8 s)
        details = _pages(ec2.describe_vpc_endpoint_services, "ServiceDetails", ServiceNames=list(full))
    except ClientError as e:                  # a name this Region does not offer -> the slower filter tolerates it
        if not _code(e).startswith("InvalidServiceName"):
            raise
        details = _pages(ec2.describe_vpc_endpoint_services, "ServiceDetails",
                         Filters=[{"Name": "service-name", "Values": list(full)}])
    rows, seen = [], set()
    for d in details:
        short = full.get(d["ServiceName"])
        if not short:
            continue
        seen.add(short)
        slide, serves = wanted[short]
        for st in d.get("ServiceType", [{}]):
            rows.append({"service": short, "type": st.get("ServiceType", "?"),
                         "private_dns": d.get("PrivateDnsName") or "-", "azs": len(d.get("AvailabilityZones", [])),
                         "policy_supported": d.get("VpcEndpointPolicySupported"),
                         "acceptance_required": d.get("AcceptanceRequired"), "serves": serves, "slide": slide,
                         "in_this_demo": short == SERVICE and st.get("ServiceType") == "Interface"})
    for short in wanted:
        if short not in seen:
            rows.append({"service": short, "type": "not offered in this Region", "private_dns": "-", "azs": 0,
                         "policy_supported": None, "acceptance_required": None, "serves": wanted[short][1],
                         "slide": wanted[short][0], "in_this_demo": False})
    order = list(wanted)
    return sorted(rows, key=lambda r: (order.index(r["service"]), r["type"]))


def _flatten_shape(shape, path: str, rows: list, api: str, required: bool, depth: int = 0) -> None:
    md = shape.metadata
    if shape.type_name == "structure" and depth < 4:
        if not shape.members:
            return
        for name, member in shape.members.items():
            _flatten_shape(member, f"{path}.{name}", rows, api, name in shape.required_members, depth + 1)
        return
    if getattr(shape, "enum", None):
        allowed = " | ".join(shape.enum)
    elif shape.type_name == "list":
        item = shape.member
        pat = item.metadata.get("pattern") or getattr(item, "enum", None) or item.type_name
        bounds = f"{md.get('min', 0)}–{md.get('max', '…')} × " if "min" in md or "max" in md else ""
        allowed = f"list of {bounds}{pat}"
    elif "pattern" in md:
        allowed = f"pattern {md['pattern']}"
    else:
        allowed = shape.type_name
    rows.append({"api": api, "field": path, "required": required, "allowed": allowed})


def runtime_vpc_config_shape(subnets: list[str] | None = None, security_groups: list[str] | None = None) -> dict:
    """Read-only, from the installed botocore model (no AWS call): how AgentCore resources join a VPC (slides 28-30).
    -> {"rows": [{api, field, required, allowed}], "example": the CreateAgentRuntime networkConfiguration for
    `subnets` / `security_groups` (placeholders if None), "service_linked_role", "note"}. We do NOT create a VPC-mode
    Runtime: the first one creates the account-level AWSServiceRoleForBedrockAgentCoreNetwork role (absent in a fresh account)."""
    import botocore.loaders
    import botocore.model

    model = botocore.model.ServiceModel(
        botocore.loaders.create_loader().load_service_model("bedrock-agentcore-control", "service-2"),
        "bedrock-agentcore-control")
    rows: list[dict] = []
    for api, member in (("CreateAgentRuntime", "networkConfiguration"), ("CreateCodeInterpreter", "networkConfiguration"),
                        ("CreateBrowser", "networkConfiguration"), ("CreateGatewayTarget", "privateEndpoint")):
        shape = model.operation_model(api).input_shape
        if member in shape.members:
            _flatten_shape(shape.members[member], member, rows, api, member in shape.required_members)
    gateway_fields = model.operation_model("CreateGateway").input_shape.members
    if not any("network" in f.lower() or "vpc" in f.lower() or "private" in f.lower() for f in gateway_fields):
        rows.append({"api": "CreateGateway", "field": "(no network field)", "required": False,
                     "allowed": "ingress stays private through the bedrock-agentcore.gateway interface endpoint"})
    example = {"networkMode": "VPC", "networkModeConfig": {
        "subnets": list(subnets or ["subnet-0123456789abcdef0", "subnet-0fedcba9876543210"]),
        "securityGroups": list(security_groups or ["sg-0123456789abcdef0"])}}
    return {"rows": rows, "example": example, "service_linked_role": "AWSServiceRoleForBedrockAgentCoreNetwork",
            "note": "Runtime ENIs go in >= 2 AZs of your private subnets; the service-linked role is created by the "
                    "first VPC-mode Runtime in the account (an account-level change, so this notebook stays read-only)."}


# ======================================================================================
# The Lambda that runs INSIDE the VPC (inline source, zipped in memory with a fixed timestamp)
# ======================================================================================
HANDLER_SOURCE = r'''
"""Runs INSIDE the private VPC: which AgentCore calls get through the interface endpoint, which the endpoint policy
denies, and which cannot leave the VPC at all (no endpoint for that service and no internet route)."""
import ipaddress
import json
import os
import socket
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

CFG = Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1})   # one attempt, short timeouts
REGION = os.environ["AWS_REGION"]
HOSTS = {"agentcore": f"bedrock-agentcore.{REGION}.amazonaws.com",
         "control": f"bedrock-agentcore-control.{REGION}.amazonaws.com",
         "sts": f"sts.{REGION}.amazonaws.com",
         "gateway": f"mladas-probe-0000000000.gateway.bedrock-agentcore.{REGION}.amazonaws.com"}


def _ms(t):
    return round((time.time() - t) * 1000)


def _try(fn):
    t = time.time()
    try:
        fn()
        return {"outcome": "OK", "ms": _ms(t)}
    except ClientError as e:                   # the service answered: allowed (ResourceNotFound) or denied (403)
        err = e.response.get("Error", {})
        return {"outcome": err.get("Code"), "message": (err.get("Message") or "")[:500],
                "http": e.response.get("ResponseMetadata", {}).get("HTTPStatusCode"), "ms": _ms(t)}
    except (BotoCoreError, OSError) as e:      # ConnectTimeoutError: no route out of the VPC
        return {"outcome": type(e).__name__, "message": str(e)[:300], "ms": _ms(t)}


def _resolve(host):
    t = time.time()
    try:
        ips = sorted({a[4][0] for a in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)})
    except OSError:
        try:                                   # getaddrinfo's NXDOMAIN error is unreadable in Lambda (Errno 16)
            ips = sorted(set(socket.gethostbyname_ex(host)[2]))
        except OSError as e:
            return {"error": f"{type(e).__name__}: {e}", "ms": _ms(t)}
    return {"ips": ips, "private": all(ipaddress.ip_address(i).is_private for i in ips), "ms": _ms(t)}


def lambda_handler(event, context):
    t0 = time.time()
    print(json.dumps({"marker": event.get("marker"), "note": "log line written from inside the VPC"}))
    only = event.get("only") or ["allowed", "denied_resource", "denied_action", "no_endpoint"]
    allowed, other = event["allowed_memory"], event["other_memory"]
    ac = boto3.client("bedrock-agentcore", region_name=REGION, config=CFG)
    probes = {}
    if "allowed" in only:            # in the endpoint policy -> reaches AgentCore -> ResourceNotFound (fake memory id)
        probes["allowed"] = _try(lambda: ac.list_events(memoryId=allowed, actorId="probe", sessionId="probe"))
    if "denied_resource" in only:    # same action, a memory the policy does not name -> AccessDenied from the endpoint
        probes["denied_resource"] = _try(lambda: ac.list_events(memoryId=other, actorId="probe", sessionId="probe"))
    if "denied_action" in only:      # an action the policy does not list
        probes["denied_action"] = _try(lambda: ac.list_memory_extraction_jobs(memoryId=allowed))
    if "no_endpoint" in only:        # no STS endpoint in this VPC, no internet route -> connect timeout (~3 s)
        probes["no_endpoint"] = _try(lambda: boto3.client("sts", region_name=REGION, config=CFG).get_caller_identity())
    if "control_plane" in only:      # no bedrock-agentcore-control endpoint -> timeout per public IP (~9 s)
        probes["control_plane"] = _try(lambda: boto3.client("bedrock-agentcore-control", region_name=REGION,
                                                            config=CFG).list_memories(maxResults=1))
    hosts = ["agentcore", "sts"] + (["control"] if "control_plane" in only else []) + \
        (["gateway"] if "gateway_dns" in only else [])
    dns = {HOSTS[h]: _resolve(HOSTS[h]) for h in hosts}
    return {"probes": probes, "dns": dns, "hosts": HOSTS, "boto3": boto3.__version__, "handler_ms": _ms(t0),
            "marker": event.get("marker")}
'''


def _handler_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo("handler.py", date_time=(2026, 9, 27, 0, 0, 0))   # fixed timestamp: same zip every time
        info.external_attr = 0o644 << 16
        z.writestr(info, HANDLER_SOURCE)
    return buf.getvalue()


def _code_sha(zip_bytes: bytes) -> str:
    return base64.b64encode(hashlib.sha256(zip_bytes).digest()).decode()


# ======================================================================================
# Shared plumbing
# ======================================================================================
def _code(e: BaseException) -> str:
    return e.response.get("Error", {}).get("Code", "") if isinstance(e, ClientError) else type(e).__name__


class _Clients:
    """All clients for one session, created on the CALLER's thread (boto3 sessions are not thread-safe; clients are)."""

    def __init__(self, session: boto3.Session):
        self.ec2 = session.client("ec2", config=_CFG)
        self.iam = session.client("iam", config=_CFG)
        self.lam = session.client("lambda", config=_CFG)
        self.lam_invoke = session.client("lambda", config=_INVOKE_CFG)
        self.logs = session.client("logs", config=_CFG)
        self.sts = session.client("sts", config=_CFG)


class _Cancelled(Exception):
    pass


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _run_lock(key: str) -> threading.Lock:
    """One lock per run in this process, so two start() calls for the same run id cannot both create a VPC."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _tag_list(tags: dict) -> list[dict]:
    return [{"Key": k, "Value": str(v)} for k, v in tags.items()]


def _tagspec(rtype: str, name: str, tags: dict) -> list[dict]:
    return [{"ResourceType": rtype, "Tags": [{"Key": "Name", "Value": name}] + _tag_list(tags)}]


def _is_lambda_eni(eni: dict, function: str) -> bool:
    return eni.get("Description", "").startswith(f"AWS Lambda VPC ENI-{function}")


class TeardownResult(dict):
    """{label: "gone ✓" | "gone ✓ (deleting)" | "pending: why" | "error: why"} in dependency order, verified by id.
    Extra attributes: .timeline [(t_s, what)], .seconds, and the properties .all_gone / .pending / .errors."""

    def __init__(self, *args: Any, **kw: Any):
        super().__init__(*args, **kw)
        self.timeline: list[tuple[float, str]] = []
        self.seconds: float = 0.0

    @property
    def all_gone(self) -> bool:
        return all(v.startswith(_GONE) for v in self.values())

    @property
    def pending(self) -> list[str]:
        return [k for k, v in self.items() if v.startswith("pending")]

    @property
    def errors(self) -> list[str]:
        return [k for k, v in self.items() if v.startswith("error")]


# ======================================================================================
# The deployment
# ======================================================================================
class VpcProbeDeployment:
    """VPC -> 2 private subnets -> 2 SGs -> bedrock-agentcore interface endpoint (policy at creation) || IAM role ->
    Lambda in the VPC -> readiness probe, in a daemon thread. Re-run safe: every step first looks for this run id's
    resource (tags / names) and adopts it, so a second start() with the same RUN_ID never duplicates anything."""

    def __init__(self, run_id: str, session: boto3.Session, prefix: str = DEFAULT_PREFIX, *,
                 cidr: str = "10.83.0.0/16", tags: dict | None = None, policy: str | dict | None = "slide32",
                 azs: tuple[str, str] | None = None):
        """run_id: the notebook's RUN_ID · session: mc.get_session() · policy: "slide32" (default), a policy dict,
        or None (AWS's default full-access endpoint policy: then every probe reaches AgentCore)."""
        if not session.region_name:
            raise ValueError("the session needs a region (mc.get_session() sets us-east-1)")
        self.run_id, self.session, self.prefix = run_id, session, prefix
        self.region = session.region_name
        self.names = resource_names(run_id, prefix)
        self.cidr = cidr
        self.tags = {"project": "mladas", "module": "M03", "run_id": run_id, **(tags or {})}
        self.allowed_memory, self.other_memory = fake_memory_ids(run_id)
        self._policy_arg, self._azs_arg = policy, azs
        self._c = _Clients(session)
        self._account: str | None = None
        self._role_arn: str | None = None
        self.ids: dict[str, Any] = {}
        self.timings: dict[str, float] = {}
        self.events: list[tuple[float, str, str]] = []
        self.status, self.error = "NOT_STARTED", None
        self.drain_result: TeardownResult | None = None
        self._invocations: list[float] = []
        self._released_at: float | None = None
        self._ep_created: float | None = None                 # epoch seconds: the endpoint bills from here ...
        self._ep_deleted: float | None = None                 # ... until its deletion request
        self._t0 = time.time()
        self._thread: threading.Thread | None = None
        self._drain_thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._done = threading.Event()

    # ------------------------------------------------------------------------------ properties
    @property
    def account(self) -> str:
        if self._account is None:
            self._account = self._c.sts.get_caller_identity()["Account"]
        return self._account

    @property
    def policy(self) -> dict | None:
        """The endpoint policy this deployment gives the endpoint AT CREATION (valid slide-32 policy by default)."""
        if self._policy_arg == "slide32":
            return slide32_policy(self.account, self.region, self.allowed_memory)
        return self._policy_arg if isinstance(self._policy_arg, dict) or self._policy_arg is None \
            else json.loads(self._policy_arg)

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    @property
    def drain_done(self) -> bool:
        return self._drain_thread is not None and not self._drain_thread.is_alive()

    # ------------------------------------------------------------------------------ public API
    def start(self) -> VpcProbeDeployment:
        """Deploy (or adopt) in a daemon thread and return at once. A no-op while running or READY. Never raises:
        a failure sets .status = "FAILED" and .error (VpcLimitExceeded included, with a clear message)."""
        if (self._thread and self._thread.is_alive()) or self.ready:
            return self
        self._cancel.clear()
        self._done.clear()
        self._t0, self.events, self.error = time.time(), [], None
        self._log("STARTING", f"{self.names['base']}: VPC + bedrock-agentcore endpoint + in-VPC probe Lambda")
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"vpcprobe-{self.names['base']}")
        self._thread.start()
        return self

    def wait(self, timeout: float = 90) -> bool:
        """Block until READY / FAILED / CANCELLED or `timeout` s (keep it <= 90 in class). True when READY."""
        if self._thread is not None:
            self._done.wait(timeout)
        return self.ready

    def progress(self) -> list[tuple[float, str, str]]:
        """[(seconds since start, step, message), ...] — the same shape as GatewayDeployment.progress()."""
        return list(self.events)

    def probe(self, only: list[str] | tuple[str, ...] | None = None, marker: str | None = None) -> dict:
        """ONE Lambda invoke from inside the VPC (default set: 1-6.5 s). `only` picks from PROBES; the default is
        allowed · denied_resource · denied_action · no_endpoint. Extra: "control_plane" (~9 s timeout: no
        bedrock-agentcore-control endpoint) and "gateway_dns" (a Gateway host name -> NXDOMAIN inside this VPC).
        -> {"rows": [{probe, call, resource, expected, outcome, http, ms, result, as_expected, reason, message, host,
        dns}], "ok": every row as expected, "dns": {host: text}, "invoke_s", "handler_ms", "lambda_boto3", "error"}.
        Never raises for AWS errors (they come back in "error" with rows = [])."""
        only = list(only or DEFAULT_PROBES)
        bad = [p for p in only if p not in PROBES]
        if bad:
            raise ValueError(f"unknown probe(s) {bad}; choose from {PROBES}")
        empty = {"rows": [], "ok": False, "dns": {}, "invoke_s": None, "handler_ms": None, "lambda_boto3": None}
        if self.status in ("RELEASED", "DELETING", "DELETED", "DELETE_PENDING", "DELETE_INCOMPLETE"):
            return {**empty, "error": f"the probe Lambda is gone (status {self.status})"}
        try:
            raw = self._invoke(only, marker)
        except (ClientError, BotoCoreError) as e:
            return {**empty, "error": _mask(f"{_code(e)}: {e}", self._account)}
        if raw.get("function_error"):
            return {**empty, "invoke_s": raw.get("invoke_s"),
                    "error": _mask(f"Lambda {raw['function_error']}: {raw.get('errorMessage')}", self._account)}
        return self._rows(raw, only)

    def network_layers(self) -> dict:
        """Read-only describe calls on THIS VPC for §7.1 (slide 27: route tables, network ACLs, security groups).
        -> {"vpc": {...}, "route_tables": [...], "internet_gateways": [...], "nat_gateways": [...],
        "network_acls": [...], "security_groups": [...], "endpoints": [...], "summary": [facts as sentences]}
        Every list is DataFrame-ready. Never raises (an error comes back as {"error": ...})."""
        try:
            return self._network_layers()
        except (ClientError, BotoCoreError, RuntimeError, KeyError, IndexError) as e:
            return {"error": _mask(f"{_code(e)}: {e}", self._account)}

    def runtime_network_config(self) -> dict:
        """The CreateAgentRuntime networkConfiguration you WOULD pass to put a Runtime in this VPC (not called)."""
        return runtime_vpc_config_shape(self.ids.get("subnets"), [self.ids["fn_sg"]] if self.ids.get("fn_sg") else None)

    def release_network(self) -> dict:
        """Delete the probe Lambda NOW (§7's last cell) so its hyperplane ENIs drain during the rest of class
        (18.2-19.0 min in 4/5 research runs). Keeps the execution role: Lambda needs it to delete the ENIs.
        -> {"function", "result": "deleted" | "already gone" | "error: …", "seconds", "note"}. Never raises."""
        t = time.time()
        if self._thread is not None and self._thread.is_alive():
            self._cancel.set()
            self._thread.join(timeout=30)
        try:
            self._c.lam.delete_function(FunctionName=self.names["function"])
            result = "deleted"
            self._log("RELEASED", f"Lambda {self.names['function']} deleted; Lambda frees its ENIs in ~18-19 min")
        except ClientError as e:
            result = ("already gone" if _code(e) == "ResourceNotFoundException"
                      else _mask(f"error: {_code(e)}: {e}", self._account))
            if result == "already gone":
                self.status = "RELEASED"
        except BotoCoreError as e:
            result = f"error: {type(e).__name__}: {e}"
        if not result.startswith("error"):
            self._released_at = self._released_at or time.time()
        return {"function": self.names["function"], "result": result, "seconds": round(time.time() - t, 2),
                "note": f"Lambda releases the function's ENIs about {ENI_DRAIN_MINUTES[0]:.0f}-{ENI_DRAIN_MINUTES[1]:.0f} "
                        "min after DeleteFunction; the VPC, subnets, SGs and role wait for that ($0)."}

    def teardown(self, wait_enis: float = 0, poll: float = 15) -> TeardownResult:
        """Delete this run's resources in dependency order and verify each by id. Phase 1 (seconds): Lambda,
        interface endpoint (the only billable part), log group. Phase 2 (ENI-bound, $0): security groups, subnets,
        VPC, and only then the IAM role — retried for `wait_enis` seconds. wait_enis=0 is the wrap-up's choice:
        the endpoint goes at once and the shell comes back as "pending: …". Safe to call twice; never raises."""
        self._cancel.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=60)
        self.status = "DELETING"
        if self.ids.get("endpoint") and self._ep_deleted is None:
            self._ep_deleted = time.time()                     # the endpoint (the only billable part) goes first
        self._log("DELETING", f"teardown(wait_enis={wait_enis:.0f})")
        try:
            out = _delete(self._c, self.names, self.run_id, wait_enis=wait_enis, poll=poll, known=self.ids,
                          released_at=self._released_at, account=self._account)
        except BaseException as e:  # noqa: BLE001 — a teardown must report, not crash the wrap-up
            out = TeardownResult({"VPC probe teardown": _mask(f"error: {type(e).__name__}: {e}", self._account)})
        self.status = "DELETED" if out.all_gone else ("DELETE_INCOMPLETE" if out.errors else "DELETE_PENDING")
        self._log(self.status, f"{sum(v.startswith(_GONE) for v in out.values())} gone, {len(out.pending)} pending, "
                               f"{len(out.errors)} errors in {out.seconds} s")
        return out

    def drain_in_background(self, wait_enis: float = 1800, poll: float = 15) -> VpcProbeDeployment:
        """Finish the ENI-bound shell in a daemon thread (keeps going while the kernel lives). Check .drain_done and
        .drain_result (a TeardownResult); teardown_m03.py --run-id is the safety net if the kernel stops first."""
        if self._drain_thread is not None and self._drain_thread.is_alive():
            return self

        def work():
            try:
                self.drain_result = _delete(self._c, self.names, self.run_id, wait_enis=wait_enis, poll=poll,
                                            known=self.ids, released_at=self._released_at, account=self._account)
            except BaseException as e:  # noqa: BLE001
                self.drain_result = TeardownResult({"VPC probe drain": _mask(f"error: {type(e).__name__}: {e}",
                                                                             self._account)})
            self.status = "DELETED" if self.drain_result.all_gone else "DELETE_PENDING"

        self._drain_thread = threading.Thread(target=work, daemon=True, name=f"vpcdrain-{self.names['base']}")
        self._drain_thread.start()
        return self

    def cost_estimate(self) -> dict:
        """What this run's VPC demo costs: endpoint AZ-hours since CreationTimestamp (until now or its deletion
        request) × $0.01, plus the probe Lambda (requests + arm64 GB-s). VPC, subnets, SGs, role, ENIs: $0."""
        azs = max(len(self.ids.get("subnets", [])), 1)
        if self._ep_created is None and self.ids.get("endpoint"):
            try:
                e = self._c.ec2.describe_vpc_endpoints(VpcEndpointIds=[self.ids["endpoint"]])["VpcEndpoints"][0]
                self._ep_created = e["CreationTimestamp"].timestamp()
            except (ClientError, BotoCoreError, IndexError, KeyError):
                pass
        end = self._ep_deleted or time.time()
        hours = max(0.0, (end - self._ep_created) / 3600) if self._ep_created else 0.0
        endpoint_usd = hours * azs * PRICES["endpoint_az_hour"]
        lambda_usd = len(self._invocations) * PRICES["lambda_request"] + \
            sum(self._invocations) * 0.25 * PRICES["lambda_gb_second_arm"]
        return {"endpoint_az_hours": round(hours * azs, 3), "endpoint_usd": round(endpoint_usd, 4),
                "lambda_invocations": len(self._invocations), "lambda_usd": round(lambda_usd, 6),
                "total_usd": round(endpoint_usd + lambda_usd, 4),
                "note": f"interface endpoint ${PRICES['endpoint_az_hour']}/AZ-hour × {azs} AZs; "
                        f"a NAT gateway would be ${PRICES['nat_gateway_hour']}/hour + $/GB"}

    # ------------------------------------------------------------------------------ deploy steps
    def _log(self, step: str, msg: str) -> None:
        self.status = step
        self.events.append((round(time.time() - self._t0, 1), step, _mask(msg, self._account)))

    def _note(self, msg: str) -> None:
        """A progress line that does not change the status."""
        self.events.append((round(time.time() - self._t0, 1), self.status, _mask(msg, self._account)))

    def _mark(self, key: str) -> None:
        self.timings[key] = round(time.time() - self._t0, 1)

    def _sleep(self, seconds: float) -> None:
        if self._cancel.wait(seconds):
            raise _Cancelled()

    def _run(self) -> None:
        try:
            _ = self.account                                   # one STS call, in the thread (it can fail)
            with _run_lock(self.names["base"]):                # find-or-create is atomic per run id in this process
                self._log("CREATING_NETWORK", f"{self.names['vpc']}: 2 private subnets, 2 SGs, no internet route")
                self._ensure_network()
                self._mark("network_s")
                self._log("CREATING_ENDPOINT_AND_LAMBDA", "interface endpoint (policy at creation) || role -> Lambda")
                self._ensure_endpoint()
            self._ensure_role()
            self._ensure_function()
            self._wait_function()
            self._mark("lambda_active_s")
            self._wait_endpoint()
            self._mark("endpoint_available_s")
            self._log("PROBING", "waiting for 2 fast answers through the endpoint ('available' is not 'reachable')")
            self._wait_reachable()
            self._mark("ready_s")
            self._log("READY", f"endpoint {self.ids['endpoint']} + Lambda {self.names['function']} "
                               f"in {self.timings['ready_s']:.0f} s")
        except _Cancelled:
            self._log("CANCELLED", "teardown() or release_network() requested; deployment stopped")
        except ClientError as e:
            self.error = self._vpc_limit_message() if _code(e) == "VpcLimitExceeded" else \
                _mask(f"{_code(e)}: {e}", self._account)
            self._log("FAILED", self.error)
        except BaseException as e:  # noqa: BLE001 — surface everything to the notebook, never kill the kernel
            self.error = _mask(f"{type(e).__name__}: {e}", self._account)
            self._log("FAILED", self.error)
        finally:
            self._done.set()

    def _vpc_limit_message(self) -> str:
        try:
            n = len(_pages(self._c.ec2.describe_vpcs, "Vpcs"))
        except (ClientError, BotoCoreError):
            n = "?"
        return (f"VpcLimitExceeded: {self.region} already has {n} VPCs (quota: 5 per Region by default). §7 falls back "
                f"to the recorded results. Free a slot (teardown_m03.py --list) or ask for a quota increase.")

    def _pick_azs(self) -> tuple[str, str]:
        """Two AZs where both the endpoint service and Lambda VPC functions work (us-east-1: use1-az3 is out)."""
        if self._azs_arg:
            return tuple(self._azs_arg)
        ec2 = self._c.ec2
        zones = ec2.describe_availability_zones(Filters=[{"Name": "state", "Values": ["available"]},
                                                         {"Name": "zone-type", "Values": ["availability-zone"]}])
        ok = {z["ZoneName"] for z in zones["AvailabilityZones"] if z["ZoneId"] not in LAMBDA_UNSUPPORTED_AZ_IDS}
        try:
            svc = ec2.describe_vpc_endpoint_services(ServiceNames=[f"com.amazonaws.{self.region}.{SERVICE}"])
            ok &= set(svc["ServiceDetails"][0]["AvailabilityZones"])
        except (ClientError, IndexError, KeyError):
            pass
        picked = sorted(ok)[:2]
        if len(picked) < 2:
            raise RuntimeError(f"need 2 AZs that support Lambda VPC functions and {SERVICE}; found {picked}")
        return picked[0], picked[1]

    def _ensure_network(self) -> None:
        ec2, n, tags = self._c.ec2, self.names, self.tags
        vpcs = _find_vpcs(ec2, self.run_id, n["vpc"])
        if vpcs:                                               # re-run with the same RUN_ID: adopt
            vpc = vpcs[0]["VpcId"]
            self.cidr = vpcs[0]["CidrBlock"]
            self._note(f"adopted existing VPC {vpc} ({self.cidr})" +
                       (f"; {len(vpcs) - 1} more with this run id will be deleted at teardown" if len(vpcs) > 1 else ""))
        else:
            vpc = ec2.create_vpc(CidrBlock=self.cidr, TagSpecifications=_tagspec("vpc", n["vpc"], tags))["Vpc"]["VpcId"]
            self._note(f"created VPC {vpc} ({self.cidr})")
            deadline = time.time() + 60
            while ec2.describe_vpcs(VpcIds=[vpc])["Vpcs"][0]["State"] != "available":
                if time.time() > deadline:
                    raise TimeoutError(f"VPC {vpc} not available after 60 s")
                self._sleep(1)
        self.ids["vpc"] = vpc
        ec2.modify_vpc_attribute(VpcId=vpc, EnableDnsSupport={"Value": True})     # both are needed for private DNS
        ec2.modify_vpc_attribute(VpcId=vpc, EnableDnsHostnames={"Value": True})
        vf = [{"Name": "vpc-id", "Values": [vpc]}]
        mine = [s for s in _pages(ec2.describe_subnets, "Subnets", Filters=vf) if _has_run_tag(s, self.run_id)]
        by_az = {s["AvailabilityZone"]: s["SubnetId"] for s in mine}
        azs = tuple(sorted(by_az))[:2] if len(by_az) >= 2 else self._pick_azs()
        o = self.cidr.split(".")
        subnets = []
        for i, az in enumerate(azs):
            if az not in by_az:
                try:
                    by_az[az] = ec2.create_subnet(
                        VpcId=vpc, CidrBlock=f"{o[0]}.{o[1]}.{i + 1}.0/24", AvailabilityZone=az,
                        TagSpecifications=_tagspec("subnet", f"{n['subnet']}-{az[-1]}", tags))["Subnet"]["SubnetId"]
                except ClientError as e:                      # a concurrent run created it a moment ago
                    if _code(e) != "InvalidSubnet.Conflict":
                        raise
                    by_az[az] = next(s["SubnetId"] for s in _pages(ec2.describe_subnets, "Subnets", Filters=vf)
                                     if s["AvailabilityZone"] == az)
            subnets.append(by_az[az])
        self.ids["subnets"], self.ids["azs"] = subnets, list(azs)
        self.ids["ep_sg"] = self._ensure_sg(n["ep_sg"], "MLADAS M03: HTTPS from the VPC to the AgentCore endpoint",
                                            https_from=self.cidr)
        self.ids["fn_sg"] = self._ensure_sg(n["fn_sg"], "MLADAS M03: in-VPC probe Lambda (no ingress)", https_from=None)
        # tag what AWS created implicitly with the VPC, so §7.1's tables and the console show whose they are
        main_rt = vf + [{"Name": "association.main", "Values": ["true"]}]
        default_acl = vf + [{"Name": "default", "Values": ["true"]}]
        default_sg = vf + [{"Name": "group-name", "Values": ["default"]}]
        implicit = [(r["RouteTableId"], n["route_table"])
                    for r in _pages(ec2.describe_route_tables, "RouteTables", Filters=main_rt)]
        implicit += [(a["NetworkAclId"], n["nacl"])
                     for a in _pages(ec2.describe_network_acls, "NetworkAcls", Filters=default_acl)]
        implicit += [(g["GroupId"], n["default_sg"])
                     for g in _pages(ec2.describe_security_groups, "SecurityGroups", Filters=default_sg)]
        for rid, name in implicit:
            ec2.create_tags(Resources=[rid], Tags=[{"Key": "Name", "Value": name}] + _tag_list(tags))

    def _ensure_sg(self, name: str, description: str, https_from: str | None) -> str:
        ec2, vpc = self._c.ec2, self.ids["vpc"]
        flt = [{"Name": "vpc-id", "Values": [vpc]}, {"Name": "group-name", "Values": [name]}]
        found = _pages(ec2.describe_security_groups, "SecurityGroups", Filters=flt)
        if found:
            gid = found[0]["GroupId"]
        else:
            try:
                gid = ec2.create_security_group(GroupName=name, VpcId=vpc, Description=description,
                                                TagSpecifications=_tagspec("security-group", name, self.tags))["GroupId"]
            except ClientError as e:
                if _code(e) != "InvalidGroup.Duplicate":
                    raise
                gid = _pages(ec2.describe_security_groups, "SecurityGroups", Filters=flt)[0]["GroupId"]
        if https_from:
            try:
                ec2.authorize_security_group_ingress(GroupId=gid, IpPermissions=[{
                    "IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
                    "IpRanges": [{"CidrIp": https_from, "Description": "HTTPS from inside the VPC"}]}])
            except ClientError as e:
                if _code(e) != "InvalidPermission.Duplicate":
                    raise
        return gid

    def _ensure_endpoint(self) -> None:
        ec2, svc = self._c.ec2, f"com.amazonaws.{self.region}.{SERVICE}"
        live = [e for e in _pages(ec2.describe_vpc_endpoints, "VpcEndpoints",
                                  Filters=[{"Name": "vpc-id", "Values": [self.ids["vpc"]]},
                                           {"Name": "service-name", "Values": [svc]}])
                if e["State"].lower() not in ("deleting", "deleted", "failed", "rejected") and _has_run_tag(e, self.run_id)]
        policy = self.policy
        if live:
            ep = live[0]
            self.ids["endpoint"] = ep["VpcEndpointId"]
            self._ep_t = self._ep_created = ep["CreationTimestamp"].timestamp()
            same = policy is None or json.loads(ep.get("PolicyDocument") or "{}") == policy
            self._note(f"adopted existing endpoint {ep['VpcEndpointId']} ({ep['State']})" +
                       ("" if same else "; its policy differs and is NOT live-edited (2-4.5 min to apply)"))
            return
        kw: dict[str, Any] = {
            "VpcEndpointType": "Interface", "VpcId": self.ids["vpc"], "ServiceName": svc,
            "SubnetIds": self.ids["subnets"], "SecurityGroupIds": [self.ids["ep_sg"]], "PrivateDnsEnabled": True,
            "IpAddressType": "ipv4", "ClientToken": uuid.uuid4().hex,
            "TagSpecifications": _tagspec("vpc-endpoint", self.names["endpoint"], self.tags)}
        if policy is not None:
            kw["PolicyDocument"] = json.dumps(policy)          # AT CREATION: enforced from the first call
        self._ep_t = self._ep_created = time.time()
        self.ids["endpoint"] = ec2.create_vpc_endpoint(**kw)["VpcEndpoint"]["VpcEndpointId"]
        kind = "slide-32 policy" if self._policy_arg == "slide32" else "custom policy" if policy else "default policy"
        self._note(f"created endpoint {self.ids['endpoint']} ({svc}, private DNS on, {kind})")

    def _ensure_role(self) -> None:
        iam, role = self._c.iam, self.names["role"]
        trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                                                         "Action": "sts:AssumeRole"}]}
        try:
            self._role_arn = iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(trust),
                                             Tags=_tag_list(self.tags),
                                             Description=f"MLADAS M03 in-VPC probe ({self.run_id})")["Role"]["Arn"]
            self._note(f"created role {role}")
        except ClientError as e:
            if _code(e) != "EntityAlreadyExists":
                raise
            self._role_arn = iam.get_role(RoleName=role)["Role"]["Arn"]
            self._note(f"adopted existing role {role}")
        # ENI management + logs for a VPC function; the AgentCore actions are allowed here, so ONLY the endpoint denies
        iam.attach_role_policy(RoleName=role,
                               PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole")
        iam.put_role_policy(RoleName=role, PolicyName="agentcore-probe", PolicyDocument=json.dumps({
            "Version": "2012-10-17", "Statement": [
                {"Effect": "Allow", "Action": ["bedrock-agentcore:ListEvents", "bedrock-agentcore:ListMemoryExtractionJobs"],
                 "Resource": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:memory/*"},
                {"Effect": "Allow", "Action": "bedrock-agentcore:ListMemories", "Resource": "*"}]}))

    def _ensure_function(self) -> None:
        n, lam, logs = self.names, self._c.lam, self._c.logs
        try:                                                   # pre-create the log group: tagged, 1-day retention
            logs.create_log_group(logGroupName=n["log_group"], tags={k: str(v) for k, v in self.tags.items()})
        except ClientError as e:
            if _code(e) != "ResourceAlreadyExistsException":
                raise
        logs.put_retention_policy(logGroupName=n["log_group"], retentionInDays=1)
        code = _handler_zip()
        deadline, retries = time.time() + 120, 0
        while True:
            try:
                lam.create_function(
                    FunctionName=n["function"], Runtime="python3.12", Role=self._role_arn,
                    Handler="handler.lambda_handler", Code={"ZipFile": code}, Timeout=30, MemorySize=256,
                    Architectures=["arm64"], Tags={k: str(v) for k, v in self.tags.items()},
                    VpcConfig={"SubnetIds": self.ids["subnets"], "SecurityGroupIds": [self.ids["fn_sg"]]},
                    Description=f"MLADAS M03 in-VPC probe ({self.run_id})")
                self._note(f"created Lambda {n['function']} in the VPC" +
                           (f" after {retries} IAM-propagation retries" if retries else ""))
                break
            except ClientError as e:
                # IAM propagation: "The role defined for the function cannot be assumed by Lambda" / "The provided
                # execution role does not have permissions to call CreateNetworkInterface" (2-3 retries in 5/5 builds)
                if _code(e) == "InvalidParameterValueException" and "role" in str(e).lower() and time.time() < deadline:
                    retries += 1
                    self._sleep(3)
                    continue
                if _code(e) != "ResourceConflictException":
                    raise
                self._adopt_function(code)
                break
        self.timings["lambda_iam_retries"] = retries
        self._fn_t = time.time()

    def _adopt_function(self, code: bytes) -> None:
        lam, fn = self._c.lam, self.names["function"]
        self._wait_function(timeout=420, quiet=True)
        if lam.get_function_configuration(FunctionName=fn).get("CodeSha256") != _code_sha(code):
            lam.update_function_code(FunctionName=fn, ZipFile=code)
            self._note(f"adopted existing Lambda {fn}; updated its code")
        else:
            self._note(f"adopted existing Lambda {fn}")

    def _wait_function(self, timeout: float = 420, quiet: bool = False) -> None:
        deadline, last = time.time() + timeout, None
        while True:
            c = self._c.lam.get_function_configuration(FunctionName=self.names["function"])
            state, upd = c.get("State"), c.get("LastUpdateStatus")
            if state == "Active" and upd in (None, "Successful"):
                if not quiet and hasattr(self, "_fn_t"):
                    self.timings["lambda_create_to_active_s"] = round(time.time() - self._fn_t, 1)
                return
            if state == "Failed" or upd == "Failed":
                why = c.get("StateReason") or c.get("LastUpdateStatusReason")
                raise RuntimeError(f"Lambda {self.names['function']}: {why}")
            if not quiet and (state, upd) != last:
                self._note(f"Lambda {state}/{upd} (a VPC function takes ~220 s to become Active)")
                last = (state, upd)
            if time.time() > deadline:
                raise TimeoutError(f"Lambda not Active after {timeout:.0f} s (VPC functions: ~220 s)")
            self._sleep(5)

    def _wait_endpoint(self, timeout: float = 600) -> None:
        deadline = time.time() + timeout
        while True:
            ep = self._c.ec2.describe_vpc_endpoints(VpcEndpointIds=[self.ids["endpoint"]])["VpcEndpoints"][0]
            state = ep["State"].lower()
            if state == "available":
                self.timings["endpoint_create_to_available_s"] = round(time.time() - self._ep_t, 1)
                return
            if state in ("failed", "rejected", "deleted", "deleting"):
                raise RuntimeError(f"endpoint {self.ids['endpoint']}: {ep['State']} {ep.get('LastError')}")
            if time.time() > deadline:
                raise TimeoutError(f"endpoint not available after {timeout:.0f} s")
            self._sleep(5)

    def _wait_reachable(self, timeout: float = 240) -> None:
        """READY = 2 fast answers in a row through the endpoint; once, a just-'available' endpoint timed out ~2.5 min."""
        deadline, ok, t_avail, last = time.time() + timeout, 0, time.time(), None
        while True:
            try:
                r = self._invoke(["allowed"], marker="readiness")
                a = (r.get("probes") or {}).get("allowed", {})
                last = a.get("outcome") or r.get("function_error")
                fast = last in ("ResourceNotFoundException", "ValidationException", "AccessDeniedException") and \
                    a.get("ms", 99999) < 2500
            except (ClientError, BotoCoreError) as e:
                last, fast = _code(e), False
            ok = ok + 1 if fast else 0
            if ok >= 2:
                self.timings["endpoint_reachable_after_available_s"] = round(time.time() - t_avail, 1)
                return
            if time.time() > deadline:
                raise TimeoutError(f"no call through the endpoint answered within {timeout:.0f} s (last: {last})")
            self._sleep(2 if fast else 5)

    # ------------------------------------------------------------------------------ probe + views
    def _invoke(self, only: list[str], marker: str | None) -> dict:
        ev = {"allowed_memory": self.allowed_memory, "other_memory": self.other_memory, "only": only, "marker": marker}
        t = time.time()
        r = self._c.lam_invoke.invoke(FunctionName=self.names["function"], Payload=json.dumps(ev).encode())
        body = json.loads(r["Payload"].read() or b"{}")
        body["invoke_s"] = round(time.time() - t, 2)
        self._invocations.append(body["invoke_s"])
        if r.get("FunctionError"):
            body["function_error"] = r["FunctionError"]
        return body

    def _rows(self, raw: dict, only: list[str]) -> dict:
        acct, hosts = self._account, raw.get("hosts", {})
        dns = {h: _dns_text(d) for h, d in (raw.get("dns") or {}).items()}
        denied = "AccessDeniedException" if self._policy_arg is not None else "ResourceNotFoundException"
        spec = {  # probe -> (call, resource, expected, host key)
            "allowed": ("ListEvents on the memory the policy names", self.allowed_memory,
                        "ResourceNotFoundException", "agentcore"),
            "denied_resource": ("ListEvents on another memory", self.other_memory, denied, "agentcore"),
            "denied_action": ("ListMemoryExtractionJobs (action not in the policy)", self.allowed_memory, denied,
                              "agentcore"),
            "no_endpoint": ("sts:GetCallerIdentity (no STS endpoint here)", "-", "ConnectTimeoutError", "sts"),
            "control_plane": ("ListMemories via bedrock-agentcore-control (no endpoint)", "-", "ConnectTimeoutError",
                              "control"),
        }
        rows = []
        for key in only:
            if key == "gateway_dns":
                host = hosts.get("gateway", "")
                d = (raw.get("dns") or {}).get(host, {})
                outcome = "NXDOMAIN" if "error" in d else ("resolved" if d else "not checked")
                rows.append({"probe": key, "call": "resolve a Gateway URL's host name", "resource": "-",
                             "expected": "NXDOMAIN", "outcome": outcome, "http": None, "ms": d.get("ms"),
                             "result": "hidden by the bedrock-agentcore private DNS zone" if outcome == "NXDOMAIN"
                             else "resolves", "as_expected": outcome == "NXDOMAIN",
                             "reason": "needs the bedrock-agentcore.gateway endpoint" if outcome == "NXDOMAIN" else "",
                             "message": _mask(d.get("error", ", ".join(d.get("ips", []))), acct),
                             "host": host, "dns": dns.get(host, "-")})
                continue
            call, resource, expected, hkey = spec[key]
            p = (raw.get("probes") or {}).get(key, {})
            outcome, msg = p.get("outcome"), _mask(p.get("message", ""), acct)
            host = hosts.get(hkey, "")
            rows.append({"probe": key, "call": call, "resource": resource, "expected": expected, "outcome": outcome,
                         "http": p.get("http"), "ms": p.get("ms"), "result": _result(outcome, msg),
                         "as_expected": outcome == expected, "reason": _reason(outcome, msg), "message": msg,
                         "host": host, "dns": dns.get(host, "-")})
        return {"rows": rows, "ok": bool(rows) and all(r["as_expected"] for r in rows), "dns": dns,
                "invoke_s": raw.get("invoke_s"), "handler_ms": raw.get("handler_ms"),
                "lambda_boto3": raw.get("boto3"), "error": None}

    def _network_layers(self) -> dict:
        ec2 = self._c.ec2
        vpc = self.ids.get("vpc") or next(iter(v["VpcId"] for v in _find_vpcs(ec2, self.run_id, self.names["vpc"])), None)
        if not vpc:
            return {"error": f"no VPC for run {self.run_id}"}
        vf = [{"Name": "vpc-id", "Values": [vpc]}]
        v = ec2.describe_vpcs(VpcIds=[vpc])["Vpcs"][0]
        attr = lambda a, k: ec2.describe_vpc_attribute(VpcId=vpc, Attribute=a)[k]["Value"]
        subnets = {s["SubnetId"]: s for s in _pages(ec2.describe_subnets, "Subnets", Filters=vf)}
        sgs = _pages(ec2.describe_security_groups, "SecurityGroups", Filters=vf)
        sg_name = {g["GroupId"]: g["GroupName"] for g in sgs}
        enis = _pages(ec2.describe_network_interfaces, "NetworkInterfaces", Filters=vf)

        route_rows = []
        for rt in _pages(ec2.describe_route_tables, "RouteTables", Filters=vf):
            main = any(a.get("Main") for a in rt.get("Associations", []))
            explicit = [a["SubnetId"] for a in rt.get("Associations", []) if a.get("SubnetId")]
            applies = ", ".join(explicit) if explicit else f"every subnet without its own table ({len(subnets)})"
            for r in rt.get("Routes", []):
                route_rows.append({
                    "route_table": rt["RouteTableId"] + (" (main)" if main else ""), "applies_to": applies,
                    "destination": r.get("DestinationCidrBlock") or r.get("DestinationIpv6CidrBlock")
                    or r.get("DestinationPrefixListId"),
                    "target": r.get("GatewayId") or r.get("NatGatewayId") or r.get("TransitGatewayId")
                    or r.get("VpcPeeringConnectionId") or r.get("NetworkInterfaceId") or "-",
                    "state": r.get("State")})
        igws = [{"id": g["InternetGatewayId"]} for g in _pages(
            ec2.describe_internet_gateways, "InternetGateways", Filters=[{"Name": "attachment.vpc-id", "Values": [vpc]}])]
        nats = [{"id": g["NatGatewayId"], "state": g["State"]} for g in _pages(
            ec2.describe_nat_gateways, "NatGateways", Filter=vf) if g["State"] not in ("deleted", "deleting")]

        nacl_rows = []
        for acl in _pages(ec2.describe_network_acls, "NetworkAcls", Filters=vf):
            for e in sorted(acl["Entries"], key=lambda e: (e["Egress"], e["RuleNumber"])):
                pr = e.get("PortRange")
                nacl_rows.append({
                    "network_acl": acl["NetworkAclId"] + (" (default)" if acl.get("IsDefault") else ""),
                    "direction": "outbound" if e["Egress"] else "inbound",
                    "rule": "*" if e["RuleNumber"] == 32767 else e["RuleNumber"], "protocol": _proto(e["Protocol"]),
                    "ports": f"{pr['From']}-{pr['To']}" if pr else "all",
                    "cidr": e.get("CidrBlock") or e.get("Ipv6CidrBlock"), "action": e["RuleAction"],
                    "subnets": len(acl.get("Associations", []))})

        used_by: dict[str, list[str]] = {}
        for e in enis:
            what = ("probe Lambda" if _is_lambda_eni(e, self.names["function"]) else
                    "interface endpoint" if e.get("InterfaceType") == "vpc_endpoint" else e.get("InterfaceType", "eni"))
            for g in e.get("Groups", []):
                used_by.setdefault(g["GroupId"], []).append(what)
        sg_rows = []
        for g in sorted(sgs, key=lambda g: g["GroupName"]):
            users = used_by.get(g["GroupId"], [])
            attached = ", ".join(f"{u} ×{users.count(u)}" for u in sorted(set(users))) or "(nothing attached)"
            for direction, perms in (("inbound", g.get("IpPermissions", [])),
                                     ("outbound", g.get("IpPermissionsEgress", []))):
                if not perms:
                    sg_rows.append({"security_group": g["GroupName"], "id": g["GroupId"], "attached_to": attached,
                                    "direction": direction, "protocol": "-", "ports": "-",
                                    "peer": "(no rules: nothing allowed)", "description": ""})
                for p in perms:
                    peers = [(r["CidrIp"], r.get("Description", "")) for r in p.get("IpRanges", [])] + \
                            [(r["CidrIpv6"], r.get("Description", "")) for r in p.get("Ipv6Ranges", [])] + \
                            [(r["PrefixListId"], r.get("Description", "")) for r in p.get("PrefixListIds", [])] + \
                            [(sg_name.get(r.get("GroupId"), r.get("GroupId")), r.get("Description", ""))
                             for r in p.get("UserIdGroupPairs", [])]
                    for peer, desc in peers or [("-", "")]:
                        fp, tp = p.get("FromPort"), p.get("ToPort")
                        sg_rows.append({"security_group": g["GroupName"], "id": g["GroupId"], "attached_to": attached,
                                        "direction": direction, "protocol": _proto(p.get("IpProtocol", "-1")),
                                        "ports": "all" if fp in (None, -1) else (f"{fp}" if fp == tp else f"{fp}-{tp}"),
                                        "peer": peer, "description": desc})

        ep_rows = []
        for e in _pages(ec2.describe_vpc_endpoints, "VpcEndpoints", Filters=vf):
            if e["State"].lower() == "deleted":
                continue
            try:
                pol = json.loads(e.get("PolicyDocument") or "{}")
            except ValueError:
                pol = {}
            st = (pol.get("Statement") or [{}])[0]
            actions = st.get("Action", [])
            ep_rows.append({"endpoint": e["VpcEndpointId"], "service": e["ServiceName"], "type": e["VpcEndpointType"],
                            "state": e["State"], "private_dns": e.get("PrivateDnsEnabled"),
                            "subnets": len(e.get("SubnetIds", [])),
                            "security_groups": ", ".join(sg_name.get(g["GroupId"], g["GroupId"])
                                                         for g in e.get("Groups", [])),
                            "policy": "default (full access)" if actions == "*" else
                            f"{len(actions) if isinstance(actions, list) else 1} actions on "
                            f"{1 if isinstance(st.get('Resource'), str) else len(st.get('Resource') or [])} resource(s)",
                            "dns_names": [d["DnsName"] for d in e.get("DnsEntries", [])][:3],
                            "policy_document": json.loads(_mask(json.dumps(pol), self._account))})

        default_routes = [r for r in route_rows if r["destination"] in ("0.0.0.0/0", "::/0")]
        explicit_denies = sum(1 for r in nacl_rows if r["action"] == "deny" and r["rule"] != "*")
        summary = [
            (f"Route tables: {len({r['route_table'] for r in route_rows})}; routes to the internet (0.0.0.0/0): "
             f"{len(default_routes)}; internet gateways: {len(igws)}; NAT gateways: {len(nats)}."),
            (f"Network ACLs: {len({r['network_acl'] for r in nacl_rows})} (stateless, per subnet); "
             f"deny rules besides the final '*': {explicit_denies}."),
            f"Security groups: {len(sgs)} (stateful, per network interface); interfaces in the VPC: {len(enis)}.",
            f"Interface endpoints: {len(ep_rows)}; private DNS on: {sum(1 for r in ep_rows if r['private_dns'])}.",
        ]
        return {"vpc": {"id": vpc, "name": self.names["vpc"], "cidr": v["CidrBlock"],
                        "enableDnsSupport": attr("enableDnsSupport", "EnableDnsSupport"),
                        "enableDnsHostnames": attr("enableDnsHostnames", "EnableDnsHostnames"),
                        "subnets": [{"id": s, "az": d["AvailabilityZone"], "cidr": d["CidrBlock"]}
                                    for s, d in sorted(subnets.items(), key=lambda kv: kv[1]["AvailabilityZone"])]},
                "route_tables": route_rows, "internet_gateways": igws, "nat_gateways": nats,
                "network_acls": nacl_rows, "security_groups": sg_rows, "endpoints": ep_rows, "summary": summary}


# ======================================================================================
# Probe-row helpers
# ======================================================================================
def _proto(p: str) -> str:
    return {"-1": "all", "6": "tcp", "17": "udp", "1": "icmp"}.get(str(p), str(p))


def _dns_text(d: dict) -> str:
    if not d:
        return "-"
    if "error" in d:
        return "does not resolve (NXDOMAIN)" if re.search(r"No address|not known|Errno -[25]|Errno 16", d["error"]) \
            else d["error"]
    return f"{', '.join(d['ips'])} ({'private' if d.get('private') else 'public'})"


def _result(outcome: str | None, msg: str) -> str:
    if outcome in ("ResourceNotFoundException", "ValidationException", "OK"):
        return "reached AgentCore privately"
    if outcome == "AccessDeniedException":
        return "denied by the endpoint policy" if "VPC endpoint policy" in msg else "denied by IAM"
    if outcome in _TIMEOUTS or (outcome or "").endswith("Timeout"):
        return "unreachable: no route out of the VPC"
    return outcome or "no answer"


def _reason(outcome: str | None, msg: str) -> str:
    m = re.search(r"because (.+?)\.?$", msg)
    if m:
        return m.group(1)
    if outcome in _TIMEOUTS:
        return "connect timeout (no endpoint for this service, no internet route)"
    return msg[:120]


# ======================================================================================
# adopt / find / delete / verify (the wrap-up and teardown_m03.py)
# ======================================================================================
def _has_run_tag(resource: dict, run_id: str) -> bool:
    return any(t["Key"] == "run_id" and t["Value"] == run_id for t in resource.get("Tags", []))


def _find_vpcs(ec2, run_id: str, vpc_name: str) -> list[dict]:
    flt = [{"Name": "tag:run_id", "Values": [run_id]}, {"Name": "tag:project", "Values": ["mladas"]},
           {"Name": "tag:Name", "Values": [vpc_name]}]
    return sorted(_pages(ec2.describe_vpcs, "Vpcs", Filters=flt), key=lambda v: v["VpcId"])


def adopt(run_id: str, session: boto3.Session, prefix: str = DEFAULT_PREFIX, **kw) -> VpcProbeDeployment:
    """Attach to this run id's existing resources WITHOUT creating anything (e.g. after a kernel restart).
    .status: "READY" (Lambda Active + endpoint available), "ADOPTED_INCOMPLETE" (call .start() to finish) or
    "NOT_FOUND" (no VPC with this run id's tags). Never raises for AWS errors (status "FAILED" + .error)."""
    dep = VpcProbeDeployment(run_id, session, prefix, **kw)
    try:
        ec2 = dep._c.ec2
        vpcs = _find_vpcs(ec2, run_id, dep.names["vpc"])
        if not vpcs:
            dep.status = "NOT_FOUND"
            return dep
        vf = [{"Name": "vpc-id", "Values": [vpcs[0]["VpcId"]]}]
        dep.ids["vpc"], dep.cidr = vpcs[0]["VpcId"], vpcs[0]["CidrBlock"]
        subs = sorted((s for s in _pages(ec2.describe_subnets, "Subnets", Filters=vf) if _has_run_tag(s, run_id)),
                      key=lambda s: s["AvailabilityZone"])
        dep.ids["subnets"], dep.ids["azs"] = [s["SubnetId"] for s in subs], [s["AvailabilityZone"] for s in subs]
        for g in _pages(ec2.describe_security_groups, "SecurityGroups", Filters=vf):
            for key in ("ep_sg", "fn_sg"):
                if g["GroupName"] == dep.names[key]:
                    dep.ids[key] = g["GroupId"]
        eps = [e for e in _pages(ec2.describe_vpc_endpoints, "VpcEndpoints", Filters=vf)
               if e["State"].lower() not in ("deleting", "deleted") and _has_run_tag(e, run_id)]
        if eps:
            dep.ids["endpoint"] = eps[0]["VpcEndpointId"]
            dep._ep_created = eps[0]["CreationTimestamp"].timestamp()
        try:
            active = dep._c.lam.get_function_configuration(FunctionName=dep.names["function"])["State"] == "Active"
        except ClientError:
            active = False
        dep.status = "READY" if active and eps and eps[0]["State"].lower() == "available" else "ADOPTED_INCOMPLETE"
        dep.events.append((0.0, dep.status, (f"adopted VPC {dep.ids['vpc']}, endpoint {dep.ids.get('endpoint', '-')}, "
                                              f"Lambda {'Active' if active else 'missing'}")))
    except (ClientError, BotoCoreError) as e:
        dep.status, dep.error = "FAILED", f"{_code(e)}: {e}"
    finally:
        dep._done.set()
    return dep


def _find_detail(c: _Clients, names: dict, run_id: str) -> dict:
    """This run's resources: EC2 by the run_id + project + Name TAGS (and only resources inside those VPCs that
    carry this run id's tag), Lambda / IAM / logs by exact NAME. Lambda ENIs by their description."""
    ec2 = c.ec2
    vpcs = [v["VpcId"] for v in _find_vpcs(ec2, run_id, names["vpc"])]
    out: dict[str, Any] = {"vpcs": vpcs, "subnets": [], "sgs": {}, "endpoints": {}, "enis": [], "function": False,
                           "role": False, "log_groups": []}
    if vpcs:
        vf = [{"Name": "vpc-id", "Values": vpcs}]
        out["subnets"] = [s["SubnetId"] for s in _pages(ec2.describe_subnets, "Subnets", Filters=vf)
                          if _has_run_tag(s, run_id)]
        out["sgs"] = {g["GroupId"]: g["GroupName"]
                      for g in _pages(ec2.describe_security_groups, "SecurityGroups", Filters=vf)
                      if g["GroupName"] != "default" and _has_run_tag(g, run_id)}
        out["endpoints"] = {e["VpcEndpointId"]: e["State"].lower() for e in
                            _pages(ec2.describe_vpc_endpoints, "VpcEndpoints", Filters=vf)
                            if e["State"].lower() != "deleted" and _has_run_tag(e, run_id)}
    out["enis"] = _lambda_enis(ec2, names["function"])
    try:
        c.lam.get_function(FunctionName=names["function"])
        out["function"] = True
    except ClientError as e:
        if _code(e) != "ResourceNotFoundException":
            raise
    try:
        c.iam.get_role(RoleName=names["role"])
        out["role"] = True
    except ClientError as e:
        if _code(e) != "NoSuchEntity":
            raise
    out["log_groups"] = _log_groups(c.logs, names["log_group"])
    return out


def _lambda_enis(ec2, function: str) -> list[dict]:
    """The probe Lambda's hyperplane ENIs, wherever they are (by description, not by VPC)."""
    enis = _pages(ec2.describe_network_interfaces, "NetworkInterfaces",
                  Filters=[{"Name": "description", "Values": [f"AWS Lambda VPC ENI-{function}*"]}])
    return [{"id": e["NetworkInterfaceId"], "status": e["Status"], "type": e.get("InterfaceType"),
             "vpc": e.get("VpcId"), "subnet": e.get("SubnetId"), "groups": [g["GroupId"] for g in e.get("Groups", [])]}
            for e in enis if _is_lambda_eni(e, function)]


def _function_gone(c: _Clients, function: str) -> bool:
    try:
        c.lam.get_function(FunctionName=function)
        return False
    except ClientError as e:
        return _code(e) == "ResourceNotFoundException"


def _log_groups(logs, name: str) -> list[str]:
    return [g["logGroupName"] for p in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=name)
            for g in p["logGroups"] if g["logGroupName"] == name]


def find_run_resources(session: boto3.Session, run_id: str, prefix: str = DEFAULT_PREFIX) -> dict[str, list]:
    """Read-only: what this run still has. EC2 by tags (run_id + project + Name), Lambda / IAM / logs by name.
    -> {"vpcs", "subnets", "security_groups", "endpoints" (not deleting), "enis" ([{id, status, ...}] of the probe
    Lambda), "functions", "roles", "log_groups"}; every list empty = nothing left."""
    names = resource_names(run_id, prefix)
    d = _find_detail(_Clients(session), names, run_id)
    return {"vpcs": d["vpcs"], "subnets": d["subnets"], "security_groups": list(d["sgs"]),
            "endpoints": [e for e, s in d["endpoints"].items() if s != "deleting"], "enis": d["enis"],
            "functions": [names["function"]] if d["function"] else [], "roles": [names["role"]] if d["role"] else [],
            "log_groups": d["log_groups"]}


def delete_run_resources(session: boto3.Session, run_id: str, prefix: str = DEFAULT_PREFIX, *, wait_enis: float = 0,
                         poll: float = 15, log: Callable[[str], None] | None = None) -> TeardownResult:
    """Delete this run's VPC-probe resources from names/tags alone (teardown_m03.py --run-id), in dependency order,
    verified by id. wait_enis: how long to keep retrying the ENI-bound shell (~18-20 min after DeleteFunction; pass
    1800 in a script). Touches nothing without this run id's tags / names. Safe to call again."""
    c = _Clients(session)
    try:
        account = c.sts.get_caller_identity()["Account"]
    except (ClientError, BotoCoreError):
        account = None
    return _delete(c, resource_names(run_id, prefix), run_id, wait_enis=wait_enis, poll=poll, log=log, account=account)


def verify_gone(session: boto3.Session, run_id: str, ids: dict | None = None,
                prefix: str = DEFAULT_PREFIX) -> TeardownResult:
    """By id, deletes nothing: {label: "gone ✓" | "gone ✓ (deleting)" | "pending: why" | "error: why"}.
    ids: a deployment's .ids (vpc, subnets, ep_sg, fn_sg, endpoint); without it the ids come from the tags."""
    c = _Clients(session)
    names = resource_names(run_id, prefix)
    targets = _targets(_find_detail(c, names, run_id), names, ids)
    return _verify(c, names, targets, {}, None, None, time.time(), [])


def list_run_ids(session: boto3.Session, prefix: str = DEFAULT_PREFIX) -> list[str]:
    """Read-only, for teardown_m03.py --list: every run id that still owns a VPC-probe VPC, Lambda or role."""
    ec2, lam, iam = (session.client(s, config=_CFG) for s in ("ec2", "lambda", "iam"))
    stem = f"{prefix}-vpc-"
    ids = set()
    for v in _pages(ec2.describe_vpcs, "Vpcs", Filters=[{"Name": "tag:project", "Values": ["mladas"]}]):
        tags = {t["Key"]: t["Value"] for t in v.get("Tags", [])}
        if tags.get("Name", "").startswith(stem) and tags.get("run_id"):
            ids.add(tags["run_id"])
    for p in lam.get_paginator("list_functions").paginate():
        for f in p["Functions"]:
            if f["FunctionName"].startswith(stem) and f["FunctionName"].endswith("-probe"):
                rid = lam.list_tags(Resource=f["FunctionArn"]).get("Tags", {}).get("run_id")
                if rid:
                    ids.add(rid)
    for p in iam.get_paginator("list_roles").paginate():
        for r in p["Roles"]:
            if r["RoleName"].startswith(stem) and r["RoleName"].endswith("-probe"):
                tags = {t["Key"]: t["Value"] for t in iam.get_role(RoleName=r["RoleName"])["Role"].get("Tags", [])}
                if tags.get("run_id"):
                    ids.add(tags["run_id"])
    return sorted(ids)


def _targets(found: dict, names: dict, known: dict | None) -> dict:
    """Everything to delete / verify: what the tags found, plus the ids a deployment object remembers."""
    known = known or {}
    t = {"vpcs": list(found["vpcs"]), "subnets": list(found["subnets"]), "sgs": dict(found["sgs"]),
         "endpoints": dict(found["endpoints"])}
    if known.get("vpc") and known["vpc"] not in t["vpcs"]:
        t["vpcs"].append(known["vpc"])
    for s in known.get("subnets", []):
        if s not in t["subnets"]:
            t["subnets"].append(s)
    for key in ("ep_sg", "fn_sg"):
        if known.get(key):
            t["sgs"].setdefault(known[key], names[key])
    if known.get("endpoint"):
        t["endpoints"].setdefault(known["endpoint"], "?")
    return t


def _delete(c: _Clients, names: dict, run_id: str, *, wait_enis: float, poll: float = 15, known: dict | None = None,
            log: Callable[[str], None] | None = None, released_at: float | None = None,
            account: str | None = None) -> TeardownResult:
    ec2, t0 = c.ec2, time.time()
    timeline: list[tuple[float, str]] = []
    errors: dict[str, str] = {}

    def mark(what: str) -> None:
        timeline.append((round(time.time() - t0, 1), what))
        if log:
            log(f"[{time.time() - t0:7.1f}s] {_mask(what, account)}")

    def err(label: str, e: BaseException) -> None:
        errors[label] = _mask(f"error: {_code(e)}: {str(e)[:200]}", account)
        mark(f"{label}: {_code(e)}")

    found = _find_detail(c, names, run_id)
    targets = _targets(found, names, known)

    # ---- phase 1 (seconds): the Lambda, the interface endpoint (the only billable part), the log group
    if found["function"]:
        try:
            c.lam.delete_function(FunctionName=names["function"])
            released_at = released_at or time.time()
            mark(f"Lambda {names['function']} deleted (its ENIs drain in "
                 f"~{ENI_DRAIN_MINUTES[0]:.0f}-{ENI_DRAIN_MINUTES[1]:.0f} min)")
        except ClientError as e:
            if _code(e) != "ResourceNotFoundException":
                err(f"Lambda {names['function']}", e)
    for ep, state in targets["endpoints"].items():
        if state in ("deleting", "deleted"):
            continue
        try:
            bad = ec2.delete_vpc_endpoints(VpcEndpointIds=[ep]).get("Unsuccessful", [])
            codes = [u.get("Error", {}).get("Code", "") for u in bad]
            if bad and not all(cd.endswith("NotFound") for cd in codes):
                errors[f"VPC endpoint {ep}"] = f"error: {bad[0].get('Error', {}).get('Message')}"
            else:
                mark(f"endpoint {ep} delete requested (billing stops; NotFound in ~2 min)")
        except ClientError as e:
            if not _code(e).endswith("NotFound"):
                err(f"VPC endpoint {ep}", e)
    for lg in found["log_groups"]:
        try:
            c.logs.delete_log_group(logGroupName=lg)
            mark(f"log group {lg} deleted")
        except ClientError as e:
            if _code(e) != "ResourceNotFoundException":
                err(f"log group {lg}", e)

    # ---- phase 2 (ENI-bound, $0): SGs, subnets, VPC, then the role — retried until `wait_enis`
    pend_sgs, pend_subnets, pend_vpcs = dict(targets["sgs"]), list(targets["subnets"]), list(targets["vpcs"])
    role_pending = found["role"]
    deadline = time.time() + wait_enis
    while True:
        lam_enis = _lambda_enis(ec2, names["function"])
        for e in lam_enis:
            if e["status"] == "available":                 # Lambda detached it: deletable by hand (saves the wait)
                try:
                    ec2.delete_network_interface(NetworkInterfaceId=e["id"])
                    mark(f"Lambda ENI {e['id']} (available) deleted")
                except ClientError:
                    pass
        enis = _pages(ec2.describe_network_interfaces, "NetworkInterfaces",
                      Filters=[{"Name": "vpc-id", "Values": pend_vpcs}]) if pend_vpcs else []
        busy_sgs = {g["GroupId"] for e in enis for g in e.get("Groups", [])}
        busy_subnets = {e.get("SubnetId") for e in enis}
        for sg, name in list(pend_sgs.items()):
            if sg in busy_sgs:
                continue
            try:
                ec2.delete_security_group(GroupId=sg)
                mark(f"security group {name} ({sg}) deleted")
                pend_sgs.pop(sg)
            except ClientError as e:
                if _code(e) == "InvalidGroup.NotFound":
                    pend_sgs.pop(sg)
                elif _code(e) != "DependencyViolation":
                    err(f"security group {name}", e)
                    pend_sgs.pop(sg)
        for sn in list(pend_subnets):
            if sn in busy_subnets:
                continue
            try:
                ec2.delete_subnet(SubnetId=sn)
                mark(f"subnet {sn} deleted")
                pend_subnets.remove(sn)
            except ClientError as e:
                if _code(e) == "InvalidSubnetID.NotFound":
                    pend_subnets.remove(sn)
                elif _code(e) != "DependencyViolation":
                    err(f"subnet {sn}", e)
                    pend_subnets.remove(sn)
        if not pend_sgs and not pend_subnets and not enis:
            for v in list(pend_vpcs):
                try:
                    ec2.delete_vpc(VpcId=v)
                    mark(f"VPC {v} deleted")
                    pend_vpcs.remove(v)
                except ClientError as e:
                    if _code(e) == "InvalidVpcID.NotFound":
                        pend_vpcs.remove(v)
                    elif _code(e) != "DependencyViolation":
                        err(f"VPC {v}", e)
                        pend_vpcs.remove(v)
        if role_pending and not lam_enis and _function_gone(c, names["function"]):   # never before Lambda deleted its ENIs
            try:
                role = names["role"]
                for p in c.iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]:
                    c.iam.detach_role_policy(RoleName=role, PolicyArn=p["PolicyArn"])
                for p in c.iam.list_role_policies(RoleName=role)["PolicyNames"]:
                    c.iam.delete_role_policy(RoleName=role, PolicyName=p)
                c.iam.delete_role(RoleName=role)
                mark(f"IAM role {role} deleted (no Lambda ENI left)")
            except ClientError as e:
                if _code(e) != "NoSuchEntity":
                    err(f"IAM role {names['role']}", e)
            role_pending = False
        if not (pend_sgs or pend_subnets or pend_vpcs or role_pending) or time.time() >= deadline:
            break
        time.sleep(poll)
    if _log_groups(c.logs, names["log_group"]):            # a last invocation can re-create it: second sweep
        try:
            c.logs.delete_log_group(logGroupName=names["log_group"])
            mark("log group deleted (second sweep)")
        except ClientError:
            pass
    return _verify(c, names, targets, errors, released_at, account, t0, timeline)


def _verify(c: _Clients, names: dict, targets: dict, errors: dict, released_at: float | None, account: str | None,
            t0: float, timeline: list) -> TeardownResult:
    """One label per resource, in dependency order, each checked BY ID (EC2 describe by id -> *.NotFound, Lambda
    GetFunction -> ResourceNotFound, IAM GetRole -> NoSuchEntity)."""
    ec2 = c.ec2
    out = TeardownResult()
    lam_enis = _lambda_enis(ec2, names["function"])
    since = f"; DeleteFunction was {(time.time() - released_at) / 60:.0f} min ago" if released_at else ""
    drain = (f"pending: {len(lam_enis)} Lambda ENI(s) still attached — Lambda frees them "
             f"~{ENI_DRAIN_MINUTES[0]:.0f}-{ENI_DRAIN_MINUTES[1]:.0f} min after DeleteFunction{since}, $0")

    def exists(label: str, check: Callable[[], Any], gone_codes: tuple[str, ...]) -> bool | None:
        try:
            check()
            return True
        except ClientError as e:
            if _code(e) in gone_codes:
                return False
            errors.setdefault(label, _mask(f"error: {_code(e)}: {str(e)[:200]}", account))
            return None

    def put(label: str, still: bool | None, pending_why: str) -> None:
        if label in errors and still is not False:
            out[label] = errors[label]
        elif still is False:
            out[label] = _GONE
        elif still is None:
            out[label] = errors.get(label, "error: could not check")
        else:
            out[label] = pending_why

    label = f"Lambda {names['function']}"
    put(label, exists(label, lambda: c.lam.get_function(FunctionName=names["function"]), ("ResourceNotFoundException",)),
        "pending: still exists (release_network() or teardown() deletes it)")
    for ep in targets["endpoints"]:
        label = f"VPC endpoint {ep}"
        try:
            got = ec2.describe_vpc_endpoints(VpcEndpointIds=[ep])["VpcEndpoints"]
            state = got[0]["State"].lower() if got else "deleted"
            out[label] = errors.get(label) or (_GONE if state == "deleted" else f"{_GONE} (deleting)" if state == "deleting"
                                               else f"error: still {state}")
        except ClientError as e:
            out[label] = _GONE if _code(e).endswith("NotFound") else _mask(f"error: {_code(e)}", account)
    label = f"log group {names['log_group']}"
    out[label] = _GONE if not _log_groups(c.logs, names["log_group"]) else errors.get(label, "pending: still exists")
    out["Lambda ENIs"] = _GONE if not lam_enis else drain
    held_by_endpoint = {g for e in _pages(ec2.describe_network_interfaces, "NetworkInterfaces",
                                          Filters=[{"Name": "vpc-id", "Values": targets["vpcs"]}])
                        if e.get("InterfaceType") == "vpc_endpoint" for g in (x["GroupId"] for x in e.get("Groups", []))} \
        if targets["vpcs"] else set()
    for sg, name in targets["sgs"].items():
        label = f"security group {name}"
        why = ("pending: waiting for the endpoint's network interfaces to go (~2 min after DeleteVpcEndpoints), $0"
               if sg in held_by_endpoint else drain if lam_enis else "pending: DependencyViolation, retry later, $0")
        put(label, exists(label, lambda sg=sg: ec2.describe_security_groups(GroupIds=[sg]), ("InvalidGroup.NotFound",)), why)
    for sn in targets["subnets"]:
        label = f"subnet {sn}"
        put(label, exists(label, lambda sn=sn: ec2.describe_subnets(SubnetIds=[sn]), ("InvalidSubnetID.NotFound",)),
            drain if lam_enis else "pending: network interfaces still in it, $0")
    if not targets["vpcs"]:
        out[f"VPC {names['vpc']}"] = f"{_GONE} (no VPC with this run id's tags)"
    for v in targets["vpcs"]:
        label = f"VPC {v}"
        put(label, exists(label, lambda v=v: ec2.describe_vpcs(VpcIds=[v]), ("InvalidVpcID.NotFound",)),
            drain if lam_enis else "pending: subnets / security groups still in it, $0")
    label = f"IAM role {names['role']}"
    put(label, exists(label, lambda: c.iam.get_role(RoleName=names["role"]), ("NoSuchEntity",)),
        "pending: kept until Lambda has deleted its ENIs (Lambda needs this role to do it), $0")
    out.timeline, out.seconds = timeline, round(time.time() - t0, 1)
    return out


__all__ = [
    "DEFAULT_PREFIX",
    "DEFAULT_PROBES",
    "ENI_DRAIN_MINUTES",
    "HANDLER_SOURCE",
    "MEMORY_ACTIONS",
    "PRICES",
    "PROBES",
    "SERVICE",
    "TeardownResult",
    "VpcProbeDeployment",
    "adopt",
    "delete_run_resources",
    "endpoint_services",
    "fake_memory_ids",
    "find_run_resources",
    "list_run_ids",
    "resource_names",
    "run_suffix",
    "runtime_vpc_config_shape",
    "slide32_policy",
    "validate_policy",
    "verify_gone",
]

"""
agentcore_deploy — deploy a Python agent to Amazon Bedrock AgentCore Runtime from a notebook, WITHOUT Docker.

Uses AgentCore Runtime *direct code deployment*: linux/arm64 wheels + your source are zipped, uploaded to S3,
and referenced from CreateAgentRuntime(codeConfiguration=...). This mirrors the 2026 samples in
awslabs/agentcore-samples (01-features/02-host-your-agent) — the old Python starter-toolkit CLI is deprecated.

Everything created is named/tagged with the notebook's RUN_ID and removed by `teardown()`:
    S3 bucket (run-scoped) + code zip · IAM execution role (inline policy) · AgentCore runtime (+ its log group)

The deployment runs in a background thread so the notebook can keep teaching while it builds:
    job = RuntimeDeployment(...).start()   ...   job.wait(); job.runtime_arn

M04 (2026-09-28): entry_point may be a list, e.g. ["opentelemetry-instrument", "agent.py"] (ADOT auto-instrumentation
with aws-opentelemetry-distro in requirements.txt), and bucket= names the run-scoped bucket. Both are backward compatible
(M01 passes neither). agentcore_observability.RuntimeObsDeployment builds on this class.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


class RuntimeDeployment:
    """Package -> upload -> IAM role -> CreateAgentRuntime -> wait READY, in a background thread."""

    def __init__(self, *, name: str, source_dir: str | Path, run_id: str, session: boto3.Session,
                 entry_point: str | list[str] = "agent.py", protocol: str = "A2A", python_runtime: str = "PYTHON_3_12",
                 model_ids: tuple[str, ...] = ("us.amazon.nova-2-lite-v1:0",), environment: dict[str, str] | None = None,
                 platform_version: str | None = None, description: str = "MLADAS demo agent",
                 tags: dict[str, str] | None = None, bucket: str | None = None):
        """entry_point: "agent.py" (M01) or a list, e.g. ["opentelemetry-instrument", "agent.py"] to start the agent
        under ADOT auto-instrumentation (M04; needs aws-opentelemetry-distro in requirements.txt).
        bucket: the run-scoped S3 bucket name (default mladas-<account>-<run suffix>)."""
        self.session, self.run_id = session, run_id
        self.region = session.region_name
        self.account = session.client("sts").get_caller_identity()["Account"]
        suffix = run_id.replace("-", "").lower()
        self.name = f"{name}_{suffix}"[:48]                                  # [a-zA-Z][a-zA-Z0-9_]{0,47}
        self.source_dir = Path(source_dir)
        self.entry_point, self.protocol, self.python_runtime = entry_point, protocol, python_runtime
        self.model_ids, self.environment = model_ids, dict(environment or {})
        self.platform_version, self.description = platform_version, description
        self.bucket = (bucket or f"mladas-{self.account}-{suffix}")[:63]
        self.key = f"{self.name}/code.zip"
        base = name.replace("_", "-")
        prefix = base if base.startswith("mladas") else f"mladas-{base}"
        self.role_name = f"{prefix[:63 - len(suffix)]}-{suffix}"            # keep the full run suffix (teardown matches it)
        self.tags = {"course": "MLADAS", "mladas-run-id": run_id, **(tags or {})}
        self.status = "PENDING"
        self.events: list[tuple[float, str, str]] = []
        self.runtime_arn = self.runtime_id = self.role_arn = None
        self.error: BaseException | None = None
        self._t0 = time.time()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------------------------- public API
    def start(self) -> "RuntimeDeployment":
        self._thread = threading.Thread(target=self._run, name=f"deploy-{self.name}", daemon=True)
        self._thread.start()
        return self

    def wait(self, timeout: float = 900, poll: float = 5, verbose: bool = True) -> bool:
        """Block until READY/FAILED (or timeout). Returns True when READY."""
        seen = 0
        deadline = time.time() + timeout
        while self.status not in ("READY", "FAILED") and time.time() < deadline:
            if verbose and len(self.events) > seen:
                for e in self.events[seen:]:
                    print(f"  +{e[0]:6.1f}s  {e[1]:<16} {e[2]}")
                seen = len(self.events)
            time.sleep(poll)
        if verbose:
            for e in self.events[seen:]:
                print(f"  +{e[0]:6.1f}s  {e[1]:<16} {e[2]}")
        return self.status == "READY"

    def progress(self) -> list[tuple[float, str, str]]:
        return list(self.events)

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    # ---------------------------------------------------------------------------------- steps
    def _log(self, step: str, msg: str) -> None:
        self.status = step
        self.events.append((round(time.time() - self._t0, 1), step, msg))

    def _run(self) -> None:
        try:
            zip_path = self._package()
            try:
                self._upload(zip_path)
            finally:                                   # the unzipped arm64 site-packages are ~150 MB: don't keep them
                shutil.rmtree(zip_path.parent, ignore_errors=True)
            self._create_role()
            self._create_runtime()
            self._wait_ready()
        except BaseException as exc:  # noqa: BLE001 — surface everything to the notebook
            self.error = exc
            self._log("FAILED", f"{type(exc).__name__}: {exc}")

    def _package(self) -> Path:
        self._log("PACKAGING", f"installing linux/arm64 wheels for {self.python_runtime}")
        build = Path(tempfile.mkdtemp(prefix="mladas-pkg-"))
        pkg = build / "pkg"
        pyver = self.python_runtime.replace("PYTHON_", "").replace("_", ".")
        req = (self.source_dir / "requirements.txt").resolve()
        # Jupyter launched from a GUI may not have Homebrew on PATH, so also look in the usual install locations
        uv = shutil.which("uv") or next((str(c) for c in (Path("/opt/homebrew/bin/uv"), Path("/usr/local/bin/uv"),
                                                           Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv")
                                         if c.exists()), None)
        if uv:
            cmd = [uv, "pip", "install", "-q", "--python-platform", "aarch64-manylinux2014", "--python-version", pyver,
                   "--only-binary", ":all:", "--target", str(pkg), "-r", str(req)]
            # Console scripts (bin/opentelemetry-instrument, M04) get the discovering interpreter as their shebang. From
            # a notebook uv finds ../.venv; if that path has a space, uv writes a /bin/sh wrapper that execs the
            # local machine's python -> "Runtime initialization time exceeded" on AgentCore (observed 2026-09-28).
            # So: run from the build dir, without VIRTUAL_ENV, with the real (space-free) interpreter path when possible.
            real_py = os.path.realpath(sys.executable)
            if " " not in real_py:
                cmd[3:3] = ["--python", real_py]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "-q", "--platform", "manylinux2014_aarch64",
                   "--python-version", pyver, "--implementation", "cp", "--only-binary=:all:",
                   "--target", str(pkg), "-r", str(req)]
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "CONDA_PREFIX", "UV_PYTHON", "PYTHONHOME")}
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(build), env=env)
        if proc.returncode:
            shutil.rmtree(build, ignore_errors=True)
            raise RuntimeError(f"packaging failed ({proc.returncode}): {proc.stderr.strip()[-600:]}")
        for f in self.source_dir.iterdir():                     # your source goes at the zip root
            if f.is_file() and f.suffix in (".py", ".json", ".txt", ".md"):
                shutil.copy2(f, pkg / f.name)
        zip_path = build / "code.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for p in pkg.rglob("*"):
                if p.is_file() and "__pycache__" not in p.parts:
                    z.write(p, p.relative_to(pkg))
        self._log("PACKAGED", f"code.zip = {zip_path.stat().st_size / 1e6:.1f} MB")
        return zip_path

    def _upload(self, zip_path: Path) -> None:
        s3 = self.session.client("s3")
        self._log("UPLOADING", f"s3://{self.bucket}/{self.key}")
        try:
            if self.region == "us-east-1":
                s3.create_bucket(Bucket=self.bucket)
            else:
                s3.create_bucket(Bucket=self.bucket, CreateBucketConfiguration={"LocationConstraint": self.region})
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou",):
                raise
        s3.put_bucket_tagging(Bucket=self.bucket, Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in self.tags.items()]})
        s3.upload_file(str(zip_path), self.bucket, self.key)

    def _create_role(self) -> None:
        iam = self.session.client("iam")
        self._log("IAM_ROLE", f"{self.role_name} (least privilege: Nova invoke + logs/traces)")
        trust = {"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": self.account},
                          "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:*"}}}]}
        model_resources = []
        for mid in self.model_ids:
            base = mid.split(".", 1)[1] if mid.startswith(("us.", "global.", "eu.", "apac.")) else mid
            model_resources += [f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{mid}",
                                f"arn:aws:bedrock:*::foundation-model/{base}"]
        policy = {"Version": "2012-10-17", "Statement": [
            {"Sid": "InvokeNova", "Effect": "Allow",
             "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], "Resource": model_resources},
            {"Sid": "Logs", "Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                        "logs:DescribeLogStreams", "logs:DescribeLogGroups"],
             "Resource": [f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*",
                          f"arn:aws:logs:{self.region}:{self.account}:log-group:*"]},
            {"Sid": "Traces", "Effect": "Allow",
             "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules",
                        "xray:GetSamplingTargets"], "Resource": "*"},
            {"Sid": "Metrics", "Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*",
             "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}},
            {"Sid": "WorkloadIdentity", "Effect": "Allow",
             "Action": ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                        "bedrock-agentcore:GetWorkloadAccessTokenForUserId"],
             "Resource": [f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default",
                          f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default/workload-identity/*"]},
        ]}
        try:
            self.role_arn = iam.create_role(
                RoleName=self.role_name, AssumeRolePolicyDocument=json.dumps(trust),
                Description=f"AgentCore Runtime execution role for {self.name}",
                Tags=[{"Key": k, "Value": v} for k, v in self.tags.items()])["Role"]["Arn"]
        except iam.exceptions.EntityAlreadyExistsException:
            self.role_arn = iam.get_role(RoleName=self.role_name)["Role"]["Arn"]
        iam.put_role_policy(RoleName=self.role_name, PolicyName="agentcore-runtime", PolicyDocument=json.dumps(policy))

    def _create_runtime(self) -> None:
        ctl = self.session.client("bedrock-agentcore-control")
        self._log("CREATE_RUNTIME", f"{self.name}  protocol={self.protocol}  platform={self.platform_version or 'default'}")
        kwargs: dict[str, Any] = dict(
            agentRuntimeName=self.name, description=self.description,
            agentRuntimeArtifact={"codeConfiguration": {
                "code": {"s3": {"bucket": self.bucket, "prefix": self.key}},
                "runtime": self.python_runtime,
                "entryPoint": list(self.entry_point) if isinstance(self.entry_point, (list, tuple))
                else [self.entry_point]}},
            roleArn=self.role_arn, networkConfiguration={"networkMode": "PUBLIC"},
            protocolConfiguration={"serverProtocol": self.protocol},
            environmentVariables=self.environment, tags=self.tags,
            clientToken=f"mladas-{uuid.uuid4().hex}",
        )
        if self.platform_version:
            kwargs["platformVersion"] = self.platform_version
        deadline = time.time() + 120                           # new IAM roles take a few seconds to propagate
        while True:
            try:
                resp = ctl.create_agent_runtime(**kwargs)
                break
            except ClientError as e:
                if e.response["Error"]["Code"] == "ConflictException":      # same RUN_ID deployed before: adopt it
                    existing = next((r for r in self._list_runtimes() if r["agentRuntimeName"] == self.name), None)
                    if existing:
                        self.runtime_arn, self.runtime_id = existing["agentRuntimeArn"], existing["agentRuntimeId"]
                        self._log("CREATE_RUNTIME", f"{self.name} already exists -> reusing it")
                        return
                msg = str(e)
                retriable = e.response["Error"]["Code"] in ("ValidationException", "AccessDeniedException") and \
                    ("role" in msg.lower() or "assume" in msg.lower())
                if retriable and time.time() < deadline:
                    time.sleep(8)
                    kwargs["clientToken"] = f"mladas-{uuid.uuid4().hex}"
                    continue
                raise
        self.runtime_arn, self.runtime_id = resp["agentRuntimeArn"], resp["agentRuntimeId"]

    def _list_runtimes(self) -> list[dict]:
        ctl = self.session.client("bedrock-agentcore-control")
        out, token = [], None
        while True:
            page = ctl.list_agent_runtimes(maxResults=100, **({"nextToken": token} if token else {}))
            out += page.get("agentRuntimes", [])
            token = page.get("nextToken")
            if not token:
                return out

    def _wait_ready(self, timeout: float = 900) -> None:
        ctl = self.session.client("bedrock-agentcore-control")
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            rt = ctl.get_agent_runtime(agentRuntimeId=self.runtime_id)
            st = rt["status"]
            if st != last:
                self._log("RUNTIME_" + st, rt.get("failureReason", "") or self.runtime_id)
                last = st
            if st == "READY":
                self.status = "READY"
                return
            if st.endswith("FAILED"):
                raise RuntimeError(rt.get("failureReason", st))
            time.sleep(10)
        raise TimeoutError("runtime not READY in time")

    # ---------------------------------------------------------------------------------- cleanup
    def teardown(self, wait: bool = True) -> list[str]:
        """Delete exactly what this deployment created (runtime, log group, IAM role, S3 objects + bucket)."""
        done: list[str] = []
        ctl = self.session.client("bedrock-agentcore-control")
        if self._thread is not None and self._thread.is_alive():      # never race a deployment still in flight
            self._thread.join(timeout=300)
        if not self.runtime_id:                                         # e.g. the thread died after CreateAgentRuntime
            found = next((r for r in self._list_runtimes() if r["agentRuntimeName"] == self.name), None)
            if found:
                self.runtime_arn, self.runtime_id = found["agentRuntimeArn"], found["agentRuntimeId"]
        if self.runtime_id:
            try:
                for ep in ctl.list_agent_runtime_endpoints(agentRuntimeId=self.runtime_id).get("runtimeEndpoints", []):
                    if ep["name"] != "DEFAULT":
                        ctl.delete_agent_runtime_endpoint(agentRuntimeId=self.runtime_id, endpointName=ep["name"])
                ctl.delete_agent_runtime(agentRuntimeId=self.runtime_id)
                done.append(f"runtime {self.runtime_id} (deleting)")
                if wait:
                    for _ in range(60):
                        try:
                            ctl.get_agent_runtime(agentRuntimeId=self.runtime_id)
                            time.sleep(5)
                        except ClientError:
                            break
            except ClientError as e:
                done.append(f"runtime: {e.response['Error']['Code']}")
            # A runtime can flush one last log batch while it is deleting, which re-creates its log group,
            # so sweep twice with a short pause.
            logs = self.session.client("logs")
            for sweep in range(2 if wait else 1):
                for lg in logs.describe_log_groups(logGroupNamePrefix=f"/aws/bedrock-agentcore/runtimes/{self.name}")["logGroups"]:
                    logs.delete_log_group(logGroupName=lg["logGroupName"])
                    done.append(f"log group {lg['logGroupName']}")
                if sweep == 0 and wait:
                    time.sleep(15)
        iam = self.session.client("iam")
        try:
            for p in iam.list_role_policies(RoleName=self.role_name)["PolicyNames"]:
                iam.delete_role_policy(RoleName=self.role_name, PolicyName=p)
            iam.delete_role(RoleName=self.role_name)
            done.append(f"IAM role {self.role_name}")
        except iam.exceptions.NoSuchEntityException:
            pass
        s3 = self.session.resource("s3")
        try:
            b = s3.Bucket(self.bucket)
            b.object_versions.delete()
            b.objects.delete()
            b.delete()
            done.append(f"S3 bucket {self.bucket}")
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchBucket":
                done.append(f"S3: {e.response['Error']['Code']}")
        for d in Path(tempfile.gettempdir()).glob("mladas-pkg-*"):   # packaging leftovers from interrupted runs
            try:                                                      # (never another notebook's packaging in flight)
                if time.time() - d.stat().st_mtime > 3600:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
        return done


def invoke_a2a_via_boto3(session: boto3.Session, runtime_arn: str, text: str, session_id: str | None = None) -> dict:
    """Low-level alternative to an A2A client: send one JSON-RPC `message/send` through InvokeAgentRuntime."""
    dp = session.client("bedrock-agentcore")
    body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "message/send",
            "params": {"message": {"role": "user", "kind": "message", "messageId": uuid.uuid4().hex,
                                   "parts": [{"kind": "text", "text": text}]}}}
    resp = dp.invoke_agent_runtime(agentRuntimeArn=runtime_arn, runtimeSessionId=session_id or f"mladas-{uuid.uuid4().hex}",
                                   payload=json.dumps(body).encode(), contentType="application/json",
                                   accept="application/json")
    raw = resp["response"].read() if hasattr(resp.get("response"), "read") else resp.get("response")
    return json.loads(raw)


if __name__ == "__main__":  # manual smoke test:  python agentcore_deploy.py <source_dir>
    s = boto3.Session(profile_name=os.getenv("MLADAS_AWS_PROFILE") or None,
                      region_name=os.getenv("MLADAS_AWS_REGION", "us-east-1"))
    job = RuntimeDeployment(name="smoke_agent", source_dir=sys.argv[1], run_id=time.strftime("%Y%m%d-%H%M%S"),
                            session=s).start()
    print("READY" if job.wait() else f"FAILED: {job.error}")

"""
agentcore_outbound — OUTBOUND identity for a notebook agent: Amazon Bedrock AgentCore Identity (workload identity +
token vault + credential providers) in front of a partner API that really checks credentials (M03 §3).

    import agentcore_outbound as aco
    partner = aco.OctankPartner(RUN_ID, SESSION, NOTEBOOK_DIR).start()     # §0, background, ~21-24 s
    ident = aco.OutboundIdentity(RUN_ID, SESSION).start(partner)           # §0, background: + ~2 s once Octank is READY
    ident.wait(90)                                                         # §3 (or ident.create(partner) in the foreground)
    vault_tool = aco.octank_report_tool_vault(ident, partner, "CUST-1001") # the key never reaches the model
    req = ident.user_token_request("CUST-1001")                            # 3LO: {"authorizationUrl", "sessionUri"}
    token = aco.wait_for_consent(ident, req["sessionUri"], "CUST-1001", max_wait=120)
    ident.teardown(); partner.teardown()                                   # {label: "gone ✓" | "pending: …" | "error: …"}

The partner, Octank Credit Bureau, is simulated in our own account:
    * its IdP: an Amazon Cognito user pool (ESSENTIALS, managed login v2 domain), resource server `octank` with scope
      `octank/report.read`, an M2M app client for the bank (client credentials), an authorization-code app client for
      3LO (PKCE), and ONE test user `sofia` whose generated password is written ONLY to <state_dir>/.m03_3lo_login.txt
      (mode 0600) and never printed;
    * its API: one Lambda behind one API Gateway HTTP API
        GET /oauth/report/{bureau_ref}   native JWT authorizer (issuer = the pool, audience = both clients, scope
                                         octank/report.read). A user token (3LO) reads only that user's report:
                                         sofia -> BR-77410 = 200, BR-55102 (James) = 403. An M2M token reads either.
        GET /apikey/report/{bureau_ref}  the Lambda compares SHA-256(x-api-key) with a stored hash: Octank never
                                         stores the key; the key itself lives only in the token vault.

PUBLIC API
  Constants     SCOPE · RETURN_URL (http://127.0.0.1:8765/octank/callback) · TEST_USER ("sofia") · LOGIN_FILE ·
                IDENTITY_PRICE_PER_REQUEST ($0.00001 per token/API-key request) · COGNITO_M2M_PRICE_PER_TOKEN ($0.00225)
  resource_names(run_id, prefix="mladas-m03")   every name this module uses for a run (exact, run-suffixed)
  OctankPartner(run_id, session, state_dir=None, *, prefix, tags)
      .start() -> self (background, never raises) · .wait(timeout=90) -> bool · .ready · .status · .error ·
      .progress() -> [(t_s, step, message)] · .timings · .ids · .url(kind, ref) (kind "oauth" | "apikey") ·
      .call(kind, ref, *, token=None, api_key=None) -> {"http_status", ...partner JSON} · .issuer · .discovery_url ·
      .login_domain · .login_file · .domain_age_s() · .api_key_sha256 · .facts(mask=True) -> [rows] ·
      .invoke_as_user(username, ref) -> SIMULATED 3LO call (Lambda invoked with the claims API Gateway would pass;
      for the "nobody signed in" fallback only) · .teardown()
  OutboundIdentity(run_id, session, *, prefix, return_url=RETURN_URL, tags)
      .create(partner) -> self (foreground ~2 s, never raises; .ready / .status / .error) · .start(partner) -> self
      (background: waits for the partner, then create) · .wait(timeout=90) -> bool · .progress() ·
      .wat(user_id=None, user_jwt=None) ·
      .api_key(user_id=None) · .m2m_token(scopes=None) · .user_token_request(user_id, session_uri=None, *, force,
      custom_state) -> {"authorizationUrl","sessionUri"} | {"accessToken"} | {"sessionStatus"} ·
      .complete(session_uri, user_id) · .token_vault() -> dict · .facts(mask=True) -> [rows] (DescribeSecret metadata
      only, never GetSecretValue) · .least_privilege_policy(mask=True) · .requests / .wat_requests / .m2m_tokens /
      .cost_usd / .request_log · .teardown()
  CallbackServer(identity, user_id, *, port=8765)   127.0.0.1 only; .start() / .stop() / .received / .completed
      (start it BEFORE showing the sign-in URL; the browser must run on the same machine as the kernel)
  wait_for_consent(identity, session_uri, user_id, max_wait, poll=3.0) -> access token | None (0 = don't wait)
  octank_report_tool_vault(identity, partner, user_id)  Strands @tool; fetches the key INSIDE the tool body
  octank_report_tool_with_key(partner, api_key)         the naive @tool: the model passes the key it was given
  octank_report_tool_m2m(identity, partner)             Strands @tool over OAuth2 client credentials
  secret_leaked(secret, *texts, window=12) -> bool · peek(value) -> "first10…" · jwt_claims(token) -> dict
  (optional, slide 14) PartnerGateway / partner_gateway_targets(identity, partner) — a SigV4 AgentCore Gateway with
      two OpenAPI targets whose credentials come from the vault (API_KEY + OAUTH); .start/.wait/.call/.teardown
  find_run_resources(session, run_id, prefix=…, state_dir=None) -> {kind: [...]}      (read-only, by name)
  delete_run_resources(session, run_id, prefix=…, state_dir=None) -> {label: status}  (gateway -> identity -> partner)

Every AWS call goes through the boto3 Session you pass in (the notebook passes mc.get_session(), your AWS credentials). We do
NOT use the bedrock_agentcore SDK decorators (@requires_api_key / @requires_access_token): they build their clients
from boto3's DEFAULT session at decoration time and, with no workload access token in context, create an untagged
`workload-<hex>` identity plus a `.agentcore.json` file. The data-plane calls below do the same job explicitly.

Names carry the run suffix (agentcore_gateway.run_suffix: the RUN_ID lowercased, alphanumerics only); every taggable
resource is tagged {project: mladas, module: M03, run_id: <RUN_ID>}; every class adopts what already exists for the same
RUN_ID. Adopting the partner ROTATES its API key (the old key is unknown to a new process): call
identity.create(partner) again afterwards — it stores the new key in the vault. Teardown order: optional gateway ->
identity -> partner (each returns {label: status}; delete_run_resources does the same from names alone).

Verified in a test account in us-east-1, 2026-09-27: boto3 1.43.103, bedrock-agentcore 1.23.1, strands-agents 1.57.1.
Secrets (API key, client secrets, the test password, tokens) are never printed or returned in tool results.
"""

from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import os
import re
import secrets
import string
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from agentcore_gateway import run_suffix

DEFAULT_PREFIX = "mladas-m03"
SCOPE = "octank/report.read"
RESOURCE_SERVER = "octank"
CALLBACK_PORT = 8765
CALLBACK_PATH = "/octank/callback"
RETURN_URL = f"http://127.0.0.1:{CALLBACK_PORT}{CALLBACK_PATH}"
TEST_USER = "sofia"
LOGIN_FILE = ".m03_3lo_login.txt"
IDENTITY_PRICE_PER_REQUEST = 0.00001      # AgentCore Identity: $0.010 per 1,000 token or API-key requests (pricing page)
COGNITO_M2M_PRICE_PER_TOKEN = 0.00225     # Cognito client-credentials token issued (billed to the IdP owner = Octank)
GONE = "gone ✓"

_CFG = Config(retries={"total_max_attempts": 5, "mode": "standard"}, connect_timeout=5, read_timeout=30)
_DP_CFG = Config(retries={"total_max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=10)
_NOT_FOUND = ("ResourceNotFoundException", "NotFoundException", "NoSuchEntity", "UserNotFoundException")
_KINDS = {"oauth": "oauth", "apikey": "apikey", "key": "apikey"}


# ======================================================================================
# Names and small helpers
# ======================================================================================
def resource_names(run_id: str, prefix: str = DEFAULT_PREFIX) -> dict[str, str]:
    """Every name this module uses for one run. Exact names only: find/delete never match by a bare prefix."""
    sfx = run_suffix(run_id)
    fn = f"{prefix}-octank-{sfx}"
    n = {
        "pool": f"{prefix}-octank-idp-{sfx}",
        "domain_stem": f"{prefix}-octank-{sfx}",          # + "-<6 hex>": Cognito domain prefixes are global
        "m2m_client": f"{prefix}-octank-bank-m2m-{sfx}",
        "code_client": f"{prefix}-octank-bank-3lo-{sfx}",
        "function": fn,
        "function_role": f"{prefix}-octankfn-{sfx}",
        "log_group": f"/aws/lambda/{fn}",
        "api": f"{prefix}-octank-{sfx}",
        "workload": f"{prefix}-bank-agent-{sfx}",
        "apikey_provider": f"{prefix}-octank-key-{sfx}",
        "m2m_provider": f"{prefix}-octank-m2m-{sfx}",
        "user_provider": f"{prefix}-octank-3lo-{sfx}",
        "gateway": f"{prefix}-octankgw-{sfx}",           # optional slide-14 gateway
        "gateway_role": f"{prefix}-octankgw-{sfx}",
    }
    if len(n["domain_stem"]) + 7 > 63 or any(w in n["domain_stem"] for w in ("aws", "amazon", "cognito")):
        raise ValueError(f"Cognito domain {n['domain_stem']!r}: <= 56 chars and no 'aws'/'amazon'/'cognito'")
    if max(len(fn), len(n["function_role"])) > 64 or len(n["gateway"]) > 48:
        raise ValueError(f"run id {run_id!r} is too long for Lambda/IAM/Gateway names: use a shorter one")
    return n


def _code(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "")


def _msg(e: BaseException, n: int = 160) -> str:
    if isinstance(e, ClientError):
        return f"{_code(e)}: {e.response.get('Error', {}).get('Message', '')}"[:n]
    return f"{type(e).__name__}: {e}"[:n]


def peek(value: str | None, n: int = 10) -> str:
    """Safe preview of a secret or token: the first n characters + '…' (never the whole value)."""
    return (str(value)[:n] + "…") if value else "None"


def jwt_claims(token: str) -> dict:
    """Decode a JWT's payload for DISPLAY (no signature check: the partner's API Gateway verifies it)."""
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def secret_leaked(secret: str | None, *texts: Any, window: int = 12) -> bool:
    """True if `secret`, or any `window`-character slice of it, appears in any of `texts` (strings, or objects such as
    agent.messages, which are JSON-dumped). Catches partial leaks, e.g. a key with its prefix trimmed."""
    if not secret:
        return False
    blob = "\n".join(t if isinstance(t, str) else json.dumps(t, default=str) for t in texts)
    if secret in blob:
        return True
    w = min(window, len(secret))
    return any(secret[i:i + w] in blob for i in range(len(secret) - w + 1))


def _mask(text: Any, account: str | None) -> str:
    s = str(text)
    return s.replace(account, "********" + account[-4:]) if account else s


def _tags_list(tags: dict) -> list[dict]:
    return [{"Key": k, "Value": str(v)} for k, v in tags.items()]


class _Cancelled(Exception):
    pass


def _exists(check) -> bool:
    """True if check() succeeds, False on a not-found error; anything else propagates."""
    try:
        check()
        return True
    except ClientError as e:
        if _code(e) in _NOT_FOUND:
            return False
        raise


def _wait_gone(check, timeout: float, poll: float = 1.5) -> bool:
    deadline = time.time() + timeout
    while _exists(check):
        if time.time() > deadline:
            return False
        time.sleep(poll)
    return True


def _retry_call(fn, retryable, *, max_wait: float = 90, delay: float = 3, cancel: threading.Event | None = None,
                on_retry=None):
    """fn() until it succeeds; retry ClientErrors for which retryable(e) is True (IAM propagation). -> (result, retries)"""
    deadline, n = time.time() + max_wait, 0
    while True:
        try:
            return fn(), n
        except ClientError as e:
            if not retryable(e) or time.time() + delay > deadline:
                raise
            n += 1
            if on_retry and n == 1:
                on_retry(e)
            if cancel is not None:
                if cancel.wait(delay):
                    raise _Cancelled() from None
            else:
                time.sleep(delay)


def _new_password() -> str:
    """Satisfies Cognito's default policy (upper, lower, digit, symbol, >= 8)."""
    alphabet = string.ascii_letters + string.digits
    return "Oc1!" + "".join(secrets.choice(alphabet) for _ in range(16))


# ======================================================================================
# The partner's API: one Lambda (inline source, zipped in memory, no Docker)
# ======================================================================================
PARTNER_HANDLER = r'''
"""Octank Credit Bureau partner API (API Gateway HTTP API, payload format 2.0). Demo data only.

GET /apikey/report/{bureau_ref}  the caller sends x-api-key; Octank keeps only its SHA-256 (env API_KEY_SHA256)
GET /oauth/report/{bureau_ref}   API Gateway's JWT authorizer already checked issuer, client, expiry and scope.
                                 A token issued to a signed-in user (3LO) may read only that user's own report.
Nothing here logs headers: the API key and bearer tokens never reach CloudWatch.
"""
import hashlib
import hmac
import json
import os

REPORTS = {   # the same figures as bank_data.CREDIT_BUREAU_CACHE (the bank keeps a cached copy)
    "BR-77410": {"score": 742, "open_tradelines": 6, "delinquencies_24m": 0, "utilization": 0.18, "inquiries_6m": 1,
                 "as_of": "2026-08-31"},
    "BR-55102": {"score": 688, "open_tradelines": 4, "delinquencies_24m": 1, "utilization": 0.41, "inquiries_6m": 3,
                 "as_of": "2026-08-31"},
    "BR-90315": {"score": 671, "open_tradelines": 2, "delinquencies_24m": 0, "utilization": 0.09, "inquiries_6m": 1,
                 "as_of": "2026-08-31"},
}
OWN_REPORT = {"sofia": "BR-77410", "james": "BR-55102", "aisha": "BR-90315"}


def _reply(code, body):
    return {"statusCode": code, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}


def handler(event, context):
    path = event.get("rawPath", "")
    ref = (event.get("pathParameters") or {}).get("bureau_ref", "")
    if path.startswith("/apikey/"):
        sent = (event.get("headers") or {}).get("x-api-key", "")
        if not hmac.compare_digest(hashlib.sha256(sent.encode()).hexdigest(), os.environ["API_KEY_SHA256"]):
            return _reply(401, {"message": "invalid or missing x-api-key"})
        caller = {"auth": "api-key"}
    else:
        claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt", {}).get("claims", {})
        user = claims.get("username")
        caller = {"auth": "oauth2", "flow": "3LO (user)" if user else "2LO (client credentials)",
                  "client_id": claims.get("client_id"), "username": user, "scope": claims.get("scope")}
        if user and OWN_REPORT.get(user) != ref:
            return _reply(403, {"message": f"user {user} may not read {ref}", "caller": caller})
    if ref not in REPORTS:
        return _reply(404, {"message": f"unknown bureau_ref {ref}"})
    return _reply(200, {"bureau": "Octank Credit Bureau", "bureau_ref": ref, **REPORTS[ref], "caller": caller})
'''


def _handler_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo("octank.py", date_time=(2026, 9, 27, 0, 0, 0))   # fixed timestamp: same zip every time
        info.external_attr = 0o644 << 16
        z.writestr(info, PARTNER_HANDLER)
    return buf.getvalue()


# ======================================================================================
# 1. The partner: Octank Credit Bureau (Cognito IdP + HTTP API + Lambda)
# ======================================================================================
class OctankPartner:
    """Octank's IdP and API, built in a background thread (~21 s, 3 IAM-propagation retries on CreateFunction).
    Re-run safe: adopts every resource that already carries this run's name (and rotates the API key)."""

    ROUTES = {"oauth": "GET /oauth/report/{bureau_ref}", "apikey": "GET /apikey/report/{bureau_ref}"}

    def __init__(self, run_id: str, session: boto3.Session, state_dir: str | Path | None = None, *,
                 prefix: str = DEFAULT_PREFIX, tags: dict | None = None):
        """run_id: the notebook's RUN_ID · session: mc.get_session() · state_dir: where the 0600 login file goes
        (the notebook folder; .gitignore lists .m03_3lo_login.txt)."""
        self.run_id, self.session, self.prefix = run_id, session, prefix
        self.names = resource_names(run_id, prefix)
        self.region = session.region_name
        self.state_dir = Path(state_dir or ".").resolve()
        self.login_file = self.state_dir / LOGIN_FILE
        self.tags = {"project": "mladas", "module": "M03", "run_id": run_id, **(tags or {})}
        # clients are created on the caller's thread (boto3 sessions are not thread-safe; clients are)
        self._cg = session.client("cognito-idp", config=_CFG)
        self._lam = session.client("lambda", config=_CFG)
        self._iam = session.client("iam", config=_CFG)
        self._api = session.client("apigatewayv2", config=_CFG)
        self._logs = session.client("logs", config=_CFG)
        self._sts = session.client("sts", config=_CFG)
        self.account: str | None = None
        self.ids: dict[str, str] = {}
        self.timings: dict[str, float] = {}
        self.adopted: list[str] = []
        self.status, self.error = "NOT_STARTED", None
        self.events: list[tuple[float, str, str]] = []
        self.api_key_sha256: str | None = None     # the only form of the key Octank keeps
        self._api_key: str | None = None            # issued to the bank; handed to the vault by OutboundIdentity.create
        self._domain_created_at: float | None = None
        self._t0 = time.time()
        self._thread: threading.Thread | None = None
        self._cancel, self._done = threading.Event(), threading.Event()

    def __repr__(self) -> str:
        return f"OctankPartner(run_id={self.run_id!r}, status={self.status!r})"

    # ---------------------------------------------------------------------------------- lifecycle
    def start(self) -> "OctankPartner":
        """Build (or adopt) everything in a daemon thread and return at once. No-op while running or READY."""
        if (self._thread and self._thread.is_alive()) or self.ready:
            return self
        self._cancel.clear()
        self._done.clear()
        self._t0, self.events, self.error, self.adopted = time.time(), [], None, []
        self._log("STARTING", f"Octank Credit Bureau for run {self.run_id}")
        self._thread = threading.Thread(target=self._run, name=f"octank-{self.names['api']}", daemon=True)
        self._thread.start()
        return self

    def wait(self, timeout: float = 90) -> bool:
        """Block until READY / FAILED or `timeout` s (keep <= 90 in class). True when READY."""
        if self._thread is not None:
            self._done.wait(timeout)
        return self.ready

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    def progress(self) -> list[tuple[float, str, str]]:
        """[(seconds since start, step, message), ...] — the same shape as GatewayDeployment.progress()."""
        return list(self.events)

    def _log(self, step: str, msg: str) -> None:
        self.status = step
        self.events.append((round(time.time() - self._t0, 1), step, msg))

    def _note(self, msg: str) -> None:
        self.events.append((round(time.time() - self._t0, 1), self.status, msg))

    def _mark(self, key: str) -> None:
        self.timings[key] = round(time.time() - self._t0, 1)

    def _sleep(self, s: float) -> None:
        if self._cancel.wait(s):
            raise _Cancelled()

    def _run(self) -> None:
        try:
            self.account = self._sts.get_caller_identity()["Account"]
            self._create_idp()
            self._create_function()
            self._create_http_api()
            self._create_test_user()
            self._probe()
            self._mark("ready_s")
            self._log("READY", f"{self.ids['api_endpoint']} · pool {self.ids['pool_id']} · "
                               f"{'adopted: ' + ', '.join(self.adopted) if self.adopted else 'all new'}")
        except _Cancelled:
            self._log("CANCELLED", "teardown() requested; build stopped")
        except BaseException as e:  # noqa: BLE001 — surface everything to the notebook, never kill the kernel
            self.error = _msg(e, 400)
            self._log("FAILED", self.error)
        finally:
            self._done.set()

    # ---------------------------------------------------------------------------------- Cognito: Octank's IdP
    def _find_pool(self) -> str | None:
        for page in self._cg.get_paginator("list_user_pools").paginate(MaxResults=60):
            for p in page["UserPools"]:
                if p["Name"] == self.names["pool"]:
                    return p["Id"]
        return None

    def _create_idp(self) -> None:
        n, cg = self.names, self._cg
        self._log("CREATING_IDP", f"Cognito pool {n['pool']} (ESSENTIALS) + managed login v2 domain + scope {SCOPE}")
        pool_id = self._find_pool()
        if pool_id:
            self.adopted.append("pool")
            self._note(f"adopted existing pool {pool_id}")
        else:
            pool_id = cg.create_user_pool(
                PoolName=n["pool"], UserPoolTier="ESSENTIALS", DeletionProtection="INACTIVE",
                AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
                UsernameConfiguration={"CaseSensitive": False}, UserPoolTags=self.tags)["UserPool"]["Id"]
        self.ids["pool_id"] = pool_id
        domain = cg.describe_user_pool(UserPoolId=pool_id)["UserPool"].get("Domain")
        if domain:
            self.adopted.append("domain")
        else:   # never resolve a new domain in its first ~20 s: the Mac caches NXDOMAIN for up to 900 s
            domain = f"{n['domain_stem']}-{secrets.token_hex(3)}"
            cg.create_user_pool_domain(Domain=domain, UserPoolId=pool_id, ManagedLoginVersion=2)
            self._domain_created_at = time.time()
        self.ids["domain"] = domain
        try:
            cg.describe_resource_server(UserPoolId=pool_id, Identifier=RESOURCE_SERVER)
        except ClientError as e:
            if _code(e) != "ResourceNotFoundException":
                raise
            cg.create_resource_server(UserPoolId=pool_id, Identifier=RESOURCE_SERVER, Name="Octank Credit Bureau API",
                                      Scopes=[{"ScopeName": "report.read", "ScopeDescription": "Read credit reports"}])
        clients = {c["ClientName"]: c["ClientId"] for page in cg.get_paginator("list_user_pool_clients").paginate(
            UserPoolId=pool_id, MaxResults=60) for c in page["UserPoolClients"]}
        if n["m2m_client"] in clients:
            self.ids["m2m_client_id"] = clients[n["m2m_client"]]
        else:
            self.ids["m2m_client_id"] = cg.create_user_pool_client(
                UserPoolId=pool_id, ClientName=n["m2m_client"], GenerateSecret=True,
                AllowedOAuthFlows=["client_credentials"], AllowedOAuthScopes=[SCOPE],
                AllowedOAuthFlowsUserPoolClient=True)["UserPoolClient"]["ClientId"]
        if n["code_client"] in clients:
            self.ids["code_client_id"] = clients[n["code_client"]]
        else:   # code flow needs a callback URL at creation: a placeholder until the 3LO provider gives us its own
            self.ids["code_client_id"] = cg.create_user_pool_client(
                **self._code_client_kwargs(["https://localhost/placeholder"]), GenerateSecret=True)["UserPoolClient"]["ClientId"]
        try:
            cg.create_managed_login_branding(UserPoolId=pool_id, ClientId=self.ids["code_client_id"],
                                             UseCognitoProvidedValues=True)
        except ClientError as e:
            if _code(e) != "ManagedLoginBrandingExistsException":
                raise
        self._mark("cognito_s")

    def _code_client_kwargs(self, callback_urls: list[str]) -> dict:
        return dict(UserPoolId=self.ids["pool_id"], ClientName=self.names["code_client"],
                    AllowedOAuthFlows=["code"], AllowedOAuthScopes=["openid", SCOPE],
                    AllowedOAuthFlowsUserPoolClient=True, SupportedIdentityProviders=["COGNITO"],
                    CallbackURLs=callback_urls)

    def set_callback_urls(self, urls: list[str]) -> None:
        """Register AgentCore's 3LO callback at Octank. UpdateUserPoolClient REPLACES every field: resend them all."""
        self._cg.update_user_pool_client(ClientId=self.ids["code_client_id"], **self._code_client_kwargs(urls))

    def client_secret(self, which: str) -> str:
        """which = 'm2m' | 'code'. Read from Cognito on demand for the credential provider; never stored or printed."""
        return self._cg.describe_user_pool_client(UserPoolId=self.ids["pool_id"], ClientId=self.ids[f"{which}_client_id"]
                                                  )["UserPoolClient"]["ClientSecret"]

    # ---------------------------------------------------------------------------------- Lambda
    def _ensure_role(self) -> str:
        role = self.names["function_role"]
        trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                                                         "Action": "sts:AssumeRole"}]}
        try:
            arn = self._iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(trust), Tags=_tags_list(self.tags),
                                        Description=f"MLADAS M03 Octank partner Lambda ({self.run_id})")["Role"]["Arn"]
        except ClientError as e:
            if _code(e) != "EntityAlreadyExists":
                raise
            arn = self._iam.get_role(RoleName=role)["Role"]["Arn"]
            self.adopted.append("role")
        log_arn = f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.names['log_group']}"
        self._iam.put_role_policy(RoleName=role, PolicyName="write-own-logs", PolicyDocument=json.dumps({
            "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Resource": [log_arn, f"{log_arn}:*"],
                                                    "Action": ["logs:CreateLogStream", "logs:PutLogEvents"]}]}))
        return arn

    def _wait_function(self, ok, timeout: float = 60) -> None:
        deadline = time.time() + timeout
        while True:
            c = self._lam.get_function_configuration(FunctionName=self.names["function"])
            if ok(c):
                return
            if c.get("State") == "Failed" or c.get("LastUpdateStatus") == "Failed":
                raise RuntimeError(f"Lambda {self.names['function']}: {c.get('StateReason') or c.get('LastUpdateStatusReason')}")
            if time.time() > deadline:
                raise TimeoutError(f"Lambda {self.names['function']} not ready after {timeout:.0f} s")
            self._sleep(1)

    def _create_function(self) -> None:
        fn = self.names["function"]
        self._log("CREATING_LAMBDA", f"{fn} (python3.12, arm64): checks SHA-256(x-api-key), reads JWT claims")
        role_arn = self._ensure_role()
        try:   # pre-create the log group: tagged, 1-day retention
            self._logs.create_log_group(logGroupName=self.names["log_group"], tags=self.tags)
        except ClientError as e:
            if _code(e) != "ResourceAlreadyExistsException":
                raise
        self._logs.put_retention_policy(logGroupName=self.names["log_group"], retentionInDays=1)
        # Octank issues a new API key to the bank. Only its hash goes to the Lambda.
        self._api_key = "oct_" + secrets.token_urlsafe(24)
        self.api_key_sha256 = hashlib.sha256(self._api_key.encode()).hexdigest()
        env = {"Variables": {"API_KEY_SHA256": self.api_key_sha256}}
        code = _handler_zip()
        try:
            f, n = _retry_call(
                lambda: self._lam.create_function(
                    FunctionName=fn, Runtime="python3.12", Architectures=["arm64"], Role=role_arn,
                    Handler="octank.handler", Code={"ZipFile": code}, Timeout=10, MemorySize=256, Environment=env,
                    Tags=self.tags, Description=f"MLADAS M03 Octank Credit Bureau partner API ({self.run_id})"),
                # "The role defined for the function cannot be assumed by Lambda." = the new role has not propagated
                lambda e: _code(e) == "InvalidParameterValueException" and "assume" in str(e).lower(),
                max_wait=90, cancel=self._cancel,
                on_retry=lambda e: self._note(f"CreateFunction: {_code(e)}, retrying every 3 s (IAM propagation)"))
            self.ids["function_arn"] = f["FunctionArn"]
            self.timings["lambda_iam_retries"] = n
        except ClientError as e:
            if _code(e) != "ResourceConflictException":
                raise
            self.adopted.append("lambda")
            idle = lambda c: c.get("State") != "Pending" and c.get("LastUpdateStatus") != "InProgress"  # noqa: E731
            self._wait_function(idle)
            self._lam.update_function_code(FunctionName=fn, ZipFile=code)
            self._wait_function(idle)
            self._lam.update_function_configuration(FunctionName=fn, Role=role_arn, Environment=env)
            self._note("adopted existing Lambda: code refreshed, API key rotated (old key now rejected)")
            self.ids["function_arn"] = self._lam.get_function_configuration(FunctionName=fn)["FunctionArn"]
        self._wait_function(lambda c: c.get("State") == "Active" and c.get("LastUpdateStatus") in (None, "Successful"))
        self._mark("lambda_active_s")

    # ---------------------------------------------------------------------------------- HTTP API
    def _find_api(self) -> dict | None:
        token = None
        while True:
            page = self._api.get_apis(MaxResults="100", **({"NextToken": token} if token else {}))
            for a in page.get("Items", []):
                if a["Name"] == self.names["api"]:
                    return a
            token = page.get("NextToken")
            if not token:
                return None

    def _create_http_api(self) -> None:
        api, ids = self._api, self.ids
        self._log("CREATING_API", f"HTTP API {self.names['api']}: {self.ROUTES['oauth']} (JWT) · "
                                  f"{self.ROUTES['apikey']} (x-api-key)")
        found = self._find_api()
        if found:
            ids["api_id"], ids["api_endpoint"] = found["ApiId"], found["ApiEndpoint"]
            self.adopted.append("http_api")
        else:
            a = api.create_api(Name=self.names["api"], ProtocolType="HTTP", Tags=self.tags,
                               Description=f"MLADAS M03 Octank Credit Bureau ({self.run_id})")
            ids["api_id"], ids["api_endpoint"] = a["ApiId"], a["ApiEndpoint"]
        api_id = ids["api_id"]
        jwt_cfg = {"Issuer": self.issuer, "Audience": [ids["m2m_client_id"], ids["code_client_id"]]}
        auths = {a["Name"]: a["AuthorizerId"] for a in api.get_authorizers(ApiId=api_id).get("Items", [])}
        if "octank-cognito" in auths:
            ids["authorizer_id"] = auths["octank-cognito"]
            api.update_authorizer(ApiId=api_id, AuthorizerId=ids["authorizer_id"], JwtConfiguration=jwt_cfg)
        else:
            ids["authorizer_id"] = api.create_authorizer(
                ApiId=api_id, AuthorizerType="JWT", Name="octank-cognito", JwtConfiguration=jwt_cfg,
                IdentitySource=["$request.header.Authorization"])["AuthorizerId"]
        integ = [i["IntegrationId"] for i in api.get_integrations(ApiId=api_id).get("Items", [])
                 if i.get("IntegrationUri") == ids["function_arn"]]
        integ_id = integ[0] if integ else api.create_integration(
            ApiId=api_id, IntegrationType="AWS_PROXY", IntegrationUri=ids["function_arn"],
            PayloadFormatVersion="2.0")["IntegrationId"]
        routes = {r["RouteKey"] for r in api.get_routes(ApiId=api_id).get("Items", [])}
        target = f"integrations/{integ_id}"
        if self.ROUTES["oauth"] not in routes:
            api.create_route(ApiId=api_id, RouteKey=self.ROUTES["oauth"], Target=target, AuthorizationType="JWT",
                             AuthorizerId=ids["authorizer_id"], AuthorizationScopes=[SCOPE])
        if self.ROUTES["apikey"] not in routes:
            api.create_route(ApiId=api_id, RouteKey=self.ROUTES["apikey"], Target=target, AuthorizationType="NONE")
        try:
            api.get_stage(ApiId=api_id, StageName="$default")
        except ClientError as e:
            if _code(e) != "NotFoundException":
                raise
            api.create_stage(ApiId=api_id, StageName="$default", AutoDeploy=True, Tags=self.tags)
        try:
            self._lam.add_permission(FunctionName=self.names["function"], StatementId="octank-http-api",
                                     Action="lambda:InvokeFunction", Principal="apigateway.amazonaws.com",
                                     SourceArn=f"arn:aws:execute-api:{self.region}:{self.account}:{api_id}/*/*")
        except ClientError as e:
            if _code(e) != "ResourceConflictException":
                raise
        self._mark("api_s")

    # ---------------------------------------------------------------------------------- the 3LO test user
    def _login_file_matches(self) -> bool:
        try:
            text = self.login_file.read_text()
        except OSError:
            return False
        return (f"pool: {self.ids['pool_id']}" in text and f"run_id: {self.run_id}" in text
                and re.search(r"^password: \S+", text, re.M) is not None)

    def _write_login_file(self, password: str) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.login_file.exists():
            os.chmod(self.login_file, 0o600)
        fd = os.open(self.login_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("# Octank Credit Bureau test user for the M03 §3.4 3LO sign-in. Never project or commit this file.\n"
                    f"# run_id: {self.run_id}   pool: {self.ids['pool_id']}   (deleted by the notebook's cleanup)\n"
                    f"username: {TEST_USER}\npassword: {password}\n")
        os.chmod(self.login_file, 0o600)

    def _create_test_user(self) -> None:
        self._log("CREATING_USER", f"test user {TEST_USER!r}; password -> {self.login_file.name} (0600, never printed)")
        pool = self.ids["pool_id"]
        try:
            self._cg.admin_get_user(UserPoolId=pool, Username=TEST_USER)
            exists = True
        except ClientError as e:
            if _code(e) != "UserNotFoundException":
                raise
            exists = False
        if exists and self._login_file_matches():
            self.adopted.append("test user")
            return
        if not exists:
            self._cg.admin_create_user(UserPoolId=pool, Username=TEST_USER, MessageAction="SUPPRESS",
                                       UserAttributes=[{"Name": "email", "Value": "sofia@example.com"},
                                                       {"Name": "email_verified", "Value": "true"}])
        password = _new_password()
        self._cg.admin_set_user_password(UserPoolId=pool, Username=TEST_USER, Password=password, Permanent=True)
        self._write_login_file(password)
        del password

    def _probe(self) -> None:
        """Readiness through the consumer's read path: the key route must answer 401 (Lambda reached, no key)."""
        import httpx

        self._log("PROBING", "GET /apikey/report/BR-00000 without a key -> expect 401 from the Lambda")
        deadline, last = time.time() + 30, None
        while time.time() < deadline:
            try:
                r = httpx.get(self.url("apikey", "BR-00000"), timeout=10)
                last = r.status_code
                if last == 401:
                    return
            except httpx.HTTPError as e:
                last = type(e).__name__
            self._sleep(1.5)
        raise TimeoutError(f"partner API did not answer 401 within 30 s (last: {last})")

    # ---------------------------------------------------------------------------------- facts and calls
    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.ids['pool_id']}"

    @property
    def discovery_url(self) -> str:
        return f"{self.issuer}/.well-known/openid-configuration"

    @property
    def login_domain(self) -> str:
        return f"https://{self.ids['domain']}.auth.{self.region}.amazoncognito.com"

    def domain_age_s(self) -> float | None:
        """Seconds since this object created the Cognito domain (None if adopted = old). Resolve it only after ~20 s."""
        return None if self._domain_created_at is None else round(time.time() - self._domain_created_at, 1)

    def url(self, kind: str, ref: str) -> str:
        """kind = 'oauth' (Authorization: Bearer <token>) or 'apikey' (x-api-key header)."""
        if kind not in _KINDS:
            raise ValueError(f"kind must be 'oauth' or 'apikey', not {kind!r}")
        return f"{self.ids['api_endpoint']}/{_KINDS[kind]}/report/{ref}"

    def call(self, kind: str, ref: str, *, token: str | None = None, api_key: str | None = None,
             timeout: float = 10) -> dict:
        """One GET to the partner. -> {"http_status": int, **partner JSON} (the JSON carries a `caller` block:
        auth, flow, client_id, username, scope — identifiers, never credentials)."""
        import httpx

        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if api_key:
            headers["x-api-key"] = api_key
        r = httpx.get(self.url(kind, ref), headers=headers, timeout=timeout)
        try:
            body = r.json()
        except ValueError:
            body = {"message": r.text[:200]}
        return {"http_status": r.status_code, **(body if isinstance(body, dict) else {"body": body})}

    def invoke_as_user(self, username: str, ref: str) -> dict:
        """SIMULATION for a fallback cell (nobody signed in): invoke Octank's Lambda directly with the claims API
        Gateway would forward after verifying `username`'s 3LO token. It skips the JWT check itself, so label the output
        as simulated. -> {"http_status", ...partner JSON, "simulated": True}"""
        claims = {"username": username, "client_id": self.ids["code_client_id"], "token_use": "access",
                  "scope": f"openid {SCOPE}", "iss": self.issuer}
        event = {"version": "2.0", "routeKey": self.ROUTES["oauth"], "rawPath": f"/oauth/report/{ref}",
                 "pathParameters": {"bureau_ref": ref}, "headers": {},
                 "requestContext": {"http": {"method": "GET"}, "authorizer": {"jwt": {"claims": claims,
                                                                                      "scopes": ["openid", SCOPE]}}}}
        r = self._lam.invoke(FunctionName=self.names["function"], Payload=json.dumps(event).encode())
        reply = json.loads(r["Payload"].read())
        return {"http_status": reply.get("statusCode"), **json.loads(reply.get("body") or "{}"), "simulated": True}

    def facts(self, mask: bool = True) -> list[dict]:
        """What Octank runs, for a display table (account id masked)."""
        acct = self.account if mask else None
        ids = self.ids
        rows = [
            {"part": "IdP (Cognito user pool, ESSENTIALS)", "value": ids.get("pool_id"), "detail": self.names["pool"]},
            {"part": "sign-in page (managed login v2)", "value": self.login_domain if ids.get("domain") else None,
             "detail": f"test user {TEST_USER}; password in {self.login_file.name} (0600)"},
            {"part": "resource server + scope", "value": SCOPE, "detail": f"issuer {self.issuer if ids.get('pool_id') else '-'}"},
            {"part": "app client: bank M2M (client credentials)", "value": ids.get("m2m_client_id"), "detail": self.names["m2m_client"]},
            {"part": "app client: bank 3LO (authorization code + PKCE)", "value": ids.get("code_client_id"),
             "detail": self.names["code_client"]},
            {"part": "API (HTTP API)", "value": ids.get("api_endpoint"),
             "detail": f"{self.ROUTES['oauth']} (JWT authorizer) · {self.ROUTES['apikey']} (Lambda checks SHA-256)"},
            {"part": "Lambda", "value": _mask(ids.get("function_arn"), acct), "detail": "stores only the key's SHA-256"},
        ]
        return rows

    # ---------------------------------------------------------------------------------- teardown
    def teardown(self) -> dict[str, str]:
        """Delete in dependency order (API -> Lambda -> logs -> role -> domain -> pool -> login file), verified by id.
        Safe while the build thread runs (cancelled + joined first) and safe to call twice."""
        self._cancel.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=60)
        self.status = "DELETING"
        out = _delete_partner(self.session, self.names, run_id=self.run_id,
                              pool_ids=[self.ids["pool_id"]] if self.ids.get("pool_id") else [],
                              api_ids=[self.ids["api_id"]] if self.ids.get("api_id") else [],
                              login_file=self.login_file,
                              clients=(self._cg, self._lam, self._iam, self._api, self._logs))
        self._api_key = None
        self.status = "DELETED" if all(v == GONE for v in out.values()) else "DELETE_INCOMPLETE"
        return out


# ======================================================================================
# 2. AgentCore Identity: workload identity + token vault credential providers
# ======================================================================================
class OutboundIdentity:
    """The bank agent's workload identity + three credential providers for Octank (API key, OAuth2 M2M, OAuth2 3LO).
    Data-plane helpers call the AgentCore Identity API through YOUR session (no SDK decorators)."""

    def __init__(self, run_id: str, session: boto3.Session, *, prefix: str = DEFAULT_PREFIX,
                 return_url: str = RETURN_URL, tags: dict | None = None):
        self.run_id, self.session, self.prefix = run_id, session, prefix
        self.names = resource_names(run_id, prefix)
        self.region = session.region_name
        self.return_url = return_url
        self.tags = {"project": "mladas", "module": "M03", "run_id": run_id, **(tags or {})}
        self._cp = session.client("bedrock-agentcore-control", config=_CFG)
        self._dp = session.client("bedrock-agentcore", config=_DP_CFG)
        self._sm = session.client("secretsmanager", config=_CFG)
        self.account: str | None = None
        self.info: dict[str, Any] = {}
        self.adopted: list[str] = []
        self.timings: dict[str, float] = {}
        self.status, self.error = "NOT_CREATED", None
        self.requests = 0          # GetResourceApiKey + GetResourceOauth2Token calls = the billed Identity requests
        self.wat_requests = 0      # GetWorkloadAccessToken* + CompleteResourceTokenAuth calls (not priced separately)
        self.m2m_tokens = 0        # client-credentials tokens Octank's Cognito issued (a new one per call)
        self.request_log: list[dict] = []
        self.issued_sessions: set[str] = set()
        self.events: list[tuple[float, str, str]] = []
        self._t0 = time.time()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._done, self._cancel = threading.Event(), threading.Event()

    def __repr__(self) -> str:
        return f"OutboundIdentity(run_id={self.run_id!r}, status={self.status!r}, requests={self.requests})"

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    def start(self, partner: OctankPartner, partner_timeout: float = 180) -> "OutboundIdentity":
        """Background variant of create() for §0's early start: waits for the partner (up to partner_timeout s), then
        creates. Returns at once; never raises. Use start() OR create(), not both at the same time."""
        if (self._thread and self._thread.is_alive()) or self.ready:
            return self
        self._done.clear()
        self._cancel.clear()
        self._t0, self.events = time.time(), []
        self._event("WAITING_PARTNER", f"waiting up to {partner_timeout:.0f} s for Octank ({partner.status})")

        def run():
            try:
                deadline = time.time() + partner_timeout
                while not partner.ready and not self._cancel.is_set() and time.time() < deadline \
                        and partner.status not in ("FAILED", "CANCELLED", "DELETING", "DELETED", "DELETE_INCOMPLETE"):
                    self._cancel.wait(0.5)
                if self._cancel.is_set():
                    self.status = "CANCELLED"
                    self._event("CANCELLED", "teardown() requested before the partner was READY")
                elif partner.ready:
                    self.create(partner)
                else:
                    self.status, self.error = "FAILED", f"Octank partner is {partner.status}: {partner.error or 'timeout'}"
                    self._event("FAILED", self.error)
            except BaseException as e:  # noqa: BLE001 — never kill the kernel from a thread
                self.status, self.error = "FAILED", _msg(e, 400)
                self._event("FAILED", self.error)
            finally:
                self._done.set()
        self.status = "WAITING_PARTNER"
        self._thread = threading.Thread(target=run, name=f"octank-identity-{self.run_id}", daemon=True)
        self._thread.start()
        return self

    def wait(self, timeout: float = 90) -> bool:
        """Block until start()'s thread has finished or `timeout` s. True when READY."""
        if self._thread is not None:
            self._done.wait(timeout)
        return self.ready

    def progress(self) -> list[tuple[float, str, str]]:
        """[(seconds since start/create, step, message), ...]"""
        return list(self.events)

    def _event(self, step: str, msg: str) -> None:
        self.events.append((round(time.time() - self._t0, 1), step, msg))

    @property
    def cost_usd(self) -> float:
        """Identity fee so far: billed requests x $0.00001 (free when called from AgentCore Runtime or Gateway)."""
        return round(self.requests * IDENTITY_PRICE_PER_REQUEST, 8)

    # ---------------------------------------------------------------------------------- control plane
    def create(self, partner: OctankPartner) -> "OutboundIdentity":
        """Workload identity (+ the loopback return URL) and 3 providers; adopts existing ones and syncs them with the
        partner (new API key, new client ids). ~2 s. Never raises: check .ready / .error."""
        t0 = time.time()
        if self._thread is None:
            self._t0, self.events = t0, []
        self.status, self.error, self.adopted = "CREATING", None, []
        self._event("CREATING", f"workload identity {self.names['workload']} + 3 credential providers")
        try:
            if not partner.ready:
                raise RuntimeError(f"Octank partner is {partner.status}: {partner.error or 'not READY yet'}")
            self.account = partner.account
            self._ensure_workload()
            self._ensure_api_key(partner)
            self._ensure_oauth2("m2m", partner, "m2m")
            self._ensure_oauth2("user", partner, "code")
            partner.set_callback_urls([self.info["user_callback"]])   # Octank must accept AgentCore's callback
            self.timings["create_s"] = round(time.time() - t0, 2)
            self.status = "READY"
            self._event("READY", f"in {self.timings['create_s']} s; "
                                 f"{'adopted: ' + ', '.join(self.adopted) if self.adopted else 'all new'}")
        except (ClientError, BotoCoreError, RuntimeError, KeyError, ValueError) as e:
            self.status, self.error = "FAILED", _msg(e, 400)
            self._event("FAILED", self.error)
        return self

    def _ensure_workload(self) -> None:
        name = self.names["workload"]
        try:
            wi = self._cp.get_workload_identity(name=name)
            self.adopted.append("workload identity")
            urls = wi.get("allowedResourceOauth2ReturnUrls") or []
            if self.return_url not in urls:
                wi = self._cp.update_workload_identity(name=name, allowedResourceOauth2ReturnUrls=[*urls, self.return_url])
        except ClientError as e:
            if _code(e) != "ResourceNotFoundException":
                raise
            wi = self._cp.create_workload_identity(name=name, allowedResourceOauth2ReturnUrls=[self.return_url],
                                                   tags=self.tags)
        self.info["workload_arn"] = wi["workloadIdentityArn"]
        self.info["return_urls"] = wi.get("allowedResourceOauth2ReturnUrls") or [self.return_url]

    def _ensure_api_key(self, partner: OctankPartner) -> None:
        name, key = self.names["apikey_provider"], partner._api_key
        if not key:
            raise RuntimeError("the partner holds no freshly issued API key (was it torn down?)")
        try:
            self._cp.get_api_key_credential_provider(name=name)
            p = self._cp.update_api_key_credential_provider(name=name, apiKey=key)   # the partner rotated its key
            self.adopted.append("API-key provider (key updated)")
        except ClientError as e:
            if _code(e) != "ResourceNotFoundException":
                raise
            p = self._cp.create_api_key_credential_provider(name=name, apiKey=key, tags=self.tags)
        self.info["apikey_arn"] = p["credentialProviderArn"]
        self.info["apikey_secret"] = p["apiKeySecretArn"]["secretArn"]

    def _ensure_oauth2(self, key: str, partner: OctankPartner, which: str) -> None:
        name = self.names[f"{key}_provider"]
        client_id = partner.ids[f"{which}_client_id"]
        config = {"customOauth2ProviderConfig": {"oauthDiscovery": {"discoveryUrl": partner.discovery_url},
                                                 "clientId": client_id, "clientSecret": partner.client_secret(which)}}
        try:
            p = self._cp.get_oauth2_credential_provider(name=name)
            out = (p.get("oauth2ProviderConfigOutput") or {}).get("customOauth2ProviderConfig") or {}
            if out.get("clientId") != client_id or (out.get("oauthDiscovery") or {}).get("discoveryUrl") != partner.discovery_url:
                p = self._cp.update_oauth2_credential_provider(name=name, credentialProviderVendor="CustomOauth2",
                                                               oauth2ProviderConfigInput=config)
                self.adopted.append(f"OAuth2 {key} provider (re-pointed at the new Octank client)")
            else:
                self.adopted.append(f"OAuth2 {key} provider")
        except ClientError as e:
            if _code(e) != "ResourceNotFoundException":
                raise
            p = self._cp.create_oauth2_credential_provider(name=name, credentialProviderVendor="CustomOauth2",
                                                           oauth2ProviderConfigInput=config, tags=self.tags)
        self.info[f"{key}_arn"] = p["credentialProviderArn"]
        self.info[f"{key}_secret"] = p["clientSecretArn"]["secretArn"]
        self.info[f"{key}_callback"] = p.get("callbackUrl")
        self.info[f"{key}_status"] = p.get("status")

    # ---------------------------------------------------------------------------------- data plane
    def _call(self, api: str, fn, *, billed: bool, **kw) -> dict:
        t = time.time()
        ok = False
        try:
            r = fn(**kw)
            ok = True
            return r
        finally:
            with self._lock:
                if billed:
                    self.requests += 1
                else:
                    self.wat_requests += 1
                self.request_log.append({"t": round(t, 3), "api": api, "latency_s": round(time.time() - t, 3), "ok": ok})
                del self.request_log[:-500]

    def wat(self, user_id: str | None = None, user_jwt: str | None = None) -> str:
        """Workload access token: the agent's identity, optionally bound to the end user it acts for. Opaque (not a
        JWT). user_jwt: a Cognito access token of the signed-in user (validated by AgentCore)."""
        w = self.names["workload"]
        if user_jwt:
            return self._call("GetWorkloadAccessTokenForJWT", self._dp.get_workload_access_token_for_jwt, billed=False,
                              workloadName=w, userToken=user_jwt)["workloadAccessToken"]
        if user_id:
            return self._call("GetWorkloadAccessTokenForUserId", self._dp.get_workload_access_token_for_user_id,
                              billed=False, workloadName=w, userId=user_id)["workloadAccessToken"]
        return self._call("GetWorkloadAccessToken", self._dp.get_workload_access_token, billed=False,
                          workloadName=w)["workloadAccessToken"]

    def api_key(self, user_id: str | None = None) -> str:
        """Octank's API key from the token vault (WAT + GetResourceApiKey, ~0.25 s). Keep it out of prompts."""
        return self._call("GetResourceApiKey", self._dp.get_resource_api_key, billed=True,
                          workloadIdentityToken=self.wat(user_id),
                          resourceCredentialProviderName=self.names["apikey_provider"])["apiKey"]

    def m2m_token(self, scopes: list[str] | None = None) -> str:
        """OAuth2 client credentials (2LO): a Bearer token for Octank with no user in it. A NEW token every call."""
        tok = self._call("GetResourceOauth2Token(M2M)", self._dp.get_resource_oauth2_token, billed=True,
                         workloadIdentityToken=self.wat(), resourceCredentialProviderName=self.names["m2m_provider"],
                         scopes=scopes or [SCOPE], oauth2Flow="M2M")["accessToken"]
        with self._lock:
            self.m2m_tokens += 1
        return tok

    def user_token_request(self, user_id: str, session_uri: str | None = None, *, force: bool = False,
                           custom_state: str | None = None) -> dict:
        """3LO (authorization code + PKCE) on behalf of `user_id`. Returns ONE of:
        {"authorizationUrl", "sessionUri"}  a new single-use sign-in URL (valid ~10 min; never open it to "check" it)
        {"accessToken"}                     the user already consented (the vault holds a token / refresh token)
        {"sessionStatus": "IN_PROGRESS"}    polled with session_uri before the user finished signing in"""
        req = dict(workloadIdentityToken=self.wat(user_id), resourceCredentialProviderName=self.names["user_provider"],
                   scopes=["openid", SCOPE], oauth2Flow="USER_FEDERATION", resourceOauth2ReturnUrl=self.return_url)
        if session_uri:
            req["sessionUri"] = session_uri
        if force:
            req["forceAuthentication"] = True
        if custom_state:
            req["customState"] = custom_state
        r = self._call("GetResourceOauth2Token(USER_FEDERATION)", self._dp.get_resource_oauth2_token, billed=True, **req)
        out = {k: v for k, v in r.items() if k != "ResponseMetadata"}
        if out.get("sessionUri"):
            self.issued_sessions.add(out["sessionUri"])
        return out

    def complete(self, session_uri: str, user_id: str) -> None:
        """Bind a finished sign-in session to OUR user (CompleteResourceTokenAuth). A real app checks its own signed-in
        user before calling this; that check is what stops a stolen sign-in link from binding to someone else."""
        self._call("CompleteResourceTokenAuth", self._dp.complete_resource_token_auth, billed=False,
                   sessionUri=session_uri, userIdentifier={"userId": user_id})

    def token_vault(self) -> dict:
        """GetTokenVault: where the vault's secrets are encrypted (read only; SetTokenVaultCMK is account-level)."""
        v = self._cp.get_token_vault()
        kms = v.get("kmsConfiguration") or {}
        return {"tokenVaultId": v.get("tokenVaultId"), "keyType": kms.get("keyType"),
                "kmsKeyArn": _mask(kms["kmsKeyArn"], self.account) if kms.get("kmsKeyArn") else None,
                "lastModifiedDate": str(v.get("lastModifiedDate"))}

    def facts(self, mask: bool = True) -> list[dict]:
        """One row per Identity resource: name, ARN, and — for providers — the service-managed Secrets Manager secret
        (DescribeSecret metadata only: name, OwningService, KMS key; never GetSecretValue)."""
        acct = self.account if mask else None
        rows = [{"resource": "workload identity (the agent)", "name": self.names["workload"],
                 "arn": _mask(self.info.get("workload_arn"), acct), "status": self.status,
                 "secret": None, "secret_owner": None, "detail": "return URLs: " + ", ".join(self.info.get("return_urls", []))}]
        for key, label in (("apikey", "API-key credential provider"), ("m2m", "OAuth2 provider: M2M (2LO)"),
                           ("user", "OAuth2 provider: 3LO (user)")):
            secret_arn = self.info.get(f"{key}_secret")
            meta: dict = {}
            if secret_arn:
                try:
                    meta = self._sm.describe_secret(SecretId=secret_arn)
                except ClientError as e:
                    meta = {"Name": f"({_code(e)})"}
            detail = ("the key Octank issued" if key == "apikey" else
                      f"CustomOauth2 · {self.info.get(f'{key}_status')}" +
                      (f" · callback {self.info.get('user_callback')}" if key == "user" else ""))
            rows.append({"resource": label, "name": self.names[f"{key}_provider"],
                         "arn": _mask(self.info.get(f"{key}_arn"), acct), "status": self.info.get(f"{key}_status", "READY"),
                         "secret": meta.get("Name"), "secret_owner": meta.get("OwningService"),
                         "detail": detail + (" · KMS " + ("aws/secretsmanager" if not meta.get("KmsKeyId") else "CMK")
                                             if meta else "")})
        return rows

    def least_privilege_policy(self, mask: bool = True) -> dict:
        """What a LOCAL agent principal needs (verified 2026-09-27): the workload token on [directory, workload];
        the fetches on [directory, workload, token-vault/default, provider ARNs AS RETURNED]; and GetSecretValue on the
        managed secrets ONLY when AgentCore calls it (aws:CalledVia), so the role cannot read the raw secret."""
        a = f"arn:aws:bedrock-agentcore:{self.region}:{self.account}"
        wi_dir = f"{a}:workload-identity-directory/default"
        wi = f"{wi_dir}/workload-identity/{self.names['workload']}"
        pol = {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadAccessTokenForUserId"], "Resource": [wi_dir, wi]},
            {"Effect": "Allow", "Action": ["bedrock-agentcore:GetResourceApiKey", "bedrock-agentcore:GetResourceOauth2Token"],
             "Resource": [wi_dir, wi, f"{a}:token-vault/default", self.info.get("apikey_arn"), self.info.get("m2m_arn")]},
            {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
             "Resource": [self.info.get("apikey_secret"), self.info.get("m2m_secret")],
             "Condition": {"ForAnyValue:StringEquals": {"aws:CalledVia": ["bedrock-agentcore.amazonaws.com"]}}}]}
        return json.loads(_mask(json.dumps(pol), self.account)) if mask else pol

    # ---------------------------------------------------------------------------------- teardown
    def teardown(self) -> dict[str, str]:
        """Providers (their managed secrets are force-deleted with them) -> workload identity, verified by id.
        Cancels and joins start()'s thread first; safe to call twice."""
        self._cancel.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=30)
        out = _delete_identity(self.session, self.names,
                               secret_arns=[self.info[k] for k in ("apikey_secret", "m2m_secret", "user_secret") if self.info.get(k)],
                               clients=(self._cp, self._sm))
        self.status = "DELETED" if all(v == GONE for v in out.values()) else "DELETE_INCOMPLETE"
        return out


# ======================================================================================
# 3. The 3LO return URL: a tiny local HTTP server (stdlib, 127.0.0.1 only)
# ======================================================================================
_SERVERS: dict[int, "CallbackServer"] = {}


class CallbackServer:
    """Receives AgentCore's redirect to http://127.0.0.1:<port>/octank/callback?session_id=<sessionUri> after the user
    signs in at Octank, and binds that session to `user_id` (CompleteResourceTokenAuth). Browser on the same machine.
    Only a session this app started (identity.issued_sessions) is completed, once; any other session_id gets HTTP 400
    and nothing is bound. The reply page HTML-escapes everything it echoes."""

    def __init__(self, identity: OutboundIdentity, user_id: str, *, port: int = CALLBACK_PORT, path: str = CALLBACK_PATH):
        self.identity, self.user_id, self.port, self.path = identity, user_id, port, path
        self.received: list[dict] = []
        self.status, self.error = "NOT_STARTED", None
        self._completed_sids: set[str] = set()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __repr__(self) -> str:
        return f"CallbackServer(127.0.0.1:{self.port}{self.path}, status={self.status!r}, received={len(self.received)})"

    @property
    def completed(self) -> bool:
        """True once a sign-in session was bound to our user."""
        return any(ev.get("result") == "completed" for ev in self.received)

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (http.server API)
                u = urlsplit(self.path)
                if u.path != outer.path:
                    self._reply(404, "not found")
                    return
                sid = (parse_qs(u.query).get("session_id") or [""])[0]
                ev = {"t": round(time.time(), 1), "session_id": peek(sid, 34),
                      "known_session": sid in outer.identity.issued_sessions}
                if not sid:
                    code, msg, ev["result"] = 400, "Missing session_id.", "missing session_id"
                elif not ev["known_session"]:
                    # session binding: only a sign-in session THIS app started (GetResourceOauth2Token returned it) is
                    # completed for our user; any other page that calls this URL gets nothing bound
                    code, msg, ev["result"] = 400, "Unknown sign-in session.", "unknown session"
                elif sid in outer._completed_sids:                    # a replayed callback binds nothing twice
                    code, msg, ev["result"] = 409, "This sign-in is already complete.", "already completed"
                else:
                    try:
                        outer.identity.complete(sid, outer.user_id)
                        outer._completed_sids.add(sid)
                        code, ev["result"] = 200, "completed"
                        msg = (f"Octank Credit Bureau access granted to AnyCompany Bank for {outer.user_id}. "
                               "You can close this tab and return to the notebook.")
                    except (ClientError, BotoCoreError) as e:
                        code, msg, ev["result"] = 400, f"Could not complete the sign-in: {_msg(e, 120)}", _msg(e, 120)
                ev["http_status"] = code
                outer.received.append(ev)
                self._reply(code, msg)

            def _reply(self, code: int, text: str) -> None:
                body = (f"<!doctype html><meta charset='utf-8'><title>AnyCompany Bank</title>"   # escaped: an error
                        f"<p style='font-family:sans-serif'>{html.escape(text)}</p>").encode()   # can echo the input
                self.send_response(code)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):   # keep the notebook quiet
                pass

        return Handler

    def start(self) -> "CallbackServer":
        """Listen on 127.0.0.1:<port>. Replaces a server this process already runs on that port. Never raises."""
        old = _SERVERS.get(self.port)
        if old is not None and old is not self:
            old.stop()
        if self._httpd is not None:
            return self
        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), self._handler())
            self._httpd.daemon_threads = True
        except OSError as e:
            self.status, self.error = "FAILED", f"port {self.port} is busy ({e.strerror}): stop the other process"
            return self
        self._thread = threading.Thread(target=self._httpd.serve_forever, name=f"octank-callback-{self.port}", daemon=True)
        self._thread.start()
        _SERVERS[self.port] = self
        self.status = "LISTENING"
        return self

    def stop(self) -> None:
        """Stop listening (idempotent)."""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if _SERVERS.get(self.port) is self:
            _SERVERS.pop(self.port, None)
        if self.status == "LISTENING":
            self.status = "STOPPED"


def wait_for_consent(identity: OutboundIdentity, session_uri: str, user_id: str, max_wait: float,
                     poll: float = 3.0) -> str | None:
    """Poll the 3LO session until the user has signed in (-> the access token) or `max_wait` s pass (-> None).
    One status line that updates in place; never blocks longer than max_wait (+ one in-flight call of <= 10 s).
    max_wait <= 0 means "don't wait" (harness runs): returns None at once, without calling AWS."""
    if max_wait <= 0:
        print("Not waiting for a sign-in (max_wait = 0): the 3LO session stays open until it expires (~10 min).")
        return None
    try:                  # one line that updates in place inside a notebook; plain lines in a script
        from IPython import get_ipython
        from IPython.display import Pretty, display
        if get_ipython() is None or not hasattr(get_ipython(), "kernel"):
            display = None
    except ImportError:
        Pretty, display = str, None
    print(f"Waiting up to {max_wait:.0f} s for {user_id} to sign in at Octank ...")
    t0, handle, token = time.time(), None, None
    while True:
        elapsed = time.time() - t0
        try:
            r = identity.user_token_request(user_id, session_uri=session_uri)
            token = r.get("accessToken")
            if token:
                state = "signed in ✓ (token received from the vault)"
            elif r.get("authorizationUrl"):
                state = "the sign-in session expired: create a new authorization URL"
            else:
                state = r.get("sessionStatus") or "waiting"
        except (ClientError, BotoCoreError) as e:
            r, state = {}, _msg(e, 90)
        line = f"{time.time() - t0:5.0f}s  Octank sign-in for {user_id}: {state}"
        if display is None:
            print(line)
        elif handle is None:
            handle = display(Pretty(line), display_id=True)
        else:
            handle.update(Pretty(line))
        if token:
            return token
        if r.get("authorizationUrl"):
            return None
        remaining = max_wait - (time.time() - t0)
        if remaining < 0.5:
            print(f"No sign-in after {max_wait:.0f} s — continuing without the user's token.")
            return None
        time.sleep(min(poll, remaining))


# ======================================================================================
# 4. Strands tools (the key / token is fetched INSIDE the tool body, never in the schema, prompt or result)
# ======================================================================================
def _report_result(r) -> dict:
    try:
        body = r.json()
    except ValueError:
        body = {"message": r.text[:200]}
    body = body if isinstance(body, dict) else {"body": body}
    body.pop("caller", None)                       # identifiers only, but the model does not need them
    return {"http_status": r.status_code, **body}


def octank_report_tool_vault(identity: OutboundIdentity, partner: OctankPartner, user_id: str):
    """The vault arm: a Strands tool whose ONLY parameter is bureau_ref. It asks the token vault for Octank's API key at
    call time, on behalf of `user_id`, and the key never enters the model's context."""
    import httpx
    from strands import tool

    @tool
    def octank_credit_report(bureau_ref: str) -> dict:
        """Fetch the latest Octank Credit Bureau report (score, open tradelines, delinquencies, utilization) for a
        bureau reference such as "BR-00000". Authentication is handled for you.

        Args:
            bureau_ref: the customer's Octank bureau reference, e.g. "BR-00000".
        """
        try:
            key = identity.api_key(user_id)
            r = httpx.get(partner.url("apikey", bureau_ref), headers={"x-api-key": key}, timeout=10)
            del key
            return _report_result(r)
        except (ClientError, BotoCoreError, httpx.HTTPError) as e:
            return {"http_status": None, "error": f"Octank call failed: {type(e).__name__}"}

    return octank_credit_report


def octank_report_tool_with_key(partner: OctankPartner, api_key: str):
    """The NAIVE arm (anti-pattern, for the leak demo): the key sits in the agent's prompt and the model passes it as
    a tool argument, so it lives in the model's context (prompt, tool call, conversation history). `api_key` is the
    configured key, used only if the model leaves the argument empty."""
    import httpx
    from strands import tool

    configured = api_key

    @tool
    def octank_credit_report(bureau_ref: str, api_key: str = "") -> dict:
        """Fetch the latest Octank Credit Bureau report (score, open tradelines, delinquencies, utilization) for a
        bureau reference such as "BR-00000".

        Args:
            bureau_ref: the customer's Octank bureau reference, e.g. "BR-00000".
            api_key: the Octank API key from your instructions.
        """
        try:
            r = httpx.get(partner.url("apikey", bureau_ref), headers={"x-api-key": api_key or configured}, timeout=10)
            return _report_result(r)
        except httpx.HTTPError as e:
            return {"http_status": None, "error": f"Octank call failed: {type(e).__name__}"}

    return octank_credit_report


def octank_report_tool_m2m(identity: OutboundIdentity, partner: OctankPartner):
    """OAuth2 client credentials (2LO): the tool gets a fresh Bearer token from the vault inside its body."""
    import httpx
    from strands import tool

    @tool
    def octank_credit_report_oauth(bureau_ref: str) -> dict:
        """Fetch the latest Octank Credit Bureau report (score, open tradelines, delinquencies, utilization) for a
        bureau reference such as "BR-00000", over OAuth 2.0. Authentication is handled for you.

        Args:
            bureau_ref: the customer's Octank bureau reference, e.g. "BR-00000".
        """
        try:
            token = identity.m2m_token()
            r = httpx.get(partner.url("oauth", bureau_ref), headers={"Authorization": f"Bearer {token}"}, timeout=10)
            del token
            return _report_result(r)
        except (ClientError, BotoCoreError, httpx.HTTPError) as e:
            return {"http_status": None, "error": f"Octank call failed: {type(e).__name__}"}

    return octank_credit_report_oauth


# ======================================================================================
# 5. OPTIONAL (slide 14): the Gateway, not the agent, holds the credentials
# ======================================================================================
class PartnerGateway:
    """OPTIONAL. A SigV4 (AWS_IAM) AgentCore Gateway with two OpenAPI targets for Octank: `OctankKey` (credential
    provider type API_KEY, header x-api-key) and `OctankOauth` (OAUTH, client credentials). The gateway's own workload
    identity fetches the credentials from the vault; the agent only signs its MCP calls. READY in ~15 s including the
    ~10 s IAM warm-up (the first tool calls after the role policy is attached fail, so the build retries them)."""

    TARGETS = {"OctankKey": "apikey", "OctankOauth": "oauth"}

    def __init__(self, identity: OutboundIdentity, partner: OctankPartner, *, tags: dict | None = None):
        self.identity, self.partner = identity, partner
        self.session, self.run_id, self.region = identity.session, identity.run_id, identity.region
        self.names = identity.names
        self.tags = {"project": "mladas", "module": "M03", "run_id": self.run_id, **(tags or {})}
        self._cp = self.session.client("bedrock-agentcore-control", config=_CFG)
        self._iam = self.session.client("iam", config=_CFG)
        self.gateway_id: str | None = None
        self.gateway_url: str | None = None
        self.target_ids: dict[str, str] = {}
        self.timings: dict[str, float] = {}
        self.status, self.error = "NOT_STARTED", None
        self.events: list[tuple[float, str, str]] = []
        self._t0 = time.time()
        self._thread: threading.Thread | None = None
        self._cancel, self._done = threading.Event(), threading.Event()

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    @property
    def tool_names(self) -> list[str]:
        return [f"{t}___getCreditReport" for t in self.TARGETS]

    def progress(self) -> list[tuple[float, str, str]]:
        return list(self.events)

    def _log(self, step: str, msg: str) -> None:
        self.status = step
        self.events.append((round(time.time() - self._t0, 1), step, msg))

    def start(self) -> "PartnerGateway":
        if (self._thread and self._thread.is_alive()) or self.ready:
            return self
        self._cancel.clear()
        self._done.clear()
        self._t0, self.events, self.error = time.time(), [], None
        self._log("STARTING", f"gateway {self.names['gateway']} with targets {', '.join(self.TARGETS)}")
        self._thread = threading.Thread(target=self._run, name=f"octank-gw-{self.run_id}", daemon=True)
        self._thread.start()
        return self

    def wait(self, timeout: float = 90) -> bool:
        if self._thread is not None:
            self._done.wait(timeout)
        return self.ready

    def _run(self) -> None:
        try:
            if not self.identity.ready:
                raise RuntimeError(f"OutboundIdentity is {self.identity.status}: create it first")
            role_arn = self._ensure_role()
            self._ensure_gateway(role_arn)
            self._ensure_targets()
            self._warm_up()
            self.timings["ready_s"] = round(time.time() - self._t0, 1)
            self._log("READY", f"{', '.join(self.tool_names)} answer through the vault")
        except _Cancelled:
            self._log("CANCELLED", "teardown() requested")
        except BaseException as e:  # noqa: BLE001
            self.error = _msg(e, 400)
            self._log("FAILED", self.error)
        finally:
            self._done.set()

    def _ensure_role(self) -> str:
        acct, ident = self.identity.account, self.identity.info
        a = f"arn:aws:bedrock-agentcore:{self.region}:{acct}"
        wi_dir = f"{a}:workload-identity-directory/default"
        wi = f"{wi_dir}/workload-identity/{self.names['gateway']}-*"      # the gateway's own identity = its id
        trust = {"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole",
            # confused-deputy guards: this account, and only this run's partner gateway (gateway/<gateway-name>-*)
            "Condition": {"StringEquals": {"aws:SourceAccount": acct},
                          "ArnLike": {"aws:SourceArn": f"{a}:gateway/{self.names['gateway']}-*"}}}]}
        policy = {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadAccessToken"], "Resource": [wi_dir, wi]},
            {"Effect": "Allow", "Action": ["bedrock-agentcore:GetResourceApiKey", "bedrock-agentcore:GetResourceOauth2Token"],
             "Resource": [wi_dir, wi, f"{a}:token-vault/default", ident["apikey_arn"], ident["m2m_arn"]]},
            {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
             "Resource": [ident["apikey_secret"], ident["m2m_secret"]],
             "Condition": {"ForAnyValue:StringEquals": {"aws:CalledVia": ["bedrock-agentcore.amazonaws.com"]}}}]}
        role = self.names["gateway_role"]
        self._log("CREATING_ROLE", f"{role}: vault fetches for the two Octank providers only")
        try:
            arn = self._iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(trust), Tags=_tags_list(self.tags),
                                        Description=f"MLADAS M03 Octank gateway ({self.run_id})")["Role"]["Arn"]
        except ClientError as e:
            if _code(e) != "EntityAlreadyExists":
                raise
            arn = self._iam.get_role(RoleName=role)["Role"]["Arn"]
            self._iam.update_assume_role_policy(RoleName=role, PolicyDocument=json.dumps(trust))   # adopt = current trust
        self._iam.put_role_policy(RoleName=role, PolicyName="octank-vault", PolicyDocument=json.dumps(policy))
        self._policy_at = time.time()
        return arn

    def _ensure_gateway(self, role_arn: str) -> None:
        self._log("CREATING_GATEWAY", f"{self.names['gateway']} (inbound AWS_IAM, MCP)")
        found = [g for g in _paged(self._cp.list_gateways, "items", maxResults=100) if g["name"] == self.names["gateway"]]
        if found:
            self.gateway_id = found[0]["gatewayId"]
        else:
            gw, _ = _retry_call(lambda: self._cp.create_gateway(
                name=self.names["gateway"], roleArn=role_arn, protocolType="MCP", authorizerType="AWS_IAM",
                description=f"MLADAS M03 Octank outbound credentials ({self.run_id})", tags=self.tags,
                clientToken=uuid.uuid4().hex + uuid.uuid4().hex[:8]),
                lambda e: _code(e) in ("ValidationException", "AccessDeniedException") and "role" in str(e).lower(),
                max_wait=60, cancel=self._cancel)
            self.gateway_id = gw["gatewayId"]
        deadline = time.time() + 90
        while True:
            g = self._cp.get_gateway(gatewayIdentifier=self.gateway_id)
            self.gateway_url = g.get("gatewayUrl")
            if g["status"] == "READY":
                break
            if g["status"] not in ("CREATING", "UPDATING") or time.time() > deadline:
                raise RuntimeError(f"gateway {self.gateway_id} is {g['status']}: {g.get('statusReasons')}")
            if self._cancel.wait(1):
                raise _Cancelled()

    def _spec(self, kind: str, how: str) -> str:
        path = f"/{kind}/report/{{bureau_ref}}"
        return json.dumps({
            "openapi": "3.0.1", "info": {"title": "Octank Credit Bureau", "version": "1.0"},
            "servers": [{"url": self.partner.ids["api_endpoint"]}],
            "paths": {path: {"get": {
                "operationId": "getCreditReport", "description": f"Octank credit report for a bureau reference ({how})",
                "parameters": [{"name": "bureau_ref", "in": "path", "required": True,
                                "description": "Bureau reference such as BR-00000", "schema": {"type": "string"}}],
                "responses": {"200": {"description": "credit report",
                                      "content": {"application/json": {"schema": {"type": "object"}}}}}}}}})

    def _ensure_targets(self) -> None:
        info = self.identity.info
        creds = {
            "OctankKey": {"credentialProviderType": "API_KEY", "credentialProvider": {"apiKeyCredentialProvider": {
                "providerArn": info["apikey_arn"], "credentialParameterName": "x-api-key", "credentialLocation": "HEADER"}}},
            "OctankOauth": {"credentialProviderType": "OAUTH", "credentialProvider": {"oauthCredentialProvider": {
                "providerArn": info["m2m_arn"], "scopes": [SCOPE], "grantType": "CLIENT_CREDENTIALS"}}}}
        self._log("CREATING_TARGETS", "OctankKey (API_KEY provider) · OctankOauth (OAUTH provider, client credentials)")
        existing = {t["name"]: t["targetId"] for t in _paged(self._cp.list_gateway_targets, "items",
                                                             gatewayIdentifier=self.gateway_id, maxResults=100)}
        for name, kind in self.TARGETS.items():
            if name in existing:
                self.target_ids[name] = existing[name]
                continue
            self.target_ids[name] = self._cp.create_gateway_target(
                gatewayIdentifier=self.gateway_id, name=name,
                targetConfiguration={"mcp": {"openApiSchema": {"inlinePayload": self._spec(kind, kind)}}},
                credentialProviderConfigurations=[creds[name]])["targetId"]
        deadline = time.time() + 60
        for name, tid in self.target_ids.items():
            while (t := self._cp.get_gateway_target(gatewayIdentifier=self.gateway_id, targetId=tid))["status"] != "READY":
                if t["status"] not in ("CREATING", "UPDATING", "SYNCHRONIZING") or time.time() > deadline:
                    raise RuntimeError(f"target {name} is {t['status']}: {t.get('statusReasons')}")
                if self._cancel.wait(1):
                    raise _Cancelled()

    def _warm_up(self) -> None:
        self._log("WARMING", "first calls fail for ~10 s after the role policy is attached (IAM propagation): retrying")
        for tool_name in self.tool_names:
            r = self.call(tool_name, "BR-77410", retry_s=60)
            if not r["ok"]:
                raise RuntimeError(f"{tool_name} still failing after 60 s: {r['text'][:160]}")
        self.timings["warm_after_policy_s"] = round(time.time() - self._policy_at, 1)

    def call(self, tool_name: str, bureau_ref: str, *, retry_s: float = 0.0) -> dict:
        """One MCP tools/call over SigV4 (raw JSON-RPC). -> {"ok", "text", "latency_s", "tries"}.
        retry_s > 0 retries every 2 s while the call fails (the IAM warm-up)."""
        import httpx

        import mladas_common as mc

        auth = mc.SigV4HttpxAuth(session=self.session, region=self.region)
        deadline, tries = time.time() + retry_s, 0
        while True:
            tries += 1
            t = time.time()
            body = {"jsonrpc": "2.0", "id": tries, "method": "tools/call",
                    "params": {"name": tool_name, "arguments": {"bureau_ref": bureau_ref}}}
            try:
                r = httpx.post(self.gateway_url, json=body, auth=auth, timeout=30,
                               headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
                j = _jsonrpc_body(r)
                res = j.get("result") or {}
                text = "".join(c.get("text", "") for c in res.get("content", [])) or json.dumps(j.get("error") or j)[:300]
                ok = bool(res) and not res.get("isError")
            except (httpx.HTTPError, ValueError) as e:     # ValueError: a non-JSON reply
                ok, text = False, f"{type(e).__name__}: {str(e)[:200]}"
            out = {"ok": ok, "text": text, "latency_s": round(time.time() - t, 3), "tries": tries}
            if ok or time.time() + 2 > deadline:
                return out
            if self._cancel.wait(2):
                raise _Cancelled()

    def mcp_client(self, **kwargs: Any):
        """A Strands MCPClient for this gateway, SigV4-signed with the notebook's AWS session."""
        import mladas_common as mc
        from strands.tools.mcp import MCPClient

        kwargs.setdefault("startup_timeout", 30)
        return MCPClient(url=self.gateway_url, auth_provider=mc.SigV4HttpxAuth(session=self.session, region=self.region),
                         **kwargs)

    def teardown(self) -> dict[str, str]:
        """Targets -> gateway (its workload identity goes with it) -> role, verified by id."""
        self._cancel.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=60)
        out = _delete_gateway(self.session, self.names, [self.gateway_id] if self.gateway_id else [],
                              clients=(self._cp, self._iam))
        self.status = "DELETED" if all(v == GONE for v in out.values()) else "DELETE_INCOMPLETE"
        return out


def partner_gateway_targets(identity: OutboundIdentity, partner: OctankPartner, *, start: bool = True) -> PartnerGateway:
    """OPTIONAL (slide 14): build the Octank gateway in the background; `.wait(90)` then `.call(tool, ref)`."""
    gw = PartnerGateway(identity, partner)
    return gw.start() if start else gw


def _jsonrpc_body(r) -> dict:
    """A gateway reply is JSON or a one-event SSE stream."""
    if "text/event-stream" in r.headers.get("content-type", ""):
        for line in r.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return {}
    return r.json()


# ======================================================================================
# 6. Deleting (classes' teardown) and finding/deleting by run id (teardown_m03.py)
# ======================================================================================
def _paged(call, key: str, **kw) -> list[dict]:
    out, token = [], None
    while True:
        page = call(**kw, **({"nextToken": token} if token else {}))
        out += page.get(key, [])
        token = page.get("nextToken")
        if not token:
            return out


def _gw_id_re(gateway_name: str) -> re.Pattern:
    return re.compile(re.escape(gateway_name) + r"-[a-z0-9]{10}")


def _verify(check, *, timeout: float = 20) -> str:
    """'gone ✓' when check() raises not-found (or reports a deleting state) within `timeout` s."""
    try:
        return GONE if _wait_gone(check, timeout) else f"pending: still there after {timeout:.0f} s"
    except (ClientError, BotoCoreError) as e:
        return f"error: {_msg(e, 120)}"


def _isolated(out: dict, label: str, fn) -> None:
    """Run one deletion step; any failure becomes 'error: …' under `label` and never stops the other steps."""
    try:
        fn()
    except (ClientError, BotoCoreError, RuntimeError, OSError, TimeoutError) as e:
        out[label] = f"error: {_msg(e, 160)}"


def _delete_gateway(session, names: dict, known_ids: list[str], clients=None) -> dict[str, str]:
    cp, iam = clients or (session.client("bedrock-agentcore-control", config=_CFG), session.client("iam", config=_CFG))
    out: dict[str, str] = {}
    ids = list(known_ids)
    _isolated(out, "gateways (list)", lambda: ids.extend(
        g["gatewayId"] for g in _paged(cp.list_gateways, "items", maxResults=100)
        if g["name"] == names["gateway"] and g["gatewayId"] not in ids))
    for gid in ids:
        def drop(gid=gid):
            try:
                tids = [t["targetId"] for t in _paged(cp.list_gateway_targets, "items", gatewayIdentifier=gid, maxResults=100)]
            except ClientError as e:
                if _code(e) != "ResourceNotFoundException":
                    raise
                tids = []
            for tid in tids:
                try:
                    cp.delete_gateway_target(gatewayIdentifier=gid, targetId=tid)
                except ClientError as e:
                    if _code(e) != "ResourceNotFoundException":
                        raise
            for tid in tids:
                out[f"gateway target {tid}"] = _verify(lambda tid=tid: cp.get_gateway_target(gatewayIdentifier=gid, targetId=tid), timeout=60)
            deadline = time.time() + 60
            while True:   # DeleteGateway refuses while targets are still being deleted
                try:
                    cp.delete_gateway(gatewayIdentifier=gid)
                    break
                except ClientError as e:
                    if _code(e) == "ResourceNotFoundException":
                        break
                    if _code(e) not in ("ConflictException", "ValidationException") or time.time() > deadline:
                        raise
                    time.sleep(3)
            out[f"gateway {gid}"] = _verify(lambda: cp.get_gateway(gatewayIdentifier=gid), timeout=60)
            out[f"gateway workload identity {gid}"] = _verify(lambda: cp.get_workload_identity(name=gid), timeout=30)
        _isolated(out, f"gateway {gid}", drop)

    def drop_role():   # the optional gateway's role: report it only if this run ever had one
        if ids or _exists(lambda: iam.get_role(RoleName=names["gateway_role"])):
            _drop_role(iam, names["gateway_role"], out)
    _isolated(out, f"IAM role {names['gateway_role']}", drop_role)
    return out


def _drop_role(iam, role: str, out: dict) -> None:
    try:
        for p in iam.list_role_policies(RoleName=role)["PolicyNames"]:
            iam.delete_role_policy(RoleName=role, PolicyName=p)
        for p in iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]:
            iam.detach_role_policy(RoleName=role, PolicyArn=p["PolicyArn"])
        iam.delete_role(RoleName=role)
    except ClientError as e:
        if _code(e) != "NoSuchEntity":
            raise
    out[f"IAM role {role}"] = _verify(lambda: iam.get_role(RoleName=role))


def _managed_secrets(sm, names: dict) -> list[dict]:
    """The service-managed secrets of this run's providers: bedrock-agentcore-identity!default/<kind>/<provider>-<8 hex>."""
    found = []
    for kind, key in (("apikey", "apikey_provider"), ("oauth2", "m2m_provider"), ("oauth2", "user_provider")):
        prefix = f"bedrock-agentcore-identity!default/{kind}/{names[key]}-"
        pat = re.compile(re.escape(prefix) + r"[0-9a-f]{8}$")
        for page in sm.get_paginator("list_secrets").paginate(Filters=[{"Key": "name", "Values": [prefix]}]):
            found += [{"arn": s["ARN"], "name": s["Name"], "owner": s.get("OwningService")}
                      for s in page.get("SecretList", []) if pat.match(s["Name"])]
    return found


def _delete_identity(session, names: dict, *, secret_arns: list[str] = (), clients=None) -> dict[str, str]:
    cp, sm = clients or (session.client("bedrock-agentcore-control", config=_CFG), session.client("secretsmanager", config=_CFG))
    out: dict[str, str] = {}
    known = list(secret_arns)
    try:
        known += [s["arn"] for s in _managed_secrets(sm, names)]
    except (ClientError, BotoCoreError) as e:
        out["managed secrets (list)"] = f"error: {_msg(e)}"
    steps = (("API-key provider", "apikey_provider", cp.delete_api_key_credential_provider, cp.get_api_key_credential_provider),
             ("OAuth2 M2M provider", "m2m_provider", cp.delete_oauth2_credential_provider, cp.get_oauth2_credential_provider),
             ("OAuth2 3LO provider", "user_provider", cp.delete_oauth2_credential_provider, cp.get_oauth2_credential_provider),
             ("workload identity", "workload", cp.delete_workload_identity, cp.get_workload_identity))
    for label, key, drop, get in steps:
        name = names[key]

        def step(drop=drop, get=get, name=name, label=label):
            try:
                drop(name=name)
            except ClientError as e:
                if _code(e) != "ResourceNotFoundException":
                    raise
            out[f"{label} {name}"] = _verify(lambda: get(name=name))
        _isolated(out, f"{label} {name}", step)

    # Each provider's managed secret is force-deleted with it (no recovery window): DescribeSecret shows DeletedDate
    # at once and ResourceNotFound a few seconds later. Wait up to 15 s; remove a straggler ourselves.
    def secret_state(arn: str) -> str | None:
        try:
            d = sm.describe_secret(SecretId=arn)
            return GONE if d.get("DeletedDate") else None
        except ClientError as e:
            if _code(e) == "ResourceNotFoundException":
                return GONE
            raise
    for arn in dict.fromkeys(known):
        label = f"managed secret {arn.split(':secret:')[-1].rsplit('-', 1)[0]}"

        def step(arn=arn, label=label):
            deadline = time.time() + 15
            while (state := secret_state(arn)) is None and time.time() < deadline:
                time.sleep(1)
            if state is None:
                sm.delete_secret(SecretId=arn, ForceDeleteWithoutRecovery=True)
                state = secret_state(arn) or "pending: still there after its provider was deleted"
            out[label] = state
        _isolated(out, label, step)
    return out


def _delete_partner(session, names: dict, *, run_id: str, pool_ids: list[str] = (), api_ids: list[str] = (),
                    login_file: Path | None = None, clients=None) -> dict[str, str]:
    cg, lam, iam, api, logs = clients or tuple(session.client(s, config=_CFG) for s in
                                               ("cognito-idp", "lambda", "iam", "apigatewayv2", "logs"))
    out: dict[str, str] = {}
    # 1. HTTP API (routes, authorizer, integration and stage go with it)
    found_apis = []
    try:
        found_apis = [a["ApiId"] for a in _list_apis(api) if a["Name"] == names["api"]]
    except (ClientError, BotoCoreError) as e:
        out["HTTP API (list)"] = f"error: {_msg(e)}"
    for api_id in dict.fromkeys([*api_ids, *found_apis]):
        def drop_api(api_id=api_id):
            try:
                api.delete_api(ApiId=api_id)
            except ClientError as e:
                if _code(e) != "NotFoundException":
                    raise
            out[f"HTTP API {api_id}"] = _verify(lambda: api.get_api(ApiId=api_id))
        _isolated(out, f"HTTP API {api_id}", drop_api)
    # 2. Lambda
    fn = names["function"]

    def drop_fn():
        try:
            lam.delete_function(FunctionName=fn)
        except ClientError as e:
            if _code(e) != "ResourceNotFoundException":
                raise
        out[f"Lambda {fn}"] = _verify(lambda: lam.get_function(FunctionName=fn))
    _isolated(out, f"Lambda {fn}", drop_fn)
    # 3. log group: sweep twice (a last invocation can still flush a batch and re-create the group)
    lg = names["log_group"]

    def lg_exists() -> bool:
        return any(g["logGroupName"] == lg for g in logs.describe_log_groups(logGroupNamePrefix=lg).get("logGroups", []))

    def drop_logs():
        for sweep in range(2):
            try:
                logs.delete_log_group(logGroupName=lg)
            except ClientError as e:
                if _code(e) != "ResourceNotFoundException":
                    raise
            if sweep == 0:
                time.sleep(2)
        out[f"log group {lg}"] = "pending: re-created by a late invocation" if lg_exists() else GONE
    _isolated(out, f"log group {lg}", drop_logs)
    # 4. the Lambda's role
    _isolated(out, f"IAM role {names['function_role']}", lambda: _drop_role(iam, names["function_role"], out))
    # 5. Cognito: the domain first (DeleteUserPool refuses while it exists), then the pool (clients, resource server,
    #    branding and the test user go with it)
    found_pools = []
    try:
        found_pools = [p["Id"] for page in cg.get_paginator("list_user_pools").paginate(MaxResults=60)
                       for p in page["UserPools"] if p["Name"] == names["pool"]]
    except (ClientError, BotoCoreError) as e:
        out["Cognito pools (list)"] = f"error: {_msg(e)}"
    for pool_id in dict.fromkeys([*pool_ids, *found_pools]):
        def drop_pool(pool_id=pool_id):
            try:
                domain = cg.describe_user_pool(UserPoolId=pool_id)["UserPool"].get("Domain")
            except ClientError as e:
                if _code(e) != "ResourceNotFoundException":
                    raise
                domain = None
            if domain:
                cg.delete_user_pool_domain(Domain=domain, UserPoolId=pool_id)
                d = cg.describe_user_pool_domain(Domain=domain).get("DomainDescription") or {}
                out[f"Cognito domain {domain}"] = GONE if not d or d.get("Status") == "DELETING" else \
                    f"pending: {d.get('Status')}"
            deadline = time.time() + 30
            while True:
                try:
                    cg.delete_user_pool(UserPoolId=pool_id)
                    break
                except ClientError as e:
                    if _code(e) == "ResourceNotFoundException":
                        break
                    if _code(e) != "InvalidParameterException" or time.time() > deadline:
                        raise
                    time.sleep(2)
            out[f"Cognito pool {pool_id}"] = _verify(lambda: cg.describe_user_pool(UserPoolId=pool_id))
            out[f"Cognito test user {TEST_USER} ({pool_id})"] = _verify(
                lambda: cg.admin_get_user(UserPoolId=pool_id, Username=TEST_USER), timeout=5)
        _isolated(out, f"Cognito pool {pool_id}", drop_pool)
    # 6. the local login file (only if it belongs to this run)
    if login_file is not None:
        path = Path(login_file)

        def drop_file():
            if path.exists() and f"run_id: {run_id}" in path.read_text():
                path.unlink()
            out[f"login file {path.name}"] = GONE if not path.exists() or f"run_id: {run_id}" not in path.read_text() \
                else "pending: could not remove it"
        _isolated(out, f"login file {path.name}", drop_file)
    return out


def _list_apis(api) -> list[dict]:
    out, token = [], None
    while True:
        page = api.get_apis(MaxResults="100", **({"NextToken": token} if token else {}))
        out += page.get("Items", [])
        token = page.get("NextToken")
        if not token:
            return out


def find_run_resources(session: boto3.Session, run_id: str, prefix: str = DEFAULT_PREFIX,
                       state_dir: str | Path | None = None) -> dict[str, list]:
    """Read-only: what this run's outbound demo still has in the account, found by exact name (paginated).
    -> {"user_pools", "http_apis", "functions", "roles", "log_groups", "workload_identities", "credential_providers",
        "managed_secrets", "gateways", "login_file"}"""
    n = resource_names(run_id, prefix)
    cg, lam, iam, api, logs, cp, sm = (session.client(s, config=_CFG) for s in (
        "cognito-idp", "lambda", "iam", "apigatewayv2", "logs", "bedrock-agentcore-control", "secretsmanager"))
    pools = []
    for page in cg.get_paginator("list_user_pools").paginate(MaxResults=60):
        for p in page["UserPools"]:
            if p["Name"] == n["pool"]:
                pools.append({"id": p["Id"], "name": p["Name"],
                              "domain": cg.describe_user_pool(UserPoolId=p["Id"])["UserPool"].get("Domain")})
    gws = [g for g in _paged(cp.list_gateways, "items", maxResults=100) if g["name"] == n["gateway"]]
    gw_re = _gw_id_re(n["gateway"])
    out = {
        "user_pools": pools,
        "http_apis": [{"id": a["ApiId"], "name": a["Name"]} for a in _list_apis(api) if a["Name"] == n["api"]],
        "functions": [n["function"]] if _exists(lambda: lam.get_function(FunctionName=n["function"])) else [],
        "roles": [r for r in (n["function_role"], n["gateway_role"]) if _exists(lambda r=r: iam.get_role(RoleName=r))],
        "log_groups": [g["logGroupName"] for g in logs.describe_log_groups(logGroupNamePrefix=n["log_group"]).get("logGroups", [])
                       if g["logGroupName"] == n["log_group"]],
        "workload_identities": ([n["workload"]] if _exists(lambda: cp.get_workload_identity(name=n["workload"])) else []) +
                               [w["name"] for w in _paged(cp.list_workload_identities, "workloadIdentities", maxResults=20)
                                if gw_re.fullmatch(w["name"])],
        "credential_providers": [n[k] for k, get in (("apikey_provider", cp.get_api_key_credential_provider),
                                                     ("m2m_provider", cp.get_oauth2_credential_provider),
                                                     ("user_provider", cp.get_oauth2_credential_provider))
                                 if _exists(lambda k=k, get=get: get(name=n[k]))],
        "managed_secrets": [s["name"] for s in _managed_secrets(sm, n)],
        "gateways": [{"id": g["gatewayId"], "status": g["status"]} for g in gws],
        "login_file": [],
    }
    if state_dir is not None:
        f = Path(state_dir) / LOGIN_FILE
        if f.exists() and f"run_id: {run_id}" in f.read_text():
            out["login_file"] = [str(f)]
    return out


def delete_run_resources(session: boto3.Session, run_id: str, prefix: str = DEFAULT_PREFIX,
                         state_dir: str | Path | None = None) -> dict[str, str]:
    """Delete this run's outbound resources from their names alone (gateway -> identity -> partner), verified by id.
    Touches nothing whose name does not carry this run id. -> {label: "gone ✓" | "pending: …" | "error: …"}"""
    n = resource_names(run_id, prefix)
    out: dict[str, str] = {}
    for label, fn in (("gateway", lambda: _delete_gateway(session, n, [])),
                      ("identity", lambda: _delete_identity(session, n)),
                      ("partner", lambda: _delete_partner(session, n, run_id=run_id,
                                                          login_file=Path(state_dir) / LOGIN_FILE if state_dir else None))):
        try:
            out.update(fn())
        except (ClientError, BotoCoreError, RuntimeError, OSError) as e:
            out[label] = f"error: {_msg(e, 160)}"
    return out

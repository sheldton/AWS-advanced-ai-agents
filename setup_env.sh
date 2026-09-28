#!/usr/bin/env bash
# Create the MLADAS notebook environment (Python 3.12 venv + Jupyter kernel "MLADAS (Python 3.12)").
# Requires uv (https://docs.astral.sh/uv/) — also used by the notebooks to build linux/arm64 packages
# for AgentCore Runtime direct code deployment.
set -euo pipefail
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "uv not found: see https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
uv venv --seed --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
KERNEL_NAME="${MLADAS_KERNEL_NAME:-mladas}"          # the notebooks' kernelspec expects "mladas"
.venv/bin/python -m ipykernel install --user --name "$KERNEL_NAME" --display-name "MLADAS (Python 3.12)"
echo
echo "Done. Start Jupyter (any install) and pick the kernel 'MLADAS (Python 3.12)'."
echo "AWS: the notebooks use your standard AWS credentials (env vars, AWS_PROFILE, default profile, SSO) in us-east-1."
echo "     Set MLADAS_AWS_PROFILE to use a named profile for these demos only."
aws sts get-caller-identity ${MLADAS_AWS_PROFILE:+--profile "$MLADAS_AWS_PROFILE"} --query Arn --output text \
  >/dev/null 2>&1 && echo "AWS credentials OK" \
  || echo "WARNING: could not verify your AWS credentials (aws sts get-caller-identity)"

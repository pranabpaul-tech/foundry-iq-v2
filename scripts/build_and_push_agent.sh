#!/bin/sh
# Copyright (c) Microsoft. All rights reserved.
#
# Build and push one agent's image via ACR Tasks (cloud-side build, no local
# Docker needed). Run this from anywhere reachable to ACR's public endpoint
# (it stayed public/IP-allowlisted -- only got a private endpoint added
# alongside, for callers already inside the VNet).
#
# This exists because the jumpbox (an ACI container, not a VM -- see
# infra/04-jumpbox.bicep for why) has no Docker-in-Docker support, so the
# usual `azd deploy` build+push+register flow can't run from inside the
# private VNet where the project endpoint actually lives.
#
# Usage: ./build_and_push_agent.sh <agent-name>
#   e.g. ./build_and_push_agent.sh kb-agent

set -eu

AGENT_NAME="${1:?Usage: build_and_push_agent.sh <agent-name>}"
ACR_NAME="${ACR_NAME:-foundryiqv2acr}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-foundryiq-v2}"
REPO_URL="${REPO_URL:-https://github.com/pranabpaul-tech/foundry-iq-v2.git}"
BRANCH="${BRANCH:-main}"
TAG="${TAG:-v1}"

az acr build \
  --registry "$ACR_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --image "${AGENT_NAME}:${TAG}" \
  "${REPO_URL}#${BRANCH}:agents/${AGENT_NAME}" \
  --no-logs

echo "Built and pushed: ${ACR_NAME}.azurecr.io/${AGENT_NAME}:${TAG}"

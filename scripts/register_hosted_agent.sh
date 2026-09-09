#!/bin/sh
# Copyright (c) Microsoft. All rights reserved.
#
# Register one agent as a hosted agent version against the private project
# (foundryiqv2p3ygk/iqv2project). This is a DATA-PLANE call to the project
# endpoint, unreachable from outside the VNet -- run this from inside the
# jumpbox (infra/04-jumpbox.bicep), after `az login` there and after the
# image has been pushed to ACR (build_and_push_agent.sh, run from outside
# the VNet since the jumpbox has no Docker).
#
# Two things this script's JSON body construction works around (both
# confirmed the hard way while building this environment):
#   1. `az container exec --exec-command` has no shell involved -- the
#      string is split on whitespace into argv with no quoting respected.
#      If running this by hand via `az container exec`, minify the JSON to
#      zero spaces and single-quote the whole command at the calling
#      shell's level (to stop *that* shell's own brace-expansion on `{...}`).
#      This script itself doesn't have that problem when run as a file
#      inside the jumpbox (e.g. `curl -sL <raw-file-url> | sh -s -- kb-agent`).
#   2. FOUNDRY_PROJECT_ENDPOINT and any FOUNDRY_*/AGENT_* env var name is
#      reserved by the platform and rejected if you try to set it yourself
#      -- it's auto-injected into the container.
#
# Usage (from inside the jumpbox): ./register_hosted_agent.sh <agent-name> <env-vars-json>
#   e.g. ./register_hosted_agent.sh kb-agent '{"AZURE_AI_MODEL_DEPLOYMENT_NAME":"gpt-4.1","AZURE_SEARCH_ENDPOINT":"https://foundryiqv2pau4search.search.windows.net","AZURE_SEARCH_KNOWLEDGE_BASE_NAME":"aw-knowledge-base"}'
#   e.g. ./register_hosted_agent.sh courier-agent '{"AZURE_AI_MODEL_DEPLOYMENT_NAME":"gpt-4.1"}'

set -eu

AGENT_NAME="${1:?Usage: register_hosted_agent.sh <agent-name> <env-vars-json>}"
ENV_VARS_JSON="${2:?Usage: register_hosted_agent.sh <agent-name> <env-vars-json>}"

PROJECT_ENDPOINT="${PROJECT_ENDPOINT:-https://foundryiqv2p3ygk.services.ai.azure.com/api/projects/iqv2project}"
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-acrfoundryiqv2pau4.azurecr.io}"
TAG="${TAG:-v1}"
CPU="${CPU:-0.5}"
MEMORY="${MEMORY:-1Gi}"

# Preview features required for the "hosted" agent kind.
FOUNDRY_FEATURES="HostedAgents=V1Preview,WorkflowAgents=V1Preview,AgentEndpoints=V1Preview,CodeAgents=V1Preview,ExternalAgents=V1Preview,AgentsOptimization=V1Preview"

BODY="{\"definition\":{\"kind\":\"hosted\",\"cpu\":\"${CPU}\",\"memory\":\"${MEMORY}\",\"container_configuration\":{\"image\":\"${ACR_LOGIN_SERVER}/${AGENT_NAME}:${TAG}\"},\"environment_variables\":${ENV_VARS_JSON},\"protocol_versions\":[{\"protocol\":\"responses\",\"version\":\"2.0.0\"}]}}"

az rest --method post \
  --url "${PROJECT_ENDPOINT}/agents/${AGENT_NAME}/versions?api-version=v1" \
  --resource https://ai.azure.com \
  --headers "Foundry-Features=${FOUNDRY_FEATURES}" \
  --body "$BODY"

echo "Registered ${AGENT_NAME}. Grant its AgentIdentity any RBAC it needs (see README.md), then check status:"
echo "  az rest --method get --url ${PROJECT_ENDPOINT}/agents/${AGENT_NAME}/versions/1?api-version=v1 --resource https://ai.azure.com --query status"

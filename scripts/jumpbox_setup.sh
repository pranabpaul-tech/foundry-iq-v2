#!/bin/sh
# Copyright (c) Microsoft. All rights reserved.
#
# One-shot jumpbox setup: builds the knowledge base, then registers and
# RBAC-grants all three agents. Run from inside the jumpbox, after
# `az login` there (see README.md "Step-by-step setup").
#
# Fetches the rest of this repo itself (a tarball, not git -- no git binary
# on this image), so only this one file needs to be placed here manually,
# as TWO separate `az container exec` calls (not one piped command --
# `--exec-command` has no shell behind it and splits on whitespace with no
# quote preservation, so a pipe or quoted compound command never survives
# it; each call below only ever has space-free argv tokens, which does):
#
#   az container exec -g <rg> -n <jumpbox> --container-name jumpbox \
#     --exec-command "curl -sL https://raw.githubusercontent.com/pranabpaul-tech/foundry-iq-v2/main/scripts/jumpbox_setup.sh -o /tmp/jumpbox_setup.sh"
#   az container exec -g <rg> -n <jumpbox> --container-name jumpbox \
#     --exec-command "sh /tmp/jumpbox_setup.sh"

set -eu

REPO_URL="${REPO_URL:-https://github.com/pranabpaul-tech/foundry-iq-v2}"
BRANCH="${BRANCH:-main}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-foundryiq-v2}"
ACCOUNT_NAME="${ACCOUNT_NAME:-foundryiqv2p3ygk}"
PROJECT_NAME="${PROJECT_NAME:-iqv2project}"
PROJECT_ENDPOINT="${PROJECT_ENDPOINT:-https://${ACCOUNT_NAME}.services.ai.azure.com/api/projects/${PROJECT_NAME}}"
SEARCH_NAME="${SEARCH_NAME:-foundryiqv2pau4search}"
SEARCH_ENDPOINT="${SEARCH_ENDPOINT:-https://${SEARCH_NAME}.search.windows.net}"
MODEL="${AZURE_AI_MODEL_DEPLOYMENT_NAME:-gpt-4.1}"
KB_NAME="${AZURE_SEARCH_KNOWLEDGE_BASE_NAME:-aw-knowledge-base}"

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
SEARCH_SCOPE="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.Search/searchServices/${SEARCH_NAME}"
PROJECT_SCOPE="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/${ACCOUNT_NAME}/projects/${PROJECT_NAME}"

echo "==> Fetching repo + installing dependencies"
WORKDIR=$(mktemp -d)
cd "$WORKDIR"
tdnf install -y tar python3-pip >/tmp/jumpbox-setup-tdnf.log 2>&1
curl -sL "${REPO_URL}/archive/refs/heads/${BRANCH}.tar.gz" | tar xz --strip-components=1
python3 -m pip install --quiet -r scripts/requirements.txt

echo "==> Building the knowledge base"
AZURE_SEARCH_ENDPOINT="$SEARCH_ENDPOINT" \
AZURE_AI_MODEL_DEPLOYMENT_NAME="$MODEL" \
python3 scripts/build_search_index.py

grant_role() {
  echo "    granting ${3} at ${2}"
  az role assignment create \
    --assignee-object-id "$1" \
    --assignee-principal-type ServicePrincipal \
    --role "$3" \
    --scope "$2" \
    >/dev/null
}

echo "==> Registering kb-agent"
sh scripts/register_hosted_agent.sh kb-agent \
  "{\"AZURE_AI_MODEL_DEPLOYMENT_NAME\":\"${MODEL}\",\"AZURE_SEARCH_ENDPOINT\":\"${SEARCH_ENDPOINT}\",\"AZURE_SEARCH_KNOWLEDGE_BASE_NAME\":\"${KB_NAME}\"}" \
  >/dev/null
kb_principal_id=$(az rest --method get --url "${PROJECT_ENDPOINT}/agents/kb-agent?api-version=v1" \
  --resource https://ai.azure.com --query instance_identity.principal_id -o tsv)
grant_role "$kb_principal_id" "$SEARCH_SCOPE" "Search Index Data Reader"
grant_role "$kb_principal_id" "$SEARCH_SCOPE" "Search Service Contributor"

echo "==> Registering orchestrator-agent"
sh scripts/register_hosted_agent.sh orchestrator-agent \
  "{\"AZURE_AI_MODEL_DEPLOYMENT_NAME\":\"${MODEL}\",\"AZURE_SEARCH_ENDPOINT\":\"${SEARCH_ENDPOINT}\",\"AZURE_SEARCH_KNOWLEDGE_BASE_NAME\":\"${KB_NAME}\"}" \
  >/dev/null
orchestrator_principal_id=$(az rest --method get --url "${PROJECT_ENDPOINT}/agents/orchestrator-agent?api-version=v1" \
  --resource https://ai.azure.com --query instance_identity.principal_id -o tsv)
grant_role "$orchestrator_principal_id" "$SEARCH_SCOPE" "Search Index Data Reader"
grant_role "$orchestrator_principal_id" "$SEARCH_SCOPE" "Search Service Contributor"
grant_role "$orchestrator_principal_id" "$PROJECT_SCOPE" "Foundry User"

echo "==> Registering courier-agent"
sh scripts/register_hosted_agent.sh courier-agent \
  "{\"AZURE_AI_MODEL_DEPLOYMENT_NAME\":\"${MODEL}\"}" \
  >/dev/null
# No RBAC needed -- courier-agent only uses the native WebSearchTool.

echo ""
echo "Done. All three agents registered and RBAC-granted."
echo "Test: scripts/invoke_hosted_agent.sh kb-agent \"What is Adventure Works' refund policy?\""
echo "Next: the Fabric toolbox connection (scripts/create_fabric_toolbox.sh) and,"
echo "optionally, Bot Service / Teams -- see README.md."

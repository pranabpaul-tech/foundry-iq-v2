#!/bin/sh
# Copyright (c) Microsoft. All rights reserved.
#
# Create the Fabric OBO connection + toolbox against the private project.
# Both are data-plane/management-plane calls that need to originate from
# inside the VNet once the account is private -- run this from inside the
# jumpbox (infra/04-jumpbox.bicep), after `az login` there.
#
# Replaces an earlier Python version (scripts/create_fabric_toolbox.py,
# using AIProjectClient) that can't run here: the jumpbox has no Python.
#
# Usage: ./create_fabric_toolbox.sh <fabric-workspace-id> <fabric-data-agent-id>

set -eu

WORKSPACE_ID="${1:?Usage: create_fabric_toolbox.sh <fabric-workspace-id> <fabric-data-agent-id>}"
DATA_AGENT_ID="${2:?Usage: create_fabric_toolbox.sh <fabric-workspace-id> <fabric-data-agent-id>}"

SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-f26d977d-4a4e-45b3-b4a8-68d268c44852}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-foundryiq-v2}"
ACCOUNT_NAME="${ACCOUNT_NAME:-foundryiqv2p3ygk}"
PROJECT_NAME="${PROJECT_NAME:-iqv2project}"
PROJECT_ENDPOINT="${PROJECT_ENDPOINT:-https://${ACCOUNT_NAME}.services.ai.azure.com/api/projects/${PROJECT_NAME}}"
CONNECTION_NAME="${CONNECTION_NAME:-fabric-dataagent-obo}"
TOOLBOX_NAME="${TOOLBOX_NAME:-fabric-iq-toolbox}"

SERVER_URL="https://api.fabric.microsoft.com/v1/mcp/workspaces/${WORKSPACE_ID}/dataagents/${DATA_AGENT_ID}/agent"
CONNECTION_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/${ACCOUNT_NAME}/projects/${PROJECT_NAME}/connections/${CONNECTION_NAME}"

echo "Creating connection: ${CONNECTION_NAME}"
az rest --method put \
  --url "https://management.azure.com${CONNECTION_ID}?api-version=2025-10-01-preview" \
  --body "{\"properties\":{\"category\":\"RemoteTool\",\"authType\":\"UserEntraToken\",\"target\":\"${SERVER_URL}\",\"audience\":\"https://api.fabric.microsoft.com\"}}"

echo "Creating toolbox: ${TOOLBOX_NAME}"
az rest --method post \
  --url "${PROJECT_ENDPOINT}/toolboxes/${TOOLBOX_NAME}/versions?api-version=v1" \
  --resource https://ai.azure.com \
  --body "{\"description\":\"Fabric-IQ-toolbox-OBO-enabled\",\"tools\":[{\"type\":\"fabric_iq_preview\",\"project_connection_id\":\"${CONNECTION_ID}\",\"server_label\":\"fabric-dataagent\",\"server_url\":\"${SERVER_URL}\",\"require_approval\":\"never\"}]}"

echo "Done. Toolbox MCP endpoint (consumer, always default_version):"
echo "  ${PROJECT_ENDPOINT}/toolboxes/${TOOLBOX_NAME}/mcp?api-version=v1"

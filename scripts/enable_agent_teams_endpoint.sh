#!/bin/sh
# Copyright (c) Microsoft. All rights reserved.
#
# Enable the Activity Protocol endpoint on a hosted agent, with Foundry's
# own source-IP-filtered public exception for Bot Service/Teams traffic
# (enable_m365_public_endpoint) plus a Bot Service authorization scheme.
# Everything else on the project (Responses API, agent management) stays
# private. This is a data-plane call to the project endpoint -- run from
# inside the jumpbox (infra/04-jumpbox.bicep), after `az login` there.
#
# Run this before creating the Bot Service resource (infra/05-bot-service.bicep).
#
# Usage: ./enable_agent_teams_endpoint.sh <agent-name> [BotServiceRbac|BotServiceTenant]
#   e.g. ./enable_agent_teams_endpoint.sh orchestrator-agent BotServiceTenant
#
# BotServiceRbac: only callers with Azure RBAC on the Foundry project.
# BotServiceTenant: any signed-in member of this tenant. Neither is
# anonymous-public -- see README.md "Bot Service / Teams" for why (tested:
# a raw/anonymous Direct Line caller satisfies neither scheme, since it
# carries no real per-caller Entra token through to Foundry; Teams does).

set -eu

AGENT_NAME="${1:?Usage: enable_agent_teams_endpoint.sh <agent-name> [BotServiceRbac|BotServiceTenant]}"
AUTH_SCHEME="${2:-BotServiceTenant}"

PROJECT_ENDPOINT="${PROJECT_ENDPOINT:-https://foundryiqv2p3ygk.services.ai.azure.com/api/projects/iqv2project}"

# This PATCH replaces protocol_configuration and authorization_schemes
# wholesale -- keep "responses" and "Entra" here or the endpoint loses them.
az rest --method patch \
  --url "${PROJECT_ENDPOINT}/agents/${AGENT_NAME}?api-version=v1" \
  --resource https://ai.azure.com \
  --body "{\"agent_endpoint\":{\"protocol_configuration\":{\"responses\":{},\"activity\":{\"enable_m365_public_endpoint\":true}},\"authorization_schemes\":[{\"type\":\"Entra\"},{\"type\":\"${AUTH_SCHEME}\"}]}}"

echo "Enabled. Get the agent's identity client ID (needed as msaAppId for the Bot Service resource):"
echo "  az rest --method get --url ${PROJECT_ENDPOINT}/agents/${AGENT_NAME}?api-version=v1 --resource https://ai.azure.com --query instance_identity.client_id"

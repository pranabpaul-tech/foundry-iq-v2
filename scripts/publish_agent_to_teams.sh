#!/bin/sh
# Copyright (c) Microsoft. All rights reserved.
#
# Publish a hosted agent as a Microsoft 365 (Teams) app. This generates and
# submits an actual Teams app manifest -- without it, a Teams deep link built
# from the agent's raw identity App ID resolves to nothing ("couldn't find
# the bot"), even if the Bot Service resource and Foundry endpoint are wired
# up correctly. Run this AFTER infra/05-bot-service.bicep is deployed, from
# inside the jumpbox (infra/04-jumpbox.bicep), after `az login` there.
#
# publishScope defaults to Shared (visible only to the publisher, no M365
# admin approval needed) rather than Tenant (org-wide, needs an M365 admin
# to approve -- a Global-Reader-only account can't do this).
#
# Values passed to Teams (name/description) can't contain literal spaces if
# you're invoking this via `az container exec --exec-command` by hand (no
# shell involved there -- the string is split on whitespace with no quoting
# respected); use hyphens in that case. Running this script as a file does
# not have that problem.
#
# Usage: ./publish_agent_to_teams.sh <agent-name> [publishScope]
#   e.g. ./publish_agent_to_teams.sh orchestrator-agent Shared

set -eu

AGENT_NAME="${1:?Usage: publish_agent_to_teams.sh <agent-name> [publishScope]}"
PUBLISH_SCOPE="${2:-Shared}"

PROJECT_ENDPOINT="${PROJECT_ENDPOINT:-https://foundryiqv2pbmgl.services.ai.azure.com/api/projects/iqv2project}"

RESPONSE=$(az rest --method post \
  --url "${PROJECT_ENDPOINT}/agents/${AGENT_NAME}/microsoft365/publish?api-version=v1" \
  --resource https://ai.azure.com \
  --body "{\"publishScope\":\"${PUBLISH_SCOPE}\"}")

echo "$RESPONSE"

TEAMS_APP_ID=$(echo "$RESPONSE" | grep -o '"teamsAppId"[^,}]*' | grep -o '"[0-9a-f-]*"$' | tr -d '"')
if [ -n "${TEAMS_APP_ID:-}" ]; then
  echo ""
  echo "Published. Open in Teams:"
  echo "  https://teams.microsoft.com/l/app/${TEAMS_APP_ID}"
fi

#!/bin/sh
# Copyright (c) Microsoft. All rights reserved.
#
# Call a hosted agent's Responses endpoint. This is a data-plane call to the
# private project -- run from inside the jumpbox (infra/04-jumpbox.bicep),
# after `az login` there.
#
# JSON string values can't contain literal spaces if you're invoking this
# via `az container exec --exec-command` by hand (no shell involved there --
# the string is split on whitespace with no quoting respected). Running this
# script as a file (e.g. via `curl -sL <raw-file-url> | sh -s -- ...`) does
# not have that problem -- use real spaces in your question in that case.
#
# Usage: ./invoke_hosted_agent.sh <agent-name> <question>
#   e.g. ./invoke_hosted_agent.sh kb-agent "What is Adventure Works' refund policy?"

set -eu

AGENT_NAME="${1:?Usage: invoke_hosted_agent.sh <agent-name> <question>}"
shift
QUESTION="$*"

PROJECT_ENDPOINT="${PROJECT_ENDPOINT:-https://foundryiqv2pbmgl.services.ai.azure.com/api/projects/iqv2project}"

# Minimal JSON-string escaping for the question (double quotes/backslashes only).
ESCAPED=$(printf '%s' "$QUESTION" | sed 's/\\/\\\\/g; s/"/\\"/g')

az rest --method post \
  --url "${PROJECT_ENDPOINT}/agents/${AGENT_NAME}/endpoint/protocols/openai/responses?api-version=v1" \
  --resource https://ai.azure.com \
  --body "{\"input\":\"${ESCAPED}\"}"

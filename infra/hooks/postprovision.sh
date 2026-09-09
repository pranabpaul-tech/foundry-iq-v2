#!/bin/sh
# Copyright (c) Microsoft. All rights reserved.
#
# Runs automatically after `azd provision`. Everything here runs from
# wherever `azd` itself runs (a normal dev machine, outside the VNet) --
# ACR and the Fabric REST API both stay reachable from outside the VNet,
# and RBAC/capacity-state changes are ARM management-plane calls, not
# gated by any private endpoint.
#
# What's deliberately NOT here -- building the knowledge base and
# registering the agents -- needs the project's and Search's DATA planes,
# both private-endpoint-only after this hook runs, so they can only run
# from inside the VNet. See the printed instructions at the end for that
# one remaining manual step (scripts/jumpbox_setup.sh, from the jumpbox).
#
# azd exports every main.bicep output (RESOURCE_GROUP, ACCOUNT_NAME,
# SEARCH_NAME, ACR_NAME, JUMPBOX_NAME, ...) as an environment variable
# already -- nothing here needs to be typed in by hand.

set -eu

echo "==> Writing .env from azd outputs"
azd env get-values > .env
echo "    Wrote $(pwd)/.env"

echo "==> Making Azure AI Search private (publicNetworkAccess: Disabled)"
echo "    Its private endpoint already exists (infra/02-data-services.bicep) --"
echo "    this is a one-property flip via 'az search service update', not a full"
echo "    Bicep resource block, since redeclaring an existing Search service in"
echo "    Bicep without every current setting (replica/partition count, etc.)"
echo "    risks resetting them."
az search service update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$SEARCH_NAME" \
  --public-network-access disabled

echo "==> Building and pushing agent images (ACR Tasks -- no local Docker needed, ACR stays public)"
for agent in kb-agent courier-agent orchestrator-agent; do
  scripts/build_and_push_agent.sh "$agent"
done

echo "==> Resuming the Fabric capacity and ensuring a workspace exists"
scripts/set_fabric_capacity_state.sh resume "$FABRIC_CAPACITY_NAME"
scripts/provision_fabric_workspace.sh foundryiq-workspace "$FABRIC_CAPACITY_NAME"

cat <<EOF

==================================================================
One manual step remains. It needs a real interactive sign-in and
network access to the now-private project/Search data planes, so
it has to run from inside the VNet (the jumpbox), not from here:

  az container exec -g $RESOURCE_GROUP -n $JUMPBOX_NAME --container-name jumpbox \\
    --exec-command "az login --use-device-code"

  (complete the device-code prompt, then -- two separate calls, not one
  piped command: --exec-command has no shell behind it, see the comment
  at the top of scripts/jumpbox_setup.sh)

  az container exec -g $RESOURCE_GROUP -n $JUMPBOX_NAME --container-name jumpbox \\
    --exec-command "curl -sL https://raw.githubusercontent.com/pranabpaul-tech/foundry-iq-v2/main/scripts/jumpbox_setup.sh -o /tmp/jumpbox_setup.sh"
  az container exec -g $RESOURCE_GROUP -n $JUMPBOX_NAME --container-name jumpbox \\
    --exec-command "sh /tmp/jumpbox_setup.sh"

That builds the knowledge base and registers + grants RBAC to all three
agents. See README.md "Step-by-step setup" for what comes after (the
Fabric toolbox connection and, optionally, Bot Service / Teams).
==================================================================
EOF

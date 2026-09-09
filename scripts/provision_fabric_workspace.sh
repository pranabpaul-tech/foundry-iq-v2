#!/bin/sh
# Copyright (c) Microsoft. All rights reserved.
#
# Provision a Fabric workspace (assigned to infra/06-fabric-capacity.bicep's
# capacity) plus empty Lakehouse, Ontology, and Data Agent item shells in
# it. This is the workspace/item-level equivalent of Bicep for Fabric --
# there IS no ARM/Bicep resource type for workspaces or items (only the
# capacity itself is ARM, see infra/06-fabric-capacity.bicep), so this talks
# to the Fabric REST API (api.fabric.microsoft.com) directly, the same way
# scripts/create_fabric_toolbox.sh already does for the OBO connection.
#
# Idempotent: re-running with the same names finds and reuses the existing
# workspace/items instead of erroring or duplicating them.
#
# Items created here are empty shells -- a Lakehouse with no tables, an
# Ontology with no schema, a Data Agent with no configured data sources.
# Configuring them (tables, ontology schema, the Data Agent's data source
# + published instructions) is a Fabric-portal or Fabric-SDK task, not
# something a single REST POST can express -- point
# scripts/create_fabric_toolbox.sh at the Data Agent's ID once it's
# configured.
#
# Requires the capacity to be Active (scripts/set_fabric_capacity_state.sh
# resume) -- Fabric item creation fails on a Paused capacity.
#
# Usage: ./provision_fabric_workspace.sh [workspace-name] [capacity-name]
#   e.g. ./provision_fabric_workspace.sh foundryiq-workspace foundryiqv2fabric

set -eu

WORKSPACE_NAME="${1:-foundryiq-workspace}"
CAPACITY_NAME="${2:-foundryiqv2fabric}"
FABRIC_API="https://api.fabric.microsoft.com/v1"

# -- Look up the capacity's Fabric-side ID (not the ARM resource ID) --
CAPACITY_ID=$(az rest --method get --url "${FABRIC_API}/capacities" --resource https://api.fabric.microsoft.com \
  --query "value[?displayName=='${CAPACITY_NAME}'].id | [0]" -o tsv)
if [ -z "$CAPACITY_ID" ] || [ "$CAPACITY_ID" = "None" ]; then
  echo "Capacity '${CAPACITY_NAME}' not found or not visible to this account (Fabric admin/capacity role needed)." >&2
  exit 1
fi
echo "Capacity ${CAPACITY_NAME}: ${CAPACITY_ID}"

# -- Find or create the workspace --
WORKSPACE_ID=$(az rest --method get --url "${FABRIC_API}/workspaces" --resource https://api.fabric.microsoft.com \
  --query "value[?displayName=='${WORKSPACE_NAME}'].id | [0]" -o tsv)
if [ -z "$WORKSPACE_ID" ] || [ "$WORKSPACE_ID" = "None" ]; then
  echo "Creating workspace: ${WORKSPACE_NAME}"
  WORKSPACE_ID=$(az rest --method post --url "${FABRIC_API}/workspaces" --resource https://api.fabric.microsoft.com \
    --body "{\"displayName\":\"${WORKSPACE_NAME}\",\"capacityId\":\"${CAPACITY_ID}\"}" \
    --query id -o tsv)
else
  echo "Reusing workspace: ${WORKSPACE_NAME} (${WORKSPACE_ID})"
fi

echo "Assigning workspace to capacity ${CAPACITY_NAME}"
az rest --method post --url "${FABRIC_API}/workspaces/${WORKSPACE_ID}/assignToCapacity" --resource https://api.fabric.microsoft.com \
  --body "{\"capacityId\":\"${CAPACITY_ID}\"}"

# -- Find-or-create one item per (displayName, type) pair --
create_item_if_missing() {
  item_name="$1"
  item_type="$2"
  existing=$(az rest --method get --url "${FABRIC_API}/workspaces/${WORKSPACE_ID}/items?type=${item_type}" --resource https://api.fabric.microsoft.com \
    --query "value[?displayName=='${item_name}'].id | [0]" -o tsv)
  if [ -n "$existing" ] && [ "$existing" != "None" ]; then
    echo "Reusing ${item_type}: ${item_name} (${existing})"
    return
  fi
  echo "Creating ${item_type}: ${item_name}"
  az rest --method post --url "${FABRIC_API}/workspaces/${WORKSPACE_ID}/items" --resource https://api.fabric.microsoft.com \
    --body "{\"displayName\":\"${item_name}\",\"type\":\"${item_type}\"}"
}

create_item_if_missing "aw-docs-lakehouse" "Lakehouse"
create_item_if_missing "aw-docs-ontology" "Ontology"
create_item_if_missing "aw-sales-data-agent" "DataAgent"

echo ""
echo "Done. Workspace ID: ${WORKSPACE_ID}"
echo "Configure the Ontology schema, load the Lakehouse, and wire the Data"
echo "Agent's data source + instructions from the Fabric portal, then run:"
echo "  scripts/create_fabric_toolbox.sh ${WORKSPACE_ID} <data-agent-id>"

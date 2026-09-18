#!/bin/sh
# Copyright (c) Microsoft. All rights reserved.
#
# Resume or suspend the Fabric capacity (infra/06-fabric-capacity.bicep).
# `state` is a read-only ARM property -- Bicep/ARM PUT can't set it, only
# this dedicated resume/suspend action can. Run from any machine signed in
# with `az login` (management-plane call, not affected by the VNet).
#
# The orchestrator's Fabric toolbox (scripts/create_fabric_toolbox.sh) only
# works while the capacity is Active -- resume it before testing, suspend
# it afterward to stop billing.
#
# Idempotent -- checks current state first and no-ops if already there. A
# fresh capacity starts Active (nothing to resume from), and Fabric's REST
# API rejects a resume/suspend action targeting the state it's already in
# with "Service is not ready to be updated" (BadRequest, subCode 7) rather
# than treating it as a no-op.
#
# Usage: ./set_fabric_capacity_state.sh resume|suspend [capacity-name]

set -eu

ACTION="${1:?Usage: set_fabric_capacity_state.sh resume|suspend [capacity-name]}"
CAPACITY_NAME="${2:-foundryiqv2fabric}"

SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-f26d977d-4a4e-45b3-b4a8-68d268c44852}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-foundryiq-v2}"

case "$ACTION" in
  resume|suspend) ;;
  *) echo "Usage: set_fabric_capacity_state.sh resume|suspend [capacity-name]" >&2; exit 1 ;;
esac

TARGET_STATE="Active"
[ "$ACTION" = "suspend" ] && TARGET_STATE="Paused"

CURRENT_STATE=$(az resource show -g "$RESOURCE_GROUP" -n "$CAPACITY_NAME" \
  --resource-type Microsoft.Fabric/capacities --query properties.state -o tsv)
if [ "$CURRENT_STATE" = "$TARGET_STATE" ]; then
  echo "${CAPACITY_NAME} is already ${CURRENT_STATE} -- nothing to do."
  exit 0
fi

az rest --method post \
  --url "https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.Fabric/capacities/${CAPACITY_NAME}/${ACTION}?api-version=2023-11-01"

echo "Requested: ${ACTION} on ${CAPACITY_NAME}. Check state:"
echo "  az resource show -g ${RESOURCE_GROUP} -n ${CAPACITY_NAME} --resource-type Microsoft.Fabric/capacities --query properties.state"

/*
  Step 6 -- Microsoft Fabric capacity, under this resource group and under
  IaC. `orchestrator-agent`'s Fabric toolbox (scripts/create_fabric_toolbox.sh)
  depends on this capacity being Active.

  This is a NEW capacity (`foundryiqv2fabric`), not the original `fabric3iq`
  moved in place: a direct ARM resource-group move of `fabric3iq` (from
  rg-3iqdemo) was attempted first and failed --
  `ResourceMoveTimedOut: Move resources for provider 'Microsoft.Fabric' did
  not finish within allowed time '00:15:00'` -- a known limitation, not a
  one-off (Microsoft's own guidance for cross-group/cross-subscription Fabric
  moves is to provision a new capacity in the target group and reassign
  workspaces to it, not rely on ARM move). So that's what this does: new
  capacity here, then reassign the workspace(s) that were on `fabric3iq`
  (scripts/provision_fabric_workspace.sh handles workspace assignment) to
  this one, then decommission `fabric3iq`.

  Region stays West US, matching the original -- Fabric workspaces are
  region-pinned to whatever capacity they're assigned to at creation, so
  reassigning across regions risks a data-residency change, not just a
  compute move.

  What Bicep/ARM can and can't reach, for Fabric specifically:
  `Microsoft.Fabric/capacities` IS a real ARM resource type (stable API
  2023-11-01) -- the capacity itself is what this file manages. Everything
  *inside* Fabric -- workspaces, and every item type in them (Lakehouse,
  Data Agent, Digital Twin Builder/"ontology", notebooks, etc.) -- has NO
  ARM resource type. Those live entirely in the Fabric control plane and are
  only reachable via the Fabric REST API (api.fabric.microsoft.com), the
  same way this project's existing scripts/create_fabric_toolbox.sh already
  talks to Fabric. See scripts/provision_fabric_workspace.sh for the
  equivalent of "the rest of this file" for workspace/Lakehouse/Digital
  Twin Builder/Data Agent -- it's a script, not Bicep, because there is no
  Bicep for those.

  Note on Active/Paused: `state` is a read-only property on this resource
  (confirmed against the 2023-11-01 schema -- FabricCapacityProperties only
  accepts `administration`), not something this Bicep can set. Pause/Resume
  is its own control-plane action, not a PUT property -- use
  scripts/set_fabric_capacity_state.sh instead. This deployment only manages
  the capacity's existence, SKU, and administrators.
*/

@description('Name of the Fabric capacity.')
param capacityName string = 'foundryiqv2fabric'

@description('Region for the capacity -- moving resource group does not move region, and Fabric workspaces are region-pinned to their capacity.')
param location string = 'West US'

@description('Fabric SKU, e.g. F64.')
param skuName string = 'F64'

@description('Entra UPNs/emails of Fabric capacity administrators.')
param adminMembers array = [
  'pranabp@MngEnvMCAP072730.onmicrosoft.com'
]

resource fabricCapacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: capacityName
  location: location
  sku: {
    name: skuName
    tier: 'Fabric'
  }
  properties: {
    administration: {
      members: adminMembers
    }
  }
}

output capacityName string = fabricCapacity.name
output capacityId string = fabricCapacity.id

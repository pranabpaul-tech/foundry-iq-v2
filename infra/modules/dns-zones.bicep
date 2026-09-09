/*
  Private DNS zones for the private Foundry IQ v2 environment, each linked
  to the VNet. Simplified from template 19's per-zone module (we don't need
  its "reference an existing zone in another RG/subscription" branch --
  every zone here is created fresh).
*/

@description('ARM ID of the VNet to link every zone to.')
param vnetId string

@description('Fully-qualified private DNS zone names to create and link.')
param zoneNames string[]

@description('Suffix to keep vnet-link names unique per deployment.')
param suffix string

resource zones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for zoneName in zoneNames: {
  name: zoneName
  location: 'global'
}]

resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (zoneName, i) in zoneNames: {
  parent: zones[i]
  name: '${replace(zoneName, '.', '-')}-${suffix}-link'
  location: 'global'
  properties: {
    virtualNetwork: { id: vnetId }
    registrationEnabled: false
  }
}]

output zoneIds array = [for (zoneName, i) in zoneNames: zones[i].id]
output zoneNames array = zoneNames

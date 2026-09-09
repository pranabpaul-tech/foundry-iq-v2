/*
  Step 1 -- network foundation for the private Foundry IQ v2 environment.
  VNet + 3 subnets + 12 private DNS zones. Deploy this first -- 02, 03, and
  04 all depend on the VNet/subnets it creates.

  Deploy order for a fresh environment: 01-network -> 02-data-services ->
  03-foundry-account -> 04-jumpbox (order-independent of 03) -> then
  register the 3 agents from inside the jumpbox (see README.md).

  See README.md and .claude/plans/dazzling-watching-garden.md for the full
  picture and the two hard-won findings that shaped this design: template
  15/"04-byo-vnet-private" doesn't support MCP tools behind the VNet
  (template 19 does -- that's what this is built from), and native web
  search *does* work behind this posture (confirmed by testing, no
  workaround needed).
*/

@description('Azure region for all network resources. Must match the Foundry account region.')
param location string = 'uksouth'

@description('Base name used to derive network resource names.')
param baseName string = 'foundryiqv2'

var suffix = uniqueString(resourceGroup().id, 'network')
var vnetName = '${baseName}-vnet'

module vnet 'modules/vnet.bicep' = {
  name: 'vnet-deploy'
  params: {
    location: location
    vnetName: vnetName
  }
}

var dnsZoneNames = [
  'privatelink.services.ai.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.cognitiveservices.azure.com'
  'privatelink.search.windows.net'
  'privatelink.blob.${environment().suffixes.storage}'
  'privatelink.documents.azure.com'
  'privatelink.fabric.microsoft.com'
  'privatelink.azurecr.io'
  'privatelink.monitor.azure.com'
  'privatelink.oms.opinsights.azure.com'
  'privatelink.ods.opinsights.azure.com'
  'privatelink.agentsvc.azure-automation.net'
]

module dnsZones 'modules/dns-zones.bicep' = {
  name: 'dns-zones-deploy'
  params: {
    vnetId: vnet.outputs.vnetId
    zoneNames: dnsZoneNames
    suffix: take(suffix, 6)
  }
}

output vnetId string = vnet.outputs.vnetId
output vnetName string = vnet.outputs.vnetName
output agentSubnetId string = vnet.outputs.agentSubnetId
output agentSubnetName string = vnet.outputs.agentSubnetName
output peSubnetId string = vnet.outputs.peSubnetId
output peSubnetName string = vnet.outputs.peSubnetName
output mcpSubnetId string = vnet.outputs.mcpSubnetId
output mcpSubnetName string = vnet.outputs.mcpSubnetName
output dnsZoneIds array = dnsZones.outputs.zoneIds
output dnsZoneNames array = dnsZones.outputs.zoneNames

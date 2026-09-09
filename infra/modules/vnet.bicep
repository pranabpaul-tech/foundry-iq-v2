/*
  VNet + 3 subnets for the private Foundry IQ v2 environment.
  Adapted from azure-ai-foundry/foundry-samples' template 19
  (19-private-network-agent-tools/modules-network-secured/vnet.bicep) --
  the one template in that family that supports agent tools (MCP, Fabric
  Data Agent) behind the VNet, unlike template 15.
*/

@description('Azure region for the deployment.')
param location string

@description('Name of the virtual network.')
param vnetName string

@description('Agent subnet name -- delegated to Microsoft.App/environments, hosts the Foundry capability host.')
param agentSubnetName string = 'agent-subnet'

@description('Private endpoint subnet name.')
param peSubnetName string = 'pe-subnet'

@description('MCP subnet name -- reserved for self-hosted MCP/OpenAPI/Function/A2A tool servers. Unused today (Fabric is reached via its own private link, not a server we host) but provisioned to match the reference template.')
param mcpSubnetName string = 'mcp-subnet'

@description('Address space for the VNet.')
param vnetAddressPrefix string = '192.168.0.0/16'

@description('Address prefix for the agent subnet.')
param agentSubnetPrefix string = '192.168.0.0/24'

@description('Address prefix for the private endpoint subnet.')
param peSubnetPrefix string = '192.168.1.0/24'

@description('Address prefix for the MCP subnet.')
param mcpSubnetPrefix string = '192.168.2.0/24'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: [
      {
        name: agentSubnetName
        properties: {
          addressPrefix: agentSubnetPrefix
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: peSubnetName
        properties: {
          addressPrefix: peSubnetPrefix
        }
      }
      {
        name: mcpSubnetName
        properties: {
          addressPrefix: mcpSubnetPrefix
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output agentSubnetId string = '${vnet.id}/subnets/${agentSubnetName}'
output peSubnetId string = '${vnet.id}/subnets/${peSubnetName}'
output mcpSubnetId string = '${vnet.id}/subnets/${mcpSubnetName}'
output agentSubnetName string = agentSubnetName
output peSubnetName string = peSubnetName
output mcpSubnetName string = mcpSubnetName

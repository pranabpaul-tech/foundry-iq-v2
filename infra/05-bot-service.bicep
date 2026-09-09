/*
  Step 5 -- Azure Bot Service resource, wired to the private orchestrator
  agent via Foundry's own native mechanism, not a custom relay:

  Foundry exposes a service-managed, source-IP-filtered public exception
  for the agent's Activity Protocol route only (Bot Service and Microsoft
  365 source ranges) -- everything else on the project (Responses API,
  agent management) stays fully private. This Bot Service resource's
  endpoint points directly at Foundry's own public exception URL, which
  Microsoft's Bot Service/Teams infrastructure reaches without touching
  our VNet. (See the publicNetworkAccess note further down: unlike
  Microsoft's reference example, THIS resource stays 'Enabled'.)

  Channel is Microsoft Teams, not Direct Line -- confirmed by testing that
  a raw/anonymous Direct Line caller can't satisfy either authorization
  scheme (BotServiceRbac or BotServiceTenant both require a real per-caller
  Entra token, which Direct Line's classic secret-based flow never carries
  through to Foundry; Teams does, since every Teams message already carries
  the sender's real signed-in token). Auth scheme is BotServiceTenant: any
  signed-in member of this tenant can use the bot via Teams -- not
  anonymous-public, but the broadest this native mechanism supports.
  Genuinely anonymous public access would need a different architecture
  (APIM/relay in the VNet translating Bot Framework Activity protocol to
  the Responses API) -- not what this builds.

  publicNetworkAccess on THIS resource is deliberately 'Enabled' (unlike
  Microsoft's own reference example, which sets it Disabled for a fully
  locked-down scenario): confirmed by testing that Disabled also blocks
  Direct Line's own client-facing API (NetworkDenied), not just inbound
  Foundry traffic. The private half of this design is entirely on the
  Foundry side (the source-IP-filtered activity-protocol exception) --
  this resource itself is meant to be reachable, per the ask that started
  this ("keep it publicly accessible but connected... over private
  endpoint").

  Adapted from Microsoft's own reference:
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/publish-copilot-virtual-network

  Prerequisite (run first, from inside the jumpbox):
  scripts/enable_agent_teams_endpoint.sh orchestrator-agent BotServiceTenant

  After this resource deploys, publish the agent as a Teams app (also from
  inside the jumpbox) -- without this step the Teams deep link resolves to
  nothing ("couldn't find the bot"):
  scripts/publish_agent_to_teams.sh orchestrator-agent
*/

@description('Name for the Bot Service resource.')
param botName string = 'foundryiq-orchestrator-bot'

@description('Display name shown in Teams.')
param displayName string = 'Adventure Works Assistant'

@description('Orchestrator agent identity client ID (instance_identity.client_id).')
param msaAppId string

@description('Microsoft Entra tenant ID.')
param tenantId string

@description('Orchestrator agent Activity Protocol endpoint.')
param endpoint string = 'https://foundryiqv2p3ygk.services.ai.azure.com/api/projects/iqv2project/agents/orchestrator-agent/endpoint/protocols/activityProtocol?api-version=2025-05-15-preview'

@description('Bot Service SKU. F0 is the free tier.')
param botServiceSku string = 'F0'

resource botService 'Microsoft.BotService/botServices@2022-09-15' = {
  name: botName
  kind: 'azurebot'
  location: 'global'
  sku: {
    name: botServiceSku
  }
  properties: {
    displayName: displayName
    endpoint: endpoint
    msaAppId: msaAppId
    msaAppTenantId: tenantId
    msaAppType: 'SingleTenant'
    publicNetworkAccess: 'Enabled'
  }
}

resource botServiceMsTeamsChannel 'Microsoft.BotService/botServices/channels@2021-03-01' = {
  parent: botService
  location: 'global'
  name: 'MsTeamsChannel'
  properties: {
    channelName: 'MsTeamsChannel'
  }
}

output botServiceName string = botService.name
output botServiceId string = botService.id

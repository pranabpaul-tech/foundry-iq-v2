/*
  azd entry point. Composes 01-network, 02-data-services, 03-foundry-account,
  04-jumpbox, and 06-fabric-capacity as modules with automatic output ->
  param wiring (subnet IDs, resource names) -- `azd provision` deploys all
  five in correct dependency order in one command, instead of five separate
  `az deployment group create` calls with subnet/resource IDs manually
  copy-pasted between them.

  05-bot-service.bicep is deliberately NOT composed here: it needs
  orchestrator-agent's instance_identity.client_id as msaAppId, which only
  exists after the agent is registered (a data-plane call, itself only
  possible after this deployment finishes) -- see README.md "Bot Service /
  Teams" for that follow-on step, run once you have that ID.

  Deploys into an EXISTING resource group (targetScope defaults to
  resourceGroup) -- azd's environment must have AZURE_RESOURCE_GROUP set to
  rg-foundryiq-v2 (or wherever you're deploying); see README.md "Step-by-
  step setup".

  Every top-level output below is picked up automatically into the azd
  environment (`azd env get-values`) using these exact names -- they match
  .env.example 1:1, so `azd env get-values > .env` after `azd provision`
  produces a working .env with no manual endpoint copy-pasting.
*/

@description('Azure region. Must match the region of the existing Search/Storage/ACR resources.')
param location string = 'uksouth'

@description('Base name used to derive new resource names.')
param baseName string = 'foundryiqv2'

@description('Name of the existing AI Search service to attach (private endpoint added, publicNetworkAccess disabled by the postprovision hook).')
param searchName string

@description('Name of the existing Storage account to attach (private endpoint added).')
param storageName string

@description('Name of the existing ACR to attach (stays public -- used by az acr build, which has no VNet-reachable equivalent from outside).')
param acrName string

@description('Foundry project name.')
param projectName string = 'iqv2project'

@description('Fabric capacity name.')
param fabricCapacityName string = 'foundryiqv2fabric'

@description('Fabric SKU.')
param fabricSkuName string = 'F64'

@description('Entra UPNs/emails of Fabric capacity administrators.')
param fabricAdminMembers array = [
  'pranabp@MngEnvMCAP072730.onmicrosoft.com'
]

module network '01-network.bicep' = {
  name: 'network'
  params: {
    location: location
    baseName: baseName
  }
}

module dataServices '02-data-services.bicep' = {
  name: 'data-services'
  params: {
    location: location
    baseName: baseName
    searchName: searchName
    storageName: storageName
    acrName: acrName
    vnetName: network.outputs.vnetName
    peSubnetName: network.outputs.peSubnetName
  }
}

module foundryAccount '03-foundry-account.bicep' = {
  name: 'foundry-account'
  params: {
    location: location
    baseName: '${baseName}p'
    projectName: projectName
    agentSubnetId: network.outputs.agentSubnetId
    peSubnetId: network.outputs.peSubnetId
    searchName: searchName
    storageName: storageName
    acrName: acrName
    cosmosName: dataServices.outputs.cosmosName
  }
}

module jumpbox '04-jumpbox.bicep' = {
  name: 'jumpbox'
  params: {
    location: location
    vnetName: network.outputs.vnetName
  }
}

module fabricCapacity '06-fabric-capacity.bicep' = {
  name: 'fabric-capacity'
  params: {
    capacityName: fabricCapacityName
    skuName: fabricSkuName
    adminMembers: fabricAdminMembers
  }
}

output RESOURCE_GROUP string = resourceGroup().name
output SUBSCRIPTION_ID string = subscription().subscriptionId
output ACCOUNT_NAME string = foundryAccount.outputs.accountName
output PROJECT_NAME string = foundryAccount.outputs.projectName
output FOUNDRY_PROJECT_ENDPOINT string = foundryAccount.outputs.projectEndpoint
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = foundryAccount.outputs.chatDeploymentName
output AZURE_EMBEDDING_MODEL_DEPLOYMENT_NAME string = foundryAccount.outputs.embeddingDeploymentName
output AZURE_SEARCH_ENDPOINT string = 'https://${searchName}.search.windows.net'
output AZURE_OPENAI_ENDPOINT string = 'https://${foundryAccount.outputs.accountName}.openai.azure.com/'
output ACR_NAME string = acrName
output ACR_LOGIN_SERVER string = '${acrName}.azurecr.io'
output SEARCH_NAME string = searchName
output STORAGE_NAME string = storageName
output JUMPBOX_NAME string = jumpbox.outputs.containerGroupName
output FABRIC_CAPACITY_NAME string = fabricCapacity.outputs.capacityName
output VNET_NAME string = network.outputs.vnetName

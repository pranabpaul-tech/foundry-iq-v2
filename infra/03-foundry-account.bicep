/*
  Step 3 -- network-injected Foundry account + project. This is the only
  Foundry account in the environment -- the original public one
  (foundryiqv2pau4) was decommissioned once this one was verified working
  end-to-end, since networkInjections is set at account-creation time (see
  ai-account-identity.bicep in the reference template: it always creates a
  fresh account, there's no "retrofit an existing account" branch) so an
  in-place upgrade was never possible.

  Adapted from azure-ai-foundry/foundry-samples' template 19
  (19-private-network-agent-tools/modules-network-secured/ai-account-identity.bicep,
  ai-project-identity.bicep, add-project-capability-host.bicep).

  Expect the capability host step to take roughly 30-35 minutes -- this is
  documented as normal in the reference template, not a hang. Deploy
  01-network.bicep and 02-data-services.bicep first.

  Known deployment quirk (hit and fixed during the original rollout): the
  account transiently flips back to "Accepted" while its model deployments
  are being written, which races the account's own private endpoint if
  submitted in the same deployment -- see the dependsOn on accountPe below.
*/

@description('Azure region. Must match the VNet region.')
param location string = 'uksouth'

@description('Base name for the new private Foundry account.')
param baseName string = 'foundryiqv2p'

@description('Foundry project name.')
param projectName string = 'iqv2project'

@description('Chat model deployment.')
param chatModelName string = 'gpt-4.1'
param chatModelVersion string = '2025-04-14'
param chatModelCapacity int = 30

@description('Embedding model deployment.')
param embeddingModelName string = 'text-embedding-3-large'
param embeddingModelVersion string = '1'
param embeddingModelCapacity int = 30

@description('Resource ID of the agent subnet (delegated to Microsoft.App/environments).')
param agentSubnetId string

@description('Name of the existing (already privately-linked) AI Search service.')
param searchName string

@description('Name of the existing (already privately-linked) Storage account.')
param storageName string

@description('Name of the existing ACR (public, IP-allowlisted -- used for az acr build, not privately linked).')
param acrName string = 'foundryiqv2acr'

@description('Name of the new (already privately-linked) Cosmos DB account from Phase 2.')
param cosmosName string

@description('Resource ID of the private endpoint subnet, for the account\'s own private endpoint.')
param peSubnetId string

var suffix = take(uniqueString(resourceGroup().id, 'phase3v2'), 4)
var accountName = toLower('${baseName}${suffix}')

// ---------- account ----------

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  sku: { name: 'S0' }
  kind: 'AIServices'
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    networkAcls: {
      defaultAction: 'Deny'
      virtualNetworkRules: []
      ipRules: []
      bypass: 'AzureServices'
    }
    publicNetworkAccess: 'Disabled'
    networkInjections: [
      {
        scenario: 'agent'
        subnetArmId: agentSubnetId
        useMicrosoftManagedNetwork: false
      }
    ]
    disableLocalAuth: true
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: account
  name: chatModelName
  sku: {
    name: 'GlobalStandard'
    capacity: chatModelCapacity
  }
  properties: {
    model: {
      name: chatModelName
      format: 'OpenAI'
      version: chatModelVersion
    }
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: account
  name: embeddingModelName
  sku: {
    name: 'Standard'
    capacity: embeddingModelCapacity
  }
  properties: {
    model: {
      name: embeddingModelName
      format: 'OpenAI'
      version: embeddingModelVersion
    }
  }
  dependsOn: [ chatDeployment ]
}

// ---------- account private endpoint (so the jumpbox / private callers can reach the data plane) ----------

resource aiServicesDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: 'privatelink.services.ai.azure.com'
}
resource openAiDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: 'privatelink.openai.azure.com'
}
resource cognitiveServicesDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: 'privatelink.cognitiveservices.azure.com'
}

resource accountPe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${accountName}-pe'
  location: location
  properties: {
    subnet: { id: peSubnetId }
    privateLinkServiceConnections: [
      {
        name: '${accountName}-plsc'
        properties: {
          privateLinkServiceId: account.id
          groupIds: ['account']
        }
      }
    ]
  }
  // Explicit dependency: the account transiently flips back to "Accepted"
  // while its model deployments are being written, which races a PE
  // creation submitted in the same deployment batch if only the implicit
  // dependency (via account.id) is relied on. Confirmed by reproducing:
  // creating this same PE via plain `az network private-endpoint create`
  // (no concurrent deployments in flight) succeeded immediately.
  dependsOn: [
    chatDeployment
    embeddingDeployment
  ]
}

resource accountDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: accountPe
  name: 'dns-group'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'aiservices', properties: { privateDnsZoneId: aiServicesDnsZone.id } }
      { name: 'openai', properties: { privateDnsZoneId: openAiDnsZone.id } }
      { name: 'cogservices', properties: { privateDnsZoneId: cognitiveServicesDnsZone.id } }
    ]
  }
}

// ---------- project + BYO connections ----------

resource searchService 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchName
}
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-12-01-preview' existing = {
  name: cosmosName
}
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    description: 'Foundry IQ v2 multi-agent project (private/VNet-integrated)'
    displayName: 'Foundry IQ v2 (private)'
  }

  resource cosmosConnection 'connections@2025-04-01-preview' = {
    name: cosmosName
    properties: {
      category: 'CosmosDB'
      target: cosmosAccount.properties.documentEndpoint
      authType: 'AAD'
      metadata: {
        ApiType: 'Azure'
        ResourceId: cosmosAccount.id
        location: cosmosAccount.location
      }
    }
  }

  resource storageConnection 'connections@2025-04-01-preview' = {
    name: storageName
    properties: {
      category: 'AzureStorageAccount'
      target: storageAccount.properties.primaryEndpoints.blob
      authType: 'AAD'
      metadata: {
        ApiType: 'Azure'
        ResourceId: storageAccount.id
        location: storageAccount.location
      }
    }
  }

  resource searchConnection 'connections@2025-04-01-preview' = {
    name: searchName
    properties: {
      category: 'CognitiveSearch'
      target: 'https://${searchName}.search.windows.net'
      authType: 'AAD'
      metadata: {
        ApiType: 'Azure'
        ResourceId: searchService.id
        location: searchService.location
      }
    }
  }
}

// ACR connection so azd/hosted agents can pull images. ACR itself stays public +
// IP-allowlisted (Phase 2 only added a private endpoint alongside, didn't disable
// public access) -- images are built with `az acr build` from outside the VNet
// (ACI has no Docker-in-Docker support) and pulled by the network-injected
// account/project via this ManagedIdentity connection.
resource acrConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: '${acrName}-conn'
  properties: {
    category: 'ContainerRegistry'
    target: acr.properties.loginServer
    authType: 'ManagedIdentity'
    isSharedToAll: true
    credentials: {
      clientId: project.identity.principalId
      resourceId: acr.id
    }
    metadata: {
      ResourceId: acr.id
    }
  }
}

resource acrPullRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
}
resource projectToAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(project.id, acrPullRole.id, acr.id)
  properties: {
    principalId: project.identity.principalId
    roleDefinitionId: acrPullRole.id
    principalType: 'ServicePrincipal'
  }
}

// ---------- RBAC: project identity needs data-plane access to Search/Storage/Cosmos ----------

resource searchIndexDataContributorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
}
resource searchServiceContributorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
}
resource storageBlobDataContributorRole 'Microsoft.Authorization/roleDefinitions@2022-05-01-preview' existing = {
  name: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
}
resource cosmosDbOperatorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: '230815da-be43-4aae-9cb4-875f7bd000aa'
}

resource projectToSearchIndexData 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: searchService
  name: guid(project.id, searchIndexDataContributorRole.id, searchService.id)
  properties: {
    principalId: project.identity.principalId
    roleDefinitionId: searchIndexDataContributorRole.id
    principalType: 'ServicePrincipal'
  }
}
resource projectToSearchService 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: searchService
  name: guid(project.id, searchServiceContributorRole.id, searchService.id)
  properties: {
    principalId: project.identity.principalId
    roleDefinitionId: searchServiceContributorRole.id
    principalType: 'ServicePrincipal'
  }
}
resource projectToStorageBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storageAccount
  name: guid(project.id, storageBlobDataContributorRole.id, storageAccount.id)
  properties: {
    principalId: project.identity.principalId
    roleDefinitionId: storageBlobDataContributorRole.id
    principalType: 'ServicePrincipal'
  }
}
resource projectToCosmosOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: cosmosAccount
  name: guid(project.id, cosmosDbOperatorRole.id, cosmosAccount.id)
  properties: {
    principalId: project.identity.principalId
    roleDefinitionId: cosmosDbOperatorRole.id
    principalType: 'ServicePrincipal'
  }
}
// Cosmos DB data-plane RBAC is a separate system from ARM roles -- built-in
// "Cosmos DB Built-in Data Contributor" (id 00000000-0000-0000-0000-000000000002).
resource projectToCosmosData 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2022-05-15' = {
  parent: cosmosAccount
  name: guid(project.id, cosmosName, 'data-contributor')
  properties: {
    principalId: project.identity.principalId
    roleDefinitionId: resourceId('Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions', cosmosName, '00000000-0000-0000-0000-000000000002')
    scope: cosmosAccount.id
  }
}

// ---------- project-level capability host (Agents) ----------
// The account-level capability host is created implicitly by networkInjections
// above; only one per account is allowed, so it's not declared here.

#disable-next-line BCP037
resource projectCapabilityHost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = {
  name: 'iqv2caphost'
  parent: project
  properties: {
    capabilityHostKind: 'Agents'
    vectorStoreConnections: [searchName]
    storageConnections: [storageName]
    threadStorageConnections: [cosmosName]
  }
  dependsOn: [
    project::cosmosConnection
    project::storageConnection
    project::searchConnection
    projectToSearchIndexData
    projectToSearchService
    projectToStorageBlob
    projectToCosmosOperator
    projectToCosmosData
  ]
}

output accountName string = account.name
output accountId string = account.id
output accountPrincipalId string = account.identity.principalId
output projectName string = project.name
output projectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${projectName}'
output chatDeploymentName string = chatModelName
output embeddingDeploymentName string = embeddingModelName

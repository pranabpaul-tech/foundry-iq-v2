/*
  Foundry IQ v2 — core resources.

  Phase 1 posture: everything public, for fast iteration. Phase 8 flips
  Storage/ACR/Search to private endpoints (see private-endpoints.bicep);
  the Foundry account stays public throughout by design.
*/

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Base name used to derive resource names.')
param baseName string = 'foundryiqv2'

@description('Foundry project name.')
param projectName string = 'iqv2project'

@description('Chat model deployment.')
param chatModelName string = 'gpt-4.1'
param chatModelVersion string = '2025-04-14'
param chatModelCapacity int = 30

@description('Embedding model deployment, used by the Foundry IQ Knowledge Base.')
param embeddingModelName string = 'text-embedding-3-large'
param embeddingModelVersion string = '1'
param embeddingModelCapacity int = 30

@description('Public IP allowed to push images to ACR during development.')
param developerIp string

@description('Microsoft Fabric workspace ID backing the Fabric data agent (GUID).')
param fabricWorkspaceId string

@description('Microsoft Fabric data agent ID within the workspace (GUID).')
param fabricDataAgentId string

var suffix = substring(uniqueString(resourceGroup().id), 0, 4)
var accountName = toLower('${baseName}${suffix}')
var storageName = toLower('${baseName}${suffix}stor')
var searchName = toLower('${baseName}${suffix}search')
var acrName = toLower('acr${baseName}${suffix}')

// ---------- Foundry account + project ----------

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  sku: { name: 'S0' }
  kind: 'AIServices'
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    // Foundry intentionally stays public — Phase 8 does not change this.
    publicNetworkAccess: 'Enabled'
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
  // Model deployments on the same account must be created serially.
  dependsOn: [ chatDeployment ]
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    description: 'Foundry IQ v2 multi-agent project'
    displayName: 'Foundry IQ v2'
  }
}

// ---------- Storage ----------

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    // Shared-key access left enabled for Phase 1/2 convenience; Entra RBAC is
    // still what the agents and Search use.
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }

  resource blobService 'blobServices' = {
    name: 'default'

    resource docsContainer 'containers' = {
      name: 'aw-docs'
    }
  }
}

// ---------- AI Search ----------

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchName
  location: location
  sku: { name: 'standard' }
  identity: { type: 'SystemAssigned' }
  properties: {
    partitionCount: 1
    replicaCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    // Knowledge Bases require semantic search — enabled up front deliberately.
    semanticSearch: 'standard'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

// ---------- ACR ----------

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: 'Premium' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    networkRuleBypassOptions: 'AzureServices'
    networkRuleSet: {
      defaultAction: 'Allow'
      ipRules: [
        {
          action: 'Allow'
          value: developerIp
        }
      ]
    }
  }
}

// ---------- RBAC ----------

var searchIndexDataContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var searchServiceContributor = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
var storageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var cognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

// Search reads the PDFs out of blob storage for ingestion.
resource searchToStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, search.id, storageBlobDataReader)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReader)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Search calls the embedding model during ingestion and query planning.
resource searchToAccount 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, search.id, cognitiveServicesOpenAIUser)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUser)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// The project pulls agent images from ACR.
resource projectToAcr 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, project.id, acrPull)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// The project's identity queries the Knowledge Base at runtime.
resource projectToSearchData 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, project.id, searchIndexDataContributor)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributor)
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource projectToSearchService 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, project.id, searchServiceContributor)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributor)
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------- Project connections ----------

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

resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: '${searchName}-conn'
  properties: {
    category: 'CognitiveSearch'
    target: 'https://${searchName}.search.windows.net'
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: search.id
      location: location
    }
  }
}

resource storageConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: '${storageName}-conn'
  properties: {
    category: 'AzureStorageAccount'
    target: storage.properties.primaryEndpoints.blob
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: storage.id
      location: location
    }
  }
}

// Points the Fabric data-agent tool (used via a toolbox, not inline) directly
// at Fabric's own MCP endpoint. UserEntraToken auth forwards the calling
// user's identity so Fabric can do On-Behalf-Of -- the Foundry-native
// fabric_dataagent_preview tool only supports OBO for direct/prompt-agent
// callers, never for a hosted container's own fixed identity. See README.
resource fabricDataAgentConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview' = {
  parent: project
  name: 'fabric-dataagent-obo'
  properties: {
    category: 'RemoteTool'
    authType: 'UserEntraToken'
    target: 'https://api.fabric.microsoft.com/v1/mcp/workspaces/${fabricWorkspaceId}/dataagents/${fabricDataAgentId}/agent'
    audience: 'https://api.fabric.microsoft.com'
  }
}

// ---------- Outputs ----------

output accountName string = account.name
output projectNameOut string = project.name
output projectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${projectName}'
output openAiEndpoint string = 'https://${accountName}.openai.azure.com/'
output storageAccountName string = storage.name
output storageAccountId string = storage.id
output searchServiceName string = search.name
output searchEndpoint string = 'https://${searchName}.search.windows.net'
output acrNameOut string = acr.name
output acrLoginServer string = acr.properties.loginServer
output acrConnectionName string = acrConnection.name
output chatDeploymentName string = chatModelName
output embeddingDeploymentName string = embeddingModelName
output fabricDataAgentConnectionName string = fabricDataAgentConnection.name

// NOTE: the Fabric toolbox itself (fabric-iq-toolbox) is not a bicep-managed
// resource -- create/update it with scripts/create_fabric_toolbox.py.
//
// NOTE: per-hosted-agent RBAC (Search Index Data Reader + Search Service
// Contributor on `search`, and Foundry User on `project` for orchestrator-agent)
// can't be granted here: each hosted agent gets its own dedicated Entra
// "AgentIdentity" service principal, created by `azd deploy` *after* this
// template runs, so its object ID isn't known at bicep deploy time. Grant
// these roles manually (az role assignment create) once each agent exists.
// See README.md's setup sequence.

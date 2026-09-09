/*
  Step 2 -- private endpoints for the existing data services (Search,
  Storage, ACR -- no recreation, they keep their content/config) plus a new
  Cosmos DB for NoSQL (required by Foundry's standard agent setup; there
  wasn't one before this). Also creates the Log Analytics workspace used by
  Application Insights in step 3 (App Insights itself needs the new Foundry
  account to exist first, for its account-level connection).

  References the private DNS zones created in 01-network.bicep by name
  rather than recreating them. Deploy 01-network.bicep first.

  This file only ADDS the private endpoint -- it doesn't flip Search's own
  publicNetworkAccess to Disabled (that's a one-property update on an
  already-existing, already-configured service; redeclaring it as a full
  Bicep resource block here risks resetting settings -- replica/partition
  count, etc. -- that aren't repeated in this file). That flip happens via
  `az search service update --public-network-access disabled`, run
  automatically by infra/hooks/postprovision.sh after `azd provision`.
  Storage and ACR are deliberately left alone: Storage's public access was
  already governance-locked Disabled before this project started, and ACR
  stays public on purpose (az acr build has no VNet-reachable equivalent).
*/

@description('Azure region. Must match the VNet and existing resources region.')
param location string = 'uksouth'

@description('Base name used to derive new resource names (Cosmos DB, Log Analytics).')
param baseName string = 'foundryiqv2'

@description('Name of the existing AI Search service.')
param searchName string

@description('Name of the existing Storage account.')
param storageName string

@description('Name of the existing ACR.')
param acrName string

@description('Name of the VNet from Phase 1.')
param vnetName string

@description('Name of the private endpoint subnet from Phase 1.')
param peSubnetName string

var suffix = take(uniqueString(resourceGroup().id, 'phase2'), 4)
var cosmosName = toLower('${baseName}${suffix}cosmos')
var logAnalyticsName = 'law-${baseName}-${suffix}'

// ---------- existing resources ----------

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: vnetName
}

resource peSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: peSubnetName
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource searchDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: 'privatelink.search.windows.net'
}

resource blobDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
}

resource acrDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: 'privatelink.azurecr.io'
}

resource cosmosDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: 'privatelink.documents.azure.com'
}

// ---------- new: Cosmos DB for NoSQL (required by standard agent setup) ----------

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: cosmosName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
  }
}

// ---------- new: Log Analytics (App Insights + AMPLS wiring happens in Phase 3, once the account exists) ----------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ---------- private endpoints ----------

resource searchPe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${searchName}-pe'
  location: location
  properties: {
    subnet: { id: peSubnet.id }
    privateLinkServiceConnections: [
      {
        name: '${searchName}-plsc'
        properties: {
          privateLinkServiceId: search.id
          groupIds: ['searchService']
        }
      }
    ]
  }
}

resource searchDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: searchPe
  name: 'dns-group'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'config', properties: { privateDnsZoneId: searchDnsZone.id } }
    ]
  }
}

resource storagePe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${storageName}-pe'
  location: location
  properties: {
    subnet: { id: peSubnet.id }
    privateLinkServiceConnections: [
      {
        name: '${storageName}-plsc'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: ['blob']
        }
      }
    ]
  }
}

resource storageDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: storagePe
  name: 'dns-group'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'config', properties: { privateDnsZoneId: blobDnsZone.id } }
    ]
  }
}

resource acrPe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${acrName}-pe'
  location: location
  properties: {
    subnet: { id: peSubnet.id }
    privateLinkServiceConnections: [
      {
        name: '${acrName}-plsc'
        properties: {
          privateLinkServiceId: acr.id
          groupIds: ['registry']
        }
      }
    ]
  }
}

resource acrDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: acrPe
  name: 'dns-group'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'config', properties: { privateDnsZoneId: acrDnsZone.id } }
    ]
  }
}

resource cosmosPe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${cosmosName}-pe'
  location: location
  properties: {
    subnet: { id: peSubnet.id }
    privateLinkServiceConnections: [
      {
        name: '${cosmosName}-plsc'
        properties: {
          privateLinkServiceId: cosmos.id
          groupIds: ['Sql']
        }
      }
    ]
  }
}

resource cosmosDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: cosmosPe
  name: 'dns-group'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'config', properties: { privateDnsZoneId: cosmosDnsZone.id } }
    ]
  }
}

output cosmosName string = cosmos.name
output cosmosId string = cosmos.id
output logAnalyticsName string = logAnalytics.name
output logAnalyticsId string = logAnalytics.id

using './main.bicep'

param location = 'uksouth'
param baseName = 'foundryiqv2'
param projectName = 'iqv2project'
param chatModelName = 'gpt-4.1'
param chatModelVersion = '2025-04-14'
param chatModelCapacity = 30
param embeddingModelName = 'text-embedding-3-large'
param embeddingModelVersion = '1'
param embeddingModelCapacity = 30

// Set these in a gitignored main.bicepparam.local (or pass -p on the CLI)
// rather than committing real values:
//   developerIp        — your public IP, for ACR's dev-time firewall allowlist
//   fabricWorkspaceId   — your Microsoft Fabric workspace ID (GUID)
//   fabricDataAgentId  — your Fabric data agent ID within that workspace (GUID)
param developerIp = '0.0.0.0'
param fabricWorkspaceId = '00000000-0000-0000-0000-000000000000'
param fabricDataAgentId = '00000000-0000-0000-0000-000000000000'

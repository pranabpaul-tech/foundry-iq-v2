/*
  Jumpbox so az commands can reach the private Foundry project once
  publicNetworkAccess=Disabled. Order-independent of 02/03 -- deploy any
  time after 01-network.bicep.

  Deviates from the reference architecture (anihitk07's VM + Bastion): this
  subscription has zero available VM SKUs in uksouth (confirmed by
  `az vm list-skus` returning no unrestricted sizes at all -- the same
  compute restriction hit earlier in this project, where Azure Container
  Instances was the one working compute option). ACI in a VNet subnet +
  `az container exec` gives the same "interactive shell inside the private
  VNet" capability without needing a VM or Bastion.

  Real consequence of the ACI choice: this container has no Docker (no
  Docker-in-Docker in standard ACI), so `azd deploy`'s usual build+push+
  register flow doesn't work from here. The actual deploy split that was
  used instead (see README.md "Deploying agent updates"):
    1. `az acr build` (from outside the VNet -- ACR itself stays public/
       IP-allowlisted, only got a private endpoint added alongside) builds
       and pushes each agent's image with no local Docker needed.
    2. From inside this jumpbox: a raw `az rest POST .../agents/{name}/versions`
       call (HostedAgentDefinition + container_configuration.image) registers
       the agent against the private project -- this is a data-plane call to
       the project endpoint, unreachable from outside the VNet.
  One interactive `az login --use-device-code` from inside this shell is
  still needed once (this container has no identity of its own) -- not
  scriptable around, a human has to complete the device-code prompt.
*/

@description('Azure region. Must match the VNet region.')
param location string = 'uksouth'

@description('Name of the existing VNet.')
param vnetName string

@description('Address prefix for the jumpbox subnet (delegated to Microsoft.ContainerInstance/containerGroups).')
param jumpboxSubnetPrefix string = '192.168.3.0/24'

@description('Container group name.')
param containerGroupName string = 'ci-foundryiq-jump'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: vnetName
}

resource jumpboxSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: vnet
  name: 'jumpbox-subnet'
  properties: {
    addressPrefix: jumpboxSubnetPrefix
    delegations: [
      {
        name: 'Microsoft.ContainerInstance.containerGroups'
        properties: {
          serviceName: 'Microsoft.ContainerInstance/containerGroups'
        }
      }
    ]
  }
}

resource jumpbox 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: containerGroupName
  location: location
  properties: {
    osType: 'Linux'
    restartPolicy: 'Always'
    subnetIds: [
      { id: jumpboxSubnet.id }
    ]
    containers: [
      {
        name: 'jumpbox'
        properties: {
          image: 'mcr.microsoft.com/azure-cli:latest'
          command: ['/bin/sh', '-c', 'tail -f /dev/null']
          resources: {
            requests: {
              cpu: 1
              memoryInGB: 2
            }
          }
        }
      }
    ]
  }
}

output containerGroupName string = jumpbox.name

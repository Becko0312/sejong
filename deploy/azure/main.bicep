@description('Azure region. Check VM availability and pricing before deployment.')
param location string = resourceGroup().location

@description('Globally unique lowercase DNS label for the public converter.')
@minLength(3)
@maxLength(40)
param dnsLabel string

@description('Full 40-character reviewed commit SHA from Becko0312/sejong.')
@minLength(40)
@maxLength(40)
param sourceRevision string

@description('Administrator SSH public key. SSH is not exposed by the network rules.')
param sshPublicKey string

@description('Initial low-traffic burstable instance; sustained OCR may require a non-burstable size.')
param vmSize string = 'Standard_B2als_v2'

@description('Linux administrator account; no password or public SSH access.')
param adminUsername string = 'sejongadmin'

var name = 'sejong-converter'
var hostname = '${dnsLabel}.${location}.cloudapp.azure.com'
var bootstrap = replace(replace(loadTextContent('cloud-init.yaml'), '__HOSTNAME__', hostname), '__REVISION__', sourceRevision)

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${name}-nsg'
  location: location
  properties: {
    securityRules: [
      {
        name: 'PublicWeb'
        properties: {
          priority: 100
          access: 'Allow'
          direction: 'Inbound'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRanges: ['80', '443']
          sourceAddressPrefix: 'Internet'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}
resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${name}-network'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.42.0.0/16'] }
    subnets: [
      {
        name: 'web'
        properties: {
          addressPrefix: '10.42.1.0/24'
          networkSecurityGroup: { id: nsg.id }
        }
      }
    ]
  }
}
resource ip 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${name}-ip'
  location: location
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: { domainNameLabel: dnsLabel }
  }
}
resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: '${name}-nic'
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'web'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          subnet: { id: '${network.id}/subnets/web' }
          publicIPAddress: { id: ip.id }
        }
      }
    ]
  }
}
resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: name
  location: location
  properties: {
    hardwareProfile: { vmSize: vmSize }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: 64
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
        deleteOption: 'Delete'
      }
    }
    osProfile: {
      computerName: name
      adminUsername: adminUsername
      customData: base64(bootstrap)
      linuxConfiguration: {
        disablePasswordAuthentication: true
        provisionVMAgent: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    networkProfile: { networkInterfaces: [{ id: nic.id }] }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: { secureBootEnabled: true, vTpmEnabled: true }
    }
    diagnosticsProfile: { bootDiagnostics: { enabled: true } }
  }
}
output publicUrl string = 'https://${hostname}'
output vmName string = vm.name

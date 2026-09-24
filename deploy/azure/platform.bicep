@description('Consumption region (verify availability for the subscription; student subs block some regions).')
param location string = resourceGroup().location
@minLength(3)
@maxLength(12)
param prefix string = 'book2course'
@description('Public container image (GHCR), ideally pinned by digest.')
param appImage string
@description('Seeded admin username.')
param adminUsername string
@secure()
@description('Seeded admin password.')
param adminPassword string
@secure()
@description('Gemini API key for the AI tutor. Leave empty to run with the tutor offline.')
param geminiApiKey string = ''
param geminiModel string = 'gemini-2.5-flash'
@description('Container registry login server, e.g. myacr.azurecr.io.')
param registryServer string = ''
param registryUsername string = ''
@secure()
param registryPassword string = ''
@description('vCPU for the single always-on replica.')
param cpu string = '0.5'
@description('Memory for the single always-on replica.')
param memory string = '1Gi'
@description('Azure Files share size (GiB); billed on used data for Standard.')
param fileQuotaGb int = 100
@description('Monthly budget alert amount in the subscription billing currency (not a hard cap).')
param monthlyBudget int = 45
param budgetStart string
param budgetEnd string

var suffix = uniqueString(resourceGroup().id)
var appName = '${prefix}-app'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'b2c${suffix}'
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
  }
}
resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}
resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: 'data'
  properties: { shareQuota: fileQuotaGb, enabledProtocols: 'SMB' }
}

resource logs 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${prefix}-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}
// A Log Analytics workspace makes this a full environment (not the default Express),
// which is required for Azure Files storage mounts.
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}
resource envStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: environment
  name: 'data'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: 'data'
      accessMode: 'ReadWrite'
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      secrets: concat(
        [{ name: 'admin-password', value: adminPassword }],
        empty(geminiApiKey) ? [] : [{ name: 'gemini-api-key', value: geminiApiKey }],
        empty(registryPassword) ? [] : [{ name: 'registry-password', value: registryPassword }])
      registries: empty(registryServer) ? [] : [
        { server: registryServer, username: registryUsername, passwordSecretRef: 'registry-password' }
      ]
    }
    template: {
      containers: [{
        name: 'app'
        image: appImage
        resources: { cpu: json(cpu), memory: memory }
        volumeMounts: [{ volumeName: 'data', mountPath: '/data' }]
        env: concat([
          { name: 'ADMIN_USERNAME', value: adminUsername }
          { name: 'ADMIN_PASSWORD', secretRef: 'admin-password' }
          { name: 'TUTOR_PROVIDER', value: empty(geminiApiKey) ? '' : 'gemini' }
          { name: 'GEMINI_MODEL', value: geminiModel }
          { name: 'SQLITE_JOURNAL', value: 'DELETE' }
          { name: 'SEJONG_BEST_EFFORT_LOCKS', value: '1' }
          { name: 'PUBLIC_ORIGIN', value: 'https://${appName}.${environment.properties.defaultDomain}' }
          { name: 'MAX_UPLOAD_MB', value: '200' }
          { name: 'MAX_PAGES', value: '500' }
          { name: 'MAX_OUTPUT_MB', value: '1024' }
          { name: 'JOB_TIMEOUT_SECONDS', value: '2400' }
          { name: 'CLIENT_TUTOR_PER_DAY', value: '60' }
        ], empty(geminiApiKey) ? [] : [
          { name: 'GEMINI_API_KEY', secretRef: 'gemini-api-key' }
        ])
        probes: [{ type: 'Liveness', httpGet: { path: '/healthz', port: 8000 }, initialDelaySeconds: 20, periodSeconds: 30 }]
      }]
      volumes: [{ name: 'data', storageType: 'AzureFile', storageName: 'data' }]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [envStorage]
}

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: '${prefix}-budget'
  properties: {
    amount: monthlyBudget
    category: 'Cost'
    timeGrain: 'Monthly'
    timePeriod: { startDate: '${budgetStart}T00:00:00Z', endDate: '${budgetEnd}T00:00:00Z' }
    notifications: {
      Actual80: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 80, thresholdType: 'Actual', contactRoles: ['Owner'], contactEmails: [] }
      Actual100: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 100, thresholdType: 'Actual', contactRoles: ['Owner'], contactEmails: [] }
    }
  }
}

output url string = 'https://${app.properties.configuration.ingress.fqdn}'
output storageAccount string = storage.name

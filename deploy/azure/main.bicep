@description('Consumption region; verify availability for the subscription.')
param location string = resourceGroup().location
@minLength(3)
@maxLength(10)
param prefix string = 'sejong'
@description('Public GHCR API image pinned by digest.')
param apiImage string
@description('Public GHCR one-shot job image pinned by digest.')
param workerImage string
@description('Monthly tracking budget in subscription billing currency, not a hard spending cap.')
param monthlyBudget int
@description('First day of the current month in YYYY-MM-DD format.')
param budgetStart string
@description('Budget end date; choose at least one year after start.')
param budgetEnd string
@minValue(1)
@maxValue(100)
param dailyConversionLimit int = 10

var suffix = uniqueString(resourceGroup().id)
var apiName = '${prefix}-api'
var blobRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var queueRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${prefix}${suffix}'
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
  }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    isVersioningEnabled: false
    deleteRetentionPolicy: { enabled: false }
    containerDeleteRetentionPolicy: { enabled: false }
  }
}
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for name in ['control', 'uploads', 'exports']: {
  parent: blobs
  name: name
  properties: { publicAccess: 'None' }
}]
resource queues 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: storage
  name: 'default'
}
resource queue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queues
  name: 'conversions'
}
resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [{
        name: 'temporary-documents'
        enabled: true
        type: 'Lifecycle'
        definition: {
          filters: { blobTypes: ['blockBlob'], prefixMatch: ['uploads/', 'exports/'] }
          actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: 1 } } }
        }
      }]
    }
  }
}
resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-api-identity'
  location: location
}
resource workerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-job-identity'
  location: location
}
resource apiBlobs 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, apiIdentity.id, blobRole)
  scope: storage
  properties: { roleDefinitionId: blobRole, principalId: apiIdentity.properties.principalId, principalType: 'ServicePrincipal' }
}
resource apiQueue 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(queue.id, apiIdentity.id, queueRole)
  scope: queue
  properties: { roleDefinitionId: queueRole, principalId: apiIdentity.properties.principalId, principalType: 'ServicePrincipal' }
}
resource workerBlobs 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, workerIdentity.id, blobRole)
  scope: storage
  properties: { roleDefinitionId: blobRole, principalId: workerIdentity.properties.principalId, principalType: 'ServicePrincipal' }
}
resource workerQueue 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(queue.id, workerIdentity.id, queueRole)
  scope: queue
  properties: { roleDefinitionId: queueRole, principalId: workerIdentity.properties.principalId, principalType: 'ServicePrincipal' }
}
resource environment 'Microsoft.App/managedEnvironments@2026-01-01' = {
  name: '${prefix}-jobs-environment'
  kind: 'Standard'
  location: location
  properties: {
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}
var commonEnv = [
  { name: 'AZURE_STORAGE_ACCOUNT', value: storage.name }
  { name: 'AZURE_QUEUE_NAME', value: queue.name }
  { name: 'MAX_UPLOAD_MB', value: '200' }
  { name: 'MAX_PAGES', value: '500' }
  { name: 'MAX_OUTPUT_MB', value: '1024' }
  { name: 'RETENTION_HOURS', value: '24' }
  { name: 'DAILY_CONVERSION_LIMIT', value: string(dailyConversionLimit) }
  { name: 'MAX_ACTIVE_JOBS', value: '10' }
  { name: 'JOB_TIMEOUT_SECONDS', value: '1800' }
]
resource api 'Microsoft.App/containerApps@2025-01-01' = {
  name: apiName
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${apiIdentity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'http', allowInsecure: false }
    }
    template: {
      containers: [{
        name: 'api'
        image: apiImage
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: concat(commonEnv, [
          { name: 'APP_MODE', value: 'azure' }
          { name: 'AZURE_CLIENT_ID', value: apiIdentity.properties.clientId }
          { name: 'PUBLIC_ORIGIN', value: 'https://${apiName}.${environment.properties.defaultDomain}' }
          { name: 'TRUST_AZURE_INGRESS', value: '1' }
        ])
        probes: [{ type: 'Liveness', httpGet: { path: '/healthz', port: 8000 }, initialDelaySeconds: 20, periodSeconds: 30 }]
      }]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [{ name: 'http', http: { metadata: { concurrentRequests: '10' } } }]
      }
    }
  }
  dependsOn: [apiBlobs, apiQueue, containers]
}
resource job 'Microsoft.App/jobs@2025-01-01' = {
  name: '${prefix}-convert'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${workerIdentity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Event'
      replicaTimeout: 2100
      replicaRetryLimit: 0
      eventTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
        scale: {
          minExecutions: 0
          maxExecutions: 1
          pollingInterval: 60
          rules: [{
            name: 'storage-queue'
            type: 'azure-queue'
            identity: workerIdentity.id
            metadata: { accountName: storage.name, queueName: queue.name, queueLength: '1', queueLengthStrategy: 'visibleonly' }
          }]
        }
      }
    }
    template: {
      containers: [{
        name: 'converter'
        image: workerImage
        resources: { cpu: 1, memory: '2Gi' }
        env: concat(commonEnv, [{ name: 'AZURE_CLIENT_ID', value: workerIdentity.properties.clientId }])
      }]
    }
  }
  dependsOn: [workerBlobs, workerQueue, containers]
}
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: '${prefix}-monthly-budget'
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
output publicUrl string = 'https://${api.properties.configuration.ingress.fqdn}'
output jobName string = job.name
output storageAccount string = storage.name

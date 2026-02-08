# Azure Security Skill

Security-by-default patterns for Azure Infrastructure as Code.

## When to Load This Skill

- Generating any Azure infrastructure (Bicep or Terraform)
- Security requirements in ADRs
- Compliance-sensitive deployments

## Frameworks

| Framework      | Purpose                        | Reference                                                                                             |
| -------------- | ------------------------------ | ----------------------------------------------------------------------------------------------------- |
| **Zero Trust** | Never trust, always verify     | [Zero Trust](https://learn.microsoft.com/security/zero-trust/)                                        |
| **SFI**        | Secure Future Initiative       | [SFI Overview](https://learn.microsoft.com/security/zero-trust/sfi/secure-future-initiative-overview) |
| **CAF**        | Landing zones, naming, tagging | [Cloud Adoption Framework](https://learn.microsoft.com/azure/cloud-adoption-framework/)               |
| **AVM**        | Pre-built secure modules       | [Azure Verified Modules](https://azure.github.io/Azure-Verified-Modules/)                             |
| **WAF**        | Security pillar                | [Well-Architected Framework](https://learn.microsoft.com/azure/well-architected/)                     |

## Zero Trust Principles

Apply these principles to **all** infrastructure:

| Principle             | Implementation                            | IaC Pattern                            |
| --------------------- | ----------------------------------------- | -------------------------------------- |
| **Verify explicitly** | Managed identities, RBAC, MFA             | `identity: { type: 'SystemAssigned' }` |
| **Least privilege**   | Minimal permissions, no Owner/Contributor | Role assignments with specific scopes  |
| **Assume breach**     | Network segmentation, private endpoints   | `publicNetworkAccess: 'Disabled'`      |

### Zero Trust Network Access

- **No public endpoints** unless explicitly required and justified
- **Private endpoints** for all PaaS services (Storage, Key Vault, SQL, etc.)
- **Network segmentation** with NSGs and private subnets
- **Service endpoints** as fallback when private endpoints not available

### Zero Trust Identity

- **Managed identity** over connection strings or keys
- **RBAC** over shared access keys
- **Conditional Access** for user authentication
- **Just-in-time access** for admin operations

### Zero Trust Data

- **Encryption at rest** (customer-managed keys for sensitive data)
- **Encryption in transit** (TLS 1.2+ mandatory)
- **Data classification** via tags and policies

## Security Controls (Apply by Default)

| Control    | Default             | Bicep                                  | Terraform                               |
| ---------- | ------------------- | -------------------------------------- | --------------------------------------- |
| Identity   | Managed Identity    | `identity: { type: 'SystemAssigned' }` | `identity { type = "SystemAssigned" }`  |
| Network    | Private endpoints   | `publicNetworkAccess: 'Disabled'`      | `public_network_access_enabled = false` |
| Encryption | TLS 1.2+            | `minTlsVersion: 'TLS1_2'`              | `min_tls_version = "TLS1_2"`            |
| HTTPS      | Required            | `httpsOnly: true`                      | `https_only = true`                     |
| Logging    | Diagnostic settings | Deploy `diagnosticSettings` resource   | `azurerm_monitor_diagnostic_setting`    |

## Azure Verified Modules (AVM)

**ALWAYS prefer AVM over custom code** - pre-built with security best practices.

### Bicep AVM

Registry: `br/public:avm/res/<provider>/<resource>:<version>`

```bicep
// Storage Account with security defaults
module storage 'br/public:avm/res/storage/storage-account:0.9.0' = {
  name: 'storageDeployment'
  params: {
    name: 'st${workload}${env}001'
    location: location
    skuName: 'Standard_LRS'
    // AVM applies: private endpoints, encryption, HTTPS, TLS 1.2
  }
}

// Key Vault with security defaults
module keyVault 'br/public:avm/res/key-vault/vault:0.6.0' = {
  name: 'kvDeployment'
  params: {
    name: 'kv-${workload}-${env}'
    // AVM applies: RBAC, soft delete, purge protection
  }
}
```

### Terraform AVM

Source: `Azure/avm-res-<provider>-<resource>/azurerm`

```hcl
# Storage Account with security defaults
module "storage" {
  source  = "Azure/avm-res-storage-storageaccount/azurerm"
  version = "0.1.0"

  name                = "st${var.workload}${var.env}001"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  # AVM applies: private endpoints, encryption, HTTPS, TLS 1.2
}

# Key Vault with security defaults
module "keyvault" {
  source  = "Azure/avm-res-keyvault-vault/azurerm"
  version = "0.5.0"

  name                = "kv-${var.workload}-${var.env}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  # AVM applies: RBAC, soft delete, purge protection
}
```

## CAF Naming Convention

Pattern: `<resource-type>-<workload>-<environment>-<region>-<instance>`

| Resource        | Abbreviation | Example                       |
| --------------- | ------------ | ----------------------------- |
| Resource Group  | `rg`         | `rg-myapp-prod-eastus-001`    |
| Storage Account | `st`         | `stmyappprod001` (no hyphens) |
| Key Vault       | `kv`         | `kv-myapp-prod`               |
| App Service     | `app`        | `app-myapp-prod-eastus-001`   |
| SQL Database    | `sqldb`      | `sqldb-myapp-prod`            |
| Virtual Network | `vnet`       | `vnet-myapp-prod-eastus-001`  |

Reference: [CAF Naming](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-naming)

## CAF Tagging Strategy

**Mandatory tags** (include on all resources):

```bicep
var tags = {
  environment: 'prod'           // dev, staging, prod
  owner: 'team-platform'        // Team or individual
  costCenter: 'CC-12345'        // Billing code
  application: 'myapp'          // Workload name
  createdBy: 'infrastructure'   // Deployment method
}
```

Reference: [CAF Tagging](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-tagging)

## Security Checklist

Before generating code, verify:

- [ ] No hardcoded secrets (use Key Vault or managed identity)
- [ ] Private endpoints enabled (public access disabled)
- [ ] Managed identity configured (no connection strings with keys)
- [ ] TLS 1.2 minimum enforced
- [ ] HTTPS only enabled
- [ ] Diagnostic settings configured (Log Analytics)
- [ ] RBAC used over shared keys
- [ ] Network segmentation applied (NSGs, private subnets)

## Common Security Patterns

### Private Endpoint Pattern

```bicep
resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-05-01' = {
  name: 'pe-${resourceName}'
  location: location
  properties: {
    subnet: { id: subnetId }
    privateLinkServiceConnections: [{
      name: 'plsc-${resourceName}'
      properties: {
        privateLinkServiceId: targetResourceId
        groupIds: ['blob'] // or 'vault', 'sites', etc.
      }
    }]
  }
}
```

### Managed Identity Pattern

```bicep
resource appService 'Microsoft.Web/sites@2023-01-01' = {
  name: appName
  identity: {
    type: 'SystemAssigned'
  }
  // Then grant RBAC to this identity instead of using keys
}
```

### Key Vault Reference Pattern

```bicep
// Reference secret without exposing value
module app 'app.bicep' = {
  params: {
    connectionString: keyVault.getSecret('connection-string')
  }
}
```

#################################################################################
# Observability Services
#################################################################################

module "log_analytics" {
  source                                             = "Azure/avm-res-operationalinsights-workspace/azurerm"
  name                                               = "${local.identifier}-law"
  resource_group_name                                = azurerm_resource_group.private_rg.name
  location                                           = azurerm_resource_group.private_rg.location
  log_analytics_workspace_internet_ingestion_enabled = true
  log_analytics_workspace_internet_query_enabled     = true
  tags                                               = local.tags
}

module "application_insights" {
  source              = "Azure/avm-res-insights-component/azurerm"
  name                = "${local.identifier}-appi"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location
  workspace_id        = module.log_analytics.resource_id
  application_type    = "web"
  tags                = local.tags
}


#################################################################################
# Container Registry (private)
#################################################################################

module "container_registry" {
  source                        = "Azure/avm-res-containerregistry-registry/azurerm"
  name                          = replace("${local.identifier}acr", "-", "")
  resource_group_name           = azurerm_resource_group.private_rg.name
  location                      = azurerm_resource_group.private_rg.location
  sku                           = "Standard"
  zone_redundancy_enabled       = false
  public_network_access_enabled = false
  admin_enabled                 = false
  tags                          = local.tags

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
    }
  }

  private_endpoints = {
    acr = {
      subnet_resource_id            = azurerm_subnet.private_endpoints.id
      private_dns_zone_resource_ids = [azurerm_private_dns_zone.this["privatelink.azurecr.io"].id]
    }
  }
}


#################################################################################
# Storage Account for Microsoft Foundry blob uploads and NL2SQL data (private)
#################################################################################

module "ai_storage" {
  source                        = "Azure/avm-res-storage-storageaccount/azurerm"
  name                          = replace("${local.identifier}foundry", "-", "")
  resource_group_name           = azurerm_resource_group.private_rg.name
  location                      = var.region_aifoundry
  account_tier                  = "Standard"
  account_replication_type      = "LRS"
  public_network_access_enabled = false
  shared_access_key_enabled     = false
  tags                          = local.tags

  containers = {
    nl2sql = {
      name                  = "nl2sql"
      container_access_type = "private"
    }
  }

  private_endpoints = {
    blob = {
      subnet_resource_id            = azurerm_subnet.private_endpoints.id
      subresource_name              = "blob"
      private_dns_zone_resource_ids = [azurerm_private_dns_zone.this["privatelink.blob.core.windows.net"].id]
    }
  }

  role_assignments = {
    storage_blob_contributor = {
      role_definition_id_or_name = "Storage Blob Data Contributor"
      principal_id               = data.azurerm_client_config.current.object_id
    }
  }
}

resource "time_sleep" "wait_for_storage_rbac" {
  depends_on      = [module.ai_storage]
  create_duration = "60s"
}

resource "azurerm_storage_blob" "nl2sql_tables" {
  for_each               = fileset("${path.module}/../data/tables", "**/*.json")
  name                   = "tables/${each.value}"
  storage_account_name   = module.ai_storage.name
  storage_container_name = "nl2sql"
  type                   = "Block"
  source                 = "${path.module}/../data/tables/${each.value}"
  content_type           = "application/json"

  depends_on = [time_sleep.wait_for_storage_rbac]
}

resource "azurerm_storage_blob" "nl2sql_query_templates" {
  for_each               = fileset("${path.module}/../data/query_templates", "*.json")
  name                   = "query_templates/${each.value}"
  storage_account_name   = module.ai_storage.name
  storage_container_name = "nl2sql"
  type                   = "Block"
  source                 = "${path.module}/../data/query_templates/${each.value}"
  content_type           = "application/json"

  depends_on = [time_sleep.wait_for_storage_rbac]
}


#################################################################################
# Cosmos DB Account for Microsoft Foundry agent service thread storage (private)
#################################################################################

module "ai_cosmosdb" {
  source                        = "Azure/avm-res-documentdb-databaseaccount/azurerm"
  name                          = "${local.identifier}-foundry"
  resource_group_name           = azurerm_resource_group.private_rg.name
  location                      = var.region_aifoundry
  public_network_access_enabled = false
  analytical_storage_enabled    = true
  automatic_failover_enabled    = true

  geo_locations = [
    {
      location          = var.region_aifoundry
      failover_priority = 0
      zone_redundant    = false
    }
  ]

  private_endpoints = {
    cosmosdb = {
      subnet_resource_id            = azurerm_subnet.private_endpoints.id
      subresource_name              = "SQL"
      private_dns_zone_resource_ids = [azurerm_private_dns_zone.this["privatelink.documents.azure.com"].id]
    }
  }

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
      metric_categories     = ["SLI", "Requests"]
    }
  }

  tags = local.tags
}

resource "azurerm_cosmosdb_sql_role_assignment" "current_user" {
  resource_group_name = azurerm_resource_group.private_rg.name
  account_name        = module.ai_cosmosdb.name
  role_definition_id  = "${module.ai_cosmosdb.resource_id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = data.azurerm_client_config.current.object_id
  scope               = module.ai_cosmosdb.resource_id
}


#################################################################################
# AI Search - linked to Microsoft Foundry (private)
#################################################################################

module "ai_search" {
  source                        = "Azure/avm-res-search-searchservice/azurerm"
  name                          = local.identifier
  resource_group_name           = azurerm_resource_group.private_rg.name
  location                      = var.region_search
  sku                           = "basic"
  public_network_access_enabled = false
  local_authentication_enabled  = true
  authentication_failure_mode   = "http401WithBearerChallenge"
  tags                          = local.tags

  managed_identities = {
    system_assigned = true
  }

  private_endpoints = {
    search = {
      subnet_resource_id            = azurerm_subnet.private_endpoints.id
      private_dns_zone_resource_ids = [azurerm_private_dns_zone.this["privatelink.search.windows.net"].id]
    }
  }

  role_assignments = {
    search_service_contributor = {
      role_definition_id_or_name = "Search Service Contributor"
      principal_id               = data.azurerm_client_config.current.object_id
    }
    search_index_data_reader = {
      role_definition_id_or_name = "Search Index Data Reader"
      principal_id               = data.azurerm_client_config.current.object_id
    }
  }

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
    }
  }
}


#################################################################################
# AI Foundry (Standard Private + Agent subnet injection)
#################################################################################

module "ai_foundry" {
  source  = "Azure/avm-ptn-aiml-ai-foundry/azurerm"
  version = "~> 0.8.0"

  base_name                  = local.identifier
  location                   = var.region_aifoundry
  resource_group_resource_id = azurerm_resource_group.private_rg.id

  tags = local.tags

  create_byor                         = false
  create_private_endpoints            = true
  private_endpoint_subnet_resource_id = azurerm_subnet.private_endpoints.id

  ai_foundry = {
    create_ai_agent_service = true
    private_dns_zone_resource_ids = [
      azurerm_private_dns_zone.this["privatelink.openai.azure.com"].id,
      azurerm_private_dns_zone.this["privatelink.cognitiveservices.azure.com"].id,
      azurerm_private_dns_zone.this["privatelink.services.ai.azure.com"].id
    ]
    network_injections = [{
      scenario                   = "agent"
      subnetArmId                = azurerm_subnet.ai_agent_services.id
      useMicrosoftManagedNetwork = false
    }]
  }

  ai_projects = {
    cadence = {
      name                       = "dataexplorer"
      display_name               = "Data Exploration"
      description                = "Data exploration agents and related resources"
      create_project_connections = true
      cosmos_db_connection = {
        existing_resource_id = module.ai_cosmosdb.resource_id
      }
      storage_account_connection = {
        existing_resource_id = module.ai_storage.resource_id
      }
      ai_search_connection = {
        existing_resource_id = module.ai_search.resource_id
      }
    }
  }

  cosmosdb_definition = {
    byor = {
      existing_resource_id = module.ai_cosmosdb.resource_id
    }
  }

  storage_account_definition = {
    byor = {
      existing_resource_id = module.ai_storage.resource_id
    }
  }

  ai_search_definition = {
    byor = {
      existing_resource_id = module.ai_search.resource_id
    }
  }

  depends_on = [
    module.ai_storage,
    module.ai_cosmosdb,
    module.ai_search
  ]
}

resource "azurerm_cosmosdb_sql_role_assignment" "foundry_project" {
  resource_group_name = azurerm_resource_group.private_rg.name
  account_name        = module.ai_cosmosdb.name
  role_definition_id  = "${module.ai_cosmosdb.resource_id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = module.ai_foundry.ai_foundry_project_system_identity_principal_id["cadence"]
  scope               = module.ai_cosmosdb.resource_id
}


#################################################################################
# AI Model Deployments
#################################################################################

resource "azapi_resource" "ai_model_deployment_gpt5" {
  name      = "gpt-5-chat"
  parent_id = module.ai_foundry.ai_foundry_id
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  body = {
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-5-chat"
        version = "2025-10-03"
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 150
    }
  }
  schema_validation_enabled = false

  depends_on = [module.ai_foundry]
}

resource "azapi_resource" "ai_model_deployment_gpt52" {
  name      = "gpt-5.2-chat"
  parent_id = module.ai_foundry.ai_foundry_id
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  body = {
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-5.2-chat"
        version = "2025-12-11"
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 150
    }
  }
  schema_validation_enabled = false

  depends_on = [azapi_resource.ai_model_deployment_gpt5]
}

resource "azapi_resource" "ai_model_deployment_embedding_small" {
  name      = "embedding-small"
  parent_id = module.ai_foundry.ai_foundry_id
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  body = {
    properties = {
      model = {
        format  = "OpenAI"
        name    = "text-embedding-3-small"
        version = "1"
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 150
    }
  }
  schema_validation_enabled = false

  depends_on = [azapi_resource.ai_model_deployment_gpt52]
}

resource "azapi_resource" "ai_model_deployment_embedding_large" {
  name      = "embedding-large"
  parent_id = module.ai_foundry.ai_foundry_id
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  body = {
    properties = {
      model = {
        format  = "OpenAI"
        name    = "text-embedding-3-large"
        version = "1"
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 120
    }
  }
  schema_validation_enabled = false

  depends_on = [azapi_resource.ai_model_deployment_embedding_small]
}

resource "azapi_resource" "ai_model_deployment_gpt41" {
  name      = "gpt-4.1"
  parent_id = module.ai_foundry.ai_foundry_id
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  body = {
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-4.1"
        version = "2025-04-14"
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 150
    }
  }
  schema_validation_enabled = false

  depends_on = [azapi_resource.ai_model_deployment_embedding_large]
}

resource "azapi_resource" "ai_model_deployment_gpt41_mini" {
  name      = "gpt-4.1-mini"
  parent_id = module.ai_foundry.ai_foundry_id
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  body = {
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-4.1-mini"
        version = "2025-04-14"
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 150
    }
  }
  schema_validation_enabled = false

  depends_on = [azapi_resource.ai_model_deployment_gpt41]
}


#################################################################################
# Azure SQL Database (private endpoint + no public access)
#################################################################################

module "sql_server" {
  source              = "Azure/avm-res-sql-server/azurerm"
  name                = "${local.identifier}-sql"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location
  server_version      = "12.0"
  tags                = local.tags

  azuread_administrator = {
    azuread_authentication_only = true
    login_username              = data.azurerm_client_config.current.client_id
    object_id                   = data.azurerm_client_config.current.object_id
    tenant_id                   = data.azurerm_client_config.current.tenant_id
  }

  databases = {
    wideworldimporters = {
      name        = var.sql_database_name
      sku_name    = "S0"
      max_size_gb = 250
    }
  }

  public_network_access_enabled = false

  private_endpoints = {
    sql = {
      subnet_resource_id            = azurerm_subnet.private_endpoints.id
      subresource_name              = "sqlServer"
      private_dns_zone_resource_ids = [azurerm_private_dns_zone.this["privatelink.database.windows.net"].id]
    }
  }
}

resource "null_resource" "import_wideworldimporters" {
  count      = var.enable_local_exec_provisioning ? 1 : 0
  depends_on = [module.sql_server]

  triggers = {
    sql_server_name = module.sql_server.resource.name
    database_name   = var.sql_database_name
  }

  provisioner "local-exec" {
    interpreter = ["pwsh", "-Command"]
    command     = "& '${path.module}/../scripts/import-wideworldimporters.ps1' -SqlServerName '${module.sql_server.resource.name}' -DatabaseName '${var.sql_database_name}' -ResourceGroup '${azurerm_resource_group.private_rg.name}' -Force"
  }
}


#################################################################################
# Search configuration (private data-plane provisioning)
#################################################################################

resource "null_resource" "search_config" {
  count = var.enable_local_exec_provisioning ? 1 : 0

  depends_on = [
    module.ai_search,
    module.ai_storage,
    module.ai_foundry,
    azurerm_storage_blob.nl2sql_tables,
    azurerm_storage_blob.nl2sql_query_templates
  ]

  triggers = {
    search_name      = module.ai_search.resource.name
    storage_id       = module.ai_storage.resource_id
    ai_foundry_id    = module.ai_foundry.ai_foundry_id
    ai_services_name = module.ai_foundry.ai_foundry_name
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      SEARCH_NAME="${module.ai_search.resource.name}"
      SEARCH_URL="https://$${SEARCH_NAME}.search.windows.net"
      API_VERSION="2024-05-01-preview"
      STORAGE_RESOURCE_ID="${module.ai_storage.resource_id}"
      AI_FOUNDRY_ID="${module.ai_foundry.ai_foundry_id}"
      AI_SERVICES_NAME="${module.ai_foundry.ai_foundry_name}"

      SEARCH_PRINCIPAL_ID=$(az search service show --name "$${SEARCH_NAME}" --resource-group "${azurerm_resource_group.private_rg.name}" --query identity.principalId -o tsv)

      az role assignment create \
        --role "Storage Blob Data Reader" \
        --assignee-object-id "$${SEARCH_PRINCIPAL_ID}" \
        --assignee-principal-type ServicePrincipal \
        --scope "$${STORAGE_RESOURCE_ID}" \
        --only-show-errors || true

      az role assignment create \
        --role "Cognitive Services OpenAI User" \
        --assignee-object-id "$${SEARCH_PRINCIPAL_ID}" \
        --assignee-principal-type ServicePrincipal \
        --scope "$${AI_FOUNDRY_ID}" \
        --only-show-errors || true

      sleep 60

      TOKEN=$(az account get-access-token --resource https://search.azure.com --query accessToken -o tsv)

      curl -s -X PUT "$${SEARCH_URL}/datasources/agentic-tables?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{"name":"agentic-tables","type":"azureblob","credentials":{"connectionString":"ResourceId='"$${STORAGE_RESOURCE_ID}"';"},"container":{"name":"nl2sql","query":"tables"}}'

      curl -s -X PUT "$${SEARCH_URL}/datasources/agentic-query-templates?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{"name":"agentic-query-templates","type":"azureblob","credentials":{"connectionString":"ResourceId='"$${STORAGE_RESOURCE_ID}"';"},"container":{"name":"nl2sql","query":"query_templates"}}'

      curl -s -X PUT "$${SEARCH_URL}/indexes/tables?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d @${path.module}/../search-config/tables_index.json

      curl -s -X PUT "$${SEARCH_URL}/indexes/query_templates?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d @${path.module}/../search-config/query_templates_index.json

      curl -s -X PUT "$${SEARCH_URL}/skillsets/table-embed-skill?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{"name":"table-embed-skill","description":"OpenAI Embedding skill for table descriptions","skills":[{"@odata.type":"#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill","name":"vector-embed-field-description","description":"vector embedding for the description field","context":"/document","resourceUri":"https://'"$${AI_SERVICES_NAME}"'.openai.azure.com","deploymentId":"embedding-large","dimensions":3072,"modelName":"text-embedding-3-large","inputs":[{"name":"text","source":"/document/description"}],"outputs":[{"name":"embedding","targetName":"content_embeddings"}]}]}'

      curl -s -X PUT "$${SEARCH_URL}/skillsets/query-template-embed-skill?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{"name":"query-template-embed-skill","description":"OpenAI Embedding skill for query template questions","skills":[{"@odata.type":"#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill","name":"vector-embed-field-question","description":"vector embedding for the question field","context":"/document","resourceUri":"https://'"$${AI_SERVICES_NAME}"'.openai.azure.com","deploymentId":"embedding-large","dimensions":3072,"modelName":"text-embedding-3-large","inputs":[{"name":"text","source":"/document/question"}],"outputs":[{"name":"embedding","targetName":"content_embeddings"}]}]}'

      curl -s -X PUT "$${SEARCH_URL}/indexers/indexer-tables?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{"name":"indexer-tables","dataSourceName":"agentic-tables","skillsetName":"table-embed-skill","targetIndexName":"tables","parameters":{"configuration":{"dataToExtract":"contentAndMetadata","parsingMode":"json"}},"fieldMappings":[],"outputFieldMappings":[{"sourceFieldName":"/document/content_embeddings","targetFieldName":"content_vector"}]}'

      curl -s -X PUT "$${SEARCH_URL}/indexers/indexer-query-templates?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{"name":"indexer-query-templates","dataSourceName":"agentic-query-templates","skillsetName":"query-template-embed-skill","targetIndexName":"query_templates","parameters":{"configuration":{"dataToExtract":"contentAndMetadata","parsingMode":"json"}},"fieldMappings":[],"outputFieldMappings":[{"sourceFieldName":"/document/content_embeddings","targetFieldName":"content_vector"}]}'
    EOT
  }
}


#################################################################################
# Container App Environment (VNet injected + scalable workload profile)
#################################################################################

module "container_app_environment" {
  source  = "Azure/avm-res-app-managedenvironment/azurerm"
  version = "~> 0.4"

  name                = "${local.identifier}-cae"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location

  log_analytics_workspace = {
    resource_id = module.log_analytics.resource_id
  }

  infrastructure_subnet_id       = azurerm_subnet.container_apps.id
  internal_load_balancer_enabled = false
  public_network_access_enabled  = true

  workload_profile = [
    {
      name                  = "Dedicated"
      workload_profile_type = "D4"
      minimum_count         = 1
      maximum_count         = 3
    }
  ]

  zone_redundancy_enabled = false
  tags                    = local.tags
}


#################################################################################
# Container App for Backend API
#################################################################################

data "azapi_resource" "ai_foundry_hub" {
  type                   = "Microsoft.CognitiveServices/accounts@2024-10-01"
  resource_id            = module.ai_foundry.ai_foundry_id
  response_export_values = ["properties.endpoint"]
}

locals {
  ai_hub_endpoint     = data.azapi_resource.ai_foundry_hub.output.properties.endpoint
  ai_project_name     = module.ai_foundry.ai_foundry_project_name["cadence"]
  ai_project_endpoint = "${trimsuffix(local.ai_hub_endpoint, "/")}/api/projects/${local.ai_project_name}"
}

resource "azurerm_user_assigned_identity" "api_identity" {
  name                = "${local.identifier}-api-identity"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "api_acr_pull" {
  scope                = module.container_registry.resource_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_ai_foundry_developer_containerapp" {
  scope                = module.ai_foundry.ai_foundry_id
  role_definition_name = "Azure AI Developer"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_cognitive_services_user" {
  scope                = module.ai_foundry.ai_foundry_id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_ai_foundry_project" {
  scope                = module.ai_foundry.ai_foundry_project_id["cadence"]
  role_definition_name = "Azure AI Developer"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_search" {
  scope                = module.ai_search.resource_id
  role_definition_name = "Search Index Data Reader"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_sql" {
  scope                = module.sql_server.resource_id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_storage" {
  scope                = module.ai_storage.resource_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_cosmos" {
  scope                = module.ai_cosmosdb.resource_id
  role_definition_name = "Cosmos DB Account Reader Role"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_container_app" "api" {
  name                         = "${local.identifier}-api"
  resource_group_name          = azurerm_resource_group.private_rg.name
  container_app_environment_id = module.container_app_environment.resource_id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api_identity.id]
  }

  registry {
    server   = module.container_registry.resource.login_server
    identity = azurerm_user_assigned_identity.api_identity.id
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "http"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "api"
      image  = "mcr.microsoft.com/k8se/quickstart:latest"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.api_identity.client_id
      }
      env {
        name  = "AZURE_AD_TENANT_ID"
        value = data.azurerm_client_config.current.tenant_id
      }
      env {
        name  = "AZURE_AD_CLIENT_ID"
        value = var.frontend_app_client_id
      }
      env {
        name  = "AZURE_AI_PROJECT_ENDPOINT"
        value = local.ai_project_endpoint
      }
      env {
        name  = "AZURE_AI_MODEL_DEPLOYMENT_NAME"
        value = "gpt-5-chat"
      }
      env {
        name  = "AZURE_AI_EMBEDDING_DEPLOYMENT"
        value = "embedding-large"
      }
      env {
        name  = "AZURE_SEARCH_ENDPOINT"
        value = "https://${module.ai_search.resource.name}.search.windows.net"
      }
      env {
        name  = "AZURE_SEARCH_INDEX_TABLES"
        value = "tables"
      }
      env {
        name  = "AZURE_SEARCH_INDEX_QUERY_TEMPLATES"
        value = "query_templates"
      }
      env {
        name  = "AZURE_SQL_SERVER"
        value = module.sql_server.resource.fully_qualified_domain_name
      }
      env {
        name  = "AZURE_SQL_DATABASE"
        value = var.sql_database_name
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = module.application_insights.connection_string
      }
      env {
        name  = "ENABLE_INSTRUMENTATION"
        value = "true"
      }
      env {
        name  = "ENABLE_SENSITIVE_DATA"
        value = "true"
      }
      env {
        name  = "QUERY_TEMPLATE_CONFIDENCE_THRESHOLD"
        value = "0.80"
      }
      env {
        name  = "QUERY_TEMPLATE_AMBIGUITY_GAP"
        value = "0.05"
      }
      env {
        name  = "AZURE_AI_CHAT_MODEL"
        value = "gpt-4.1"
      }
      env {
        name  = "AZURE_AI_NL2SQL_MODEL"
        value = "gpt-4.1-mini"
      }
      env {
        name  = "AZURE_AI_PARAM_EXTRACTOR_MODEL"
        value = "gpt-4.1-mini"
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].container[0].image
    ]
  }

  depends_on = [
    azurerm_role_assignment.api_acr_pull,
    azurerm_role_assignment.api_ai_foundry_developer_containerapp,
    azurerm_role_assignment.api_search,
    azurerm_role_assignment.api_storage
  ]
}

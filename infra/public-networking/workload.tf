##################################################
# Observability Module
##################################################

# Log Analytics Workspace (shared)
module "log_analytics" {
  source  = "Azure/avm-res-operationalinsights-workspace/azurerm"
  name                = "${local.identifier}-law"
  resource_group_name = azurerm_resource_group.shared_rg.name
  location            = azurerm_resource_group.shared_rg.location
  tags                = local.tags
}

# Application Insights
module "application_insights" {
  source  = "Azure/avm-res-insights-component/azurerm"
  name                = "${local.identifier}-appi"
  resource_group_name = azurerm_resource_group.shared_rg.name
  location            = azurerm_resource_group.shared_rg.location
  workspace_id        = module.log_analytics.resource_id
  application_type    = "web"
  tags                = local.tags
}


##################################################
# Container Registry
##################################################

module "container_registry" {
  source  = "Azure/avm-res-containerregistry-registry/azurerm"
  name                          = replace("${local.identifier}acr", "-", "")
  resource_group_name           = azurerm_resource_group.shared_rg.name
  location                      = azurerm_resource_group.shared_rg.location
  sku                           = "Standard"
  zone_redundancy_enabled       = false
  public_network_access_enabled = true
  admin_enabled                 = false
  tags                          = local.tags

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
    }
  }
}


##################################################
# AI Foundry BYOR Resources (created separately due to AVM bug)
# See: https://github.com/Azure/terraform-azurerm-avm-ptn-aiml-ai-foundry/issues
##################################################

# Key Vault for AI Foundry
module "ai_keyvault" {
  source  = "Azure/avm-res-keyvault-vault/azurerm"
  name                              = "${local.identifier}-kv"
  resource_group_name               = azurerm_resource_group.shared_rg.name
  location                          = var.region_aifoundry
  tenant_id                         = data.azurerm_client_config.current.tenant_id
  sku_name                          = "standard"
  public_network_access_enabled     = true
  tags                              = local.tags

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
    }
  }
}

# Storage Account for AI Foundry
module "ai_storage" {
  source  = "Azure/avm-res-storage-storageaccount/azurerm"
  name                          = replace("${local.identifier}foundry", "-", "")
  resource_group_name           = azurerm_resource_group.shared_rg.name
  location                      = var.region_aifoundry
  account_tier                  = "Standard"
  account_replication_type      = "LRS"
  public_network_access_enabled = true
  shared_access_key_enabled     = false
  tags                          = local.tags
}

# Cosmos DB for AI Foundry
module "ai_cosmosdb" {
  source  = "Azure/avm-res-documentdb-databaseaccount/azurerm"
  name                          = "${local.identifier}-foundry"
  resource_group_name           = azurerm_resource_group.shared_rg.name
  location                      = var.region_aifoundry
  public_network_access_enabled = true
  analytical_storage_enabled    = true
  automatic_failover_enabled    = true
  
  geo_locations = [
    {
      location          = var.region_aifoundry
      failover_priority = 0
      zone_redundant    = false
    }
  ]

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
      metric_categories = ["SLI", "Requests"]
    }
  }

  tags = local.tags
}

# Cosmos DB Data Contributor role assignment for current user
resource "azurerm_cosmosdb_sql_role_assignment" "current_user" {
  resource_group_name = azurerm_resource_group.shared_rg.name
  account_name        = module.ai_cosmosdb.name
  # Built-in Data Contributor role: 00000000-0000-0000-0000-000000000002
  role_definition_id  = "${module.ai_cosmosdb.resource_id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = data.azurerm_client_config.current.object_id
  scope               = module.ai_cosmosdb.resource_id
}

# Cosmos DB Data Contributor role assignment for the Foundry project managed identity
resource "azurerm_cosmosdb_sql_role_assignment" "foundry_project" {
  resource_group_name = azurerm_resource_group.shared_rg.name
  account_name        = module.ai_cosmosdb.name
  # Built-in Data Contributor role: 00000000-0000-0000-0000-000000000002
  role_definition_id  = "${module.ai_cosmosdb.resource_id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = module.ai_foundry.ai_foundry_project_system_identity_principal_id["dataagent"]
  scope               = module.ai_cosmosdb.resource_id
}


# AI Search for AI Foundry
module "ai_search" {
  source  = "Azure/avm-res-search-searchservice/azurerm"
  name                          = "${local.identifier}"
  resource_group_name           = azurerm_resource_group.shared_rg.name
  location                      = var.region_aifoundry
  sku                           = "standard"
  public_network_access_enabled = true
  local_authentication_enabled  = true
  tags                          = local.tags

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
    }
  }
}


##################################################
# AI Foundry (Pattern Module)
##################################################

module "ai_foundry" {
  source  = "Azure/avm-ptn-aiml-ai-foundry/azurerm"
  version = "~> 0.8.0"

  base_name                  = local.identifier
  location                   = var.region_aifoundry
  resource_group_resource_id = azurerm_resource_group.shared_rg.id

  tags = local.tags

  # Disable BYOR creation - using existing resources via project connections only
  # Note: The *_definition blocks have bugs in AVM 0.8.0, so we skip them
  create_byor = false

  # AI Foundry configuration - enable agent service for thread storage in Cosmos DB
  ai_foundry = {
    create_ai_agent_service = true
  }

  # AI Projects configuration
  ai_projects = {
    dataagent = {
      name                       = "dataexplorer"
      display_name               = "Data Exploration"
      description                = "Data exploration agents and related resources"
      create_project_connections = true
      cosmos_db_connection = {
        existing_resource_id = module.ai_cosmosdb.resource_id
      }
      key_vault_connection = {
        existing_resource_id = module.ai_keyvault.resource_id
      }
      storage_account_connection = {
        existing_resource_id = module.ai_storage.resource_id
      }
      ai_search_connection = {
        existing_resource_id = module.ai_search.resource_id
      }
    }
  }

  # AI Model Deployments (OpenAI)
  ai_model_deployments = {
    chat52 = {
      name = "gpt-5-chat"
      model = {
        format  = "OpenAI"
        name    = "gpt-5-chat"
        version = "2025-10-03"
      }
      scale = {
        type     = "GlobalStandard"
        capacity = 150
      }
    }
    chat52 = {
      name = "gpt-5.2-chat"
      model = {
        format  = "OpenAI"
        name    = "gpt-5.2-chat"
        version = "2025-12-11"
      }
      scale = {
        type     = "GlobalStandard"
        capacity = 150
      }
    }
    embedding-small = {
      name = "embedding-small"
      model = {
        format  = "OpenAI"
        name    = "text-embedding-3-small"
        version = "1"
      }
      scale = {
        type     = "GlobalStandard"
        capacity = 150
      }
    }
    embedding-large = {
      name = "embedding-large"
      model = {
        format  = "OpenAI"
        name    = "text-embedding-3-large"
        version = "1"
      }
      scale = {
        type     = "GlobalStandard"
        capacity = 120
      }
    }
  }

  depends_on = [
    module.ai_keyvault,
    module.ai_storage,
    module.ai_cosmosdb
  ]
}


##################################################
# Application Insights Connection for AI Foundry Project
# Note: Not yet supported in AVM module. The azapi approach below is not working
# with the current API version. Add this connection manually via Azure Portal:
# AI Foundry Project -> Connections -> Add connection -> Application Insights
##################################################

# resource "azapi_resource" "ai_foundry_appinsights_connection" {
#   name      = module.application_insights.name
#   parent_id = module.ai_foundry.ai_foundry_project_id["dataagent"]
#   type      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview"
#   body = {
#     properties = {
#       category = "ApplicationInsights"
#       target   = module.application_insights.resource_id
#       authType = "AAD"
#       metadata = {
#         ApiType    = "Azure"
#         ResourceId = module.application_insights.resource_id
#         location   = azurerm_resource_group.shared_rg.location
#       }
#     }
#   }
#   schema_validation_enabled = false
#   depends_on                = [module.ai_foundry]
# }


##################################################
# Azure SQL Database with AdventureWorksLT Sample Data
##################################################

module "sql_server" {
  source  = "Azure/avm-res-sql-server/azurerm"
  name                = "${local.identifier}-sql"
  resource_group_name = azurerm_resource_group.shared_rg.name
  location            = azurerm_resource_group.shared_rg.location
  server_version      = "12.0"
  tags = local.tags

  # Use Entra ID authentication only (recommended)
  azuread_administrator = {
    azuread_authentication_only = true
    login_username              = data.azurerm_client_config.current.client_id
    object_id                   = data.azurerm_client_config.current.object_id
    tenant_id                   = data.azurerm_client_config.current.tenant_id
  }

  databases = {
    adventureworks = {
      name        = "AdventureWorksLT"
      sample_name = "AdventureWorksLT"
      sku_name    = "S0"
    }
  }

  public_network_access_enabled = true
  
  # Allow Azure services to access the server
  firewall_rules = {
    allow_azure_services = {
      start_ip_address = "0.0.0.0"
      end_ip_address   = "0.0.0.0"
    }
  }

  # Note: SQL Server doesn't support diagnostic settings at the server level.
  # Use SQL Auditing or database-level diagnostic settings instead.
}


# ==============================================================
# General Outputs
# ==============================================================

output "azure_tenant_id" {
  description = "Azure tenant ID"
  value       = data.azurerm_client_config.current.tenant_id
}

output "azure_subscription_id" {
  description = "Azure subscription ID"
  value       = data.azurerm_subscription.current.subscription_id
}

output "resource_group_name" {
  description = "Name of the resource group"
  value       = azurerm_resource_group.shared_rg.name
}

output "resource_group_location" {
  description = "Location of the resource group"
  value       = azurerm_resource_group.shared_rg.location
}

# ==============================================================
# Observability Outputs
# ==============================================================

output "appinsights_connection_string" {
  description = "Application Insights connection string"
  value       = module.application_insights.connection_string
  sensitive   = true
}

output "appinsights_instrumentation_key" {
  description = "Application Insights instrumentation key"
  value       = module.application_insights.instrumentation_key
  sensitive   = true
}

# ==============================================================
# AI Foundry Outputs
# ==============================================================

output "ai_foundry_id" {
  description = "AI Foundry account resource ID"
  value       = module.ai_foundry.ai_foundry_id
}

output "ai_foundry_project_id" {
  description = "AI Foundry project resource ID"
  value       = module.ai_foundry.ai_foundry_project_id
}

output "cosmos_db_id" {
  description = "Cosmos DB account resource ID"
  value       = module.ai_foundry.cosmos_db_id
}

output "key_vault_id" {
  description = "Key Vault resource ID"
  value       = module.ai_foundry.key_vault_id
}

output "storage_account_id" {
  description = "Storage account resource ID"
  value       = module.ai_foundry.storage_account_id
}


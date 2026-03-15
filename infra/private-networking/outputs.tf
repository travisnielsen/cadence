output "azure_tenant_id" {
  description = "Azure tenant ID used by this deployment."
  value       = data.azurerm_client_config.current.tenant_id
}

output "azure_subscription_id" {
  description = "Azure subscription ID used by this deployment."
  value       = data.azurerm_subscription.current.subscription_id
}

output "resource_group_name" {
  description = "Private networking resource group name."
  value       = azurerm_resource_group.private_rg.name
}

output "azure_location" {
  description = "Azure location for resources in this private networking deployment."
  value       = azurerm_resource_group.private_rg.location
}

output "virtual_network_id" {
  description = "Virtual network resource ID."
  value       = azurerm_virtual_network.private_vnet.id
}

output "subnet_ids" {
  description = "Subnet resource IDs keyed by logical name."
  value = {
    private_endpoints = azurerm_subnet.private_endpoints.id
    application       = azurerm_subnet.application.id
    container_apps    = azurerm_subnet.container_apps.id
    ai_agent_services = azurerm_subnet.ai_agent_services.id
    data              = azurerm_subnet.data.id
  }
}

output "private_dns_zone_ids" {
  description = "Private DNS zone resource IDs keyed by zone name."
  value       = { for zone_name, zone in azurerm_private_dns_zone.this : zone_name => zone.id }
}

output "private_endpoint_ids" {
  description = "Private endpoint IDs keyed by endpoint name."
  value       = { for endpoint_name, endpoint in azurerm_private_endpoint.this : endpoint_name => endpoint.id }
}

output "appinsights_connection_string" {
  description = "Application Insights connection string"
  value       = module.application_insights.connection_string
  sensitive   = true
}

output "ai_foundry_id" {
  description = "AI Foundry account resource ID"
  value       = module.ai_foundry.ai_foundry_id
}

output "ai_foundry_project_id" {
  description = "AI Foundry project resource ID"
  value       = module.ai_foundry.ai_foundry_project_id
}

output "container_app_identity_client_id" {
  description = "Container App managed identity client ID"
  value       = azurerm_user_assigned_identity.api_identity.client_id
}

output "container_app_identity_name" {
  description = "Container App managed identity name"
  value       = azurerm_user_assigned_identity.api_identity.name
}

output "container_registry_login_server" {
  description = "Container Registry login server"
  value       = module.container_registry.resource.login_server
}

output "container_registry_name" {
  description = "Container Registry resource name"
  value       = element(reverse(split("/", module.container_registry.resource_id)), 0)
}

output "container_app_environment_name" {
  description = "Container Apps environment name"
  value       = element(reverse(split("/", module.container_app_environment.resource_id)), 0)
}

output "storage_account_name" {
  description = "Storage account name used for NL2SQL assets"
  value       = module.ai_storage.name
}

output "search_service_name" {
  description = "AI Search service name"
  value       = module.ai_search.resource.name
}

output "ai_foundry_account_name" {
  description = "AI Foundry account name"
  value       = element(reverse(split("/", module.ai_foundry.ai_foundry_id)), 0)
}

output "sql_database_name" {
  description = "Azure SQL database name"
  value       = var.sql_database_name
}

output "container_app_name" {
  description = "Container App name for backend API"
  value       = azurerm_container_app.api.name
}

output "container_app_url" {
  description = "Container App API URL"
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "static_web_app_name" {
  description = "Azure Static Web App name for frontend hosting"
  value       = azurerm_static_web_app.frontend.name
}

output "static_web_app_url" {
  description = "Azure Static Web App URL"
  value       = "https://${azurerm_static_web_app.frontend.default_host_name}"
}

output "sql_server_name" {
  description = "Azure SQL server name"
  value       = module.sql_server.resource.name
  sensitive   = true
}

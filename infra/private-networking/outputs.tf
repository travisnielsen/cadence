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

output "container_registry_login_server" {
  description = "Container Registry login server"
  value       = module.container_registry.resource.login_server
}

output "sql_server_name" {
  description = "Azure SQL server name"
  value       = module.sql_server.resource.name
}

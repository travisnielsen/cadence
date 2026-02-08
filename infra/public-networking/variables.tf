variable "subscription_id" {
  type        = string
  description = "Azure Subscription ID to deploy environment into."
}

variable "region" {
  type    = string
  default = "westus3"
  description = "Azure region to deploy resources."
}

variable "region_aifoundry" {
  type    = string
  default = "westus3"
  description = "Azure region to deploy AI Foundry resources."
}

variable "region_search" {
  type        = string
  default     = "eastus"
  description = "Azure region to deploy AI Search. Separated due to regional capacity constraints."
}

variable "frontend_app_client_id" {
  type        = string
  description = "Azure AD App Registration client ID for the frontend application. Used by the API to validate authentication tokens."
}

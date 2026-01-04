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

variable "frontend_app_client_id" {
  type        = string
  description = "Azure AD App Registration client ID for the frontend application. Used by the API to validate authentication tokens."
}

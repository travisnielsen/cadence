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

# Private Networking Terraform

This stack creates a private networking foundation plus private service deployment for Cadence in Azure under `infra/private-networking`.

## What It Deploys

- Resource Group
- Virtual Network with 3 subnets:
  - `private-endpoints`
  - `application`
  - `data`
- Network Security Groups for `application` and `data` subnets
- Private DNS zones and VNet links
- Optional private endpoints to existing Azure services
- AVM-based service deployment cloned from public networking with private access enforced:
  - Container Registry
  - Storage Account (NL2SQL blobs)
  - Cosmos DB
  - AI Search
  - AI Foundry (private/standard mode with agent subnet injection)
  - SQL Server + database
  - Container Apps environment and API app (VNet injected)

## Inputs

1. Copy `terraform.tfvars.example` to `terraform.tfvars`.
2. Update required values:
   - `subscription_id`

Optionally add `private_endpoints` entries for existing resources.

If running from outside the private network, keep `enable_local_exec_provisioning = false`.
Set it to `true` only when Terraform execution has network access to private data-plane endpoints
for SQL import and Search index/data provisioning.

## Commands

```bash
cd infra/private-networking
terraform init
terraform fmt -recursive
terraform validate
terraform plan -out tfplan
terraform apply tfplan
```

## Notes

- Private endpoints are optional. If `private_endpoints` is empty, only network and DNS foundations are created.
- Each private endpoint `dns_zone_name` must be present in `private_dns_zone_names`.

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
Set it to `true` only when Terraform execution has network access to private data-plane endpoints for SQL import and Search index/data provisioning.

## Phase 2: Private Runner Provisioning Model

This repo uses a two-phase deployment model for private data-plane work:

1. Phase 1 (environment provisioning): deploy infrastructure with Terraform.
2. Phase 2 (private data provisioning): run one-time seeding from a private GitHub runner.

### One-Time Workflows

- `.github/workflows/bootstrap-private-runner.yml`
  - Builds a hardened GitHub runner container image in ACR.
  - Creates a managed identity with `AcrPull`.
  - Creates an event-driven Azure Container Apps Job using the `github-runner` scaler.
  - Configures ephemeral runners (`--ephemeral`) with label-based routing.
- `.github/workflows/provision-private-data.yml`
  - Runs on the private self-hosted runner (`runs-on: [self-hosted, linux, x64, cadence-private]`).
  - Uploads NL2SQL data files to private blob storage.
  - Configures AI Search datasources, indexes, skillsets, and indexers via private data-plane calls.
  - Imports WideWorldImporters into private SQL.

### Regular CI/CD

Regular CI/CD workflows continue to use GitHub-hosted runners unless private network access is required.
Use the private self-hosted runner only for jobs that must reach private endpoints.

### Required GitHub Configuration

Repository variables:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_CONTAINER_REGISTRY`
- `AZURE_CONTAINER_APP_ENVIRONMENT`
- `AZURE_STORAGE_ACCOUNT`
- `AZURE_SQL_SERVER_NAME` (server name without `.database.windows.net`)
- `AZURE_SEARCH_SERVICE_NAME`
- `AZURE_AI_FOUNDRY_ACCOUNT_NAME`
- Optional: `AZURE_SQL_DATABASE_NAME`
- Optional: `AZURE_GH_RUNNER_IDENTITY_NAME`
- Optional: `GH_RUNNER_REPO_OWNER`
- Optional: `GH_RUNNER_REPO_NAME`
- Preferred for GitHub App auth: `GH_RUNNER_APP_ID`
- Preferred for GitHub App auth: `GH_RUNNER_INSTALLATION_ID`

Repository secrets:

- Preferred: `GH_RUNNER_APP_PRIVATE_KEY` (GitHub App PEM private key)
- Fallback only: `GH_RUNNER_PAT` (fine-grained PAT for runner registration and scaler polling)

`bootstrap-private-runner.yml` automatically prefers GitHub App auth when
`GH_RUNNER_APP_ID`, `GH_RUNNER_INSTALLATION_ID`, and `GH_RUNNER_APP_PRIVATE_KEY`
are present. If not set, it falls back to PAT mode.

### GitHub App Prerequisites (Recommended)

Use this setup when possible. It provides tighter security and better API rate limits than PAT mode.

1. Create a GitHub App in GitHub Settings → Developer settings → GitHub Apps.

1. Disable webhooks for this app (not required for this scaler pattern).

1. Configure repository permissions on the app.

- Actions: Read-only
- Administration: Read and write
- Metadata: Read-only

1. If using organization scope runners, also configure organization-level self-hosted runner permissions.

1. Install the app to the target repository (or organization if using org scope).

1. Save the App ID as repo variable `GH_RUNNER_APP_ID`.

1. Save the Installation ID as repo variable `GH_RUNNER_INSTALLATION_ID`.

1. Generate a private key in the GitHub App and store the PEM content as repo secret `GH_RUNNER_APP_PRIVATE_KEY`.

### PAT Fallback Prerequisites (Fallback Only)

If GitHub App setup is not ready, use a fine-grained PAT in `GH_RUNNER_PAT` with:

- Repository access limited to the target repo(s).
- Repository permission Actions: Read-only
- Repository permission Administration: Read and write
- Repository permission Metadata: Read-only

Rotate this token regularly.

### Auth Mode Selection Behavior

In `.github/workflows/bootstrap-private-runner.yml`:

1. GitHub App mode is selected when all of the following exist:

- `GH_RUNNER_APP_ID`
- `GH_RUNNER_INSTALLATION_ID`
- `GH_RUNNER_APP_PRIVATE_KEY`

1. Otherwise, PAT mode is selected when `GH_RUNNER_PAT` exists.

1. If neither is configured, bootstrap fails fast.

The workflow prints the selected mode and emits a warning when PAT fallback is used.

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

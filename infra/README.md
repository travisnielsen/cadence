# Azure Infrastructure Deployment

This repo includes Infrastructure-as-Code (IaC) that deploys a baseline set of services for supporting RAG and NL2SQL scenarios. These services are summarized as follows:

| Service | Module/Resource | Purpose |
|---------|-----------------|---------|
| Log Analytics Workspace | `log_analytics` | Centralized logging and monitoring backend for Application Insights and diagnostics |
| Application Insights | `application_insights` | Application performance monitoring, tracing, and telemetry for AI agents |
| Container Registry | `container_registry` | Private container image registry for deploying custom applications |
| Key Vault | `ai_keyvault` | Secure storage for secrets, keys, and certificates used by AI Foundry |
| Storage Account | `ai_storage` | Blob storage for AI Foundry agent file storage and artifacts |
| Cosmos DB | `ai_cosmosdb` | NoSQL database for storing AI agent threads and conversation history |
| AI Search | `ai_search` | Vector search service for RAG patterns and semantic search capabilities |
| Microsoft Foundry | `ai_foundry` | Azure AI Foundry hub and project with model deployments (GPT-5, embeddings) |
| Azure SQL Database | `sql_server` | SQL database with Wide World Importers sample data for NL2SQL scenarios |

The IaC is based on Terraform and uses [Azure Verified Modules](https://azure.github.io/Azure-Verified-Modules/).

> [!IMPORTANT]
> Currently, this repo assumes you have permissiones to create resources in an Azure subscription and can configure RBAC roles.

## AI Search Configuration

The Terraform deployment automatically configures AI Search with vector indexes for NL2SQL scenarios:

| Component | Name | Description |
|-----------|------|-------------|
| **Data Sources** | `agentic-queries`, `agentic-tables` | Connect to blob storage containers for query examples and table schemas |
| **Indexes** | `queries`, `tables` | Vector-enabled indexes with 3072-dimension embeddings using HNSW algorithm |
| **Skillsets** | `query-embed-skill`, `table-embed-skill` | Generate embeddings via `text-embedding-3-large` model |
| **Indexers** | `indexer-queries`, `indexer-tables` | Process JSON documents and populate vector indexes |

The Search service uses managed identity authentication to access storage and AI Foundry for embedding generation. Sample data is uploaded from the `search-config/` folder during deployment.

## Deployment

In the sub-folder you are working from, create a new `terraform.tfvars` file and populate the following variables:

```terraform
subscription_id     = "<your_subscription_id>"
region              = "<azure_region_name>"
region_aifoundry    = "<azure_region_name>"
```

The region you input will depend on model and other resource availabilty. Deployments have been successfully tested in `westus3` and `eastus2`. At the time of this writing, `gpt-5.2-chat` is available for use in `eastus2`.

Open a terminal session and authenticate to your Azure environment via `az login`. Once completed, you can run the following commands to deploy the infrastructure

```terraform
# Download and initialize dependencies
terraform init

# Execute the deployment plan
terraform plan

# Deploy resources
terraform apply
```

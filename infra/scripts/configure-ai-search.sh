#!/usr/bin/env bash
set -euo pipefail

SEARCH_NAME="${1:-}"
RESOURCE_GROUP="${2:-}"
STORAGE_ACCOUNT_NAME="${3:-}"
AI_FOUNDRY_ACCOUNT_NAME="${4:-}"

if [[ -z "$SEARCH_NAME" || -z "$RESOURCE_GROUP" || -z "$STORAGE_ACCOUNT_NAME" || -z "$AI_FOUNDRY_ACCOUNT_NAME" ]]; then
  echo "Usage: configure-ai-search.sh <search-name> <resource-group> <storage-account-name> <ai-foundry-account-name>"
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SEARCH_CONFIG_DIR="$REPO_ROOT/search-config"

if [[ ! -f "$SEARCH_CONFIG_DIR/tables_index.json" || ! -f "$SEARCH_CONFIG_DIR/query_templates_index.json" ]]; then
  echo "Search index definition files not found in $SEARCH_CONFIG_DIR"
  exit 1
fi

SEARCH_URL="https://${SEARCH_NAME}.search.windows.net"
API_VERSION="2024-05-01-preview"

STORAGE_RESOURCE_ID=$(az storage account show \
  --name "$STORAGE_ACCOUNT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query id -o tsv)

TOKEN=$(az account get-access-token --resource https://search.azure.com --query accessToken -o tsv)

put_json() {
  local endpoint="$1"
  local payload_file="$2"
  curl -fsS -X PUT "$SEARCH_URL/$endpoint?api-version=$API_VERSION" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d @"$payload_file" >/dev/null
}

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

cat >"$tmpdir/ds_tables.json" <<JSON
{"name":"agentic-tables","type":"azureblob","credentials":{"connectionString":"ResourceId=$STORAGE_RESOURCE_ID;"},"container":{"name":"nl2sql","query":"tables"}}
JSON

cat >"$tmpdir/ds_templates.json" <<JSON
{"name":"agentic-query-templates","type":"azureblob","credentials":{"connectionString":"ResourceId=$STORAGE_RESOURCE_ID;"},"container":{"name":"nl2sql","query":"query_templates"}}
JSON

cat >"$tmpdir/skillset_tables.json" <<JSON
{"name":"table-embed-skill","description":"OpenAI Embedding skill for table descriptions","skills":[{"@odata.type":"#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill","name":"vector-embed-field-description","description":"vector embedding for the description field","context":"/document","resourceUri":"https://$AI_FOUNDRY_ACCOUNT_NAME.openai.azure.com","deploymentId":"embedding-large","dimensions":3072,"modelName":"text-embedding-3-large","inputs":[{"name":"text","source":"/document/description"}],"outputs":[{"name":"embedding","targetName":"content_embeddings"}]}]}
JSON

cat >"$tmpdir/skillset_templates.json" <<JSON
{"name":"query-template-embed-skill","description":"OpenAI Embedding skill for query template questions","skills":[{"@odata.type":"#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill","name":"vector-embed-field-question","description":"vector embedding for the question field","context":"/document","resourceUri":"https://$AI_FOUNDRY_ACCOUNT_NAME.openai.azure.com","deploymentId":"embedding-large","dimensions":3072,"modelName":"text-embedding-3-large","inputs":[{"name":"text","source":"/document/question"}],"outputs":[{"name":"embedding","targetName":"content_embeddings"}]}]}
JSON

cat >"$tmpdir/indexer_tables.json" <<JSON
{"name":"indexer-tables","dataSourceName":"agentic-tables","skillsetName":"table-embed-skill","targetIndexName":"tables","parameters":{"configuration":{"dataToExtract":"contentAndMetadata","parsingMode":"json"}},"fieldMappings":[],"outputFieldMappings":[{"sourceFieldName":"/document/content_embeddings","targetFieldName":"content_vector"}]}
JSON

cat >"$tmpdir/indexer_templates.json" <<JSON
{"name":"indexer-query-templates","dataSourceName":"agentic-query-templates","skillsetName":"query-template-embed-skill","targetIndexName":"query_templates","parameters":{"configuration":{"dataToExtract":"contentAndMetadata","parsingMode":"json","excludedFileNameExtensions":".sql"}},"fieldMappings":[],"outputFieldMappings":[{"sourceFieldName":"/document/content_embeddings","targetFieldName":"content_vector"}]}
JSON

put_json "datasources/agentic-tables" "$tmpdir/ds_tables.json"
put_json "datasources/agentic-query-templates" "$tmpdir/ds_templates.json"
put_json "indexes/tables" "$SEARCH_CONFIG_DIR/tables_index.json"
put_json "indexes/query_templates" "$SEARCH_CONFIG_DIR/query_templates_index.json"
put_json "skillsets/table-embed-skill" "$tmpdir/skillset_tables.json"
put_json "skillsets/query-template-embed-skill" "$tmpdir/skillset_templates.json"
put_json "indexers/indexer-tables" "$tmpdir/indexer_tables.json"
put_json "indexers/indexer-query-templates" "$tmpdir/indexer_templates.json"

echo "AI Search data-plane configuration completed for service: $SEARCH_NAME"

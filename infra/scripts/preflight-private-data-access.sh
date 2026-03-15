#!/usr/bin/env bash
set -euo pipefail

ASSIGNEE_CLIENT_ID="${1:-}"
RESOURCE_GROUP="${2:-}"
STORAGE_ACCOUNT_NAME="${3:-}"
SEARCH_SERVICE_NAME="${4:-}"
SQL_SERVER_NAME="${5:-}"
ACR_NAME="${6:-}"

if [[ -z "$ASSIGNEE_CLIENT_ID" || -z "$RESOURCE_GROUP" || -z "$STORAGE_ACCOUNT_NAME" || -z "$SEARCH_SERVICE_NAME" || -z "$SQL_SERVER_NAME" ]]; then
  echo "Usage: $0 <assignee-client-id> <resource-group> <storage-account> <search-service> <sql-server> [acr-name]"
  exit 1
fi

check_role() {
  local role_name="$1"
  local scope="$2"
  local label="$3"

  local count
  count=$(az role assignment list \
    --assignee "$ASSIGNEE_CLIENT_ID" \
    --scope "$scope" \
    --include-inherited \
    --query "[?roleDefinitionName=='$role_name'] | length(@)" \
    -o tsv)

  if [[ "$count" -ge 1 ]]; then
    echo "PASS: $label role '$role_name' is assigned"
  else
    echo "FAIL: $label role '$role_name' is missing"
    return 1
  fi
}

STORAGE_ID=$(az storage account show \
  --name "$STORAGE_ACCOUNT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query id -o tsv)

SEARCH_ID=$(az search service show \
  --name "$SEARCH_SERVICE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query id -o tsv)

SQL_SERVER_ID=$(az sql server show \
  --name "$SQL_SERVER_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query id -o tsv)

check_role "Storage Blob Data Contributor" "$STORAGE_ID" "Storage data-plane upload"
check_role "Search Service Contributor" "$SEARCH_ID" "Search index/data-source/skillset operations"
check_role "SQL DB Contributor" "$SQL_SERVER_ID" "SQL control-plane import lifecycle"

if [[ -n "$ACR_NAME" ]]; then
  ACR_ID=$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)
  check_role "AcrPush" "$ACR_ID" "Standard CI/CD image build and push"
fi

# Best-effort SQL Entra admin check. Group-based admin is valid and cannot be fully verified here.
ASSIGNEE_OBJECT_ID=""
if ASSIGNEE_OBJECT_ID=$(az ad sp show --id "$ASSIGNEE_CLIENT_ID" --query id -o tsv 2>/dev/null); then
  SQL_ADMIN_SID=$(az sql server ad-admin show \
    --server "$SQL_SERVER_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query sid -o tsv 2>/dev/null || true)

  if [[ -n "$SQL_ADMIN_SID" && "$SQL_ADMIN_SID" == "$ASSIGNEE_OBJECT_ID" ]]; then
    echo "PASS: SQL Entra admin is set to the federated principal"
  else
    echo "WARN: SQL Entra admin is not directly the federated principal. This is valid if using an Entra group that includes it."
  fi
else
  echo "WARN: Could not resolve assignee object ID from client ID for SQL Entra admin direct-match check."
fi

echo "Preflight checks completed."

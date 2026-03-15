#!/usr/bin/env bash
set -euo pipefail

SQL_SP_ID="${1:-}"

if [[ -z "$SQL_SP_ID" ]]; then
  echo "Usage: ensure-sql-directory-readers.sh <sql-server-service-principal-object-id>"
  exit 1
fi

get_directory_readers_role_id() {
  az rest \
    --method GET \
    --url "https://graph.microsoft.com/v1.0/directoryRoles" \
    --query "value[?displayName=='Directory Readers'].id | [0]" -o tsv
}

ROLE_ID="$(get_directory_readers_role_id)"

if [[ -z "$ROLE_ID" ]]; then
  TEMPLATE_ID=$(az rest \
    --method GET \
    --url "https://graph.microsoft.com/v1.0/directoryRoleTemplates" \
    --query "value[?displayName=='Directory Readers'].id | [0]" -o tsv)

  if [[ -z "$TEMPLATE_ID" ]]; then
    echo "Directory Readers role template not found in tenant"
    exit 1
  fi

  az rest \
    --method POST \
    --url "https://graph.microsoft.com/v1.0/directoryRoles" \
    --body "{\"roleTemplateId\":\"${TEMPLATE_ID}\"}" >/dev/null

  # Role activation can take a short time to become visible.
  ROLE_ID=""
  for _ in 1 2 3; do
    ROLE_ID="$(get_directory_readers_role_id)"
    if [[ -n "$ROLE_ID" ]]; then
      break
    fi
    sleep 2
  done
fi

if [[ -z "$ROLE_ID" ]]; then
  echo "Failed to resolve Directory Readers directory role ID"
  exit 1
fi

set +e
MEMBERSHIP_COUNT=$(az rest \
  --method GET \
  --url "https://graph.microsoft.com/v1.0/directoryRoles/${ROLE_ID}/members" \
  --query "length(value[?id=='${SQL_SP_ID}'])" -o tsv 2>/tmp/ensure-dir-readers.err)
membership_rc=$?
set -e

if [[ $membership_rc -ne 0 ]]; then
  if grep -q "Request_ResourceNotFound" /tmp/ensure-dir-readers.err; then
    # Retry once after re-resolving role ID in case the cached ID is stale.
    ROLE_ID="$(get_directory_readers_role_id)"
    if [[ -z "$ROLE_ID" ]]; then
      cat /tmp/ensure-dir-readers.err
      exit 1
    fi
    MEMBERSHIP_COUNT=$(az rest \
      --method GET \
      --url "https://graph.microsoft.com/v1.0/directoryRoles/${ROLE_ID}/members" \
      --query "length(value[?id=='${SQL_SP_ID}'])" -o tsv)
  else
    cat /tmp/ensure-dir-readers.err
    exit 1
  fi
fi

if [[ "$MEMBERSHIP_COUNT" == "0" ]]; then
  set +e
  ADD_OUTPUT=$(az rest \
    --method POST \
    --url "https://graph.microsoft.com/v1.0/directoryRoles/${ROLE_ID}/members/\$ref" \
    --body "{\"@odata.id\":\"https://graph.microsoft.com/v1.0/directoryObjects/${SQL_SP_ID}\"}" 2>&1)
  add_rc=$?
  set -e

  if [[ $add_rc -eq 0 ]]; then
    echo "Added ${SQL_SP_ID} to Directory Readers"
  elif echo "$ADD_OUTPUT" | grep -qi "already exist"; then
    # Graph can return a non-idempotent 400 if membership was created by a concurrent/recent call.
    echo "${SQL_SP_ID} is already a member of Directory Readers"
  else
    echo "$ADD_OUTPUT"
    exit 1
  fi
else
  echo "${SQL_SP_ID} is already a member of Directory Readers"
fi

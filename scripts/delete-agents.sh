#!/bin/bash
# Delete Azure AI Foundry agents
#
# Usage:
#   ./delete-agents.sh <endpoint> [name_pattern] [--delete] [--keep-one]
#
# Examples:
#   # List all agents
#   ./delete-agents.sh "https://aif-ay2q3p-9mia8.services.ai.azure.com/api/projects/dataexplorer"
#
#   # List agents named "chat-agent"
#   ./delete-agents.sh "https://aif-ay2q3p-9mia8.services.ai.azure.com/api/projects/dataexplorer" "chat-agent"
#
#   # Delete all agents named "chat-agent"
#   ./delete-agents.sh "https://aif-ay2q3p-9mia8.services.ai.azure.com/api/projects/dataexplorer" "chat-agent" --delete
#
#   # Delete all but keep the most recent one
#   ./delete-agents.sh "https://aif-ay2q3p-9mia8.services.ai.azure.com/api/projects/dataexplorer" "" --delete --keep-one

set -e

ENDPOINT="${1:-}"
NAME_PATTERN="${2:-}"
DO_DELETE=false
KEEP_ONE=false

# Parse flags
for arg in "${@:3}"; do
    case $arg in
        --delete)
            DO_DELETE=true
            ;;
        --keep-one)
            KEEP_ONE=true
            ;;
    esac
done

if [ -z "$ENDPOINT" ]; then
    echo "ERROR: Endpoint is required"
    echo "Usage: $0 <endpoint> [name_pattern] [--delete] [--keep-one]"
    exit 1
fi

echo "=== Azure AI Foundry Agent Manager ==="
echo ""
echo "Endpoint: $ENDPOINT"
[ -n "$NAME_PATTERN" ] && echo "Pattern:  $NAME_PATTERN"
echo ""

# Get access token
echo "Getting access token..."
TOKEN=$(az account get-access-token --resource "https://ai.azure.com" --query accessToken -o tsv)
if [ -z "$TOKEN" ]; then
    echo "ERROR: Failed to get access token. Make sure you're logged in with 'az login'"
    exit 1
fi

# List agents (with pagination)
echo "Fetching agents..."
API_VERSION="2025-05-15-preview"
LIST_URL="$ENDPOINT/assistants?api-version=$API_VERSION&limit=100"

ALL_AGENTS="[]"
HAS_MORE=true
AFTER=""

while [ "$HAS_MORE" = true ]; do
    if [ -n "$AFTER" ]; then
        PAGE_URL="$LIST_URL&after=$AFTER"
    else
        PAGE_URL="$LIST_URL"
    fi
    
    RESPONSE=$(curl -s -H "Authorization: Bearer $TOKEN" "$PAGE_URL")
    
    # Check for error
    if echo "$RESPONSE" | jq -e '.error' > /dev/null 2>&1; then
        echo "ERROR: $(echo "$RESPONSE" | jq -r '.error.message')"
        exit 1
    fi
    
    # Append data to all agents
    PAGE_DATA=$(echo "$RESPONSE" | jq -r '.data')
    ALL_AGENTS=$(echo "$ALL_AGENTS $PAGE_DATA" | jq -s 'add')
    
    # Check if there are more pages
    HAS_MORE=$(echo "$RESPONSE" | jq -r '.has_more // false')
    if [ "$HAS_MORE" = "true" ]; then
        AFTER=$(echo "$RESPONSE" | jq -r '.last_id // empty')
        if [ -z "$AFTER" ]; then
            # Fallback: get last ID from data array
            AFTER=$(echo "$PAGE_DATA" | jq -r '.[-1].id // empty')
        fi
        if [ -z "$AFTER" ]; then
            HAS_MORE=false
        fi
    fi
done

# Filter by name pattern if specified
if [ -n "$NAME_PATTERN" ]; then
    AGENTS=$(echo "$ALL_AGENTS" | jq -r --arg pattern "$NAME_PATTERN" 'map(select(.name == $pattern))')
else
    AGENTS="$ALL_AGENTS"
fi

COUNT=$(echo "$AGENTS" | jq 'length')
echo ""
echo "Found $COUNT agent(s)$([ -n "$NAME_PATTERN" ] && echo " matching '$NAME_PATTERN'")"

if [ "$COUNT" -eq 0 ]; then
    echo "No agents to process."
    exit 0
fi

# Sort by created_at (newest first)
SORTED=$(echo "$AGENTS" | jq 'sort_by(.created_at) | reverse')

# Optionally keep the most recent one
if [ "$KEEP_ONE" = true ] && [ "$COUNT" -gt 1 ]; then
    KEPT=$(echo "$SORTED" | jq -r '.[0]')
    KEPT_ID=$(echo "$KEPT" | jq -r '.id')
    KEPT_NAME=$(echo "$KEPT" | jq -r '.name')
    KEPT_CREATED=$(echo "$KEPT" | jq -r '.created_at')
    echo "Keeping: $KEPT_ID ($KEPT_NAME) - created $KEPT_CREATED"
    SORTED=$(echo "$SORTED" | jq '.[1:]')
    COUNT=$((COUNT - 1))
fi

if [ "$DO_DELETE" = true ]; then
    echo ""
    echo "Deleting $COUNT agent(s)..."
    
    for i in $(seq 0 $((COUNT - 1))); do
        AGENT=$(echo "$SORTED" | jq -r ".[$i]")
        ID=$(echo "$AGENT" | jq -r '.id')
        NAME=$(echo "$AGENT" | jq -r '.name')
        
        echo -n "  Deleting $ID ($NAME)... "
        DELETE_URL="$ENDPOINT/assistants/$ID?api-version=$API_VERSION"
        
        DELETE_RESULT=$(curl -s -X DELETE -H "Authorization: Bearer $TOKEN" "$DELETE_URL")
        
        if echo "$DELETE_RESULT" | jq -e '.error' > /dev/null 2>&1; then
            echo "FAILED: $(echo "$DELETE_RESULT" | jq -r '.error.message')"
        else
            echo "done"
        fi
    done
    
    echo ""
    echo "Deleted $COUNT agent(s)"
else
    echo ""
    echo "Agents (use --delete to remove):"
    for i in $(seq 0 $((COUNT - 1))); do
        AGENT=$(echo "$SORTED" | jq -r ".[$i]")
        ID=$(echo "$AGENT" | jq -r '.id')
        NAME=$(echo "$AGENT" | jq -r '.name')
        CREATED=$(echo "$AGENT" | jq -r '.created_at')
        echo "  $ID: $NAME (created: $CREATED)"
    done
fi

echo ""
echo "Done!"

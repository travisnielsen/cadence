#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${GH_URL:-}" ]]; then
  echo "GH_URL is required"
  exit 1
fi

if [[ -z "${REGISTRATION_TOKEN_API_URL:-}" ]]; then
  echo "REGISTRATION_TOKEN_API_URL is required"
  exit 1
fi

RUNNER_NAME="${RUNNER_NAME:-aca-$(hostname)-$RANDOM}"
RUNNER_WORKDIR="${RUNNER_WORKDIR:-_work}"
RUNNER_LABELS="${RUNNER_LABELS:-self-hosted,linux,x64,cadence-private}"

AUTH_MODE="pat"
if [[ -n "${GH_APP_ID:-}" && -n "${GH_APP_INSTALLATION_ID:-}" && -n "${GH_APP_PRIVATE_KEY:-}" ]]; then
  AUTH_MODE="app"
elif [[ -z "${GITHUB_PAT:-}" ]]; then
  echo "Provide either GITHUB_PAT or GitHub App credentials (GH_APP_ID, GH_APP_INSTALLATION_ID, GH_APP_PRIVATE_KEY)"
  exit 1
fi

base64_urlencode() {
  printf '%s' "$1" | openssl base64 -A | tr '+/' '-_' | tr -d '='
}

create_app_access_token() {
  local now exp header payload signing_input jwt app_key_file response token

  now=$(date +%s)
  exp=$((now + 540))

  header='{"alg":"RS256","typ":"JWT"}'
  payload="{\"iat\":${now},\"exp\":${exp},\"iss\":\"${GH_APP_ID}\"}"
  signing_input="$(base64_urlencode "$header").$(base64_urlencode "$payload")"

  app_key_file=$(mktemp)
  chmod 600 "$app_key_file"
  printf '%s' "$GH_APP_PRIVATE_KEY" > "$app_key_file"

  jwt="${signing_input}.$(printf '%s' "$signing_input" | openssl dgst -binary -sha256 -sign "$app_key_file" | openssl base64 -A | tr '+/' '-_' | tr -d '=')"
  rm -f "$app_key_file"

  response=$(curl -fsSL -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${jwt}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/app/installations/${GH_APP_INSTALLATION_ID}/access_tokens")

  token=$(printf '%s' "$response" | jq -r '.token')
  if [[ -z "$token" || "$token" == "null" ]]; then
    echo "Failed to acquire installation access token from GitHub App"
    exit 1
  fi

  printf '%s' "$token"
}

cleanup() {
  if [[ -n "${RUNNER_TOKEN:-}" ]]; then
    ./config.sh remove --unattended --token "$RUNNER_TOKEN" || true
  fi
}

trap cleanup EXIT INT TERM

echo "Requesting runner registration token"
if [[ "$AUTH_MODE" == "app" ]]; then
  GH_AUTH_TOKEN=$(create_app_access_token)
else
  GH_AUTH_TOKEN="$GITHUB_PAT"
fi

RUNNER_TOKEN=$(curl -fsSL -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GH_AUTH_TOKEN}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "${REGISTRATION_TOKEN_API_URL}" | jq -r '.token')

if [[ -z "$RUNNER_TOKEN" || "$RUNNER_TOKEN" == "null" ]]; then
  echo "Failed to acquire runner registration token"
  exit 1
fi

echo "Configuring ephemeral runner ${RUNNER_NAME}"
./config.sh \
  --unattended \
  --url "${GH_URL}" \
  --token "${RUNNER_TOKEN}" \
  --name "${RUNNER_NAME}" \
  --work "${RUNNER_WORKDIR}" \
  --labels "${RUNNER_LABELS}" \
  --ephemeral \
  --disableupdate

echo "Starting runner"
exec ./run.sh

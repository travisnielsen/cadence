param(
    [Parameter(Mandatory=$true)]
    [ValidateNotNullOrEmpty()]
    [string]$SUB_ID,

    [Parameter(Mandatory=$true)]
    [ValidateNotNullOrEmpty()]
    [string]$RG
)

$ErrorActionPreference = "Stop"

$scope = "/subscriptions/$SUB_ID/resourceGroups/$RG"

# Verify the subscription and resource group exist
$rgCheck = az group show --name $RG --subscription $SUB_ID 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Resource group '$RG' not found in subscription '$SUB_ID'. Please verify both values."
    exit 1
}

# Create app registration (or retrieve existing)
$APP_ID = az ad app list --display-name "github-actions-cadence" --query "[0].appId" -o tsv
if ($APP_ID) {
    Write-Output "App registration 'github-actions-cadence' already exists (appId: $APP_ID). Skipping creation."
} else {
    Write-Output "Creating app registration 'github-actions-cadence'..."
    az ad app create --display-name "github-actions-cadence"
    if ($LASTEXITCODE -ne 0) { Write-Error "Failed to create app registration."; exit 1 }
    $APP_ID = az ad app list --display-name "github-actions-cadence" --query "[0].appId" -o tsv
    if (-not $APP_ID) { Write-Error "Failed to retrieve app ID."; exit 1 }
}

# Create service principal (or skip if exists)
$spCheck = az ad sp show --id $APP_ID 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Output "Service principal for appId '$APP_ID' already exists. Skipping creation."
} else {
    Write-Output "Creating service principal..."
    az ad sp create --id $APP_ID
    if ($LASTEXITCODE -ne 0) { Write-Error "Failed to create service principal."; exit 1 }
}

# Create federated credential (or skip if exists)
$fedCred = az ad app federated-credential list --id $APP_ID --query "[?name=='github-main-branch'].name" -o tsv 2>&1
if ($fedCred -eq "github-main-branch") {
    Write-Output "Federated credential 'github-main-branch' already exists. Skipping creation."
} else {
    Write-Output "Creating federated credential 'github-main-branch'..."
    az ad app federated-credential create --id $APP_ID --parameters '{
        "name": "github-main-branch",
        "issuer": "https://token.actions.githubusercontent.com",
        "subject": "repo:travisnielsen/cadence:ref:refs/heads/main",
        "audiences": ["api://AzureADTokenExchange"]
      }'
    if ($LASTEXITCODE -ne 0) { Write-Error "Failed to create federated credential."; exit 1 }
}

# Grant Storage Blob Data Contributor role to the resource group
az role assignment create `
    --assignee $APP_ID `
    --role "Storage Blob Data Contributor" `
    --scope $scope

# Grant Storage Account Contributor role (required to modify network rules during deployment)
az role assignment create `
  --assignee $APP_ID `
  --role "Storage Account Contributor" `
  --scope $scope

# Grant Azure Container Registry Push permission (to push images)
az role assignment create `
  --assignee $APP_ID `
  --role "AcrPush" `
  --scope $scope

# Grant Container Apps Contributor permission (resource group level)
az role assignment create `
  --assignee $APP_ID `
  --role "Contributor" `
  --scope $scope


# Output values needed for GitHub Actions variables
$TENANT_ID = az account show --query "tenantId" -o tsv
Write-Output ""
Write-Output "=== Update these GitHub Actions variables (Settings > Secrets and variables > Actions > Variables) ==="
Write-Output "  AZURE_CLIENT_ID:       $APP_ID"
Write-Output "  AZURE_TENANT_ID:       $TENANT_ID"
Write-Output "  AZURE_SUBSCRIPTION_ID: $SUB_ID"
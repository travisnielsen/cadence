<#
.SYNOPSIS
    Configure SQL read access via Entra ID group for API managed identity

.DESCRIPTION
    Ensures an Entra ID group exists, ensures the API managed identity is a group member,
    and grants the group db_datareader in the target Azure SQL database.

.PARAMETER SqlServerName
    Azure SQL server name (without .database.windows.net suffix)

.PARAMETER DatabaseName
    Database name where read access should be granted

.PARAMETER ResourceGroup
    Resource group containing the user-assigned managed identity

.PARAMETER IdentityName
    User-assigned managed identity name for the API

.PARAMETER ReaderGroupName
    Entra ID group display name to grant SQL read access

.EXAMPLE
    ./setup-sql-reader-group.ps1 -SqlServerName "oovmfa-sql" -DatabaseName "WideWorldImportersStd" -ResourceGroup "cadence-oovmfa" -IdentityName "oovmfa-api-identity" -ReaderGroupName "cadence-sql-readers"
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SqlServerName,

    [Parameter(Mandatory = $true)]
    [string]$DatabaseName,

    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$IdentityName,

    [Parameter(Mandatory = $false)]
    [string]$ReaderGroupName = "cadence-sql-readers"
)

$ErrorActionPreference = "Stop"
$SqlFqdn = "$SqlServerName.database.windows.net"

Write-Host "=== SQL Reader Group Setup ===" -ForegroundColor Cyan
Write-Host "SQL Server:    $SqlFqdn"
Write-Host "Database:      $DatabaseName"
Write-Host "ResourceGroup: $ResourceGroup"
Write-Host "Identity:      $IdentityName"
Write-Host "Reader Group:  $ReaderGroupName"
Write-Host ""

try {
    $null = Get-Command az -ErrorAction Stop
}
catch {
    Write-Host "ERROR: Azure CLI (az) is not installed." -ForegroundColor Red
    exit 1
}

if (-not (Get-Module -ListAvailable -Name SqlServer)) {
    Write-Host "SqlServer module not found. Installing..." -ForegroundColor Yellow
    Install-Module -Name SqlServer -Scope CurrentUser -Force -AllowClobber
}
Import-Module SqlServer

# Resolve managed identity object id (principalId)
$identityObjectId = az identity show `
    --resource-group $ResourceGroup `
    --name $IdentityName `
    --query principalId -o tsv

if (-not $identityObjectId) {
    Write-Host "ERROR: Failed to resolve principalId for identity '$IdentityName'." -ForegroundColor Red
    exit 1
}

# Resolve or create Entra group
$groupId = az ad group list --filter "displayName eq '$ReaderGroupName'" --query "[0].id" -o tsv
if (-not $groupId) {
    $mailNickname = ($ReaderGroupName.ToLower() -replace '[^a-z0-9]', '-')
    $groupId = az ad group create `
        --display-name $ReaderGroupName `
        --mail-nickname $mailNickname `
        --query id -o tsv
    Write-Host "Created Entra group '$ReaderGroupName' ($groupId)" -ForegroundColor Green
}
else {
    Write-Host "Entra group exists: '$ReaderGroupName' ($groupId)" -ForegroundColor Green
}

# Ensure MI is a group member
$alreadyMember = az ad group member check --group $groupId --member-id $identityObjectId --query value -o tsv
if ($alreadyMember -ne "true") {
    az ad group member add --group $groupId --member-id $identityObjectId | Out-Null
    Write-Host "Added managed identity to group" -ForegroundColor Green
}
else {
    Write-Host "Managed identity already in group" -ForegroundColor Green
}

# Token for SQL
$accessToken = az account get-access-token --resource https://database.windows.net/ --query accessToken -o tsv
if (-not $accessToken) {
    Write-Host "ERROR: Failed to acquire SQL access token." -ForegroundColor Red
    exit 1
}

# Ensure SQL contained user for group and grant db_datareader
$sql = @"
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'$ReaderGroupName')
BEGIN
    CREATE USER [$ReaderGroupName] FROM EXTERNAL PROVIDER;
END;

IF NOT EXISTS (
    SELECT 1
    FROM sys.database_role_members drm
    JOIN sys.database_principals r ON drm.role_principal_id = r.principal_id
    JOIN sys.database_principals m ON drm.member_principal_id = m.principal_id
    WHERE r.name = N'db_datareader' AND m.name = N'$ReaderGroupName'
)
BEGIN
    ALTER ROLE db_datareader ADD MEMBER [$ReaderGroupName];
END;

SELECT name, type_desc FROM sys.database_principals WHERE name = N'$ReaderGroupName';
"@

$result = $null
$maxAttempts = 10
$delaySeconds = 15

for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    try {
        $result = Invoke-Sqlcmd `
            -ServerInstance $SqlFqdn `
            -Database $DatabaseName `
            -AccessToken $accessToken `
            -Query $sql `
            -Encrypt Mandatory `
            -TrustServerCertificate:$false
        break
    }
    catch {
        $message = $_.Exception.Message
        $isPropagationError = ($message -match "could not be found") -or ($message -match "does not exist")

        if ($isPropagationError -and $attempt -lt $maxAttempts) {
            Write-Host "Group not yet resolvable by SQL (attempt $attempt/$maxAttempts). Retrying in $delaySeconds seconds..." -ForegroundColor Yellow
            Start-Sleep -Seconds $delaySeconds
            continue
        }

        throw
    }
}

if (-not $result) {
    throw "Failed to configure SQL group principal after $maxAttempts attempts."
}

if ($result) {
    Write-Host "SQL user/group verified:" -ForegroundColor Yellow
    $result | ForEach-Object { Write-Host "  $($_.name) ($($_.type_desc))" }
}

Write-Host ""
Write-Host "=== SQL Reader Group Setup Complete ===" -ForegroundColor Cyan
Write-Host "Managed identity '$IdentityName' has read access via Entra group '$ReaderGroupName'." -ForegroundColor Green

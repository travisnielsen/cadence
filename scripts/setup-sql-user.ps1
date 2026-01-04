<#
.SYNOPSIS
    Setup SQL Database User for Managed Identity

.DESCRIPTION
    Creates a contained database user for the API managed identity.
    Uses SqlServer module with Entra ID access token authentication.

.PARAMETER SqlServerName
    The name of the Azure SQL Server (without .database.windows.net suffix)

.PARAMETER DatabaseName
    The name of the database to create the user in

.PARAMETER IdentityName
    The name of the managed identity to add as a database user

.EXAMPLE
    ./setup-sql-user.ps1 -SqlServerName "ay2q3p-sql" -DatabaseName "WideWorldImportersStd" -IdentityName "ay2q3p-api-identity"

.NOTES
    Prerequisites:
    - Azure CLI (az) - logged in with SQL Server admin permissions
    - PowerShell SqlServer module (Install-Module -Name SqlServer)
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SqlServerName,

    [Parameter(Mandatory = $true)]
    [string]$DatabaseName,

    [Parameter(Mandatory = $true)]
    [string]$IdentityName
)

$ErrorActionPreference = "Stop"

$SqlFqdn = "$SqlServerName.database.windows.net"

Write-Host "=== SQL Database User Setup for Managed Identity ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "SQL Server: $SqlFqdn"
Write-Host "Database:   $DatabaseName"
Write-Host "Identity:   $IdentityName"
Write-Host ""

# Check prerequisites
Write-Host "Checking prerequisites..." -ForegroundColor Yellow

# Check Azure CLI
try {
    $null = Get-Command az -ErrorAction Stop
    Write-Host "  Azure CLI found" -ForegroundColor Green
}
catch {
    Write-Host "ERROR: Azure CLI (az) is not installed." -ForegroundColor Red
    Write-Host "Install from: https://docs.microsoft.com/cli/azure/install-azure-cli"
    exit 1
}

# Check/Install SqlServer module
if (-not (Get-Module -ListAvailable -Name SqlServer)) {
    Write-Host "  SqlServer module not found. Installing..." -ForegroundColor Yellow
    try {
        Install-Module -Name SqlServer -Scope CurrentUser -Force -AllowClobber
        Write-Host "  SqlServer module installed" -ForegroundColor Green
    }
    catch {
        Write-Host "ERROR: Failed to install SqlServer module: $_" -ForegroundColor Red
        Write-Host "Try running: Install-Module -Name SqlServer -Scope CurrentUser -Force"
        exit 1
    }
}
else {
    Write-Host "  SqlServer module found" -ForegroundColor Green
}

Import-Module SqlServer

# Get access token
Write-Host ""
Write-Host "Getting Entra ID access token..." -ForegroundColor Yellow
try {
    $accessToken = az account get-access-token --resource https://database.windows.net/ --query accessToken -o tsv
    if (-not $accessToken) {
        throw "Empty token returned"
    }
    Write-Host "  Access token obtained successfully" -ForegroundColor Green
}
catch {
    Write-Host "ERROR: Failed to get access token: $_" -ForegroundColor Red
    Write-Host "Make sure you're logged in with 'az login'"
    exit 1
}

# SQL commands to execute
$sqlCommands = @"
-- Drop existing user if exists
DROP USER IF EXISTS [$IdentityName];

-- Create user using FROM EXTERNAL PROVIDER
-- This lets SQL Server resolve the managed identity correctly with proper SID byte ordering
CREATE USER [$IdentityName] FROM EXTERNAL PROVIDER;

-- Grant read permissions
ALTER ROLE db_datareader ADD MEMBER [$IdentityName];

-- Grant write permissions
ALTER ROLE db_datawriter ADD MEMBER [$IdentityName];

-- Verify the user was created
SELECT name, type_desc, CONVERT(NVARCHAR(128), sid, 1) as sid_hex 
FROM sys.database_principals 
WHERE name = '$IdentityName';
"@

Write-Host ""
Write-Host "Connecting to database and creating user..." -ForegroundColor Yellow
Write-Host ""

try {
    # Execute SQL using Invoke-Sqlcmd with access token
    $result = Invoke-Sqlcmd `
        -ServerInstance $SqlFqdn `
        -Database $DatabaseName `
        -AccessToken $accessToken `
        -Query $sqlCommands `
        -TrustServerCertificate:$false `
        -Encrypt Mandatory

    Write-Host "  User created successfully" -ForegroundColor Green
    Write-Host "  Added to db_datareader role" -ForegroundColor Green
    Write-Host "  Added to db_datawriter role" -ForegroundColor Green
    
    if ($result) {
        Write-Host ""
        Write-Host "Verified user:" -ForegroundColor Yellow
        Write-Host "  Name: $($result.name)"
        Write-Host "  Type: $($result.type_desc)"
        Write-Host "  SID:  $($result.sid_hex)"
    }
}
catch {
    Write-Host "ERROR: SQL operation failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "The managed identity '$IdentityName' now has access to:"
Write-Host "  - Read data (db_datareader)"
Write-Host "  - Write data (db_datawriter)"
Write-Host ""

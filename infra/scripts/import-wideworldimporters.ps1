<#
.SYNOPSIS
    Import Wide World Importers Standard database to Azure SQL

.DESCRIPTION
    Downloads the WideWorldImporters-Standard BACPAC from Microsoft's official
    release and imports it to Azure SQL Database using sqlpackage with Entra ID
    authentication.

    The script automatically checks for and installs required dependencies
    (sqlpackage, .NET 8 runtime) if they are missing.

.PARAMETER SqlServerName
    The name of the Azure SQL Server (without .database.windows.net suffix)

.PARAMETER ResourceGroup
    The name of the Azure Resource Group containing the SQL Server

.PARAMETER DatabaseName
    The name of the database to create (default: WideWorldImportersStd)

.EXAMPLE
    ./import-wideworldimporters.ps1 -SqlServerName "myserver-sql" -ResourceGroup "myresourcegroup"

.NOTES
    Prerequisites:
    - Azure CLI (az) - logged in with appropriate permissions
    - .NET 8 runtime (auto-installed on Linux if missing)
    - sqlpackage (auto-installed via dotnet tool if missing)
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SqlServerName,

    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $false)]
    [string]$DatabaseName = "WideWorldImportersStd",

    [Parameter(Mandatory = $false)]
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$BacpacUrl = "https://github.com/Microsoft/sql-server-samples/releases/download/wide-world-importers-v1.0/WideWorldImporters-Standard.bacpac"

# Use cross-platform temp directory
$TempDir = if ($env:TEMP) { $env:TEMP } elseif ($env:TMPDIR) { $env:TMPDIR } else { "/tmp" }
$BacpacFile = Join-Path $TempDir "WideWorldImporters-Standard.bacpac"

Write-Host "=== Wide World Importers Database Import ===" -ForegroundColor Cyan
Write-Host ""

# Check prerequisites
Write-Host "Checking prerequisites..." -ForegroundColor Yellow

# Check Azure CLI
try {
    $null = Get-Command az -ErrorAction Stop
}
catch {
    Write-Host "ERROR: Azure CLI (az) is not installed." -ForegroundColor Red
    Write-Host "Install from: https://docs.microsoft.com/cli/azure/install-azure-cli"
    exit 1
}

# Check sqlpackage and its dependencies
$sqlpackagePath = $null
try {
    $sqlpackagePath = (Get-Command sqlpackage -ErrorAction Stop).Source
}
catch {
    # Try common installation paths
    $possiblePaths = @(
        "$env:HOME/.dotnet/tools/sqlpackage",
        "$env:USERPROFILE\.dotnet\tools\sqlpackage.exe",
        "C:\Program Files\Microsoft SQL Server\160\DAC\bin\sqlpackage.exe",
        "C:\Program Files\Microsoft SQL Server\150\DAC\bin\sqlpackage.exe",
        "C:\Program Files (x86)\Microsoft SQL Server\160\DAC\bin\sqlpackage.exe"
    )
    
    foreach ($path in $possiblePaths) {
        if ($path -and (Test-Path $path)) {
            $sqlpackagePath = $path
            break
        }
    }
}

if (-not $sqlpackagePath) {
    Write-Host "  sqlpackage not found. Installing via dotnet tool..." -ForegroundColor Yellow
    
    # Check if dotnet CLI is available
    try {
        $null = Get-Command dotnet -ErrorAction Stop
    }
    catch {
        Write-Host "ERROR: .NET SDK/CLI is not installed." -ForegroundColor Red
        if ($IsLinux) {
            Write-Host "Install with: sudo apt-get install -y dotnet-sdk-8.0" -ForegroundColor Yellow
        } else {
            Write-Host "Install from: https://dotnet.microsoft.com/download" -ForegroundColor Yellow
        }
        exit 1
    }
    
    dotnet tool install -g microsoft.sqlpackage
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to install sqlpackage." -ForegroundColor Red
        exit 1
    }
    
    # Add dotnet tools to PATH for this session
    $toolsDir = if ($IsLinux -or $IsMacOS) { "$env:HOME/.dotnet/tools" } else { "$env:USERPROFILE\.dotnet\tools" }
    if ($toolsDir -and ($env:PATH -notlike "*$toolsDir*")) {
        $env:PATH = "$toolsDir$([System.IO.Path]::PathSeparator)$env:PATH"
    }
    
    $sqlpackagePath = (Get-Command sqlpackage -ErrorAction Stop).Source
}

Write-Host "  sqlpackage found: $sqlpackagePath" -ForegroundColor Green

# Verify .NET 8 runtime is available (required by sqlpackage)
if ($IsLinux -or $IsMacOS) {
    $dotnetRuntimes = dotnet --list-runtimes 2>$null
    if ($dotnetRuntimes -and ($dotnetRuntimes -notmatch "Microsoft\.NETCore\.App 8\.")) {
        Write-Host "  .NET 8 runtime not found. sqlpackage requires it." -ForegroundColor Yellow
        if ($IsLinux) {
            Write-Host "  Installing dotnet-runtime-8.0..." -ForegroundColor Yellow
            sudo apt-get update -qq && sudo apt-get install -y -qq dotnet-runtime-8.0
            if ($LASTEXITCODE -ne 0) {
                Write-Host "ERROR: Failed to install .NET 8 runtime." -ForegroundColor Red
                Write-Host "Install manually: sudo apt-get install -y dotnet-runtime-8.0" -ForegroundColor Yellow
                exit 1
            }
            Write-Host "  .NET 8 runtime installed." -ForegroundColor Green
        } else {
            Write-Host "ERROR: Install .NET 8 runtime from https://dotnet.microsoft.com/download/dotnet/8.0" -ForegroundColor Red
            exit 1
        }
    }
}

# Verify Azure login
Write-Host "Verifying Azure CLI login..." -ForegroundColor Yellow
try {
    $account = az account show 2>$null | ConvertFrom-Json
    if (-not $account) {
        throw "Not logged in"
    }
    Write-Host "  Using subscription: $($account.name)" -ForegroundColor Green
}
catch {
    Write-Host "ERROR: Not logged into Azure CLI. Run 'az login' first." -ForegroundColor Red
    exit 1
}

# Verify SQL server exists
Write-Host "Verifying SQL server exists..." -ForegroundColor Yellow
try {
    $server = az sql server show --name $SqlServerName --resource-group $ResourceGroup 2>$null | ConvertFrom-Json
    if (-not $server) {
        throw "Server not found"
    }
}
catch {
    Write-Host "ERROR: SQL server '$SqlServerName' not found in resource group '$ResourceGroup'" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Configuration:" -ForegroundColor Cyan
Write-Host "  Server: $SqlServerName.database.windows.net"
Write-Host "  Database: $DatabaseName"
Write-Host "  Resource Group: $ResourceGroup"
Write-Host ""

# Download BACPAC if needed
if (Test-Path $BacpacFile) {
    Write-Host "BACPAC file already exists at $BacpacFile" -ForegroundColor Yellow
    if (-not $Force) {
        $redownload = Read-Host "Re-download? (y/N)"
        if ($redownload -eq "y" -or $redownload -eq "Y") {
            Remove-Item $BacpacFile -Force
        }
    }
}

if (-not (Test-Path $BacpacFile)) {
    Write-Host "Downloading WideWorldImporters-Standard.bacpac..." -ForegroundColor Yellow
    Write-Host "  Source: $BacpacUrl"
    
    # Use .NET WebClient for faster download with progress
    $webClient = New-Object System.Net.WebClient
    try {
        $webClient.DownloadFile($BacpacUrl, $BacpacFile)
        Write-Host "  Download complete." -ForegroundColor Green
    }
    finally {
        $webClient.Dispose()
    }
}

Write-Host ""

# Check if database exists
Write-Host "Checking for existing database..." -ForegroundColor Yellow
$existingDb = az sql db show --name $DatabaseName --server $SqlServerName --resource-group $ResourceGroup 2>$null | ConvertFrom-Json

if ($existingDb) {
    Write-Host "Database '$DatabaseName' already exists." -ForegroundColor Yellow
    
    if (-not $Force) {
        $recreate = Read-Host "Delete and recreate from BACPAC? (y/N)"
        
        if ($recreate -ne "y" -and $recreate -ne "Y") {
            Write-Host "Aborted." -ForegroundColor Yellow
            exit 0
        }
    }
    
    Write-Host "Deleting existing database..." -ForegroundColor Yellow
    az sql db delete `
        --name $DatabaseName `
        --server $SqlServerName `
        --resource-group $ResourceGroup `
        --yes
    
    Write-Host "Waiting for deletion to complete..." -ForegroundColor Yellow
    Start-Sleep -Seconds 15
}

Write-Host ""
Write-Host "Importing BACPAC using sqlpackage..." -ForegroundColor Cyan
Write-Host "This may take 5-10 minutes..." -ForegroundColor Yellow
Write-Host ""

# Get Azure AD access token for SQL Database
Write-Host "Acquiring Azure AD access token for SQL Database..." -ForegroundColor Yellow
$accessToken = az account get-access-token --resource https://database.windows.net/ --query accessToken -o tsv
if (-not $accessToken) {
    Write-Host "ERROR: Failed to get access token. Ensure you're logged in with 'az login'." -ForegroundColor Red
    exit 1
}
Write-Host "  Access token acquired." -ForegroundColor Green

# Import using sqlpackage with access token authentication
$sqlpackageArgs = @(
    "/Action:Import",
    "/SourceFile:$BacpacFile",
    "/TargetServerName:$SqlServerName.database.windows.net",
    "/TargetDatabaseName:$DatabaseName",
    "/AccessToken:$accessToken"
)

& $sqlpackagePath @sqlpackageArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: sqlpackage import failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "=== Import Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Connection details:" -ForegroundColor Cyan
Write-Host "  Server: $SqlServerName.database.windows.net"
Write-Host "  Database: $DatabaseName"
Write-Host "  Authentication: Azure AD (your current user)"
Write-Host ""
Write-Host "Test connection with:" -ForegroundColor Yellow
Write-Host "  az sql db execute --server $SqlServerName --name $DatabaseName ```"
Write-Host "    --resource-group $ResourceGroup ```"
Write-Host "    --query 'SELECT TOP 5 CustomerName FROM Sales.Customers'"

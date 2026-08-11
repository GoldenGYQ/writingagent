param(
    [string]$InstallerPath,
    [int]$Port = 8899,
    [int]$GatewayPort = 18899
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$distRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "dist"))
$pyproject = Get-Content -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Raw
$versionMatch = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    throw "Could not read the application version from pyproject.toml."
}
if (-not $InstallerPath) {
    $InstallerPath = "dist\installer\JLU-Writing-Agent-Setup-$($versionMatch.Groups[1].Value).exe"
}
$installer = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $InstallerPath))
$installDir = [System.IO.Path]::GetFullPath((Join-Path $distRoot "smoke-install"))
$profileDir = [System.IO.Path]::GetFullPath((Join-Path $distRoot "smoke-profile"))

foreach ($target in @($installDir, $profileDir)) {
    if (-not $target.StartsWith($distRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe smoke-test target: $target"
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

if (-not (Test-Path -LiteralPath $installer)) {
    throw "Installer not found: $installer"
}

New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
$installArgs = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/SP-",
    "/DIR=`"$installDir`""
)
$install = Start-Process `
    -FilePath $installer `
    -ArgumentList $installArgs `
    -Wait `
    -PassThru `
    -WindowStyle Hidden
if ($install.ExitCode -ne 0) {
    throw "Installer exited with code $($install.ExitCode)."
}

$app = Join-Path $installDir "JLU Writing Agent.exe"
if (-not (Test-Path -LiteralPath $app)) {
    throw "Installed executable is missing: $app"
}

$config = Join-Path $profileDir "config.json"
$workspace = Join-Path $profileDir "workspace"
$appArgs = @(
    "--port", "$Port",
    "--gateway-port", "$GatewayPort",
    "--config", "`"$config`"",
    "--workspace", "`"$workspace`""
)
$process = Start-Process -FilePath $app -ArgumentList $appArgs -PassThru

$ready = $false
for ($attempt = 0; $attempt -lt 150; $attempt++) {
    Start-Sleep -Milliseconds 200
    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -TimeoutSec 1 `
            -Uri "http://127.0.0.1:$GatewayPort/health"
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    }
    catch {
        # The gateway is still starting.
    }
    if ($process.HasExited) {
        break
    }
}

if (-not $ready) {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
    throw "Installed desktop app did not expose a healthy gateway."
}

try {
    $webuiResponse = Invoke-WebRequest `
        -UseBasicParsing `
        -TimeoutSec 5 `
        -Uri "http://127.0.0.1:$Port/"
}
catch {
    Stop-Process -Id $process.Id -Force
    throw "Installed desktop app did not serve the bundled WebUI."
}
if ($webuiResponse.StatusCode -ne 200 -or $webuiResponse.Content.Trim() -eq "Not Found") {
    Stop-Process -Id $process.Id -Force
    throw "Installed desktop app did not serve the bundled WebUI."
}

$windowReady = $false
for ($attempt = 0; $attempt -lt 50; $attempt++) {
    $process.Refresh()
    if ($process.MainWindowHandle -ne 0) {
        $windowReady = $true
        break
    }
    Start-Sleep -Milliseconds 200
}
if (-not $windowReady) {
    Stop-Process -Id $process.Id -Force
    throw "Desktop process did not expose a main window."
}

$closed = $process.CloseMainWindow()
if ($closed) {
    $process.WaitForExit(15000) | Out-Null
}
if (-not $process.HasExited) {
    Stop-Process -Id $process.Id -Force
    throw "Desktop window did not close cleanly."
}

Start-Sleep -Seconds 2
$gatewayStopped = $false
try {
    Invoke-WebRequest `
        -UseBasicParsing `
        -TimeoutSec 1 `
        -Uri "http://127.0.0.1:$GatewayPort/health" | Out-Null
}
catch {
    $gatewayStopped = $true
}
if (-not $gatewayStopped) {
    throw "Gateway child remained alive after the desktop window closed."
}

# Reopen the same initialized profile without provider credentials. The desktop
# shell must still reach Settings instead of falling back to an interactive CLI
# onboarding prompt that a windowed application cannot answer.
$reopenProcess = Start-Process -FilePath $app -ArgumentList $appArgs -PassThru
$reopenReady = $false
for ($attempt = 0; $attempt -lt 150; $attempt++) {
    Start-Sleep -Milliseconds 200
    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -TimeoutSec 1 `
            -Uri "http://127.0.0.1:$GatewayPort/health"
        if ($response.StatusCode -eq 200) {
            $reopenReady = $true
            break
        }
    }
    catch {
        # The gateway is still starting.
    }
    if ($reopenProcess.HasExited) {
        break
    }
}
if (-not $reopenReady) {
    if (-not $reopenProcess.HasExited) {
        Stop-Process -Id $reopenProcess.Id -Force
    }
    throw "Desktop app could not reopen its initialized profile."
}


try {
    $reopenWebuiResponse = Invoke-WebRequest `
        -UseBasicParsing `
        -TimeoutSec 5 `
        -Uri "http://127.0.0.1:$Port/"
}
catch {
    Stop-Process -Id $reopenProcess.Id -Force
    throw "Reopened desktop app did not serve the bundled WebUI."
}
if (
    $reopenWebuiResponse.StatusCode -ne 200 -or
    $reopenWebuiResponse.Content.Trim() -eq "Not Found"
) {
    Stop-Process -Id $reopenProcess.Id -Force
    throw "Reopened desktop app did not serve the bundled WebUI."
}

$reopenWindowReady = $false
for ($attempt = 0; $attempt -lt 50; $attempt++) {
    $reopenProcess.Refresh()
    if ($reopenProcess.MainWindowHandle -ne 0) {
        $reopenWindowReady = $true
        break
    }
    Start-Sleep -Milliseconds 200
}
if (-not $reopenWindowReady) {
    Stop-Process -Id $reopenProcess.Id -Force
    throw "Reopened desktop process did not expose a main window."
}

$reopenClosed = $reopenProcess.CloseMainWindow()
if ($reopenClosed) {
    $reopenProcess.WaitForExit(15000) | Out-Null
}
if (-not $reopenProcess.HasExited) {
    Stop-Process -Id $reopenProcess.Id -Force
    throw "Reopened desktop window did not close cleanly."
}

Start-Sleep -Seconds 2
$reopenGatewayStopped = $false
try {
    Invoke-WebRequest `
        -UseBasicParsing `
        -TimeoutSec 1 `
        -Uri "http://127.0.0.1:$GatewayPort/health" | Out-Null
}
catch {
    $reopenGatewayStopped = $true
}
if (-not $reopenGatewayStopped) {
    throw "Gateway child remained alive after the reopened desktop window closed."
}

$installerItem = Get-Item -LiteralPath $installer
$bundleSize = (
    Get-ChildItem -LiteralPath $installDir -Recurse -File |
        Measure-Object Length -Sum
).Sum
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $installer

[pscustomobject]@{
    Installer = $installerItem.FullName
    InstallerMB = [math]::Round($installerItem.Length / 1MB, 2)
    InstalledMB = [math]::Round($bundleSize / 1MB, 2)
    Sha256 = $hash.Hash
    GatewayReady = $ready
    WebUIReady = $webuiResponse.StatusCode -eq 200
    CleanShutdown = $gatewayStopped
    ReopenReady = $reopenReady
    ReopenWebUIReady = $reopenWebuiResponse.StatusCode -eq 200
    ReopenShutdown = $reopenGatewayStopped
} | Format-List

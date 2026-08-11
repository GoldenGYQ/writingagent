param(
    [switch]$SkipWebBuild,
    [switch]$SkipDesktopBuild,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$webuiRoot = Join-Path $projectRoot "webui"
$specPath = Join-Path $projectRoot "packaging\windows\writing-agent.spec"
$issPath = Join-Path $projectRoot "packaging\windows\installer.iss"
$desktopDist = Join-Path $projectRoot "dist\desktop"
$bundleDir = Join-Path $desktopDist "JLU Writing Agent"
$installerDir = Join-Path $projectRoot "dist\installer"
$desktopEnvironment = Join-Path $projectRoot ".venv-desktop"

$pyproject = Get-Content -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Raw
$versionMatch = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    throw "Could not read the application version from pyproject.toml."
}
$appVersion = $versionMatch.Groups[1].Value

if (-not $SkipWebBuild) {
    Push-Location $webuiRoot
    try {
        & bun install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw "WebUI dependency installation failed." }
        & bun run build
        if ($LASTEXITCODE -ne 0) { throw "WebUI build failed." }
    }
    finally {
        Pop-Location
    }
}

if (-not $SkipDesktopBuild) {
    Push-Location $projectRoot
    $previousUvEnvironment = $env:UV_PROJECT_ENVIRONMENT
    try {
        $env:UV_PROJECT_ENVIRONMENT = $desktopEnvironment
        & uv sync --extra desktop
        if ($LASTEXITCODE -ne 0) { throw "Desktop build dependency installation failed." }
        $pyinstaller = Join-Path $desktopEnvironment "Scripts\pyinstaller.exe"
        & $pyinstaller --noconfirm --clean --distpath $desktopDist $specPath
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
    }
    finally {
        $env:UV_PROJECT_ENVIRONMENT = $previousUvEnvironment
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $bundleDir "JLU Writing Agent.exe"))) {
    throw "Desktop executable was not produced at $bundleDir."
}

if ($SkipInstaller) {
    Write-Host "Desktop bundle created: $bundleDir"
    exit 0
}

$isccCandidates = @(
    $env:ISCC_PATH,
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

if (-not $isccCandidates) {
    throw "Inno Setup 6 is required. Install it with: winget install --id JRSoftware.InnoSetup -e"
}

New-Item -ItemType Directory -Force -Path $installerDir | Out-Null
$iscc = @($isccCandidates)[0]
& $iscc "/DMyAppVersion=$appVersion" "/DSourceDir=$bundleDir" "/DOutputDir=$installerDir" $issPath
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }

$installer = Join-Path $installerDir "JLU-Writing-Agent-Setup-$appVersion.exe"
if (-not (Test-Path -LiteralPath $installer)) {
    throw "Installer was not produced at $installer."
}

Write-Host "Installer created: $installer"

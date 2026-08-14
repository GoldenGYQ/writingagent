param(
    [string]$Version,
    [switch]$PublishRelease,
    [string]$Repository = "GoldenGYQ/writingagent",
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
$appVersion = if ($Version) { $Version.Trim().TrimStart("v", "V") } else { $versionMatch.Groups[1].Value }
if ($appVersion -notmatch '^\d+\.\d+\.\d+$') {
    throw "Invalid version '$appVersion'. Use a semantic version such as 0.3.1."
}
if ($Version) {
    $updatedPyproject = [regex]::Replace(
        $pyproject,
        '(?m)^version\s*=\s*"[^"]+"',
        "version = `"$appVersion`"",
        1
    )
    if ($updatedPyproject -ne $pyproject) {
        Set-Content -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Value $updatedPyproject -Encoding utf8NoBOM
    }
    Write-Host "Project version updated to $appVersion"
}

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

$checksumPath = "$installer.sha256"
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
Set-Content -LiteralPath $checksumPath -Value "$hash  $(Split-Path -Leaf $installer)" -Encoding ascii
Write-Host "Checksum created: $checksumPath"

if ($PublishRelease) {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "GitHub CLI is required for -PublishRelease. Install it with: winget install --id GitHub.cli -e"
    }
    if (-not $Repository -or $Repository -notmatch '^[^/]+/[^/]+$') {
        throw "Repository must have the form owner/repository."
    }
    $tag = "v$appVersion"
    & gh release view $tag --repo $Repository *> $null
    if ($LASTEXITCODE -eq 0) {
        throw "GitHub Release $tag already exists in $Repository. Choose a new version."
    }
    & gh release create $tag $installer $checksumPath `
        --repo $Repository `
        --title "JLU Writing Agent $tag" `
        --generate-notes
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub Release publication failed. Check 'gh auth status' and repository permissions."
    }
    Write-Host "GitHub Release published: $Repository/$tag"
}

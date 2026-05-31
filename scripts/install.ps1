param(
    [string]$Version = $(if ($env:POLICYNIM_VERSION) { $env:POLICYNIM_VERSION } else { "latest" })
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$RepositoryUrl = if ($env:POLICYNIM_REPOSITORY_URL) { $env:POLICYNIM_REPOSITORY_URL } else { "https://github.com/nnennandukwe/policyNIM" }
$ChecksumsFile = "SHA256SUMS"

function Stop-Install([string]$Message) {
    Write-Error $Message
    exit 1
}

function Get-NormalizedArchitecture {
    $rawArch = if ($env:POLICYNIM_INSTALLER_TEST_ARCH) { $env:POLICYNIM_INSTALLER_TEST_ARCH } else { $env:PROCESSOR_ARCHITECTURE }
    if ([string]::IsNullOrWhiteSpace($rawArch)) {
        return "unknown"
    }
    switch -Regex ($rawArch) {
        "^(AMD64|x86_64)$" { "amd64"; return }
        default { $rawArch.ToLowerInvariant(); return }
    }
}

function Resolve-LatestVersion {
    try {
        $response = Invoke-WebRequest -Uri "$RepositoryUrl/releases/latest" -MaximumRedirection 10 -UseBasicParsing
        $resolvedUrl = $response.BaseResponse.ResponseUri.AbsoluteUri
    } catch {
        Stop-Install "Could not resolve the latest PolicyNIM release version. Pass a version as POLICYNIM_VERSION or as the first argument."
    }

    $latestTag = ($resolvedUrl.TrimEnd("/") -split "/")[-1]
    if (-not $latestTag.StartsWith("v")) {
        Stop-Install "Could not resolve the latest PolicyNIM release version. Pass a version as POLICYNIM_VERSION or as the first argument."
    }
    return $latestTag.Substring(1)
}

function Download-Asset([string]$SourceUrl, [string]$Destination, [string]$Label) {
    try {
        Invoke-WebRequest -Uri $SourceUrl -OutFile $Destination -UseBasicParsing
    } catch {
        Stop-Install "Could not download release asset $Label from $SourceUrl. Check the release page or retry the install."
    }
}

function Test-ShouldVerifyAttestation {
    if (-not $env:POLICYNIM_VERIFY_ATTESTATION) {
        return $false
    }
    $mode = $env:POLICYNIM_VERIFY_ATTESTATION.ToLowerInvariant()
    return $mode -in @("1", "true", "yes", "required")
}

function Get-RepositorySlug {
    $slug = $RepositoryUrl.TrimEnd("/")
    $slug = $slug -replace "^https?://github\.com/", ""
    $slug = $slug -replace "^git@github\.com:", ""
    $slug = $slug -replace "\.git$", ""
    if ($slug -notmatch "^[^/]+/[^/]+$") {
        Stop-Install "Could not derive GitHub repository slug from $RepositoryUrl for attestation verification."
    }
    return $slug
}

function Verify-Attestation([string]$AssetPath, [string]$AssetName) {
    if (-not (Test-ShouldVerifyAttestation)) {
        return
    }
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        Stop-Install "Missing required command: gh. Install GitHub CLI or unset POLICYNIM_VERIFY_ATTESTATION and retry."
    }
    $slug = Get-RepositorySlug
    & gh attestation verify $AssetPath -R $slug
    if ($LASTEXITCODE -ne 0) {
        Stop-Install "Artifact attestation verification failed for $AssetName. Check $releasePageUrl and retry the install."
    }
}

function Replace-InstallDirectory([string]$StagingDir, [string]$InstallDir, [string]$Version) {
    $installParent = Split-Path -Parent $InstallDir
    $backupDir = Join-Path $installParent ".$Version.backup.$PID"
    if (Test-Path $backupDir) {
        Remove-Item -Recurse -Force $backupDir
    }
    if (Test-Path $InstallDir) {
        Move-Item -Path $InstallDir -Destination $backupDir
    }
    try {
        Move-Item -Path $StagingDir -Destination $InstallDir
        if (Test-Path $backupDir) {
            Remove-Item -Recurse -Force $backupDir
        }
    } catch {
        if (Test-Path $backupDir) {
            Move-Item -Path $backupDir -Destination $InstallDir
        }
        Stop-Install "Could not replace install directory $InstallDir. Existing install was restored."
    }
}

function Write-Launcher([string]$InstallDir, [string]$LauncherPath) {
    $launcherDir = Split-Path -Parent $LauncherPath
    New-Item -ItemType Directory -Force -Path $launcherDir | Out-Null
    $binaryPath = Join-Path $InstallDir "policynim.exe"
    $launcher = @"
@echo off
"$binaryPath" %*
"@
    Set-Content -Path $LauncherPath -Value $launcher -Encoding ASCII
}

$osName = if ($env:POLICYNIM_INSTALLER_TEST_OS) { $env:POLICYNIM_INSTALLER_TEST_OS.ToLowerInvariant() } else { "windows" }
$archName = Get-NormalizedArchitecture
$platform = "$osName-$archName"
if ($platform -ne "windows-amd64") {
    Stop-Install "Unsupported platform: $platform. Supported platform: windows-amd64."
}

if ($Version -eq "latest") {
    $Version = Resolve-LatestVersion
}
$Version = $Version.TrimStart("v")
$tag = "v$Version"
$assetName = "policynim-$tag-windows-amd64.zip"
$releaseBaseUrl = if ($env:POLICYNIM_RELEASE_BASE_URL) { $env:POLICYNIM_RELEASE_BASE_URL } else { "$RepositoryUrl/releases/download/$tag" }
$releasePageUrl = "$RepositoryUrl/releases/tag/$tag"
$installDir = Join-Path $env:LocalAppData "PolicyNIM\$Version"
$installParent = Split-Path -Parent $installDir
$launcherDir = Join-Path $env:LocalAppData "PolicyNIM\bin"
$launcherPath = Join-Path $launcherDir "policynim.cmd"

$workDir = Join-Path ([System.IO.Path]::GetTempPath()) "policynim-install-$PID"
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
try {
    $assetPath = Join-Path $workDir $assetName
    $checksumsPath = Join-Path $workDir $ChecksumsFile
    $extractDir = Join-Path $workDir "extract"
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null

    Download-Asset "$releaseBaseUrl/$assetName" $assetPath $assetName
    Download-Asset "$releaseBaseUrl/$ChecksumsFile" $checksumsPath $ChecksumsFile

    $checksumLine = Get-Content $checksumsPath | Where-Object { ($_ -split "\s+")[-1] -eq $assetName } | Select-Object -First 1
    if (-not $checksumLine) {
        Stop-Install "Checksum entry for $assetName was not found in $ChecksumsFile. Check $releasePageUrl and retry."
    }
    $expectedChecksum = ($checksumLine -split "\s+")[0].ToLowerInvariant()
    $actualChecksum = (Get-FileHash -Algorithm SHA256 -Path $assetPath).Hash.ToLowerInvariant()
    if ($actualChecksum -ne $expectedChecksum) {
        Stop-Install "Checksum mismatch for $assetName. Check $releasePageUrl and retry the install."
    }

    Verify-Attestation $assetPath $assetName

    try {
        Expand-Archive -Path $assetPath -DestinationPath $extractDir -Force
    } catch {
        Stop-Install "Could not extract PolicyNIM bundle. Delete the downloaded asset and retry the install."
    }

    $bundleBinary = Get-ChildItem -Path $extractDir -Filter "policynim.exe" -File -Recurse | Select-Object -First 1
    if (-not $bundleBinary) {
        Stop-Install "Extracted asset $assetName did not contain policynim.exe. Check $releasePageUrl and retry."
    }

    $stagingDir = Join-Path $installParent ".$Version.staging.$PID"
    if (Test-Path $stagingDir) {
        Remove-Item -Recurse -Force $stagingDir
    }
    New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null
    Copy-Item -Path (Join-Path $bundleBinary.DirectoryName "*") -Destination $stagingDir -Recurse -Force

    New-Item -ItemType Directory -Force -Path $installParent | Out-Null
    Replace-InstallDirectory $stagingDir $installDir $Version
    Write-Launcher $installDir $launcherPath

    Write-Host "Installed PolicyNIM $Version to $installDir."
    Write-Host "Launcher: $launcherPath"
    if (($env:Path -split ";") -notcontains $launcherDir) {
        Write-Host "Add PolicyNIM to PATH for future PowerShell sessions:"
        Write-Host '$launcherDir = Join-Path $env:LocalAppData "PolicyNIM\bin"; [Environment]::SetEnvironmentVariable("Path", ([Environment]::GetEnvironmentVariable("Path", "User") + ";" + $launcherDir).Trim(";"), "User")'
    }
    Write-Host "Run ``policynim quickstart`` to choose a first-run path."
    Write-Host "Hosted MCP does not require ``policynim init`` or ``policynim ingest``."
    Write-Host "For local CLI or local MCP, run ``policynim init`` then ``policynim ingest``."
    Write-Host "Run ``policynim doctor`` to inspect first-run setup."
    Write-Host "Run ``policynim support-bundle`` before opening an issue."
} finally {
    if (Test-Path $workDir) {
        Remove-Item -Recurse -Force $workDir
    }
}

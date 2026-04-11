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
$assetName = "policynim-$tag-$platform"
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
    $assetZipPath = Join-Path $workDir "$assetName.zip"
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

    Copy-Item -Path $assetPath -Destination $assetZipPath
    try {
        Expand-Archive -Path $assetZipPath -DestinationPath $extractDir -Force
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
    Write-Host "Run ``policynim init`` to configure your local NVIDIA API key."
} finally {
    if (Test-Path $workDir) {
        Remove-Item -Recurse -Force $workDir
    }
}

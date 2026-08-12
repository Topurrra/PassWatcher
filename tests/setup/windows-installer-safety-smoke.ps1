[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Installer
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "windows-installer-cleanup.ps1")

if ($env:PASSWATCHER_SMOKE_ISOLATED_USER -ne "1") {
    throw "Run installer safety smoke tests only in a disposable Windows user or CI sandbox."
}
if ($env:PASSWATCHER_SMOKE_DESTRUCTIVE_CANARIES -ne "1") {
    throw "Set PASSWATCHER_SMOKE_DESTRUCTIVE_CANARIES=1 to authorize exact disposable canary paths."
}

$installerMatches = @(Get-Item -Path $Installer -ErrorAction Stop)
if ($installerMatches.Count -ne 1) {
    throw "Installer must resolve to exactly one file; found $($installerMatches.Count)."
}
$installerPath = $installerMatches[0].FullName

$localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$installDirectory = Join-Path $localAppData "Programs\Passwatcher"
$installedPasswatcher = Join-Path $installDirectory "passwatcher.exe"
$uninstaller = Join-Path $installDirectory "uninstall.exe"
$uninstallSubKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\Passwatcher"
$installerProductSubKey = "Software\Passwatcher"
$installerStateSubKey = "Software\Passwatcher\Installer"
$pathOwnershipValueName = "PathEntryAddedByInstall"
$unrelatedInstallerStateValueName = "UnrelatedSafetyCanary"
$unexpectedInstallFile = Join-Path $installDirectory "unexpected-user-file.sentinel"
$sentinelToken = "passwatcher-safety-$([Guid]::NewGuid().ToString('N'))"

$tempDirectory = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$canaryRoot = Join-Path $tempDirectory $sentinelToken
$canaryRootFullPath = [IO.Path]::GetFullPath($canaryRoot)
if (-not $canaryRootFullPath.StartsWith("$tempDirectory\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Canary root escaped the disposable temp directory: $canaryRootFullPath"
}
if (Test-Path -LiteralPath $canaryRootFullPath) {
    throw "Refusing to reuse an existing installer safety canary root: $canaryRootFullPath"
}
$canaryRootMarker = Join-Path $canaryRootFullPath ".passwatcher-owned-canary-root"

function New-DeletionCanary {
    param([Parameter(Mandatory)][string]$Name)

    $directory = Join-Path $canaryRootFullPath $Name
    $nestedDirectory = Join-Path $directory "nested"
    New-Item -ItemType Directory -Path $nestedDirectory | Out-Null
    $sentinelPath = Join-Path $nestedDirectory "sentinel.txt"
    Set-Content -LiteralPath $sentinelPath -Value $sentinelToken -Encoding UTF8
    return [PSCustomObject]@{ Directory = $directory; Sentinel = $sentinelPath }
}

function Assert-CanarySurvived {
    param([Parameter(Mandatory)]$Canary)

    if (-not (Test-Path -LiteralPath $Canary.Sentinel -PathType Leaf)) {
        throw "Disposable deletion canary was removed: $($Canary.Sentinel)"
    }
    $value = (Get-Content -Raw -LiteralPath $Canary.Sentinel).Trim()
    if ($value -cne $sentinelToken) {
        throw "Disposable deletion canary content changed: $($Canary.Sentinel)"
    }
}

function Invoke-CheckedProcess {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )

    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "$FilePath exited with code $($process.ExitCode)."
    }
}

function Test-RegistrySubKeyExists {
    param([Parameter(Mandatory)][string]$SubKey)

    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($SubKey, $false)
    if ($null -eq $key) {
        return $false
    }
    $key.Dispose()
    return $true
}

function Get-UserPathState {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $false)
    try {
        if ($null -eq $key) {
            return [PSCustomObject]@{ Exists = $false; Value = $null; Kind = $null }
        }
        $pathExists = $false
        foreach ($valueName in $key.GetValueNames()) {
            if ([string]::Equals($valueName, "Path", [StringComparison]::OrdinalIgnoreCase)) {
                $pathExists = $true
                break
            }
        }
        if (-not $pathExists) {
            return [PSCustomObject]@{ Exists = $false; Value = $null; Kind = $null }
        }
        return [PSCustomObject]@{
            Exists = $true
            Value = [string]$key.GetValue(
                "Path",
                $null,
                [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
            )
            Kind = $key.GetValueKind("Path")
        }
    }
    finally {
        if ($null -ne $key) {
            $key.Dispose()
        }
    }
}

function Set-UserPathState {
    param([Parameter(Mandatory)]$State)

    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment", $true)
    try {
        if ($State.Exists) {
            $key.SetValue("Path", [string]$State.Value, $State.Kind)
        }
        else {
            $key.DeleteValue("Path", $false)
        }
    }
    finally {
        $key.Dispose()
    }
}

function Set-UserPathValue {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)

    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment", $true)
    try {
        $key.SetValue("Path", $Value, [Microsoft.Win32.RegistryValueKind]::ExpandString)
    }
    finally {
        $key.Dispose()
    }
}

function Assert-UserPathValue {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Expected)

    $actual = Get-UserPathState
    if (-not $actual.Exists -or $actual.Value -cne $Expected) {
        throw "The installer changed unrelated or pre-existing user PATH content."
    }
}

function Get-InstallPathEntryCount {
    $state = Get-UserPathState
    if (-not $state.Exists) {
        return 0
    }
    return @($state.Value.Split(';') | Where-Object { $_ -ieq $installDirectory }).Count
}

function Get-PathOwnershipValue {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($installerStateSubKey, $false)
    try {
        if ($null -eq $key) {
            throw "Installer ownership state key was not created."
        }
        return [int]$key.GetValue($pathOwnershipValueName, -1)
    }
    finally {
        if ($null -ne $key) {
            $key.Dispose()
        }
    }
}

function Set-UnrelatedInstallerStateCanary {
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($installerStateSubKey, $true)
    try {
        $key.SetValue(
            $unrelatedInstallerStateValueName,
            $sentinelToken,
            [Microsoft.Win32.RegistryValueKind]::String
        )
    }
    finally {
        $key.Dispose()
    }
}

function Assert-UnrelatedInstallerStateCanary {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($installerStateSubKey, $false)
    try {
        if ($null -eq $key) {
            throw "Uninstall deleted unrelated installer state."
        }
        $value = [string]$key.GetValue($unrelatedInstallerStateValueName, $null)
        if ($value -cne $sentinelToken) {
            throw "Uninstall changed unrelated installer state."
        }
    }
    finally {
        if ($null -ne $key) {
            $key.Dispose()
        }
    }
}

function Remove-UnrelatedInstallerStateCanary {
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($installerStateSubKey, $false)
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($installerProductSubKey, $false)
}

function Wait-ForOwnedArtifactsRemoval {
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        if (
            -not (Test-Path -LiteralPath (Join-Path $installDirectory "pw.exe")) -and
            -not (Test-Path -LiteralPath $installedPasswatcher) -and
            -not (Test-Path -LiteralPath (Join-Path $installDirectory "_internal")) -and
            -not (Test-Path -LiteralPath $uninstaller)
        ) {
            return
        }
        Start-Sleep -Milliseconds 100
    }
    throw "Uninstall left Passwatcher-owned application artifacts behind."
}

function Assert-InstallerRegistryClean {
    if (Test-RegistrySubKeyExists -SubKey $uninstallSubKey) {
        throw "Uninstall left its HKCU uninstall metadata behind."
    }
    if (Test-RegistrySubKeyExists -SubKey $installerStateSubKey) {
        throw "Uninstall left HKCU\$installerStateSubKey behind."
    }
    if (Test-RegistrySubKeyExists -SubKey $installerProductSubKey) {
        throw "Uninstall left HKCU\$installerProductSubKey behind."
    }
}

if (Test-Path -LiteralPath $installDirectory) {
    throw "Refusing to overwrite an existing Passwatcher installation: $installDirectory"
}
if (Test-RegistrySubKeyExists -SubKey $uninstallSubKey) {
    throw "Refusing to overwrite existing Passwatcher uninstall metadata: HKCU\$uninstallSubKey"
}
if (Test-RegistrySubKeyExists -SubKey $installerProductSubKey) {
    throw "Refusing to overwrite existing Passwatcher installer state: HKCU\$installerProductSubKey"
}

$originalUserPath = Get-UserPathState
if ((Get-InstallPathEntryCount) -ne 0) {
    throw "Refusing to overwrite a pre-existing Passwatcher user PATH entry."
}

New-Item -ItemType Directory -Path $canaryRootFullPath | Out-Null
Set-Content -LiteralPath $canaryRootMarker -Value $sentinelToken -Encoding UTF8
$installerCanary = New-DeletionCanary -Name "installer-D-override"
$uninstallerDCanary = New-DeletionCanary -Name "uninstaller-D-override"
$uninstallerQuestionCanary = New-DeletionCanary -Name "uninstaller-question-override"
$installerOverrideDirectory = $installerCanary.Directory
$uninstallerDOverrideDirectory = $uninstallerDCanary.Directory
$uninstallerQuestionOverrideDirectory = $uninstallerQuestionCanary.Directory
$uninstallerQuestionCopy = Join-Path $canaryRootFullPath "uninstall-question-copy.exe"

try {
    Invoke-CheckedProcess -FilePath $installerPath -ArgumentList @("/S", "/D=$installerOverrideDirectory")
    Assert-CanarySurvived -Canary $installerCanary
    if (-not (Test-Path -LiteralPath (Join-Path $installDirectory "pw.exe") -PathType Leaf)) {
        throw "Installer /D= override was not pinned to the canonical install directory."
    }
    if (-not (Test-Path -LiteralPath $installedPasswatcher -PathType Leaf)) {
        throw "Installer did not create passwatcher.exe."
    }
    if ((Get-InstallPathEntryCount) -ne 1) {
        throw "Installer did not add exactly one owned user PATH entry."
    }
    if ((Get-PathOwnershipValue) -ne 1) {
        throw "Installer did not record ownership of its added PATH entry."
    }

    Set-Content -LiteralPath $unexpectedInstallFile -Value $sentinelToken -Encoding UTF8
    Invoke-CheckedProcess -FilePath $installerPath -ArgumentList @("/S")
    if (-not (Test-Path -LiteralPath $unexpectedInstallFile -PathType Leaf)) {
        throw "Upgrade deleted the unexpected install-directory sentinel."
    }
    if ((Get-PathOwnershipValue) -ne 1) {
        throw "Upgrade did not preserve ownership of the installer-added PATH entry."
    }
    Copy-Item -LiteralPath $uninstaller -Destination $uninstallerQuestionCopy

    Invoke-CheckedProcess -FilePath $uninstaller -ArgumentList @("/S", "/D=$uninstallerDOverrideDirectory")
    Wait-ForOwnedArtifactsRemoval
    Assert-CanarySurvived -Canary $uninstallerDCanary
    if (-not (Test-Path -LiteralPath $unexpectedInstallFile -PathType Leaf)) {
        throw "Uninstall deleted the unexpected install-directory sentinel."
    }
    if ((Get-InstallPathEntryCount) -ne 0) {
        throw "Owned PATH entry survived uninstall."
    }
    Assert-InstallerRegistryClean

    Invoke-CheckedProcess -FilePath $installerPath -ArgumentList @("/S")
    Copy-Item -LiteralPath $uninstaller -Destination $uninstallerQuestionCopy -Force
    Invoke-CheckedProcess -FilePath $uninstallerQuestionCopy -ArgumentList @("/S", "_?=$uninstallerQuestionOverrideDirectory")
    Wait-ForOwnedArtifactsRemoval
    Assert-CanarySurvived -Canary $uninstallerQuestionCanary
    if (-not (Test-Path -LiteralPath $unexpectedInstallFile -PathType Leaf)) {
        throw "Uninstall deleted the unexpected install-directory sentinel."
    }
    Assert-InstallerRegistryClean

    $preexistingPathValue = "safety-alpha;$installDirectory;$($installDirectory.ToUpperInvariant());safety-omega;;"
    Set-UserPathValue -Value $preexistingPathValue
    Invoke-CheckedProcess -FilePath $installerPath -ArgumentList @("/S")
    Assert-UserPathValue -Expected $preexistingPathValue
    if ((Get-PathOwnershipValue) -ne 0) {
        throw "Installer claimed ownership of a pre-existing Passwatcher PATH entry."
    }
    Invoke-CheckedProcess -FilePath $installerPath -ArgumentList @("/S")
    Assert-UserPathValue -Expected $preexistingPathValue
    if ((Get-PathOwnershipValue) -ne 0) {
        throw "Upgrade claimed ownership of a pre-existing Passwatcher PATH entry."
    }
    Set-UnrelatedInstallerStateCanary

    Invoke-CheckedProcess -FilePath $uninstaller -ArgumentList @("/S")
    Wait-ForOwnedArtifactsRemoval
    Assert-UserPathValue -Expected $preexistingPathValue
    if ((Get-InstallPathEntryCount) -ne 2) {
        throw "Uninstall did not preserve pre-existing Passwatcher PATH duplicates."
    }
    if (-not (Test-Path -LiteralPath $unexpectedInstallFile -PathType Leaf)) {
        throw "Uninstall deleted the unexpected install-directory sentinel."
    }
    if (Test-RegistrySubKeyExists -SubKey $uninstallSubKey) {
        throw "Uninstall left its HKCU uninstall metadata behind."
    }
    Assert-UnrelatedInstallerStateCanary
    Remove-UnrelatedInstallerStateCanary
    if (Test-RegistrySubKeyExists -SubKey $installerProductSubKey) {
        throw "Safety-smoke cleanup left its test-owned registry state behind."
    }

    Write-Host "Passwatcher installer safety smoke test passed."
}
finally {
    Invoke-SmokeFallbackCleanup `
        -UninstallerAction {
            if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
                $partialUninstall = Start-Process -FilePath $uninstaller -ArgumentList @("/S") -Wait -PassThru
                if ($partialUninstall.ExitCode -ne 0) {
                    throw "Partial-install uninstaller exited with code $($partialUninstall.ExitCode)."
                }
            }
        } `
        -InstallDirectoryAction {
            if (Test-Path -LiteralPath $installDirectory) {
                Remove-Item -LiteralPath $installDirectory -Recurse -Force
            }
        } `
        -PathAction {
            Set-UserPathState -State $originalUserPath
        } `
        -RegistryAction {
            [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($uninstallSubKey, $false)
            [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($installerProductSubKey, $false)
        } `
        -ConfigAction {
            $markerValue = $null
            if (Test-Path -LiteralPath $canaryRootMarker -PathType Leaf) {
                $markerValue = (Get-Content -Raw -LiteralPath $canaryRootMarker).Trim()
            }
            if ($markerValue -cne $sentinelToken) {
                throw "Refusing to clean an unowned installer safety canary root."
            }
            if (Test-Path -LiteralPath $canaryRootFullPath) {
                Remove-Item -LiteralPath $canaryRootFullPath -Recurse -Force
            }
        }
}

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Installer
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "windows-installer-cleanup.ps1")

if ($env:PASSWATCHER_SMOKE_ISOLATED_USER -ne "1") {
    throw "This smoke test changes the current user's PATH. Run it only in a disposable Windows user or CI sandbox and set PASSWATCHER_SMOKE_ISOLATED_USER=1."
}

$installerMatches = @(Get-Item -Path $Installer -ErrorAction Stop)
if ($installerMatches.Count -ne 1) {
    throw "Installer must resolve to exactly one file; found $($installerMatches.Count)."
}
$installerPath = $installerMatches[0].FullName

$appData = [Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData)
$localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$configDirectory = Join-Path $appData "Passwatcher"
$localDataDirectory = Join-Path $localAppData "Passwatcher"
$localVaultSentinel = Join-Path $localDataDirectory "vault.db"
$installDirectory = Join-Path $localAppData "Programs\Passwatcher"
$pwExecutable = Join-Path $installDirectory "pw.exe"
$passwatcherExecutable = Join-Path $installDirectory "passwatcher.exe"
$uninstaller = Join-Path $installDirectory "uninstall.exe"
$uninstallSubKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\Passwatcher"
$installerProductSubKey = "Software\Passwatcher"
$installerStateSubKey = "Software\Passwatcher\Installer"
$sentinel = "passwatcher-smoke-$([Guid]::NewGuid().ToString('N'))"
$createdConfig = $false
$createdLocalData = $false

if (Test-Path -LiteralPath $configDirectory) {
    throw "Refusing to use an existing Passwatcher config directory: $configDirectory"
}
if (Test-Path -LiteralPath $localDataDirectory) {
    throw "Refusing to use an existing Passwatcher local data directory: $localDataDirectory"
}
if (Test-Path -LiteralPath $installDirectory) {
    throw "Refusing to overwrite an existing Passwatcher installation: $installDirectory"
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

function Get-UserPathState {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $false)
    try {
        if ($null -eq $key) {
            return [PSCustomObject]@{ Exists = $false; Value = $null; Kind = $null }
        }
        $exists = $false
        foreach ($valueName in $key.GetValueNames()) {
            if ([string]::Equals($valueName, "Path", [StringComparison]::OrdinalIgnoreCase)) {
                $exists = $true
                break
            }
        }
        if (-not $exists) {
            return [PSCustomObject]@{ Exists = $false; Value = $null; Kind = $null }
        }
        $value = $key.GetValue(
            "Path",
            $null,
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
        )
        return [PSCustomObject]@{
            Exists = $true
            Value = [string]$value
            Kind = $key.GetValueKind("Path")
        }
    }
    finally {
        if ($null -ne $key) {
            $key.Dispose()
        }
    }
}

function Test-UserPathMatches {
    param([Parameter(Mandatory)]$Expected)

    $actual = Get-UserPathState
    return (
        $actual.Exists -eq $Expected.Exists -and
        $actual.Value -ceq $Expected.Value -and
        $actual.Kind -eq $Expected.Kind
    )
}

function Restore-OriginalUserPath {
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment", $true)
    try {
        if ($originalUserPath.Exists) {
            $key.SetValue("Path", $originalUserPath.Value, $originalUserPath.Kind)
        }
        else {
            $key.DeleteValue("Path", $false)
        }
    }
    finally {
        $key.Dispose()
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

function Remove-SmokeRegistryMutations {
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($uninstallSubKey, $false)
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($installerProductSubKey, $false)
}

function Get-InstallPathEntryCount {
    $state = Get-UserPathState
    if (-not $state.Exists) {
        return 0
    }
    return @($state.Value.Split(';') | Where-Object { $_ -ieq $installDirectory }).Count
}

function Assert-InstalledLaunchersAndFreshPath {
    Invoke-CheckedProcess -FilePath $pwExecutable -ArgumentList @("--help")
    Invoke-CheckedProcess -FilePath $passwatcherExecutable -ArgumentList @("--help")

    $previousProcessPath = $env:Path
    try {
        $env:Path = (
            [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [Environment]::GetEnvironmentVariable("Path", "User")
        )
        $resolvedPasswatcher = Get-Command passwatcher.exe -CommandType Application -ErrorAction Stop |
            Select-Object -First 1 -ExpandProperty Source
        if ($resolvedPasswatcher -ine $passwatcherExecutable) {
            throw "passwatcher resolved to unexpected path: $resolvedPasswatcher"
        }
        Invoke-CheckedProcess -FilePath $resolvedPasswatcher -ArgumentList @("--help")

        $resolvedPw = Get-Command pw.exe -CommandType Application -ErrorAction Stop |
            Select-Object -First 1 -ExpandProperty Source
        if ($resolvedPw -ieq $pwExecutable) {
            Invoke-CheckedProcess -FilePath $resolvedPw -ArgumentList @("--help")
        }
        else {
            Write-Warning "Passwatcher command collision: pw.exe resolves to '$resolvedPw', not '$pwExecutable'. Use 'passwatcher' as the unambiguous command."
        }
    }
    finally {
        $env:Path = $previousProcessPath
    }
}

function Assert-UninstallMetadata {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($uninstallSubKey, $false)
    try {
        if ($null -eq $key) {
            throw "Installer did not create uninstall metadata."
        }
        $expectedUninstall = '"' + $uninstaller + '"'
        $expectedQuietUninstall = $expectedUninstall + " /S"
        if ([string]$key.GetValue("UninstallString", "") -cne $expectedUninstall) {
            throw "Installer wrote malformed UninstallString metadata."
        }
        if ([string]$key.GetValue("QuietUninstallString", "") -cne $expectedQuietUninstall) {
            throw "Installer wrote malformed QuietUninstallString metadata."
        }
    }
    finally {
        if ($null -ne $key) {
            $key.Dispose()
        }
    }
}

if (Test-RegistrySubKeyExists -SubKey $uninstallSubKey) {
    throw "Refusing to overwrite existing Passwatcher uninstall metadata: HKCU\$uninstallSubKey"
}
if (Test-RegistrySubKeyExists -SubKey $installerProductSubKey) {
    throw "Refusing to overwrite existing Passwatcher installer state: HKCU\$installerProductSubKey"
}

$originalUserPath = Get-UserPathState

try {
    Invoke-CheckedProcess -FilePath $installerPath -ArgumentList @("/S")
    if (-not (Test-Path -LiteralPath $pwExecutable -PathType Leaf)) {
        throw "The installer did not create pw.exe."
    }
    if (-not (Test-Path -LiteralPath $passwatcherExecutable -PathType Leaf)) {
        throw "The installer did not create passwatcher.exe."
    }
    if ((Get-InstallPathEntryCount) -ne 1) {
        throw "The installer did not add exactly one user PATH entry."
    }
    Assert-UninstallMetadata
    Assert-InstalledLaunchersAndFreshPath

    New-Item -ItemType Directory -Path $configDirectory | Out-Null
    Set-Content -LiteralPath (Join-Path $configDirectory "config.toml") -Value "smoke_sentinel = `"$sentinel`"" -Encoding UTF8
    $createdConfig = $true
    New-Item -ItemType Directory -Path $localDataDirectory | Out-Null
    Set-Content -LiteralPath $localVaultSentinel -Value $sentinel -Encoding UTF8
    $createdLocalData = $true

    Invoke-CheckedProcess -FilePath $installerPath -ArgumentList @("/S")
    if ((Get-InstallPathEntryCount) -ne 1) {
        throw "Upgrade duplicated the user PATH entry."
    }
    $configText = Get-Content -Raw -LiteralPath (Join-Path $configDirectory "config.toml")
    if ($configText -notlike "*$sentinel*") {
        throw "Upgrade did not preserve the sentinel configuration."
    }
    if ((Get-Content -Raw -LiteralPath $localVaultSentinel) -notlike "*$sentinel*") {
        throw "Upgrade did not preserve the local vault sentinel."
    }

    Invoke-CheckedProcess -FilePath $uninstaller -ArgumentList @("/S")
    for ($attempt = 0; $attempt -lt 50 -and (Test-Path -LiteralPath $installDirectory); $attempt++) {
        Start-Sleep -Milliseconds 100
    }
    if (Test-Path -LiteralPath $installDirectory) {
        throw "Uninstall left application files behind."
    }
    if ((Get-InstallPathEntryCount) -ne 0) {
        throw "Uninstall left the Passwatcher user PATH entry behind."
    }
    if (-not (Test-UserPathMatches -Expected $originalUserPath)) {
        throw "Install/uninstall changed unrelated user PATH content."
    }
    if (Test-RegistrySubKeyExists -SubKey $uninstallSubKey) {
        throw "Uninstall left its HKCU uninstall metadata behind."
    }
    if (Test-RegistrySubKeyExists -SubKey $installerStateSubKey) {
        throw "Uninstall left its installer state key behind."
    }
    if (Test-RegistrySubKeyExists -SubKey $installerProductSubKey) {
        throw "Uninstall left its empty Passwatcher registry key behind."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $configDirectory "config.toml") -PathType Leaf)) {
        throw "Silent uninstall did not retain the sentinel configuration."
    }
    if (-not (Test-Path -LiteralPath $localVaultSentinel -PathType Leaf)) {
        throw "Silent uninstall did not retain the local DPAPI vault sentinel."
    }

    Write-Host "Passwatcher installer smoke test passed."
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
            if (-not (Test-UserPathMatches -Expected $originalUserPath)) {
                Restore-OriginalUserPath
            }
        } `
        -RegistryAction {
            if (
                (Test-RegistrySubKeyExists -SubKey $uninstallSubKey) -or
                (Test-RegistrySubKeyExists -SubKey $installerProductSubKey)
            ) {
                Remove-SmokeRegistryMutations
            }
        } `
        -ConfigAction {
            if ($createdConfig -and (Test-Path -LiteralPath $configDirectory)) {
                Remove-Item -LiteralPath $configDirectory -Recurse -Force
            }
            if ($createdLocalData -and (Test-Path -LiteralPath $localDataDirectory)) {
                Remove-Item -LiteralPath $localDataDirectory -Recurse -Force
            }
        }
}

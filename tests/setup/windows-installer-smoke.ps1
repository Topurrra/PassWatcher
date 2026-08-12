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
$installDirectory = Join-Path $localAppData "Programs\Passwatcher"
$uninstaller = Join-Path $installDirectory "uninstall.exe"
$uninstallSubKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\Passwatcher"
$installerProductSubKey = "Software\Passwatcher"
$installerStateSubKey = "Software\Passwatcher\Installer"
$sentinel = "passwatcher-smoke-$([Guid]::NewGuid().ToString('N'))"
$createdConfig = $false

if (Test-Path -LiteralPath $configDirectory) {
    throw "Refusing to use an existing Passwatcher config directory: $configDirectory"
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

function Assert-PwRunsFromUserPath {
    $childScript = @"
`$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
`$resolved = (Get-Command pw.exe -CommandType Application -ErrorAction Stop).Source
if (`$resolved -ine '$($installDirectory.Replace("'", "''"))\pw.exe') { throw "pw resolved to unexpected path: `$resolved" }
& pw --help
if (`$LASTEXITCODE -ne 0) { exit `$LASTEXITCODE }
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childScript))
    Invoke-CheckedProcess -FilePath powershell.exe -ArgumentList @("-NoProfile", "-NonInteractive", "-EncodedCommand", $encoded)
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
    if (-not (Test-Path -LiteralPath (Join-Path $installDirectory "pw.exe") -PathType Leaf)) {
        throw "The installer did not create pw.exe."
    }
    if ((Get-InstallPathEntryCount) -ne 1) {
        throw "The installer did not add exactly one user PATH entry."
    }
    Assert-PwRunsFromUserPath

    New-Item -ItemType Directory -Path $configDirectory | Out-Null
    Set-Content -LiteralPath (Join-Path $configDirectory "config.toml") -Value "smoke_sentinel = `"$sentinel`"" -Encoding UTF8
    $createdConfig = $true

    Invoke-CheckedProcess -FilePath $installerPath -ArgumentList @("/S")
    if ((Get-InstallPathEntryCount) -ne 1) {
        throw "Upgrade duplicated the user PATH entry."
    }
    $configText = Get-Content -Raw -LiteralPath (Join-Path $configDirectory "config.toml")
    if ($configText -notlike "*$sentinel*") {
        throw "Upgrade did not preserve the sentinel configuration."
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
        }
}

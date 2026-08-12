[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Installer
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

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
$originalUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$sentinel = "passwatcher-smoke-$([Guid]::NewGuid().ToString('N'))"
$createdConfig = $false
$installed = $false

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

function Get-InstallPathEntryCount {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ([string]::IsNullOrEmpty($userPath)) {
        return 0
    }
    return @($userPath.Split(';') | Where-Object { $_ -ieq $installDirectory }).Count
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

try {
    Invoke-CheckedProcess -FilePath $installerPath -ArgumentList @("/S")
    $installed = $true
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
    $installed = $false
    for ($attempt = 0; $attempt -lt 50 -and (Test-Path -LiteralPath $installDirectory); $attempt++) {
        Start-Sleep -Milliseconds 100
    }
    if (Test-Path -LiteralPath $installDirectory) {
        throw "Uninstall left application files behind."
    }
    if ((Get-InstallPathEntryCount) -ne 0) {
        throw "Uninstall left the Passwatcher user PATH entry behind."
    }
    $currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentUserPath -cne $originalUserPath) {
        throw "Install/uninstall changed unrelated user PATH content."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $configDirectory "config.toml") -PathType Leaf)) {
        throw "Silent uninstall did not retain the sentinel configuration."
    }

    Write-Host "Passwatcher installer smoke test passed."
}
finally {
    if ($installed -and (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        Start-Process -FilePath $uninstaller -ArgumentList @("/S") -Wait | Out-Null
    }
    if ($createdConfig -and (Test-Path -LiteralPath $configDirectory)) {
        Remove-Item -LiteralPath $configDirectory -Recurse -Force
    }
}

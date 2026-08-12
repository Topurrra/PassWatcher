[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$previousLocation = Get-Location

function Assert-LastExitCode {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Find-MakeNsis {
    $command = Get-Command makensis -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path $env:ProgramFiles "NSIS\makensis.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    throw "NSIS makensis was not found. Install NSIS 3.x and add makensis to PATH."
}

try {
    Set-Location -LiteralPath $projectRoot

    & python tools/build_server_zipapp.py
    Assert-LastExitCode "Server zipapp build"

    $pytestParent = Join-Path $projectRoot "build"
    New-Item -ItemType Directory -Path $pytestParent -Force | Out-Null
    $pytestBaseTemp = Join-Path $pytestParent ("pytest-" + [Guid]::NewGuid().ToString("N"))
    & python -m pytest -q --basetemp $pytestBaseTemp -p no:cacheprovider
    Assert-LastExitCode "Test suite"

    & python -m PyInstaller --clean --noconfirm packaging/passwatcher.spec
    Assert-LastExitCode "PyInstaller build"

    $executable = Join-Path $projectRoot "dist\passwatcher\pw.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "PyInstaller completed without producing $executable."
    }

    $version = & python -c "import sys; sys.path.insert(0, 'src'); import passwatcher; print(passwatcher.__version__)"
    Assert-LastExitCode "Version lookup"
    $version = $version.Trim()
    if ($version -notmatch '^\d+(\.\d+){1,3}$') {
        throw "passwatcher.__version__ must contain two to four numeric components; got '$version'."
    }

    $makeNsis = Find-MakeNsis
    & $makeNsis "/DVERSION=$version" packaging/passwatcher.nsi
    Assert-LastExitCode "NSIS installer build"

    $installer = Join-Path $projectRoot "dist\Passwatcher-Setup-$version.exe"
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "NSIS completed without producing $installer."
    }

    Write-Host "Built $executable"
    Write-Host "Built $installer"
}
finally {
    Set-Location -LiteralPath $previousLocation
}

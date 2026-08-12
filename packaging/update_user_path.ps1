[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet("Add", "Remove")]
    [string]$Operation,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$Entry,

    [ValidateSet("Absent", "Present")]
    [string]$TransformState,

    [AllowEmptyString()]
    [string]$TransformValue = "",

    [ValidateSet("true", "false")]
    [string]$TransformRestoreAbsent = "false"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$environmentSubKey = "Environment"
$pathValueName = "Path"
$stateSubKey = "Software\Passwatcher\Installer"
$stateValueName = "PathValueExistedBeforeInstall"

function Test-ValueName {
    param(
        [Parameter(Mandatory)][Microsoft.Win32.RegistryKey]$Key,
        [Parameter(Mandatory)][string]$Name
    )

    foreach ($valueName in $Key.GetValueNames()) {
        if ([string]::Equals($valueName, $Name, [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Get-UpdatedPathState {
    param(
        [Parameter(Mandatory)][bool]$Exists,
        [AllowEmptyString()][string]$Value,
        [Parameter(Mandatory)][string]$PathEntry,
        [Parameter(Mandatory)][ValidateSet("Add", "Remove")][string]$Action,
        [Parameter(Mandatory)][bool]$RestoreAbsent
    )

    $segments = @()
    if ($Exists) {
        $segments = @($Value.Split([char]';', [System.StringSplitOptions]::None))
    }
    $matchingSegments = @(
        $segments | Where-Object {
            [string]::Equals($_, $PathEntry, [StringComparison]::OrdinalIgnoreCase)
        }
    )

    if ($Action -eq "Add") {
        if ($matchingSegments.Count -gt 0) {
            return [PSCustomObject]@{ exists = $Exists; value = $Value; changed = $false }
        }
        if ($Exists) {
            return [PSCustomObject]@{ exists = $true; value = "$Value;$PathEntry"; changed = $true }
        }
        return [PSCustomObject]@{ exists = $true; value = $PathEntry; changed = $true }
    }

    if ($matchingSegments.Count -eq 0) {
        return [PSCustomObject]@{ exists = $Exists; value = $Value; changed = $false }
    }
    $remaining = @(
        $segments | Where-Object {
            -not [string]::Equals($_, $PathEntry, [StringComparison]::OrdinalIgnoreCase)
        }
    )
    $updatedValue = [string]::Join(";", [string[]]$remaining)
    if ($RestoreAbsent -and $updatedValue.Length -eq 0) {
        return [PSCustomObject]@{ exists = $false; value = $null; changed = $true }
    }
    return [PSCustomObject]@{ exists = $true; value = $updatedValue; changed = $true }
}

if ($PSBoundParameters.ContainsKey("TransformState")) {
    $result = Get-UpdatedPathState `
        -Exists ($TransformState -eq "Present") `
        -Value $TransformValue `
        -PathEntry $Entry `
        -Action $Operation `
        -RestoreAbsent ($TransformRestoreAbsent -eq "true")
    $result | ConvertTo-Json -Compress
    exit 0
}

$environmentKey = $null
$stateKey = $null
try {
    $environmentKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($environmentSubKey, $true)
    if ($null -eq $environmentKey) {
        throw "Unable to open the current-user Environment registry key."
    }

    $pathExists = Test-ValueName -Key $environmentKey -Name $pathValueName
    $pathValue = ""
    $pathKind = [Microsoft.Win32.RegistryValueKind]::ExpandString
    if ($pathExists) {
        $rawPathValue = $environmentKey.GetValue(
            $pathValueName,
            $null,
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
        )
        if ($rawPathValue -isnot [string]) {
            throw "The current-user Path registry value is not a string."
        }
        $pathValue = [string]$rawPathValue
        $pathKind = $environmentKey.GetValueKind($pathValueName)
        if ($pathKind -notin @(
            [Microsoft.Win32.RegistryValueKind]::String,
            [Microsoft.Win32.RegistryValueKind]::ExpandString
        )) {
            throw "The current-user Path registry value has an unsupported type."
        }
    }

    $restoreAbsent = $false
    if ($Operation -eq "Add") {
        $stateKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($stateSubKey, $true)
        if ($null -eq $stateKey) {
            throw "Unable to open the Passwatcher installer state registry key."
        }
        if (-not (Test-ValueName -Key $stateKey -Name $stateValueName)) {
            $stateKey.SetValue(
                $stateValueName,
                $(if ($pathExists) { 1 } else { 0 }),
                [Microsoft.Win32.RegistryValueKind]::DWord
            )
        }
    }
    else {
        $stateKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($stateSubKey, $true)
        if ($null -ne $stateKey -and (Test-ValueName -Key $stateKey -Name $stateValueName)) {
            $restoreAbsent = ([int]$stateKey.GetValue($stateValueName, 1)) -eq 0
        }
    }

    $result = Get-UpdatedPathState `
        -Exists $pathExists `
        -Value $pathValue `
        -PathEntry $Entry `
        -Action $Operation `
        -RestoreAbsent $restoreAbsent

    if ($result.changed) {
        if ($result.exists) {
            $environmentKey.SetValue($pathValueName, [string]$result.value, $pathKind)
        }
        elseif ($pathExists) {
            $environmentKey.DeleteValue($pathValueName, $false)
        }
    }

    if ($Operation -eq "Remove" -and $null -ne $stateKey) {
        $stateKey.DeleteValue($stateValueName, $false)
    }
}
finally {
    if ($null -ne $stateKey) {
        $stateKey.Dispose()
    }
    if ($null -ne $environmentKey) {
        $environmentKey.Dispose()
    }
}

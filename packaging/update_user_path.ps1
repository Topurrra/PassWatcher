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
    [string]$TransformRestoreAbsent = "false",

    [ValidateSet("true", "false")]
    [string]$TransformOwnershipKnown = "false",

    [ValidateSet("true", "false")]
    [string]$TransformEntryAddedByInstall = "false",

    [ValidateSet("Unknown", "Absent", "Present")]
    [string]$TransformLegacyPathValueExisted = "Unknown"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$environmentSubKey = "Environment"
$pathValueName = "Path"
$stateSubKey = "Software\Passwatcher\Installer"
$pathExistenceStateValueName = "PathValueExistedBeforeInstall"
$pathOwnershipStateValueName = "PathEntryAddedByInstall"

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
        [Parameter(Mandatory)][bool]$RestoreAbsent,
        [Parameter(Mandatory)][bool]$OwnershipKnown,
        [Parameter(Mandatory)][bool]$EntryAddedByInstall
    )

    $segments = @()
    if ($Exists) {
        $segments = @($Value.Split([char]';', [System.StringSplitOptions]::None))
    }
    $matchingIndex = -1
    for ($index = 0; $index -lt $segments.Count; $index++) {
        if ([string]::Equals(
            $segments[$index],
            $PathEntry,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            $matchingIndex = $index
            break
        }
    }

    if ($Action -eq "Add") {
        if ($matchingIndex -ge 0) {
            return [PSCustomObject]@{
                exists = $Exists
                value = $Value
                changed = $false
                entryAddedByInstall = $(if ($OwnershipKnown) { $EntryAddedByInstall } else { $false })
            }
        }
        if ($Exists) {
            return [PSCustomObject]@{
                exists = $true
                value = "$Value;$PathEntry"
                changed = $true
                entryAddedByInstall = $true
            }
        }
        return [PSCustomObject]@{
            exists = $true
            value = $PathEntry
            changed = $true
            entryAddedByInstall = $true
        }
    }

    if (-not $EntryAddedByInstall -or $matchingIndex -lt 0) {
        return [PSCustomObject]@{
            exists = $Exists
            value = $Value
            changed = $false
            entryAddedByInstall = $false
        }
    }

    $remaining = [Collections.Generic.List[string]]::new()
    for ($index = 0; $index -lt $segments.Count; $index++) {
        if ($index -ne $matchingIndex) {
            $remaining.Add($segments[$index])
        }
    }
    $updatedValue = [string]::Join(";", $remaining.ToArray())
    if ($RestoreAbsent -and $updatedValue.Length -eq 0) {
        return [PSCustomObject]@{
            exists = $false
            value = $null
            changed = $true
            entryAddedByInstall = $false
        }
    }
    return [PSCustomObject]@{
        exists = $true
        value = $updatedValue
        changed = $true
        entryAddedByInstall = $false
    }
}

if ($PSBoundParameters.ContainsKey("TransformState")) {
    $resolvedOwnershipKnown = $TransformOwnershipKnown -eq "true"
    $resolvedEntryAddedByInstall = $TransformEntryAddedByInstall -eq "true"
    if (-not $resolvedOwnershipKnown -and $TransformLegacyPathValueExisted -eq "Absent") {
        $resolvedOwnershipKnown = $true
        $resolvedEntryAddedByInstall = $true
    }
    $result = Get-UpdatedPathState `
        -Exists ($TransformState -eq "Present") `
        -Value $TransformValue `
        -PathEntry $Entry `
        -Action $Operation `
        -RestoreAbsent ($TransformRestoreAbsent -eq "true") `
        -OwnershipKnown $resolvedOwnershipKnown `
        -EntryAddedByInstall $resolvedEntryAddedByInstall
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
    $ownershipKnown = $false
    $entryAddedByInstall = $false
    if ($Operation -eq "Add") {
        $stateKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($stateSubKey, $true)
        if ($null -eq $stateKey) {
            throw "Unable to open the Passwatcher installer state registry key."
        }
        if (-not (Test-ValueName -Key $stateKey -Name $pathExistenceStateValueName)) {
            $stateKey.SetValue(
                $pathExistenceStateValueName,
                $(if ($pathExists) { 1 } else { 0 }),
                [Microsoft.Win32.RegistryValueKind]::DWord
            )
        }
        if (Test-ValueName -Key $stateKey -Name $pathOwnershipStateValueName) {
            $ownershipKnown = $true
            $entryAddedByInstall = (
                [int]$stateKey.GetValue($pathOwnershipStateValueName, 0)
            ) -ne 0
        }
        elseif (
            (Test-ValueName -Key $stateKey -Name $pathExistenceStateValueName) -and
            ([int]$stateKey.GetValue($pathExistenceStateValueName, 1)) -eq 0
        ) {
            # Previous Passwatcher releases recorded only whether Path existed.
            # If it was absent, the existing exact entry can only be installer-owned.
            $ownershipKnown = $true
            $entryAddedByInstall = $true
        }
    }
    else {
        $stateKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($stateSubKey, $true)
        if ($null -ne $stateKey) {
            if (Test-ValueName -Key $stateKey -Name $pathExistenceStateValueName) {
                $restoreAbsent = (
                    [int]$stateKey.GetValue($pathExistenceStateValueName, 1)
                ) -eq 0
            }
            if (Test-ValueName -Key $stateKey -Name $pathOwnershipStateValueName) {
                $ownershipKnown = $true
                $entryAddedByInstall = (
                    [int]$stateKey.GetValue($pathOwnershipStateValueName, 0)
                ) -ne 0
            }
        }
    }

    $result = Get-UpdatedPathState `
        -Exists $pathExists `
        -Value $pathValue `
        -PathEntry $Entry `
        -Action $Operation `
        -RestoreAbsent $restoreAbsent `
        -OwnershipKnown $ownershipKnown `
        -EntryAddedByInstall $entryAddedByInstall

    if ($result.changed) {
        if ($result.exists) {
            $environmentKey.SetValue($pathValueName, [string]$result.value, $pathKind)
        }
        elseif ($pathExists) {
            $environmentKey.DeleteValue($pathValueName, $false)
        }
    }

    if ($Operation -eq "Add") {
        $stateKey.SetValue(
            $pathOwnershipStateValueName,
            $(if ($result.entryAddedByInstall) { 1 } else { 0 }),
            [Microsoft.Win32.RegistryValueKind]::DWord
        )
    }
    elseif ($null -ne $stateKey) {
        $stateKey.DeleteValue($pathExistenceStateValueName, $false)
        $stateKey.DeleteValue($pathOwnershipStateValueName, $false)
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

function Invoke-SmokeCleanupAction {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][scriptblock]$Action,
        [Parameter(Mandatory)][AllowEmptyCollection()][Collections.Generic.List[string]]$Errors
    )

    try {
        & $Action
    }
    catch {
        $Errors.Add("$Name`: $($_.Exception.Message)")
    }
}

function Invoke-SmokeFallbackCleanup {
    param(
        [Parameter(Mandatory)][scriptblock]$UninstallerAction,
        [Parameter(Mandatory)][scriptblock]$InstallDirectoryAction,
        [Parameter(Mandatory)][scriptblock]$PathAction,
        [Parameter(Mandatory)][scriptblock]$RegistryAction,
        [Parameter(Mandatory)][scriptblock]$ConfigAction
    )

    $cleanupErrors = [Collections.Generic.List[string]]::new()
    Invoke-SmokeCleanupAction -Name "uninstaller" -Action $UninstallerAction -Errors $cleanupErrors
    Invoke-SmokeCleanupAction -Name "install directory" -Action $InstallDirectoryAction -Errors $cleanupErrors
    Invoke-SmokeCleanupAction -Name "user PATH" -Action $PathAction -Errors $cleanupErrors
    Invoke-SmokeCleanupAction -Name "installer registry" -Action $RegistryAction -Errors $cleanupErrors
    Invoke-SmokeCleanupAction -Name "sentinel config" -Action $ConfigAction -Errors $cleanupErrors

    if ($cleanupErrors.Count -gt 0) {
        throw "Smoke cleanup completed with errors: $([string]::Join(' | ', $cleanupErrors))"
    }
}

param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Get-CodexDesktopTarget {
    $apps = @(Get-StartApps | Where-Object { $_.AppID -like 'OpenAI.Codex_*!*' })
    if ($apps.Count -ne 1) { throw '无法唯一定位 Codex 桌面应用，未执行重启。' }
    $family = ($apps[0].AppID -split '!')[0]
    $identity, $publisher = $family -split '_', 2
    return [pscustomobject]@{
        AppId = $apps[0].AppID
        PathPattern = '*\WindowsApps\' + $identity + '_*__' + $publisher + '\app\ChatGPT.exe'
    }
}

function Get-CodexDesktopProcesses($Target) {
    return @(Get-Process -Name ChatGPT -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like $Target.PathPattern })
}

function Restart-CodexDesktop($Target) {
    # Close windows first. Only this desktop package's GUI processes may be terminated.
    foreach ($process in (Get-CodexDesktopProcesses $Target)) {
        if ($process.MainWindowHandle -ne 0) { [void]$process.CloseMainWindow() }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(6)
    while ((Get-CodexDesktopProcesses $Target).Count -gt 0 -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }
    foreach ($process in (Get-CodexDesktopProcesses $Target)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while ((Get-CodexDesktopProcesses $Target).Count -gt 0 -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }
    if ((Get-CodexDesktopProcesses $Target).Count -gt 0) { throw 'Codex 未完全退出，未启动第二个实例。' }
    Start-Process -FilePath explorer.exe -ArgumentList ('shell:AppsFolder\' + $Target.AppId) -WindowStyle Hidden
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 500
        $windows = @(Get-CodexDesktopProcesses $Target | Where-Object { $_.MainWindowHandle -ne 0 -and $_.Responding })
    } while ($windows.Count -eq 0 -and [DateTime]::UtcNow -lt $deadline)
    if ($windows.Count -eq 0) { throw '已发出启动请求，但未确认 Codex 窗口打开。请从开始菜单打开。' }
}

try {
    $target = Get-CodexDesktopTarget
    if ($DryRun) {
        [pscustomobject]@{ AppId = $target.AppId; MatchingGuiProcesses = (Get-CodexDesktopProcesses $target).Count; PathPattern = $target.PathPattern } | ConvertTo-Json -Compress
    } else {
        Restart-CodexDesktop $target
        Write-Output 'Codex restarted and its window is responding.'
    }
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}

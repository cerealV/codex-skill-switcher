$ErrorActionPreference = 'Stop'
$pythonExe = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show('找不到 Codex 自带 Python。请先运行 Codex 桌面版，再重试。', 'Skill 开关') | Out-Null
    exit 1
}
$scriptFile = Join-Path $PSScriptRoot 'skill_switcher.py'
Start-Process -FilePath $pythonExe -ArgumentList ('"' + $scriptFile + '"') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden

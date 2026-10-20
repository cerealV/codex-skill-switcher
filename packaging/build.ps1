param(
    [string]$Python = 'python',
    [string]$Iscc = 'ISCC.exe'
)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $sources = Join-Path (Get-Location) 'skill-switcher'
    & $Python -m PyInstaller --noconfirm --clean --onedir --windowed --name CodexSkillSwitcher --specpath build --workpath build/pyinstaller --distpath dist --add-data "$sources/catalog.json;." --add-data "$sources/restart_codex.ps1;." skill-switcher/skill_switcher.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    $pythonBase = & $Python -c 'import sys; print(sys.base_prefix)'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot locate Python runtime.' }
    $licenses = New-Item -ItemType Directory -Force 'dist/CodexSkillSwitcher/licenses'
    Copy-Item -LiteralPath (Join-Path $pythonBase 'LICENSE.txt') -Destination (Join-Path $licenses 'Python.txt')
    Copy-Item -LiteralPath (Join-Path $pythonBase 'tcl/tk8.6/license.terms') -Destination (Join-Path $licenses 'Tk.txt')
    Copy-Item -LiteralPath 'packaging/licenses/Tcl.txt' -Destination $licenses
    & $Iscc packaging/setup.iss
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }
    $installer = Get-Item 'dist/CodexSkillSwitcher-1.0.1-x64-setup.exe'
    $hash = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $($installer.Name)" | Set-Content -LiteralPath 'dist/SHA256SUMS.txt' -Encoding ascii
    Write-Output "Built $($installer.FullName)"
} finally {
    Pop-Location
}

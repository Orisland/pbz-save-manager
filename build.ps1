$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONPATH = Join-Path $PSScriptRoot "src"
$env:PYTHONIOENCODING = "utf-8"
$icon = Join-Path $PSScriptRoot "assets\pbz-save.ico"
$iconPng = Join-Path $PSScriptRoot "assets\pbz-save.png"
python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
python (Join-Path $PSScriptRoot "tools\make_icon.py")
python -m unittest discover -s (Join-Path $PSScriptRoot "tests") -v
python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name PBZSaveManager `
    --icon $icon `
    --add-data "$icon;." `
    --add-data "$iconPng;." `
    --paths (Join-Path $PSScriptRoot "src") `
    --collect-all unicorn `
    --collect-all cryptography `
    --hidden-import unicorn.x86_const `
    (Join-Path $PSScriptRoot "run.py")
# 启动器默认打开控制流保护，槽位解压会在 longjmp 时被系统结束。打包后清掉这个标记。
python (Join-Path $PSScriptRoot "tools\disable_process_cfg.py") (Join-Path $PSScriptRoot "dist\PBZSaveManager.exe")

# gk-harness launch shortcuts, dot-sourced from your PowerShell profile by the installer, which
# sets $env:GK_HARNESS_ROOT to the harness checkout first. gk is Opus at medium effort, gk-fable
# is Fable, gk-ultra is Opus with ultracode on, and gkh runs the installer (gkh update,
# gkh fable off). Edit them in the gk-harness repo; nothing depends on them.
# gk-ultra names a settings file rather than passing the JSON inline: Windows PowerShell 5.1
# (and a .cmd shim under pwsh) drops the double quotes inside an argument to a native command.
# A session that loaded an older version may hold these names as aliases, which would win over
# the functions below; drop them first.
foreach ($gkName in 'gk', 'gk-opus', 'gk-sonnet', 'gk-fable', 'gk-ultra', 'gkh') {
    Remove-Item -Path "Alias:$gkName" -Force -ErrorAction SilentlyContinue
}
Remove-Item -Path 'Function:gk-opus', 'Function:gk-sonnet' -Force -ErrorAction SilentlyContinue
Remove-Variable -Name gkName -ErrorAction SilentlyContinue
function gk { claude --model opus --effort medium @args }
function gk-fable { claude --model fable --effort medium @args }
function gk-ultra { claude --model opus --effort medium --settings "$env:GK_HARNESS_ROOT/shell/gk-ultra.json" @args }
# Without GK_HARNESS_ROOT gkh writes why and returns, rather than run python "/install.py".
function gkh { if (-not $env:GK_HARNESS_ROOT) { Write-Error 'gk-harness: open a new shell, or run install.py install from the gk-harness checkout'; return }; python "$env:GK_HARNESS_ROOT/install.py" @args }

# gk-harness installer entry point: finds a suitable Python and hands over to install.py.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$check = 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'

$candidates = @(
    ,@('py', '-3')
    ,@('python')
)
foreach ($c in $candidates) {
    if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
    $extra = @()
    if ($c.Count -gt 1) { $extra = $c[1..($c.Count - 1)] }
    & $c[0] @extra -c $check 2>$null
    if ($LASTEXITCODE -eq 0) {
        & $c[0] @extra (Join-Path $root 'install.py') @args
        exit $LASTEXITCODE
    }
}

[Console]::Error.WriteLine('gk-harness: Python 3.9 or newer is required.')
[Console]::Error.WriteLine('Install it from https://www.python.org/downloads/')
exit 1

# agent-peer one-door installer for native Windows (docs/tasks/0024 Phase 4).
# Thin bootstrap only - gets `agent-peer` onto PATH, then hands off to
# `agent-peer setup`. All real logic lives in Python. PowerShell 5.1+
# compatible (no `&&`/`||` chaining - confirmed live against a real
# Windows 11 / PowerShell 5.1 machine, not assumed to be PS7).
#
# Usage:
#   irm <raw-url>/install.ps1 | iex
#   powershell -File install.ps1

if (Get-Command agent-peer -ErrorAction SilentlyContinue) {
    agent-peer setup
    exit $LASTEXITCODE
}

$PY = $null
foreach ($candidate in @("python", "py -3.13", "py -3.12", "py -3.11", "py -3.10")) {
    $parts = $candidate -split " "
    $exe = $parts[0]
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        $check = & $parts[0] $parts[1..($parts.Length - 1)] -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $PY = $candidate
            break
        }
    }
}
if (-not $PY) {
    Write-Error "agent-peer: needs Python 3.10+ and none was found."
    Write-Error "Install it from https://www.python.org/downloads/ (check 'Add to PATH'), then re-run this script."
    exit 1
}
$PyParts = $PY -split " "

$INSTALLER = $null
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $INSTALLER = "uv"
} elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
    $INSTALLER = "pipx"
} else {
    & $PyParts[0] $PyParts[1..($PyParts.Length - 1)] -m pip --version *> $null
    if ($LASTEXITCODE -eq 0) {
        $INSTALLER = "pip"
    }
}
if (-not $INSTALLER) {
    Write-Error "agent-peer: no installer available - need one of: uv, pipx, or python with pip."
    Write-Error "Easiest: install uv (https://docs.astral.sh/uv/) and re-run this script."
    exit 1
}

switch ($INSTALLER) {
    "uv"   { uv tool install agent-peer }
    "pipx" { pipx install agent-peer }
    "pip"  { & $PyParts[0] $PyParts[1..($PyParts.Length - 1)] -m pip install --user agent-peer }
}
$InstallStatus = $LASTEXITCODE

if ($InstallStatus -ne 0) {
    Write-Error "agent-peer: install via $INSTALLER failed (see the error above)."
    exit 1
}

if (-not (Get-Command agent-peer -ErrorAction SilentlyContinue)) {
    Write-Error "agent-peer: installed but 'agent-peer' is not on PATH."
    if ($INSTALLER -eq "pip") {
        $binDir = & $PyParts[0] $PyParts[1..($PyParts.Length - 1)] -c "import site, os; print(os.path.join(site.getuserbase(), 'Scripts'))"
        Write-Error "It likely landed in: $binDir"
        Write-Error "Add it to PATH for future sessions: setx PATH `"$binDir;%PATH%`""
        Write-Error "Then open a new terminal and run: agent-peer setup"
    } elseif ($INSTALLER -eq "uv") {
        Write-Error "Run: uv tool update-shell"
        Write-Error "Then open a new terminal and run: agent-peer setup"
    } else {
        Write-Error "Open a new terminal (or check your $INSTALLER PATH setup) and run: agent-peer setup"
    }
    exit 1
}

agent-peer setup
exit $LASTEXITCODE

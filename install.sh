#!/bin/sh
# agent-peer one-door installer (docs/tasks/0022, Step 1 + 1a).
# Thin bootstrap only: get the `agent-peer` binary onto PATH, then hand off
# to `agent-peer setup` for everything interactive. All real logic lives in
# Python. macOS/Linux only.
#
# Usage:
#   curl -fsSL <raw-url>/install.sh | sh
#   sh install.sh

# Already installed? Never reinstall on a re-run.
if command -v agent-peer >/dev/null 2>&1; then
    exec agent-peer setup
fi

# Platform: macOS/Linux only (lsof cwd resolution, Keychain reads - no
# Windows branch anywhere in agent_peer/).
OS="$(uname -s)"
if [ "$OS" != "Darwin" ] && [ "$OS" != "Linux" ]; then
    echo "agent-peer: unsupported platform '$OS' - macOS and Linux only." >&2
    exit 1
fi

# Python present and new enough (pyproject requires-python >= 3.10). Some
# systems ship an old `python3` next to a newer interior version, so probe
# the versioned names too before giving up.
PY=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            PY="$candidate"
            break
        fi
    fi
done
if [ -z "$PY" ]; then
    echo "agent-peer: needs Python 3.10+ and none was found." >&2
    if [ "$OS" = "Darwin" ]; then
        echo "Install it via 'brew install python' or from https://www.python.org/downloads/ , then re-run this script." >&2
    else
        echo "Install it via your distro's package manager (e.g. 'apt install python3') or from https://www.python.org/downloads/ , then re-run this script." >&2
    fi
    exit 1
fi

# Installer chain, first hit wins. Plain pip is the last resort and only
# counts when `python3 -m pip` actually works (some distros ship Python
# without pip).
INSTALLER=""
if command -v uv >/dev/null 2>&1; then
    INSTALLER="uv"
elif command -v pipx >/dev/null 2>&1; then
    INSTALLER="pipx"
elif "$PY" -m pip --version >/dev/null 2>&1; then
    INSTALLER="pip"
else
    echo "agent-peer: no installer available - need one of: uv, pipx, or python3 with pip." >&2
    echo "Easiest: install uv (https://docs.astral.sh/uv/) and re-run this script." >&2
    exit 1
fi

case "$INSTALLER" in
    uv)   uv tool install agent-peer ;;
    pipx) pipx install agent-peer ;;
    pip)  "$PY" -m pip install --user agent-peer ;;
esac
INSTALL_STATUS=$?

# Check the install command's own exit code first - it can fail outright
# (e.g. PEP 668 "externally-managed-environment" blocks plain `pip install
# --user` on Debian 12+/Ubuntu 23.04+) and must never be reported as the
# unrelated "installed but not on PATH" case below.
if [ "$INSTALL_STATUS" -ne 0 ]; then
    echo "agent-peer: install via $INSTALLER failed (see the error above)." >&2
    if [ "$INSTALLER" = "pip" ]; then
        echo "If that was an 'externally-managed-environment' error (PEP 668, common on" >&2
        echo "Debian 12+/Ubuntu 23.04+): install 'uv' or 'pipx' instead (recommended), or" >&2
        echo "force it yourself: $PY -m pip install --user --break-system-packages agent-peer" >&2
    fi
    exit 1
fi

# Post-install PATH sanity check. The --user fallback lands the entrypoint
# in ~/.local/bin which is not always on PATH - say exactly where it went
# and what to add instead of leaving "installed but command not found".
if ! command -v agent-peer >/dev/null 2>&1; then
    echo "agent-peer: installed but 'agent-peer' is not on PATH." >&2
    if [ "$INSTALLER" = "pip" ]; then
        BIN_DIR="$("$PY" -c 'import site, os; print(os.path.join(site.getuserbase(), "bin"))')"
        echo "It likely landed in: $BIN_DIR" >&2
        echo "Add this to your shell profile (~/.zshrc, ~/.bashrc, ...):" >&2
        echo "  export PATH=\"$BIN_DIR:\$PATH\"" >&2
        echo "Then run: agent-peer setup" >&2
    else
        echo "Re-open your shell (or check your $INSTALLER PATH setup) and run: agent-peer setup" >&2
    fi
    exit 1
fi

exec agent-peer setup

#!/usr/bin/env bash
# Install OpenSprite from a checked-out repository on Linux.
#
# Usage:
#   bash setup-opensprite.sh
#   bash setup-opensprite.sh --dev
#   bash setup-opensprite.sh --start
#
# After install, use:
#   opensprite service start

set -euo pipefail

INSTALL_DEV=0
CREATE_LINK=1
START_SERVICE=0

for arg in "$@"; do
  case "$arg" in
    --dev)
      INSTALL_DEV=1
      ;;
    --no-link)
      CREATE_LINK=0
      ;;
    --start)
      START_SERVICE=1
      ;;
    -h|--help)
      cat <<'EOF'
Usage: bash setup-opensprite.sh [options]

Options:
  --dev       Install development dependencies with -e ".[dev]".
  --no-link   Do not create ~/.local/bin/opensprite symlink.
  --start     Start the background gateway after installation.
  -h, --help  Show this help.

This script expects to be run from the OpenSprite repository checkout.
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f "pyproject.toml" || ! -d "src/opensprite" ]]; then
  echo "Error: run this script from the OpenSprite repository root." >&2
  exit 1
fi

echo "==> OpenSprite Linux setup"
echo "Repository: $SCRIPT_DIR"

install_debian_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    return 0
  fi
  if ! command -v sudo >/dev/null 2>&1 && [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "sudo is not available; skipping apt package installation."
    return 0
  fi

  local sudo_cmd=()
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    sudo_cmd=(sudo)
  fi

  echo "==> Installing Debian/Ubuntu system packages"
  "${sudo_cmd[@]}" apt-get update
  "${sudo_cmd[@]}" apt-get install -y git python3 python3-venv python3-pip nodejs npm
}

install_debian_packages

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
    then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Error: Python 3.11+ is required." >&2
  echo "Install Python 3.11+ and re-run this script." >&2
  exit 1
fi

echo "==> Using Python: $($PYTHON_BIN --version)"

if [[ ! -d ".venv" ]]; then
  echo "==> Creating virtual environment: .venv"
  "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Upgrading pip"
python -m pip install --upgrade pip

if [[ "$INSTALL_DEV" -eq 1 ]]; then
  echo "==> Installing OpenSprite in editable dev mode"
  python -m pip install -e ".[dev]"
else
  echo "==> Installing OpenSprite in editable mode"
  python -m pip install -e .
fi

if [[ "$CREATE_LINK" -eq 1 ]]; then
  mkdir -p "$HOME/.local/bin"
  ln -sfn "$SCRIPT_DIR/.venv/bin/opensprite" "$HOME/.local/bin/opensprite"
  echo "==> Linked: $HOME/.local/bin/opensprite"
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *)
      echo "==> Add ~/.local/bin to PATH if your shell cannot find opensprite:"
      echo '    export PATH="$HOME/.local/bin:$PATH"'
      ;;
  esac
fi

echo "==> Verifying CLI"
"$SCRIPT_DIR/.venv/bin/opensprite" --version

if [[ "$START_SERVICE" -eq 1 ]]; then
  echo "==> Starting OpenSprite background service"
  "$SCRIPT_DIR/.venv/bin/opensprite" service start
  "$SCRIPT_DIR/.venv/bin/opensprite" service status
fi

cat <<EOF

OpenSprite setup complete.

Commands:
  opensprite service start
  opensprite service status
  opensprite service stop

Logs:
  tail -f ~/.opensprite/logs/gateway.log

If 'opensprite' is not found, run:
  export PATH="\$HOME/.local/bin:\$PATH"

EOF

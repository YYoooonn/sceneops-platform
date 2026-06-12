#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Checking uv..."
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is not installed."
  echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

echo "==> Syncing Python workspace packages..."
uv sync --all-packages --group dev

echo "==> Verifying core package imports..."
uv run python -c "import sceneops_core; print('sceneops_core:', sceneops_core.__file__)"
uv run python -c "import sceneops_db; print('sceneops_db:', sceneops_db.__file__)"
uv run python -c "import sceneops_storage; print('sceneops_storage:', sceneops_storage.__file__)"

echo "==> Verifying artifact contracts import..."
uv run python -c "from sceneops_core.artifacts.contracts import *; print('artifact contracts import ok')"

echo ""
echo "Setup complete."
echo ""
echo "For VSCode/Pylint, select interpreter:"
echo "  $ROOT_DIR/.venv/bin/python"
echo ""
echo "Optional shell activation:"
echo "  source .venv/bin/activate"

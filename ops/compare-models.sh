#!/usr/bin/env bash
#
# Run one battery against every local model and write a comparison workpaper.
#
# The point is not to crown a winner. Probes measure different things and a
# model that resists injection may still cite badly, so the useful output is
# the shape of the differences: which failure modes are common to every model
# on this hardware, and which belong to one of them. A finding that reproduces
# across four independent models is usually a finding about the *procedure*.
#
# Runtime is the sum of all endpoints, not the slowest -- roughly 45-70 minutes
# for four models on a GTX 1080, because ollama swaps models in and out of the
# 8 GB of VRAM between endpoints rather than holding them all resident.
#
# Usage:
#   ops/compare-models.sh              compare the models listed below
#   ops/compare-models.sh --dry-run    show what would run
#   ops/compare-models.sh a:1b b:2b    compare exactly these models instead

set -euo pipefail

export PATH="/home/justin-a/.local/bin:/usr/local/bin:/usr/bin:/bin"
UV_BIN="${UV_BIN:-/home/justin-a/.local/bin/uv}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname -- "$SCRIPT_DIR")"

SUITE="${SUITE:-suites/nightly.json}"
OUT_DIR="${OUT_DIR:-runs/comparison}"

# Everything here has to fit in 8 GB of VRAM alongside the desktop. Swap the
# list when the card changes; the label is what appears in the workpaper, so
# keep it short and recognisable.
DEFAULT_MODELS=(
  "qwen3:8b"
  "llama3.2:3b"
  "gemma3:4b"
  "phi4-mini"
)

DRY_RUN=0
MODELS=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --help|-h) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) MODELS+=("$arg") ;;
  esac
done
[ ${#MODELS[@]} -eq 0 ] && MODELS=("${DEFAULT_MODELS[@]}")

# ollama is the only endpoint here; the key is required by the adapter and
# ignored by the server (D-001 keeps real endpoints behind an explicit key).
export OPENAI_API_KEY="${OPENAI_API_KEY:-ollama}"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://localhost:11434/v1}"

cd "$REPO_DIR"

ARGS=("$SUITE")
for model in "${MODELS[@]}"; do
  # The label drops the size tag so the workpaper columns stay narrow; the
  # full model id is still recorded in each endpoint's fingerprint.
  label="${model%%:*}"
  ARGS+=(--endpoint "${label}=openai:${model}")
done
ARGS+=(--out "$OUT_DIR" --format md html)

echo "suite    $SUITE"
echo "models   ${MODELS[*]}"
echo "out      $OUT_DIR"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY RUN -- would execute:"
  echo "  $UV_BIN run python cli.py compare ${ARGS[*]}"
  exit 0
fi

exec "$UV_BIN" run python cli.py compare "${ARGS[@]}"

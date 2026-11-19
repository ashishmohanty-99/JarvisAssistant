#!/usr/bin/env bash
# One-time setup on macOS or Linux. Safe to run again: it updates what is there.
#   scripts/setup.sh
# Installs OpenJarvis next to this repository's parent (~/OpenJarvis by default), this package into
# the same environment, the browser used for application forms, and a private settings file.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
JARVIS_DIR="${JARVIS_DIR:-$HOME/OpenJarvis}"
STATE="${SIDEKICK_HOME:-$HOME/.jarvis-sidekick}"

command -v git >/dev/null || { echo "git is required."; exit 1; }
if ! command -v uv >/dev/null; then
    echo "uv is required. Install it from https://docs.astral.sh/uv/ and run this again."
    exit 1
fi

if [ -d "$JARVIS_DIR/.git" ]; then
    git -C "$JARVIS_DIR" pull --ff-only --quiet || echo "Could not update OpenJarvis; keeping the current copy."
else
    git clone --depth 1 https://github.com/open-jarvis/OpenJarvis "$JARVIS_DIR"
fi

(cd "$JARVIS_DIR" && uv sync --extra speech --quiet)
uv pip install --quiet --python "$JARVIS_DIR/.venv/bin/python" -e "$REPO[resume,apply,youtube,voice]"

if [ "${SIDEKICK_SKIP_BROWSER:-}" != "1" ]; then
    "$JARVIS_DIR/.venv/bin/playwright" install chromium
fi

mkdir -p "$STATE"
chmod 700 "$STATE"
if [ ! -f "$STATE/.env" ]; then
    cp "$REPO/.env.example" "$STATE/.env"
    echo "Created $STATE/.env: open it and fill in your keys."
fi
chmod 600 "$STATE/.env"

cat <<DONE

Installed. Next:
  1. Fill in $STATE/.env
  2. source "$JARVIS_DIR/.venv/bin/activate"
  3. set -a; . "$STATE/.env"; set +a
  4. sidekick doctor
See TESTING.md for the full test run.
DONE

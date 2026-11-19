#!/bin/zsh
# Runs the bot as a login service on this Mac: starts at login, restarts if it stops.
# Usage: deploy/macos/install.sh            (install or update)
#        deploy/macos/install.sh --remove   (stop and remove)
set -euo pipefail

LABEL=com.jarvis.sidekick.bot
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
ENV_FILE="$HOME/.jarvis-sidekick/.env"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--remove" ]]; then
    rm -f "$TARGET"
    echo "Removed $LABEL"
    exit 0
fi

SIDEKICK="$(command -v sidekick || true)"
[[ -n "$SIDEKICK" ]] || { echo "sidekick is not on PATH. Activate the environment you installed it in, then run again."; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Create $ENV_FILE first (copy .env.example and fill it in)."; exit 1; }
chmod 600 "$ENV_FILE"

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__HOME__|$HOME|g" -e "s|__SIDEKICK__|$SIDEKICK|g" "$(dirname "$0")/$LABEL.plist" > "$TARGET"
launchctl bootstrap "gui/$(id -u)" "$TARGET"
echo "Installed. Log: $HOME/.jarvis-sidekick/bot.log"
echo "Keep the Mac awake: System Settings > Battery > Options > 'Prevent automatic sleeping on power adapter when the display is off'."

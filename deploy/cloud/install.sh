#!/usr/bin/env bash
# Sets up the bot on a fresh Ubuntu 24.04 server. Run as root from a clone of this repository:
#   deploy/cloud/install.sh
# Safe to run again after `git pull`: it updates the program and keeps your settings and data.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Run this as root: sudo deploy/cloud/install.sh"; exit 1; }

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
APP=/opt/jarvis-sidekick
ENV_FILE=/etc/jarvis-sidekick/env
UNIT=/etc/systemd/system/jarvis-sidekick.service
export DEBIAN_FRONTEND=noninteractive

echo "== System packages"
apt-get update -qq
# libatomic1: needed by the Node runtime bundled with Playwright, and absent from minimal cloud images.
apt-get install -y -qq python3 python3-venv ca-certificates ufw unattended-upgrades fail2ban libatomic1 >/dev/null
python3 -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' || {
    echo "Python 3.11 to 3.13 is needed; this server has $(python3 --version). Use Ubuntu 24.04."; exit 1; }

if [ "${SIDEKICK_SKIP_FIREWALL:-}" != "1" ]; then
    echo "== Firewall: SSH in, everything else blocked; repeated failed logins get banned (fail2ban)"
    ufw allow OpenSSH >/dev/null
    ufw --force enable >/dev/null
fi

echo "== Service account"
id jarvis >/dev/null 2>&1 || useradd --system --home-dir /var/lib/jarvis-sidekick --no-create-home \
    --shell /usr/sbin/nologin jarvis

echo "== Program"
mkdir -p "$APP"
[ -x "$APP/venv/bin/python" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet --upgrade pip
"$APP/venv/bin/pip" install --quiet --upgrade "$REPO[resume,apply,youtube,brain]"
# Same version number, new code: make sure an update really replaces the installed copy.
"$APP/venv/bin/pip" install --quiet --force-reinstall --no-deps "$REPO"

if [ "${SIDEKICK_SKIP_BROWSER:-}" != "1" ]; then
    echo "== Browser for application forms"
    "$APP/venv/bin/playwright" install-deps chromium >/dev/null
    PLAYWRIGHT_BROWSERS_PATH="$APP/browsers" "$APP/venv/bin/playwright" install chromium >/dev/null
fi

echo "== Settings"
mkdir -p "$(dirname "$ENV_FILE")"
chmod 700 "$(dirname "$ENV_FILE")"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<'SETTINGS'
# Jarvis sidekick settings. Only root can read this file.
# After any change:  systemctl restart jarvis-sidekick

ELEVENLABS_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

SIDEKICK_YOUTUBE_CHANNEL=
SIDEKICK_COUNTRIES=in,nl
SIDEKICK_LOCATIONS="India, Netherlands, Japan, remote"

# Optional: free keys from developer.adzuna.com for India and Netherlands listings
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
SETTINGS
fi
# Settings added in later versions: append them to an existing file without touching what is there.
grep -q '^SIDEKICK_TIMEZONE=' "$ENV_FILE" || printf '\n# Your time zone, for the greeting\nSIDEKICK_TIMEZONE=Asia/Kolkata\n' >> "$ENV_FILE"
grep -q '^ANTHROPIC_API_KEY=' "$ENV_FILE" || printf '\n# Claude API key: turns commands into conversation (console.anthropic.com)\nANTHROPIC_API_KEY=\n' >> "$ENV_FILE"
grep -q '^SIDEKICK_MORNING=' "$ENV_FILE" || printf '\n# Daily message at this time (HH:MM, your time zone). Empty = none\nSIDEKICK_MORNING=\n' >> "$ENV_FILE"
grep -q '^YOUTUBE_CLIENT_ID=' "$ENV_FILE" || printf '\n# One-tap YouTube publishing: OAuth client from your Google Cloud project (deploy/YOUTUBE.md)\nYOUTUBE_CLIENT_ID=\nYOUTUBE_CLIENT_SECRET=\n' >> "$ENV_FILE"
grep -q '^SIDEKICK_TITLE=' "$ENV_FILE" || printf '\n# How the bot addresses you\nSIDEKICK_TITLE=Master\n' >> "$ENV_FILE"
chown root:root "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "== Service"
install -m 644 "$REPO/deploy/cloud/jarvis-sidekick.service" "$UNIT"
install -m 644 "$REPO/deploy/cloud/jarvis-sidekick-poweroff.path" /etc/systemd/system/
install -m 644 "$REPO/deploy/cloud/jarvis-sidekick-poweroff.service" /etc/systemd/system/
rm -f /var/lib/jarvis-sidekick/poweroff.request
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
    systemctl enable jarvis-sidekick >/dev/null 2>&1
    systemctl enable --now jarvis-sidekick-poweroff.path >/dev/null 2>&1
    if grep -Eq '^TELEGRAM_BOT_TOKEN=.+' "$ENV_FILE" && grep -Eq '^TELEGRAM_CHAT_ID=.+' "$ENV_FILE"; then
        systemctl restart jarvis-sidekick
        echo "Bot restarted. Watch it:  journalctl -u jarvis-sidekick -f"
    else
        cat <<NEXT

Installed. Now:
  1. nano $ENV_FILE          (fill in the three keys at the top; Ctrl+O, Enter, Ctrl+X to save)
  2. systemctl restart jarvis-sidekick
  3. journalctl -u jarvis-sidekick -n 20
Your bot should then message you "Jarvis sidekick is online".
NEXT
    fi
fi

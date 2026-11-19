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
apt-get install -y -qq python3 python3-venv ca-certificates ufw unattended-upgrades fail2ban >/dev/null
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
"$APP/venv/bin/pip" install --quiet --upgrade "$REPO[resume,apply,youtube]"

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
chown root:root "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "== Service"
install -m 644 "$REPO/deploy/cloud/jarvis-sidekick.service" "$UNIT"
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
    systemctl enable jarvis-sidekick >/dev/null 2>&1
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

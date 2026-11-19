# Running it all the time

The bot makes outbound connections only (Telegram, ElevenLabs, job and market sources). It never listens on a port, so there is nothing to open in a firewall or router.

## On your Mac (now)

```bash
mkdir -p ~/.jarvis-sidekick && cp .env.example ~/.jarvis-sidekick/.env   # fill it in
deploy/macos/install.sh
tail -f ~/.jarvis-sidekick/bot.log
```

This installs a login service (`launchd`) that starts the bot when you log in and restarts it if it stops. `deploy/macos/install.sh --remove` takes it out.

- The Mac must stay awake and online. On power: System Settings, Battery, Options, "Prevent automatic sleeping on power adapter when the display is off". With the lid closed and no external display it sleeps regardless.
- `sidekick voice` (microphone and speakers) works only here, on the Mac. From the iPad and phone you use the Telegram bot.
- The first `jobs apply` needs `playwright install chromium`.

## On a cloud server

A small Linux server is enough: Ubuntu 24.04, 2 GB of memory or more. Everything can be done from a browser or an SSH app on a tablet.

```bash
git clone https://github.com/ashishmohanty-99/JarvisAssistant
cd JarvisAssistant
deploy/cloud/install.sh
nano /etc/jarvis-sidekick/env        # fill in the three keys at the top
systemctl restart jarvis-sidekick
journalctl -u jarvis-sidekick -n 20
```

Then send your resume PDF to the bot in Telegram, and send `/doctor`.

What the installer does:
- Turns on a firewall that allows inbound SSH and nothing else, bans addresses after repeated failed logins, and enables automatic security updates.
- Installs the program under `/opt/jarvis-sidekick` and the browser used for application forms.
- Creates `/etc/jarvis-sidekick/env`, readable by root only, for your keys.
- Runs the bot as a system service under an account with no login and no privileges. The service can write only to `/var/lib/jarvis-sidekick`, where your resume, application packets and history live.

To update later: `cd JarvisAssistant && git pull && deploy/cloud/install.sh`.

Day to day:

| To | Run |
|---|---|
| see what the bot is doing | `journalctl -u jarvis-sidekick -f` |
| change a setting | `nano /etc/jarvis-sidekick/env`, then `systemctl restart jarvis-sidekick` |
| stop it | `systemctl stop jarvis-sidekick` |

What a server cannot do:
- No microphone, so no `sidekick voice`. Voice notes through Telegram work the same.
- Free-form questions need OpenJarvis and a model, which this install leaves out. The slash commands and voice-note commands need neither.

Run the bot in one place at a time. Two copies with the same token take turns stealing each other's messages.

Your resume and application history live on that server: choose a provider and region you are comfortable with, use a long unique password or an SSH key for it, and turn on two-factor sign-in for the provider account.

The installer was run on Ubuntu 24.04 without its firewall and browser-download steps, and the program was started under the service account. The service's systemd restrictions have not been exercised on a live server.

### Docker instead

`deploy/cloud/Dockerfile` and `docker-compose.yml` do the same job in a container (unprivileged user, read-only filesystem, no ports). They were written without a Docker daemon to build them on, so expect to adjust on the first build. Put settings in `/etc/jarvis-sidekick/env` and run `docker compose -f deploy/cloud/docker-compose.yml up -d --build`.

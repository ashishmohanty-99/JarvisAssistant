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

## On a cloud server (later)

A small Linux server is enough: 2 vCPU, 4 GB memory, 20 GB disk, Ubuntu LTS.

1. Lock the server down before anything else: SSH keys only (`PasswordAuthentication no`), a firewall that allows inbound SSH and nothing else (`ufw default deny incoming; ufw allow OpenSSH; ufw enable`), automatic security updates (`unattended-upgrades`).
2. Install Docker, then clone this repository.
3. Put your settings in `/etc/jarvis-sidekick/env` (same keys as `.env.example`), owned by root, `chmod 600`.
4. `docker compose -f deploy/cloud/docker-compose.yml up -d --build`
5. Copy your resume into the data volume:
   `docker compose -f deploy/cloud/docker-compose.yml cp ~/resume.pdf bot:/data/resume.pdf`

The container runs as an unprivileged user with a read-only filesystem, no Linux capabilities and no published ports.

Run the bot in one place at a time. Two copies with the same token take turns stealing each other's messages. When you move to the server, stop the Mac service first.

What changes on a server:
- No microphone, so no `sidekick voice`. Voice notes through Telegram work the same.
- Free-form questions need OpenJarvis and a model. A 4 GB server is too small for a useful local model; either point OpenJarvis at a cloud model (its `inference-cloud` extra and your own API key) or use only the slash commands, which need no model.
- Your resume and application history now live on that server. Use a provider and region you are comfortable with, and keep disk encryption on.

The Dockerfile and compose file were written without a Docker daemon to build them on. Expect to adjust on the first build.

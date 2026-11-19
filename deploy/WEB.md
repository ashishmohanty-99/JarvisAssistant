# The Jarvis screen

A page for your tablet or phone: an orb you hold to talk, spoken answers, tap buttons, the conversation, and a live board of markets and headlines. It runs the same fixed commands as the Telegram bot; there is no language model behind it.

## How it is protected

- **It listens on the server itself only** (127.0.0.1). Nothing on the internet can connect to it directly.
- **You reach it over a private network** that only your own devices are on. The steps below use Tailscale, which is free for personal use.
- **Signing in needs a code sent to your Telegram**, so the page is tied to the same single owner as the bot. Codes work once, for five minutes, and five wrong tries burn one. A session lasts 30 days; only a hash of it is stored.
- **It cannot act on the outside world.** Sending an application, publishing a video and powering off are refused on the page and stay typed commands in Telegram.
- The page loads nothing from other sites, runs no third-party script, and writes everything it receives as plain text.

## Turn it on

The Tailscale steps are written from memory of its documentation and have not been run on this setup. If a command differs, Tailscale's own page for it is the authority.

1. **On the iPad and phone:** install the Tailscale app from the App Store and sign in. This creates your private network.

2. **On the server**, join the same network:

   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   tailscale up
   ```

   `tailscale up` prints a web address. Open it and sign in with the same account as on the iPad.

3. **Let your private network through the firewall**, and nothing else:

   ```bash
   ufw allow in on tailscale0
   ```

4. **Turn on secure addresses** (the browser only allows the microphone on a secure page): in the Tailscale admin console, under DNS, enable **MagicDNS** and **HTTPS Certificates**.

5. **Switch the screen on and publish it to your private network:**

   ```bash
   sed -i "s|^SIDEKICK_WEB_PORT=.*|SIDEKICK_WEB_PORT=8377|" /etc/jarvis-sidekick/env
   systemctl restart jarvis-sidekick
   tailscale serve --bg 8377
   ```

   The last command prints the address of the page, like `https://your-server.your-network.ts.net`.

6. **Open that address on the iPad** (with Tailscale switched on there). Tap **Send me a code**, read the code your bot sends in Telegram, enter it. In Safari, use Share, then **Add to Home Screen**, to get a full-screen Jarvis icon.

## Using it

- **Hold the orb and speak**, then let go. The first time, the browser asks to use the microphone.
- It shows what it heard, answers in text, and speaks the answer.
- The buttons under the orb are the common requests. When Jarvis asks a question, the buttons become the possible answers.
- "Jarvis, go to sleep" dims it; saying "Jarvis" wakes it.
- A PDF edition appears as a link in the answer.

## What it does not do

- It does not listen until you hold the orb. There is no always-on wake word: every recording is sent to ElevenLabs and billed, so an open microphone would transcribe the room all day.
- It does not understand free conversation. A sentence is matched to the nearest command; anything else gets the list of commands.

## Turning it off

```bash
tailscale serve --bg off 2>/dev/null || tailscale serve reset
sed -i "s|^SIDEKICK_WEB_PORT=.*|SIDEKICK_WEB_PORT=|" /etc/jarvis-sidekick/env
systemctl restart jarvis-sidekick
```

# Testing it end to end

The automated tests (`pytest`, 100 of them) prove the logic with stand-ins for every outside service. This page is the other half: running against the real services on your own machine, which the build environment could not reach. Work top to bottom; each step says what you should see. Expect a few small fixes on the first pass. Note what fails and the exact message.

## 0. Set up (once)

```bash
git clone https://github.com/ashishmohanty-99/JarvisAssistant && cd JarvisAssistant
scripts/setup.sh
```

Then fill in `~/.jarvis-sidekick/.env`:

| Setting | Where it comes from | Needed for |
|---|---|---|
| `ELEVENLABS_API_KEY` | elevenlabs.io, Developers, API keys. Give the key a monthly character limit. | voice in and out |
| `TELEGRAM_BOT_TOKEN` | Telegram, message @BotFather, `/newbot` | the phone and iPad bot |
| `TELEGRAM_CHAT_ID` | Telegram, message @userinfobot | the bot answers only this id |
| `SIDEKICK_RESUME` | full path to your resume PDF | jobs |
| `SIDEKICK_YOUTUBE_CHANNEL` | your channel id, starts with `UC` | channel report |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | developer.adzuna.com (free) | India and Netherlands listings |

Load it in the terminal you test from:

```bash
source ~/OpenJarvis/.venv/bin/activate
set -a; . ~/.jarvis-sidekick/.env; set +a
```

## 1. One command for everything reachable

```bash
sidekick doctor
```

Every line should read `ok`, or `skip` for something you chose not to configure. It speaks three words through ElevenLabs and transcribes them back, and your bot sends you one Telegram message. Fix each `FAIL` before going on; the message says what to do.

## 2. Jobs

```bash
sidekick jobs search "engineering manager" --prepare 3
sidekick jobs status
```

See: your resume skills on the first line, a ranked list with matched skills and gaps, three packets under `~/.jarvis-sidekick/applications/`. Read one `cover_letter.md`: it should claim nothing your resume does not say.

For Japan, name an employer's Greenhouse or Lever board: `sidekick jobs search "engineering manager" --company <board> --location "Japan, Tokyo"`.

## 3. Applying, without sending

Pick an id from step 2 whose address is on `greenhouse.io` or `lever.co`.

```bash
sidekick jobs apply <id> --show-browser
```

See: a browser opens, fills your details and uploads the resume, then closes. The result is `ready` (everything answered) or `needs_human` with the list of unanswered questions. Nothing is sent. Open `form.png` in the packet folder and check each field. Other sites report `skipped`.

Do not use `--submit` until a dry run looks right for that employer.

## 4. Markets

```bash
sidekick brief
sidekick screen --market india
sidekick plan --monthly 25000 --years 10
```

See: index levels that match what a finance site shows for the last close, headlines with working links, a ranked screen. Symbols that could not be fetched are listed under "Unavailable this run"; a few are normal, all of them means the price source changed.

## 5. Voice on the Mac

```bash
sidekick voice-setup
sidekick voice
```

The first run asks macOS for microphone access for your terminal; allow it. Say "what is on the market briefing today". See: your words transcribed, an answer, and the answer spoken. This step also needs a model in OpenJarvis: run `jarvis doctor` if the answer is an engine error.

## 6. The bot, from the iPad or phone

```bash
sidekick bot --speak
```

In Telegram, to your bot:

| Send | Expect |
|---|---|
| `/help` | the command list |
| `/brief` | the briefing |
| a voice note: "give me the market briefing" | "Heard: ...", then the briefing, then audio |
| `/jobs engineering manager in Netherlands, remote` | ranked jobs with ids |
| `/apply <id>` | "Form filled, nothing sent" and a 6-digit code |
| a voice note: "confirm" followed by the code | no application sent |
| `/channel` | your recent videos and views |

Then the two checks that matter most:

1. From another Telegram account (a family member's phone), message the bot. It must not reply at all, and the terminal logs "dropped an update from an unauthorised chat".
2. Stop the bot, send `/brief` while it is stopped, start it again. The old `/brief` must not run.

## 7. Keep it running

```bash
deploy/macos/install.sh
tail -f ~/.jarvis-sidekick/bot.log
```

Log out and back in; the "online" message should arrive by itself.

## 8. Your content rules

Create `~/.jarvis-sidekick/content.json` (see the README), then:

```bash
echo "This makes your team compliant." > /tmp/draft.txt
sidekick content check /tmp/draft.txt --kind linkedin
```

See: two errors (the promise, the length) and exit code 1.

## When something fails

Copy the command and its full output. Keys never appear in error messages by design, so the output is safe to share; still, look it over before pasting it anywhere.

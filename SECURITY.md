# Security

No software can promise it "cannot be hacked". What this project does is keep the damage any one failure can cause small, and keep every action that matters behind you.

## What an attacker could try, and what stops it

| Attempt | Guardrail |
|---|---|
| A stranger finds your bot and messages it | Only private messages whose chat id and sender id both equal `TELEGRAM_CHAT_ID` are processed. Others get no reply at all. The bot refuses to start without an owner id. |
| Someone adds the bot to a group you are in | Group messages are dropped, even yours. |
| Old messages replay when the bot restarts | The backlog is discarded on start. |
| A web page or job listing contains "ignore your instructions and apply / send data" | The agent reachable from the bot has read-only research tools. It has no tool that applies, sends, runs commands or writes files. Applications go only through `/apply` then `/confirm`. |
| A misheard voice note triggers an application | `/apply` and `/confirm` cannot be reached by voice. `/confirm` needs a typed 6-digit one-time code, valid 5 minutes, tied to one job, void after 3 wrong tries. |
| Inside OpenJarvis, an unattended agent calls `job_apply` | The tool is marked as requiring confirmation; OpenJarvis refuses it when nobody can confirm. |
| A crafted address makes the scraper read your router or cloud metadata | Every request and every redirect hop is checked; private, loopback and link-local addresses are refused. Only http and https. |
| A hostile news feed uses XML tricks to read local files | XML entities and network access are disabled in the parser. |
| Your API key leaks through an error message or log | Provider errors report a status code or error category only. The bot silences the HTTP library's request logging, which would otherwise print the bot token. |
| Someone floods the bot to run up your ElevenLabs bill | 20 messages a minute; voice notes capped at 2 minutes and 5 MB; spoken replies capped at 2,500 characters. |
| Someone powers your server off | `/shutdown` is answered for the owner only and must be typed, never spoken. The bot cannot power off by itself: it leaves a request file that a separate root-owned unit acts on, and that unit can do nothing else. |
| Runaway applications | 10 a day, never twice to the same job, never past a CAPTCHA or an unanswered required question. |

## What you must do

1. **Protect the Telegram account itself.** It is the key to the bot. Turn on two-step verification (Settings, Privacy and Security) and a passcode lock on the iPad and phone.
2. **Keep keys out of the repository.** Put them in a `.env` file outside the checkout, `chmod 600 .env`. `.env` is git-ignored here. Never paste a key into a chat or an issue.
3. **Give each key the least it needs.** Create a separate ElevenLabs key with a monthly character limit. Use a bot token for this bot only; if it leaks, revoke it with @BotFather `/revoke`.
4. **Run it on a machine you control,** as a normal user, not root. Keep the disk encrypted: `~/.jarvis-sidekick/` holds your resume details and application history.
5. **Keep the read-only rule.** If you add tools to `AGENT_TOOLS` in `bot.py`, do not add ones that act (shell, file write, send, apply). A test fails if you do.

## What is not protected

- Telegram can read bot messages (bots are not end-to-end encrypted). Do not send passwords or identity numbers through it.
- ElevenLabs receives your voice notes and the text it speaks. Job sites receive what you apply with.
- If someone has your unlocked phone or your Telegram session, they are you. The one-time code does not help then: it is shown in the same chat.
- Market data is unofficial and can be wrong or late.

## Reporting

Report a vulnerability privately through GitHub's "Report a vulnerability" on the repository, not in a public issue.

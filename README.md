# JarvisAssistant

The `jarvis-sidekick` package: an extension for [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) that turns it into a voice-driven personal operator:

| You say or run | What happens |
|---|---|
| `sidekick voice` | Talk to Jarvis. ElevenLabs Scribe hears you, ElevenLabs speaks the answer, and the agent can use every tool below. |
| "Find Python jobs in Bengaluru that fit my resume" | Searches open job APIs, scores each listing against your resume, shows matched skills and gaps. |
| "Apply to the top one" | Fills the employer's Greenhouse or Lever form in a real browser and stops for your approval before sending. |
| "Give me today's market briefing" | Indian and world indices, USD/INR, gold, oil, movers, a momentum screen and headlines. Can be sent to Telegram or spoken. |
| "Plan Rs 25,000 a month for 10 years" | Allocation across index funds, debt and gold, with projected value under stated assumptions. |
| "What side hustle fits me?" | Ranks ideas against the skills in your resume and GitHub profile, with first steps. |
| "Read this page / this site" | Clean text from one page, or a bounded same-site crawl that honours robots.txt. |

| `sidekick content check post.txt --kind linkedin` | Checks a draft against your publishing rules before you post: private blocklist, outcome promises, length, required lines. |
| "How is my channel doing?" | Recent uploads and views for your YouTube channel. |
| A voice note or `/brief` from your phone or iPad | A private Telegram bot runs it and replies. Only your account is answered. |

OpenJarvis already ships ElevenLabs text-to-speech. This package adds the missing ElevenLabs speech-to-text backend, nine agent tools, and a `sidekick` command. It loads into OpenJarvis through its registries: no fork, no patches.

## Install

```bash
# 1. OpenJarvis (see its README for the one-line installer and model setup)
git clone https://github.com/open-jarvis/OpenJarvis && cd OpenJarvis && uv sync --extra speech

# 2. This package, into the same environment
git clone https://github.com/ashishmohanty-99/JarvisAssistant
uv pip install -e "./JarvisAssistant[resume,apply,youtube]"
playwright install chromium        # only needed for `jobs apply`

# 3. Keys
mkdir -p ~/.jarvis-sidekick && cp JarvisAssistant/.env.example ~/.jarvis-sidekick/.env
chmod 600 ~/.jarvis-sidekick/.env  # fill it in, then: set -a; . ~/.jarvis-sidekick/.env; set +a
sidekick voice-setup               # points OpenJarvis speech at ElevenLabs, both directions
```

Most commands also work without OpenJarvis (`pip install -e ".[resume,apply]"`): jobs, brief, screen, plan, hustle, scrape, transcribe. Voice chat and `sidekick jarvis ...` need it.

## Where to put a sum of money

```bash
/invest                 # in the bot: asks the amount, one time or monthly, and your risk comfort, with buttons
/invest 25000 once moderate
```

Or say it: "Jarvis, where should I invest 25 thousand?" No language model is involved. The split comes from fixed percentages per risk level (index ETFs, gold, a debt fund, and a minority in stock ideas), the stock ideas from the top of the momentum screen (steady uptrends only, nothing overbought), and the quantities from the latest prices, in whole units. It tells you what one unit costs, what is left over, and what it could not fetch. It is a starting point for your own research: it does not know your debts, taxes, holdings or goals, and it cannot promise a return.

## Conversation

With a Claude API key (`ANTHROPIC_API_KEY`) the bot stops being a list of commands. Say "Jarvis, how did the market do and what looks worth researching?" and it fetches the data it needs, answers in a few spoken-length sentences, asks a follow-up when the next step depends on you, and remembers the thread. It also keeps short notes on what you tell it about yourself (horizon, monthly amount, risk comfort); `/memory` shows them and `/forget` wipes them along with the conversation.

- Model: `claude-opus-5-5` by default; set `SIDEKICK_MODEL` to change it. Each message is a paid API call; a limit of 150 conversations a day is built in.
- Every tool the model has reads: market data, quotes, the screen, the plan calculator, job search, your application list, your channel, a web page, web search. It cannot apply for a job, power anything off or run a command; those stay typed slash commands.
- If a safety classifier declines a request, the API's server-side fallback lets another Claude model answer in the same call.
- Your messages, your notes and the tool results go to Anthropic's API to produce the answer. The conversation and notes are also stored on your machine in `~/.jarvis-sidekick/`, readable only by the bot's account.
- `SIDEKICK_MORNING=07:30` sends a morning greeting and briefing every day at that time.

Without a key everything else still works, as commands.

## Check your setup

```bash
sidekick doctor
```

It tries every integration once (prices, news, each job source, your channel, ElevenLabs, Telegram, the browser, the microphone) and prints `ok`, `FAIL` or `skip` with the reason for each. Missing keys are skipped, not failed. Run it after every change to your `.env`.

## Use

```bash
export SIDEKICK_RESUME=~/resume.pdf

sidekick voice                                   # hands-free: mic in, voice out, tools on
sidekick jarvis ask "Brief me on the markets" --agent orchestrator --tools market_brief
sidekick transcribe memo.m4a                     # ElevenLabs Scribe
sidekick say "Good morning" -o hello.mp3         # ElevenLabs voice

sidekick jobs search "python developer" --location India --prepare 3
sidekick jobs search "ai engineer" --company stripe --company notion   # Greenhouse/Lever boards
sidekick jobs apply <id>                         # fills the form, sends nothing, saves a screenshot
sidekick jobs apply <id> --submit                # asks you, then sends
sidekick jobs status

sidekick brief --send telegram --speak brief.mp3
sidekick screen --market india --top 5
sidekick plan --monthly 25000 --years 10 --risk moderate

sidekick hustle --github your-user
sidekick profile youtube UCxxxxxxxxxxxxxxxxxxxxxx
sidekick scrape https://example.com/docs --crawl 10
```

`sidekick jarvis <anything>` is `jarvis <anything>` with the sidekick loaded, so presets, the scheduler and channels all work. A daily briefing is one cron line: `0 8 * * 1-5 sidekick brief --send telegram`.

## From your phone or iPad

OpenJarvis and this package run on a computer (Mac, Linux, Windows or a small cloud server). A phone or tablet cannot run them, but it can drive them through a private Telegram bot:

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token into `TELEGRAM_BOT_TOKEN`.
2. Message **@userinfobot** and copy your numeric id into `TELEGRAM_CHAT_ID`.
3. Open your new bot and press Start. On the computer: `sidekick bot --speak`.

Now type `/brief`, `/jobs engineering manager in India, Singapore, remote`, `/status`, or hold the microphone button and say "give me the market briefing". The bot replies with what it heard, the answer, and (with `--speak`) a spoken reply. Notifications arrive like any Telegram message, on every device you are signed in to.

Sending an application from the bot takes two typed steps: `/apply <id>` fills the form and returns a one-time code, `/confirm <code>` sends. Neither can be triggered by voice. See [SECURITY.md](SECURITY.md) for every guardrail and for what you need to do yourself.

To keep it running on a Mac or on a cloud server, see [deploy/README.md](deploy/README.md). For a scheduled push: `0 8 * * 1-5 sidekick brief --send telegram`.

## Looking in several countries

```bash
export SIDEKICK_LOCATIONS="India, Netherlands, Japan, remote"   # places you would accept
export SIDEKICK_COUNTRIES="in,nl"                               # Adzuna country boards to search
sidekick jobs search "director of engineering" --prepare 5
sidekick jobs search "engineering manager" --company <board> --location "Japan, Tokyo"
```

Adzuna has boards for India, the Netherlands and 17 other countries, but not Japan. For Japan, name the Greenhouse or Lever boards of employers you are interested in with `--company`; the board name is the last part of the employer's careers address on those sites.

## Content rules

If you publish (videos, posts, a newsletter), keep your non-negotiables in `~/.jarvis-sidekick/content.json`. It stays on your machine:

```json
{
  "blocklist": ["Your Employer", "Internal Project Name"],
  "watchlist": ["a spelling of your name you no longer use"],
  "required_in_youtube_description": ["https://github.com/you/your-repo"]
}
```

`sidekick content check draft.txt --kind youtube-description` exits 1 on an error, so it can gate a script. Errors: a blocklisted name (reported by line, never repeated), a promise of an outcome ("makes you compliant", "guaranteed"), a title over 60 characters, a LinkedIn post outside 80 to 150 words, more than three hashtags, a description without "Narration: AI-generated voice." or one of your required lines. Warnings: an example set in retail, a number that needs a source, wording that reads as offensive security.

It is a word-and-pattern check. It catches slips; it does not understand meaning, so it is a second pair of eyes, not a replacement for reading the draft.

State lives in `~/.jarvis-sidekick/` (override with `SIDEKICK_HOME`): application packets, the tracker database, saved briefings, and `answers.json`.

## How applying works, and its limits

1. `jobs search --prepare N` writes a packet per job: the listing and a cover letter built only from facts in your resume. Edit the letter freely; it is never overwritten.
2. `jobs apply <id>` opens the form, fills name, email, phone, links, cover letter and uploads the resume, then reports. A screenshot is saved next to the packet.
3. Sending needs `--submit` and a yes at the prompt. Inside OpenJarvis the `job_apply` tool is marked as requiring confirmation, so an unattended agent cannot send.

It will not send when a required question is unanswered (work authorisation, notice period, salary): it lists them and stops. Put answers you are happy to reuse in `~/.jarvis-sidekick/answers.json` as `{"label text": "answer"}`. It never solves a CAPTCHA, never applies twice to one job, and stops at 10 applications a day.

Supported forms: Greenhouse and Lever. LinkedIn, Indeed and Naukri are not automated: their terms forbid it and accounts get restricted. Use their alerts to find roles, then apply on the employer's own page.

## Markets: what it is and is not

Prices come from Yahoo's chart endpoint: free, keyless, delayed, and unofficial, with no second source behind it. The screen ranks a watchlist by 3 and 6 month return per unit of volatility, with a penalty below the 200-day average: a well-known momentum factor that says what has been rising steadily, not what will. It ignores valuation and earnings. The plan is rule-based general education for an India-based investor. None of this is personalised investment advice, and nothing here can promise a profit. Edit the watchlists in `markets/data.py`.

## "Scrape the entire internet"

Nobody can. The working pattern is: search for what you need (OpenJarvis `web_search`), then read the pages that matter (`web_scrape`). The crawler stays on one site, caps at 50 pages, waits between requests, honours robots.txt, and refuses private network addresses on every redirect hop.

## Verified, and not yet

Verified by the test suite (`pytest`, 152 tests): the ElevenLabs request format and response parsing against the documented API shape; registration into a real OpenJarvis install and resolution through its speech discovery and tool executor; the form filler against a real Chromium and a local stand-in form (dry run sends nothing, confirm sends, unanswered questions and CAPTCHAs block); scoring, tracker, indicators, plan arithmetic, feed parsing, error paths that must not leak keys; the bot's owner check, backlog skip, rate limit, voice-note limits and one-time confirmation codes.

Run live on an Ubuntu 24.04 cloud server through `/doctor` (19 November 2026): the Telegram bot under its restricted system service, resume upload through the bot, ElevenLabs speech out and back in, Yahoo prices, the news feeds, the Remotive, Arbeitnow and RemoteOK job sources, the YouTube channel feed, and Chromium start-up.

Not yet run live: Adzuna, the Greenhouse and Lever job boards and their real application forms, the GitHub profile reader, the page reader, voice chat through OpenJarvis with a model, the macOS service script, and the Docker files. Expect small fixes on their first run.

## Develop

```bash
pip install -e ".[dev,resume,apply]" && playwright install chromium
pytest && ruff check .
```

# Plan

Goal: one assistant you talk to that reads the web, hunts jobs against your resume, briefs you on Indian and world markets, and helps you build side income, packaged so anyone can install it.

## Decisions

| Decision | Why |
|---|---|
| Extension package, not a fork of OpenJarvis | OpenJarvis moves daily. Registering into its speech and tool registries keeps upgrades free. |
| ElevenLabs for both directions | OpenJarvis had ElevenLabs speech out only. One provider, one key, good Hindi and Indian-English recognition. |
| Telegram as the phone and tablet front end | Push notifications, voice notes and typed commands on every device, with nothing to install and no port opened on your computer. |
| Applications need a human yes | A wrong or duplicate application costs reputation. Fill automatically, send deliberately. |
| Open job APIs and employer forms only | LinkedIn, Indeed and Naukri forbid automation and restrict accounts that try. |
| Screens and plans, not tips | Software cannot promise profit. It can show data, rank it by a stated rule, and state its assumptions. |

## Phases

### Phase 1: foundation (done, in this commit)
- [x] ElevenLabs Scribe speech-to-text backend for OpenJarvis; `sidekick voice`, `voice-setup`, `transcribe`, `say`
- [x] `web_scrape`: page reader and bounded polite crawler with SSRF and robots.txt checks
- [x] Resume parser (PDF, DOCX, text), six job sources, explainable scoring, cover letters, tracker
- [x] Greenhouse/Lever form filler with dry run, confirmation, daily cap, duplicate guard
- [x] Market data, indicators, momentum screen, news feeds, daily briefing, Telegram delivery, spoken summary
- [x] Rule-based SIP plan
- [x] GitHub and YouTube profile readers; side-hustle ranking
- [x] Private Telegram bot: commands and voice notes from a phone or tablet, owner-only, one-time codes to send
- [x] Job search across several countries and acceptable places
- [x] Content rule checker for drafts; YouTube channel report (`sidekick channel`, `/channel`)
- [x] Deployment: macOS login service; one-command Ubuntu server install with a restricted system service; Docker files
- [x] Resume by sending the file to the bot; `/doctor` from the phone
- [x] `/invest`: a rule-based split of a sum into funds and stock ideas with quantities, asked with buttons
- [x] The Jarvis screen: orb, push-to-talk voice, spoken answers, buttons and a live market board, behind a private network and a Telegram sign-in code
- [x] The briefing as a designed PDF edition (`/paper`), optionally every morning
- [x] One-tap YouTube publishing of a video sent to the bot (stage 1)
- [x] Conversation through Claude with read-only tools, memory, tap buttons, typing indicator, optional morning message
- [x] Nine OpenJarvis tools; 205 tests; lint clean; `SECURITY.md` threat table

### Phase 2: first live run (needs your keys and machine)
- [ ] Run each command against the live services and fix any parser drift (ElevenLabs, job APIs, Yahoo, feeds, YouTube)
- [ ] Try the form filler on three real Greenhouse and three real Lever postings in dry-run mode; extend selectors
- [ ] Add Ashby and Workday form adapters if your target employers use them
- [ ] Schedule the morning briefing (cron or `jarvis scheduler`)
- [ ] Create the Telegram bot and install the Mac service (`deploy/macos/install.sh`)
- [ ] Build the container once on a real Docker host; move the bot to a cloud server when the Mac should stop being the host
- [ ] Japan: collect Greenhouse/Lever board names of target employers

### Phase 3: make it yours
- [ ] YouTube stage 2: pick up the daily video and its text automatically, so nothing is forwarded by hand
- [ ] Side business: add channel numbers and open publishing tasks to the morning briefing; run `content check` over each day's drafts before they reach you
- [ ] LLM-written cover letters and screening answers, grounded in the resume, reviewed before use
- [ ] Portfolio tracking: holdings file, allocation drift alerts, fundamentals (P/E, earnings growth) in the screen
- [ ] NSE/BSE corporate announcements and results calendar in the briefing

### Phase 4: share it
- [x] Own repository
- [ ] Publish to PyPI
- [ ] Contribute the ElevenLabs speech-to-text backend upstream to OpenJarvis
- [ ] Docker image and a five-minute setup video

# LinkedIn Agent: post finder and job finder

Two tools in one project, both driven by your own saved LinkedIn session and an AI model of your choice
(default: NVIDIA `nvidia/nemotron-3.5-bolt-e4b`, free; or any `claude-*` model).

| Tool | What it does | Command |
|---|---|---|
| **Post agent** | Finds LinkedIn posts that match rules you choose (companies, your connections, keywords) and asks before every Like. | `run.bat` |
| **Job finder** | Finds recent jobs that match your skills and locations, skips ones you already applied to, and writes a ranked report. It never applies to anything. | `run.bat jobs` |

> **Warning:** LinkedIn's User Agreement prohibits bots and automated activity. Use at your own risk.
> The tools keep risk low (a human approves every Like, pauses are randomised, there is a daily Like cap,
> job search only reads pages) but account restrictions are still possible.
> Never share `.profile/` (it holds your login session) or `.env` (it holds your keys).

## Setup (once)
```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env      # then add NVIDIA_API_KEY=nvapi-...  (or ANTHROPIC_API_KEY=sk-ant-...)
python -m agent login       # sign in once in the window that opens (2FA is fine)
```
`run.bat` does the install steps for you on its first run.

**Linux / macOS:** use `./run.sh` with the same modes as `run.bat` (`./run.sh setup`, `./run.sh jobs`, `./run.sh watch`
...). Its first run creates `.venv`, installs the packages and Chromium. Do `chmod +x run.sh` once if needed.
- Needs Python 3.11+ (Debian/Ubuntu: `sudo apt install python3-venv`).
- If Chromium will not start: `sudo .venv/bin/python -m playwright install-deps chromium`.
- On a server with no screen, company pages and `login` still need a visible window:
  run `xvfb-run -a ./run.sh watch`, or log in once on a desktop machine and copy `.profile/`.
- The Python code itself is platform independent; only the launcher differs.

## Commands
Use `run.bat <mode>` (double-click `run.bat` for the normal mode) or `python -m agent <command>`.

| `run.bat` | Python command | What it does |
|---|---|---|
| `run.bat setup` | `python -m agent setup` | Choose what the post agent looks for (see below). Asked automatically on the first run. |
| `run.bat watch` | `python -m agent run --once --notify-only --show --open` | Safe test: visible browser, nothing is liked, scan report opens. **Start here.** |
| `run.bat dry` | `python -m agent run --once --notify-only` | One scan, prints matches, never likes. |
| `run.bat once` | `python -m agent run --once` | One scan, asks before each Like. |
| `run.bat` | `python -m agent run` | Scans every `interval_minutes` and asks before each Like. Ctrl+C stops. |
| `run.bat jobs` | `python -m agent jobs --open` | Job search; writes `reports/jobs.html`. |
| `run.bat applied <link or id> ...` | `python -m agent applied <link or id> ...` | Record jobs you applied to outside LinkedIn so they are never shown again. |
| `run.bat login` | `python -m agent login` | Sign in again if the session expired. |

**Run only one command at a time.** Two browsers on one profile interfere, so a second command stops with
"Another agent command (process N) is using the browser profile". Wait for the first to finish.

## Post agent
`run.bat` asks what you want on the first run (redo with `run.bat setup`): **jobs** (with your skills and
places), **company posts**, **feed keywords**, **connections' posts**, and how many days back. Answers are
saved to `choices.yaml`; edit that file or run setup again. Choose jobs only and `run.bat` writes
`reports/jobs.html` and exits; choose any post rule and it also scans every `interval_minutes` (jobs are
refreshed every `jobs_every_hours`, default 6). A post is kept if it matches **any** post rule you turn on (OR):

| Rule | Meaning |
|---|---|
| Companies | Any post from the company pages you name, any topic. Type a name (LinkedIn is searched and you pick the match) or paste a company URL. Needs a visible browser window, which opens automatically. |
| Keywords | Posts matching the keywords in `config.yaml` (plus extras you add), confirmed by the AI as really about the topic. Read from your feed and any `hashtags` / `searches` set in `config.yaml`. |
| Connections | Every post by your 1st-degree connections, no keyword needed. LinkedIn's feed shows almost none of them, so each scan visits the activity page of `connections_per_scan` connections (default 15), rotating through everyone. Most connections post rarely, so expect few matches. |

After each scan `reports/latest.html` shows every post looked at: where it was found, its age, which keywords
hit, the AI verdict and the decision. Posts older than your "days back" are ignored.

`config.yaml` holds the defaults: `keywords`, `hashtags`, `searches`, `interval_minutes` (30),
`max_likes_per_day` (20), `scroll_rounds`, `connections_per_scan`, `connection_scroll_rounds`, `model`.

## Job finder
Turn jobs on in `run.bat setup` (skills and places you type there override `jobs.yaml`), or edit `jobs.yaml`
and run `run.bat jobs`. It opens `reports/jobs.html`.

| `jobs.yaml` key | Meaning |
|---|---|
| `skills` | Your skills. A job is scored on how many of them its text really names (singular also matches plural). |
| `search_terms` | Words sent to LinkedIn, joined with OR. Leave empty to derive them from `skills`. |
| `preferred_locations`, `include_remote` | 1st preference: these places plus remote jobs. |
| `fallback_location` | 2nd preference: anywhere in this country. |
| `max_age_days` | Ignore jobs posted longer ago than this (15). |
| `min_years`, `exclude_title_words` | "Experienced only": roles asking fewer years, or with titles like intern/junior, are dropped. |
| `pages_per_search`, `max_jobs_to_score` | How many results to read; how many new job pages to open and AI-score per run (60). |
| `min_score` | Score needed to be recommended (60). A job is capped at 40 + 15 per skill really named in the posting, so 60 means at least 2 of your skills appear in it. |

How it works, cheapest steps first, to save time and AI use:
1. Searches LinkedIn for each place. Places are looked up as LinkedIn place ids at run time, because LinkedIn
   ignores a typed location. Remote jobs are recognised from each job's "(Remote)" label.
2. Drops jobs that are older than the limit, entry-level titles, duplicate postings (one job listed per city),
   and jobs you already applied to (your LinkedIn Job tracker: Applied, Interview, Archived, plus any you
   recorded with `run.bat applied`).
3. Opens the remaining jobs best-location-first, up to `max_jobs_to_score` new ones, and reads each description.
4. Calls the AI only if the posting names at least one of your skills. Every score is cached in `seen.db`, so
   a rerun re-reads and re-scores nothing it has already seen.

The report ranks recommended jobs by location preference, then score, and shows skills matched, required years
and the AI's reason. A dropdown shows why other jobs were left out. Jobs you have already viewed show
"<= 15 d" instead of an age, because LinkedIn hides it for them; they still passed LinkedIn's date filter.
A "new" badge marks jobs not shown in an earlier report.

## Auto-login (optional)
Your session is saved in `.profile/`, so you normally sign in once. To let the agent sign in again when the
session expires, add `LINKEDIN_EMAIL` and `LINKEDIN_PASSWORD` to `.env`. Leave them blank to sign in by hand.
If LinkedIn asks for 2FA or a CAPTCHA, auto-login stops and `run.bat login` opens a window to finish it.
The password is stored in plain text in `.env`: use a unique password and keep the file private.

## Files
| File | Purpose | In git? |
|---|---|---|
| `config.yaml` | Defaults for the post agent | yes |
| `jobs.yaml` | Your job preferences | yes |
| `.env.example` | Template for `.env` (placeholders only, never real keys) | yes |
| `.env` | Your API key and optional LinkedIn login | **no** |
| `choices.yaml` | Your answers from `setup` | no |
| `.profile/` | Saved LinkedIn session | **no** |
| `seen.db` | Decisions, cached job scores, applied jobs | no |
| `reports/` | `latest.html` (posts) and `jobs.html` (jobs) | no |
| `.agent.lock`, `.setup_done` | Run lock; first-run marker | no |

Code is in `agent/`: `scraper.py` (feed, company and connection pages), `jobs.py` (job search),
`matcher.py` (keywords and AI), `choices.py` (setup), `browser.py` (session and lock),
`report.py` / `jobs_report.py` (HTML), `store.py` (database), `pacing.py` (human-like pauses).

## Troubleshooting
- **"Not logged in"**: run `run.bat login`.
- **"Another agent command ... is using the browser profile"**: another run is still going. Wait for it.
- **Company pages need a visible window**: LinkedIn shows a bot-check to the hidden browser, so a window opens.
  Do not close it until the report appears. A bot-check page is skipped, never bypassed.
- **No posts or jobs found**: LinkedIn changes its page layout often. Selectors are at the top of
  `agent/scraper.py` and `agent/jobs.py`.
- **"Could not confirm like"**: LinkedIn's Like button label changed; update the label checks in `agent/scraper.py`.

## Tests
```
pytest tests/
```
About 100 tests, using a fake LinkedIn page with made-up names (no real companies or people). The suite
takes roughly 10 minutes because the older tests include the scraper's real pauses; the job tests set
`AGENT_FAST=1` to skip them.

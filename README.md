# LinkedIn LTE/4G Post Agent (semi-automatic)

Scans your LinkedIn feed and `#LTE` / `#4G` hashtag pages, picks posts really about LTE/4G
(keyword prefilter + LLM check; default `nvidia/nemotron-3.5-bolt-e4b`, or a `claude-*` model), and asks you to approve each Like.

> **Warning:** LinkedIn's User Agreement prohibits bots and automated activity. Use at your
> own risk. This tool keeps risk low: a human approves every like, delays are randomised and
> there is a daily cap, but account restrictions are still possible. Never share `.profile/`
> (it holds your login session).

## Setup
```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env   # add NVIDIA_API_KEY (free) or ANTHROPIC_API_KEY
```

## Use
```
python -m agent login                      # log in manually once (2FA ok)
python -m agent run --once --notify-only   # dry run: print matches, no likes
python -m agent run --once                 # one scan, prompt before each like
python -m agent run                        # loop every interval_minutes
```
Edit `config.yaml` for keywords, hashtags, interval and the daily cap.
If LinkedIn changes its page layout, update the selectors at the top of `agent/scraper.py`.

## Test
```
pytest tests/
```

## One-click
`run.bat` (normal), `run.bat dry` (read-only), `run.bat once`, `run.bat watch` (visible browser + report), `run.bat setup` (choose rules), `run.bat login`.

## Auto-login (optional)
Your session is saved in `.profile/`, so you normally log in once. To let the agent sign in again
when the session expires, add `LINKEDIN_EMAIL` and `LINKEDIN_PASSWORD` to `.env` (gitignored; never
put them in `config.yaml`, which is not). If LinkedIn asks for 2FA or a CAPTCHA, auto-login stops and
`python -m agent login` opens a window for you to finish it. The password is stored in plain text
in `.env`: use a unique password and keep the file private.

## What to look for (choose once, no code changes)
On the first run the agent asks what you want; `run.bat setup` asks again. Your answers are saved in
`choices.yaml`. A post is kept if it matches **any** rule you turn on (OR):

| Rule | Meaning |
|---|---|
| Companies | Any post from the company pages you name (any topic). Type a name (it is looked up and you pick the match) or paste a LinkedIn URL. Opens a visible browser window. |
| Keywords | Posts matching the keywords in `config.yaml` (plus extra ones you add), confirmed by the AI. |
| Connections | Every post by your 1st-degree connections, no keyword needed. LinkedIn's feed shows almost none of them, so each scan visits the activity page of `connections_per_scan` connections (default 15), rotating through everyone. Most connections post rarely, so expect few matches. |

You also choose how many days back to look (default 10). Run `run.bat watch` and open
`reports/latest.html` to see, per post, which rule matched and why.

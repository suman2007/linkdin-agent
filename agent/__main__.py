"""CLI: python -m agent setup | login | run [--once] [--notify-only] [--show] [--open] | jobs | applied <ids>"""
import argparse
import random
import re
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from . import actions, browser, choices, jobs, jobs_report, matcher, report, scraper
from .store import Store

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def cmd_login() -> int:
    load_dotenv(ROOT / ".env")
    creds = browser.credentials()
    with sync_playwright() as p:
        ctx = browser.launch(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ok = False
        if creds:
            print("Trying saved credentials from .env ...")
            ok = browser.auto_login(page, *creds)
        if not ok:
            if page.url == "about:blank" or "feed" in page.url:
                page.goto("https://www.linkedin.com/login")
            input("Complete the login in the browser window (2FA/CAPTCHA if asked), then press Enter here...")
            ok = browser.is_logged_in(page)
        ctx.close()
    print("Logged in; session saved." if ok else "Login not detected. Try again.")
    return 0 if ok else 1


def cmd_setup() -> int:
    """Ask what to look for (companies / keywords / connections / age) and save the answers."""
    load_dotenv(ROOT / ".env")
    resolver = None
    with sync_playwright() as p:
        ctx = browser.launch(p, headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if browser.is_logged_in(page):
                resolver = lambda name: scraper.find_companies(page, name)  # noqa: E731
            else:
                print("Not logged in yet, so company names can't be looked up. Paste company URLs instead.")
            chosen = choices.run_setup(resolver=resolver)
        finally:
            ctx.close()
    choices.save(chosen)
    cleared = Store().clear_stale()      # rules changed: posts judged under the old rules get re-judged
    print(f"\nSaved to choices.yaml. Looking for: {choices.summary(chosen)} (last {chosen['max_post_age_days']} days).")
    if cleared:
        print(f"({cleared} earlier 'irrelevant/old' decisions cleared so they are re-checked.)")
    return 0


def _row(post, decision, keywords=None, verdict=""):
    return {"source": post.source_url, "author": post.author, "age_days": post.age_days,
            "text": post.text[:240], "keywords": keywords or [], "verdict": verdict,
            "decision": decision, "link": post.url}


def run_cycle(page, cfg, store, pattern, notify_only, rows=None) -> None:
    """One scan. A post is kept if ANY enabled rule matches (company / connection / keyword).
    If `rows` is a list, a trace row per post is appended (used for the HTML report)."""
    rows = rows if rows is not None else []
    due: list[str] = []
    per_scan = cfg.get("connections_per_scan") or 0
    if cfg.get("use_connections", False) and per_scan:
        # Your feed shows almost none of your connections' posts, so visit their own post pages, a few
        # per scan, rotating through everyone (the connection checked longest ago goes first).
        slugs = scraper.list_connections(page)
        due = store.due_connections(slugs, per_scan)
        print(f"Checking {len(due)} of {len(slugs)} connections this scan.")
    posts = scraper.scan(page, dict(cfg, connection_pages=due))
    for slug in due:
        store.mark_connection_checked(slug)
    if browser.is_checkpoint(page):
        raise SystemExit("LinkedIn checkpoint/login page hit. Resolve it manually, then rerun.")
    print(f"Scanned {len(posts)} posts.")
    max_age = cfg.get("max_post_age_days")
    use_kw = cfg.get("use_keywords", True)
    use_conn = cfg.get("use_connections", False)
    for post in posts:
        prior = store.decision(post.id)
        if prior is not None:
            rows.append(_row(post, f"seen before ({prior})"))
            continue
        if max_age is not None and post.age_days is not None and post.age_days > max_age:
            store.record(post.id, "old")   # too old; skip before spending an LLM call
            rows.append(_row(post, "too old"))
            continue
        if post.liked or not post.text:
            store.record(post.id, "irrelevant")
            rows.append(_row(post, "already liked" if post.liked else "no text"))
            continue

        matched, why = [], []
        if post.origin.startswith("company:"):                    # rule 1: chosen company
            matched.append("company")
            why.append("From company page " + post.origin.split(":", 1)[1])
        if use_conn and post.degree == 1:                          # rule 2: author is your connection
            matched.append("connection")
            who = post.origin.split(":", 1)[1] if post.origin.startswith("connection:") else ""
            why.append("Posted by your connection" + (f" ({who})" if who else ""))
        kws = matcher.matched_keywords(post.text, pattern) if use_kw else []
        ai_rejected, ai_why = False, ""
        if use_kw and kws and not matched:                         # rule 3: keywords + AI (only if needed)
            ok, reason = matcher.llm_match(post.text, cfg["model"])
            if ok:
                matched.append("keyword")
                why.append(reason)
            else:
                ai_rejected, ai_why = True, reason

        if not matched:
            if ai_rejected:
                store.record(post.id, "irrelevant")
                rows.append(_row(post, "AI rejected", kws, ai_why))
            else:   # cheap check, not recorded: changing the rules takes effect on the next scan
                rows.append(_row(post, "no keyword" if use_kw else "no rule matched"))
            continue

        label, reason = " + ".join(matched), "; ".join(why)
        if notify_only:
            # dry run: report only; don't record, so a real run still prompts for these posts
            print(f"[match: {label}] {post.author}: {post.url}  ({reason})")
            rows.append(_row(post, f"match: {label} (dry run)", kws, reason))
            continue
        if store.likes_today() >= cfg["max_likes_per_day"]:
            print("Daily like cap reached; not prompting further today.")
            rows.append(_row(post, "daily cap reached", kws, reason))
            return
        if actions.ask(post, f"[{label}] {reason}"):
            done = scraper.like(page, post)
            print("Liked." if done else "Could not confirm like.")
            store.record(post.id, "liked" if done else "skipped")
            rows.append(_row(post, "liked" if done else "skipped (like not confirmed)", kws, reason))
        else:
            store.record(post.id, "skipped")
            rows.append(_row(post, "skipped (by you)", kws, reason))


def _ensure_logged_in(page) -> bool:
    """True if the saved session works, or the credentials in .env sign in again."""
    if browser.is_logged_in(page):
        return True
    creds = browser.credentials()
    if creds:
        print("Session expired; signing in with saved credentials ...")
    if creds and browser.auto_login(page, *creds):
        return True
    print("Not logged in (2FA/CAPTCHA may be required). Run: python -m agent login")
    return False


def cmd_jobs(show: bool = False, open_report: bool = False) -> int:
    """Find recent jobs matching jobs.yaml, skip the ones you applied to, rank them, write reports/jobs.html."""
    load_dotenv(ROOT / ".env")
    prefs = jobs.load_prefs()
    model = load_config()["model"]
    store = Store()
    print(f"Skills: {', '.join(prefs['skills'])} | last {prefs['max_age_days']} days | "
          f"preferred: {', '.join(prefs['preferred_locations'] + (['remote'] if prefs['include_remote'] else []))}, "
          f"then {prefs['fallback_location']}")
    with sync_playwright() as p:
        ctx = browser.launch(p, headless=not show)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if not _ensure_logged_in(page):
                return 1
            found = jobs.run(page, prefs, store, model)
        finally:
            ctx.close()
    path = jobs_report.write(found, prefs, ROOT / "reports" / "jobs.html", open_report)
    top = [j for j in found if j.decision == "recommended"]
    print(f"\n{len(top)} recommended of {len(found)} jobs. Report: {path}")
    for j in top[:10]:
        print(f"  [{j.score:>3}] tier {j.tier} | {j.title[:55]} | {j.company[:25]} | {j.location[:25]}")
    return 0


def cmd_applied(refs: list[str]) -> int:
    """Record jobs you applied to outside LinkedIn so they never show up again."""
    ids = []
    for ref in refs:
        m = re.search(r"(\d{6,})", ref)
        if m:
            ids.append(m.group(1))
        else:
            print(f"  not a job id or LinkedIn job link: {ref!r}")
    if ids:
        Store().mark_jobs_applied(ids)
        print(f"Recorded {len(ids)} job(s) as applied; they will be excluded from future reports.")
    return 0 if ids else 1


def cmd_run(once: bool, notify_only: bool, show: bool = False, open_report: bool = False) -> int:
    load_dotenv(ROOT / ".env")
    chosen = choices.load()
    if chosen is None:
        if sys.stdin.isatty():
            print("First run: choose what to look for (redo anytime with: run.bat setup).")
            cmd_setup()
            chosen = choices.load() or dict(choices.DEFAULTS)
        else:
            print("No choices saved yet (non-interactive), using the default: keywords only. "
                  "Run: python -m agent setup")
            chosen = dict(choices.DEFAULTS)
    cfg = choices.apply(load_config(), chosen)
    print(f"Looking for: {choices.summary(chosen)} | last {cfg['max_post_age_days']} days")
    pattern = matcher.build_pattern(cfg["keywords"])
    store = Store()
    with sync_playwright() as p:
        if cfg.get("companies") and not show:
            print("Company pages are only served to a visible browser window, so one will open.")
            show = True
        ctx = browser.launch(p, headless=not show)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not _ensure_logged_in(page):
            ctx.close()
            return 1
        try:
            while True:
                rows: list = []
                try:
                    run_cycle(page, cfg, store, pattern, notify_only, rows)
                finally:
                    path = report.write(rows, cfg, notify_only, ROOT / "reports" / "latest.html", open_report)
                    print(f"Report: {path}")
                if once:
                    break
                wait = cfg["interval_minutes"] * 60 * random.uniform(0.8, 1.2)
                print(f"Next scan in {wait / 60:.0f} min (Ctrl+C to stop).")
                time.sleep(wait)
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            ctx.close()
    return 0


def main() -> int:
    # Posts contain emoji; the default Windows console codec (cp1252) can't print them.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login")
    sub.add_parser("setup", help="choose companies / keywords / connections to look for")
    j = sub.add_parser("jobs", help="find recent jobs that match jobs.yaml and write reports/jobs.html")
    j.add_argument("--show", action="store_true", help="show the browser window")
    j.add_argument("--open", action="store_true", help="open the HTML job report when done")
    a = sub.add_parser("applied", help="record jobs you applied to elsewhere (job ids or LinkedIn job links)")
    a.add_argument("refs", nargs="+")
    r = sub.add_parser("run")
    r.add_argument("--once", action="store_true")
    r.add_argument("--notify-only", action="store_true")
    r.add_argument("--show", action="store_true", help="show the browser window so you can watch")
    r.add_argument("--open", action="store_true", help="open the HTML scan report when done")
    args = ap.parse_args()
    if args.cmd == "login":
        return cmd_login()
    if args.cmd == "setup":
        return cmd_setup()
    if args.cmd == "jobs":
        return cmd_jobs(args.show, args.open)
    if args.cmd == "applied":
        return cmd_applied(args.refs)
    return cmd_run(args.once, args.notify_only, args.show, args.open)


if __name__ == "__main__":
    sys.exit(main())

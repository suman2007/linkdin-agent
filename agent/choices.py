"""User choices: WHAT the agent looks for. Asked once on first run (redo with `setup`), saved to choices.yaml.

A post is kept if it matches ANY enabled rule (OR):
  - company     : it comes from a company page you chose (any topic, no keyword needed)
  - connections : its author is your 1st-degree connection (no keyword needed)
  - keywords    : it matches your keywords AND the AI confirms the topic
The code never changes; only these choices do.
"""
import re
from pathlib import Path

import yaml

CHOICES_PATH = Path(__file__).resolve().parent.parent / "choices.yaml"
DEFAULTS = {"jobs": False, "job_skills": [], "job_locations": [],
            "companies": [], "keywords": True, "connections": False,
            "extra_keywords": [], "max_post_age_days": 10}


def load(path: Path = CHOICES_PATH) -> dict | None:
    """Saved choices, or None if the user has never gone through setup."""
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {**DEFAULTS, **data}


def is_outdated(path: Path = CHOICES_PATH) -> bool:
    """True if a saved file predates the jobs question, so the user should be asked again."""
    if not path.exists():
        return False
    return "jobs" not in (yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def save(choices: dict, path: Path = CHOICES_PATH) -> None:
    path.write_text("# Written by `python -m agent setup`. Edit by hand or rerun setup.\n"
                    + yaml.safe_dump(choices, sort_keys=False, allow_unicode=True), encoding="utf-8")


def slug_from_url(text: str) -> str | None:
    m = re.search(r"linkedin\.com/company/([A-Za-z0-9_%.-]+)", text)
    return m.group(1) if m else None


def _split(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _yes(raw: str, default: bool) -> bool:
    raw = raw.strip().lower()
    return default if not raw else raw.startswith("y")


def _pick_company(item, ask, say, resolver):
    slug = slug_from_url(item)
    if slug:
        return {"name": slug, "slug": slug}
    cands = resolver(item) if resolver else []
    if not cands:
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", item):     # offline fallback: looks like a slug
            return {"name": item, "slug": item}
        say(f"  Could not find a company for {item!r}; paste its LinkedIn URL instead. Skipped.")
        return None
    if len(cands) > 1:
        say(f"  Matches for {item!r}:")
        for i, (slug, name) in enumerate(cands, 1):
            say(f"    {i}. {name}  (linkedin.com/company/{slug})")
        pick = ask(f"  Pick 1-{len(cands)}, or 0 to skip [1]: ").strip() or "1"
        if not pick.isdigit() or not 1 <= int(pick) <= len(cands):
            return None
        slug, name = cands[int(pick) - 1]
    else:
        slug, name = cands[0]
    return {"name": name.strip(), "slug": slug}


def run_setup(ask=input, say=print, resolver=None) -> dict:
    """Interactive questions. `resolver(name) -> [(slug, display_name), ...]` looks up company names."""
    say("\nChoose what the agent should do. Enable one or more of: jobs, company posts, feed keywords, connections.\n"
        "A post is kept if it matches ANY post rule you enable.\n")
    for _ in range(3):
        jobs = _yes(ask("0) Find recent jobs for your skills (report in reports/jobs.html)? [Y/n]: "), True)
        job_skills, job_locations = [], []
        if jobs:
            job_skills = _split(ask("   Your skills (comma-separated, blank = use jobs.yaml): "))
            job_locations = _split(ask("   Preferred places, e.g. Kolkata (blank = use jobs.yaml; remote is included): "))
        companies = []
        raw = ask("1) Posts from specific companies (any topic). Names or LinkedIn URLs, comma-separated,\n"
                  "   blank = none: ")
        for item in _split(raw):
            c = _pick_company(item, ask, say, resolver)
            if c and c["slug"] not in [x["slug"] for x in companies]:
                companies.append(c)
                say(f"  + {c['name']}")
        keywords = _yes(ask("2) Posts about your keywords (LTE/4G and node names; the AI confirms the topic)? [Y/n]: "), True)
        extra = _split(ask("   Extra keywords to add (comma-separated, blank = none): ")) if keywords else []
        connections = _yes(ask("3) ALL posts by your 1st-degree connections, with no keyword needed? [y/N]: "), False)
        if jobs or companies or keywords or connections:
            break
        say("\nNothing is enabled, so there would be nothing to find. Please enable at least one option.\n")
    else:
        say("Using the default: keywords only.")
        keywords = True
    age = 10
    if companies or keywords or connections:
        raw = ask("4) Only posts from the last N days [10]: ").strip()
        age = int(raw) if raw.isdigit() and 1 <= int(raw) <= 365 else 10
    return {"jobs": jobs, "job_skills": job_skills, "job_locations": job_locations,
            "companies": companies, "keywords": keywords, "connections": connections,
            "extra_keywords": extra, "max_post_age_days": age}


def posts_enabled(choices: dict) -> bool:
    return bool(choices["companies"] or choices["keywords"] or choices["connections"])


def apply_jobs(prefs: dict, choices: dict | None) -> dict:
    """Overlay the user's job skills / places (if given) on the jobs.yaml preferences."""
    prefs = dict(prefs)
    if choices and choices.get("job_skills"):
        prefs["skills"] = choices["job_skills"]
        prefs["search_terms"] = []            # derive the search words from the new skills
    if choices and choices.get("job_locations"):
        prefs["preferred_locations"] = choices["job_locations"]
    return prefs


def apply(cfg: dict, choices: dict) -> dict:
    """Merge the user's choices over config.yaml, producing the settings the scanner and matcher use."""
    cfg = dict(cfg)
    use_kw, use_conn = bool(choices["keywords"]), bool(choices["connections"])
    cfg["companies"] = [c["slug"] for c in choices["companies"]]
    cfg["use_keywords"], cfg["use_connections"] = use_kw, use_conn
    cfg["max_post_age_days"] = choices["max_post_age_days"]
    cfg["scan_feed"] = use_kw or use_conn          # keywords and connections are both read from your feed
    if use_kw:
        merged = list(cfg.get("keywords") or [])
        merged += [k for k in choices["extra_keywords"] if k.lower() not in {m.lower() for m in merged}]
        cfg["keywords"] = merged
    else:
        cfg["hashtags"], cfg["searches"] = [], []
    return cfg


def summary(choices: dict) -> str:
    parts = []
    if choices["companies"]:
        parts.append("companies: " + ", ".join(c["name"] for c in choices["companies"]))
    if choices["connections"]:
        parts.append("all posts by your connections")
    if choices["keywords"]:
        parts.append("keywords" + (f" (+{len(choices['extra_keywords'])} extra)" if choices["extra_keywords"] else ""))
    posts = " OR ".join(parts)
    if choices.get("jobs"):
        return f"jobs; posts: {posts}" if posts else "jobs"
    return posts or "nothing"

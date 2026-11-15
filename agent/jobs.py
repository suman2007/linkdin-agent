"""Job search: LinkedIn Jobs -> cheap filters -> read description -> AI score -> ranked list.

Order of work is chosen to spend as little time and AI as possible:
  1. cheap filters on the search cards (age, already applied, junior titles, location tier)
  2. descriptions are opened only for jobs that survive, best candidates first, up to a budget
  3. the AI is called only when the posting mentions at least one of your skills
  4. every result is cached in seen.db, so a rerun re-reads and re-scores nothing it has seen
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import yaml
from playwright.sync_api import Page

from . import matcher
from .pacing import wait

JOBS_PATH = Path(__file__).resolve().parent.parent / "jobs.yaml"
DEFAULTS = {
    "skills": [], "search_terms": [],
    "preferred_locations": [], "include_remote": True, "fallback_location": "India",
    "max_age_days": 15, "min_years": 3,
    "exclude_title_words": ["intern", "trainee", "fresher", "junior", "graduate"],
    "pages_per_search": 2, "max_jobs_to_score": 60, "min_score": 60,
}
TRACKER_STAGES = ("applied", "interview", "archived")      # jobs you already acted on
CARD_SEL = '[role="button"][componentkey^="job-card-component-ref-"]'
CARD_PREFIX = "job-card-component-ref-"


def load_prefs(path: Path = JOBS_PATH) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    prefs = {**DEFAULTS, **(data or {})}
    if not prefs["skills"]:
        raise SystemExit(f"No skills configured. Add a 'skills:' list to {path.name}.")
    return prefs


@dataclass
class Job:
    id: str
    title: str = ""
    company: str = ""
    location: str = ""
    workplace: str = ""                 # Remote / Hybrid / On-site / ""
    posted_days: float | None = None
    applied_on_card: bool = False
    tier: int = 2                       # 1 = preferred location or remote, 2 = elsewhere in the country
    description: str = ""
    skills: list = field(default_factory=list)
    years: float | None = None
    score: int | None = None
    reason: str = ""
    decision: str = ""
    seen_before: bool = False

    @property
    def url(self) -> str:
        return f"https://www.linkedin.com/jobs/view/{self.id}/"


# ------------------------------------------------------------------ parsing
POSTED_RE = re.compile(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", re.I)
_UNIT_DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30}
WORKPLACE_RE = re.compile(r"\s*\((Remote|Hybrid|On-?site)\)\s*$", re.I)
_WORKPLACE_NAMES = {"remote": "Remote", "hybrid": "Hybrid", "on-site": "On-site", "onsite": "On-site"}
APPLIED_LINE_RE =re.compile(r"^Applied(\s+(\d+\s+\w+\s+ago|on\b.*))?$", re.I)


def parse_posted_days(text: str) -> float | None:
    m = POSTED_RE.search(text)
    return int(m.group(1)) * _UNIT_DAYS[m.group(2).lower()] if m else None


def parse_card(text: str, job_id: str) -> Job:
    """Cards read: [Selected, ]Title (Verified job) / Title / Company / Location (Workplace) / insights / Posted ... ago."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return Job(id=job_id)
    first = re.sub(r"^Selected,\s*", "", lines[0])
    first = re.sub(r"\s*\(Verified job\)\s*$", "", first)
    i = 2 if len(lines) > 1 and lines[1] == first else 1
    company = lines[i] if i < len(lines) else ""
    loc_raw = lines[i + 1] if i + 1 < len(lines) else ""
    m = WORKPLACE_RE.search(loc_raw)
    workplace = _WORKPLACE_NAMES[m.group(1).lower()] if m else ""
    return Job(
        id=job_id, title=first, company=company,
        location=WORKPLACE_RE.sub("", loc_raw).strip(" ,"), workplace=workplace,
        posted_days=parse_posted_days(text),
        applied_on_card=any(APPLIED_LINE_RE.match(ln) for ln in lines[i + 2:]),
    )


def tier_of(job: Job, prefs: dict) -> int:
    loc = job.location.lower()
    if prefs["include_remote"] and (job.workplace.lower() == "remote" or re.search(r"\bremote\b", loc)):
        return 1
    if any(p.lower() in loc for p in prefs["preferred_locations"]):
        return 1
    return 2


def skill_regex(skill: str) -> re.Pattern:
    """Whole-token match: 'C' must not match 'Cloud' or 'C++', and 'Java' must not match 'JavaScript'.
    A plural 's' is allowed, so 'Microservice' also finds 'microservices'."""
    pat = r"(?<![A-Za-z0-9+#])" + re.escape(skill) + ("" if len(skill) == 1 else "s?") + r"(?![A-Za-z0-9+#])"
    return re.compile(pat, 0 if len(skill) == 1 else re.IGNORECASE)


def skills_in(text: str, skills: list[str]) -> list[str]:
    return [s for s in skills if skill_regex(s).search(text)]


# ------------------------------------------------------------------ search
def search_terms(prefs: dict) -> list[str]:
    if prefs["search_terms"]:
        return list(prefs["search_terms"])
    return [s if len(s) > 1 else f"{s} programming" for s in prefs["skills"]]


def search_url(prefs: dict, place: str, remote_only: bool = False, start: int = 0) -> str:
    """`place` is a LinkedIn place id (digits, from geo_id) or, as a fallback, a plain place name.
    The id is needed: LinkedIn's results page ignores a typed location and silently uses your profile's.
    LinkedIn also drops a remote filter from the address, so remote_only just adds the word 'remote'
    to the query; whether a job is really remote is read from its card."""
    terms = [f'"{t}"' if re.search(r"[\s+#]", t) else t for t in search_terms(prefs)]
    query = "(" + " OR ".join(terms) + ")" + (" AND remote" if remote_only else "")
    parts = [("keywords", query), ("geoId", place) if place.isdigit() else ("location", place),
             ("f_TPR", f"r{int(prefs['max_age_days'] * 86400)}"), ("sortBy", "DD")]
    if start:
        parts.append(("start", str(start)))
    return "https://www.linkedin.com/jobs/search-results/?" + "&".join(f"{k}={quote(v)}" for k, v in parts)


def geo_id(page: Page, name: str) -> str | None:
    """Look a place name up on LinkedIn's own place-search address; returns the first matching place id."""
    try:
        resp = page.request.get("https://www.linkedin.com/jobs-guest/api/typeaheadHits?typeaheadType=GEO&query="
                                + quote(name))
        hits = json.loads(resp.text()) if resp.ok else []
    except Exception:
        return None
    return next((str(h["id"]) for h in hits if h.get("type") == "GEO" and str(h.get("id", "")).isdigit()), None)


def searches(prefs: dict) -> list[tuple[str, bool]]:
    """(location, remote_only) in preference order: preferred places, then remote, then the whole country."""
    out = [(loc, False) for loc in prefs["preferred_locations"]]
    if prefs["include_remote"]:
        out.append((prefs["fallback_location"], True))
    out.append((prefs["fallback_location"], False))
    return out


_CARDS_JS = ("() => [...document.querySelectorAll('" + CARD_SEL + "')].map(e => ({"
             "key: e.getAttribute('componentkey'), text: e.innerText}))")


def collect_cards(page: Page, url: str) -> list[Job]:
    page.goto(url, wait_until="domcontentloaded")
    wait(page, 4000, 6500)
    if "just a moment" in page.title().lower():       # bot-check page; never try to bypass it
        print("  skipped a search: LinkedIn showed a bot-check page.")
        return []
    jobs = []
    for c in page.evaluate(_CARDS_JS):
        job_id = c["key"].replace(CARD_PREFIX, "")
        if job_id.isdigit():
            jobs.append(parse_card(c["text"], job_id))
    return jobs


_JOB_LINKS_JS = ("() => [...new Set([...document.querySelectorAll('a[href*=\"/jobs/view/\"]')]"
                 ".map(a => (a.getAttribute('href').match(/jobs.view.([0-9]+)/)||[])[1]).filter(Boolean))]")


def tracker_applied(page: Page) -> set[str]:
    """Job ids in your LinkedIn Job tracker as Applied / Interview / Archived."""
    ids: set[str] = set()
    for stage in TRACKER_STAGES:
        page.goto(f"https://www.linkedin.com/jobs-tracker/?stage={stage}", wait_until="domcontentloaded")
        wait(page, 3500, 5000)
        for _ in range(4):
            page.mouse.move(640, 500)
            page.mouse.wheel(0, 900)
            wait(page, 500, 900)
        ids.update(page.evaluate(_JOB_LINKS_JS))
    return ids


_DESC_JS = ("() => { const boxes = [...document.querySelectorAll('[data-testid=\"expandable-text-box\"]')]"
            ".map(e => e.innerText || ''); boxes.sort((a, b) => b.length - a.length);"
            " const applied = [...document.querySelectorAll('a, button')].some(e => (e.innerText || '').trim() === 'Applied');"
            " return {text: boxes[0] || '', applied: applied}; }")


def fetch_description(page: Page, job: Job) -> tuple[str, bool]:
    """(description text, you-already-applied) from the job's own page."""
    page.goto(job.url, wait_until="domcontentloaded")
    wait(page, 2500, 4000)
    if "just a moment" in page.title().lower():
        return "", False
    r = page.evaluate(_DESC_JS)
    return r["text"].strip(), bool(r["applied"])


# ------------------------------------------------------------------ AI scoring
def system_prompt(prefs: dict) -> str:
    places = ", ".join(prefs["preferred_locations"] + (["remote"] if prefs["include_remote"] else []))
    return (
        "You assess one job posting for an EXPERIENCED software engineer.\n"
        f"Candidate skills: {', '.join(prefs['skills'])}.\n"
        f"Preferred: {places}; otherwise anywhere in {prefs['fallback_location']}. "
        f"The candidate wants roles needing about {prefs['min_years']}+ years or senior/lead/staff/architect level, not entry level.\n"
        "Reply with ONE line of JSON only, no other text:\n"
        '{"score": 0-100, "skills": ["matched skills"], "years": number or null (minimum years required), '
        '"experienced_role": true or false, "reason": "max 15 words"}\n'
        "score = how well the required skills and seniority fit; 80+ means a strong fit."
    )


def parse_score(reply: str) -> dict | None:
    reply = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.DOTALL | re.IGNORECASE)
    m = re.search(r"\{.*\}", reply, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        years = d.get("years")
        return {
            "score": max(0, min(100, int(float(d["score"])))),
            "skills": [str(s) for s in (d.get("skills") or [])],
            "years": float(years) if isinstance(years, (int, float)) else None,
            "experienced_role": bool(d.get("experienced_role", True)),
            "reason": str(d.get("reason", ""))[:160],
        }
    except (ValueError, KeyError, TypeError):
        return None


def score_job(job: Job, prefs: dict, model: str, complete) -> dict:
    user = f"Title: {job.title}\nCompany: {job.company}\nLocation: {job.location} ({job.workplace})\n\n{job.description[:3500]}"
    parsed = parse_score(complete(system_prompt(prefs), user, model))
    hits = skills_in(f"{job.title}\n{job.description}", prefs["skills"])     # skills really named in the posting
    if parsed:
        # the AI tends to be generous: believe only skills found in the text, and cap the score by how many
        parsed["skills"] = hits
        parsed["score"] = min(parsed["score"], 40 + 15 * len(hits))
        return parsed
    return {"score": min(60, round(100 * len(hits) / max(1, len(prefs["skills"])))), "skills": hits,
            "years": None, "experienced_role": True, "reason": "AI reply unreadable; scored by skill count"}


# ------------------------------------------------------------------ pipeline
def run(page: Page, prefs: dict, store, model: str, complete=matcher.complete, say=print, geo_lookup=None) -> list[Job]:
    """Search, filter, read, score. Returns every job seen, each with a `decision`."""
    applied = tracker_applied(page) | store.applied_job_ids()
    say(f"Skipping {len(applied)} job(s) you already applied to or archived.")

    geo_lookup = geo_lookup or (lambda name: geo_id(page, name))
    places: dict[str, str] = {}
    for name in dict.fromkeys(loc for loc, _ in searches(prefs)):
        places[name] = geo_lookup(name) or name
        say(f"  place {name!r}: " + (f"id {places[name]}" if places[name].isdigit()
                                     else "id not found, using the name (LinkedIn may substitute your own location)"))

    found: dict[str, Job] = {}
    for location, remote_only in searches(prefs):
        label = f"{location}{' (remote)' if remote_only else ''}"
        for p in range(prefs["pages_per_search"]):
            cards = collect_cards(page, search_url(prefs, places[location], remote_only, start=25 * p))
            new = [c for c in cards if c.id not in found]
            for c in new:
                found[c.id] = c
            say(f"  search {label}, page {p + 1}: {len(cards)} jobs ({len(new)} new)")
            if len(cards) < 25:
                break
    jobs = list(found.values())

    for j in jobs:
        j.tier = tier_of(j, prefs)
    best: dict[tuple, Job] = {}                      # the same posting is often listed once per city
    for j in sorted(jobs, key=lambda j: j.tier):
        best.setdefault((j.title.lower(), j.company.lower()), j)

    bad_title = [w.lower() for w in prefs["exclude_title_words"]]
    candidates: list[Job] = []
    for j in jobs:
        if best[(j.title.lower(), j.company.lower())] is not j:
            j.decision = "duplicate"
        elif j.id in applied or j.applied_on_card:
            j.decision = "already applied"
        elif j.posted_days is not None and j.posted_days > prefs["max_age_days"]:
            j.decision = "too old"
        elif any(re.search(rf"\b{re.escape(w)}\b", j.title.lower()) for w in bad_title):
            j.decision = "not experienced"
            j.reason = "entry-level title"
        else:
            candidates.append(j)
    say(f"{len(jobs)} jobs found; {len(candidates)} pass the cheap filters.")

    # best candidates first: preferred tier, then most skills named in the title
    candidates.sort(key=lambda j: (j.tier, -len(skills_in(j.title, prefs["skills"]))))
    budget = prefs["max_jobs_to_score"]
    for j in candidates:
        cached = store.job_cached(j.id)
        if cached is None:
            if budget <= 0:
                j.decision = "not scored (budget)"
                continue
            budget -= 1
            j.description, now_applied = fetch_description(page, j)
            if now_applied:
                store.mark_jobs_applied([j.id])
                j.decision = "already applied"
                continue
            hits = skills_in(f"{j.title}\n{j.description}", prefs["skills"])
            if not j.description:
                cached = {"score": 0, "skills": [], "years": None, "experienced_role": True,
                          "reason": "description could not be read", "no_description": True}
            elif not hits:
                cached = {"score": 0, "skills": [], "years": None, "experienced_role": True,
                          "reason": "none of your skills appear in the posting", "no_skill": True}
            else:
                cached = score_job(j, prefs, model, complete)
            store.save_job(j.id, cached)
        j.score, j.skills, j.years, j.reason = cached["score"], cached["skills"], cached["years"], cached["reason"]
        if cached.get("no_description"):
            j.decision = "no description"
        elif cached.get("no_skill"):
            j.decision = "no skill match"
        elif (j.years is not None and j.years < prefs["min_years"]) or not cached["experienced_role"]:
            j.decision = "not experienced"
        elif j.score < prefs["min_score"]:
            j.decision = "low match"
        else:
            j.decision = "recommended"

    recommended = [j for j in jobs if j.decision == "recommended"]
    reported = store.jobs_reported([j.id for j in recommended])
    for j in recommended:
        j.seen_before = j.id in reported
    store.mark_jobs_reported([j.id for j in recommended])
    return rank(jobs)


def rank(jobs: list[Job]) -> list[Job]:
    """Recommended first (preferred tier, then score), then the rest grouped by decision."""
    def key(j: Job):
        return (0 if j.decision == "recommended" else 1, j.tier, -(j.score or 0), j.decision)
    return sorted(jobs, key=key)

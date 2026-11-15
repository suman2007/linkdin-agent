"""Job search tests: parsing, filters, ranking, caching, applied-job exclusion, report. Synthetic data only."""
import json

import pytest

from agent import __main__ as cli
from agent import jobs, jobs_report
from agent.store import Store

PREFS = {**jobs.DEFAULTS, "skills": ["C", "C++", "Python", "Java", "LTE", "4G", "Microservice", "Distributed System"],
         "search_terms": ["C programming", "C++", "distributed systems"],
         "preferred_locations": ["Placeville"], "include_remote": True, "fallback_location": "Examplestan",
         "pages_per_search": 1, "max_jobs_to_score": 60, "min_score": 50, "min_years": 3}
GEO = {"Placeville": "1001", "Examplestan": "2002"}
lookup = GEO.get

# id -> (title, company, location line, age text, description, AI reply or None, extra)
J = {
    "9000000001": ("Senior C++ Engineer LTE", "Example Corp", "Placeville, Region (Hybrid)", "3 days",
                   "Build LTE stacks in C++ and Python. 5+ years. [[1]]", {"score": 90, "skills": ["C++", "LTE"], "years": 5, "experienced_role": True, "reason": "strong fit"}, {}),
    "9000000002": ("Java Backend Lead", "Sample Labs", "Examplestan (Remote)", "5 days",
                   "Java microservices and distributed systems. [[2]]", {"score": 70, "skills": ["Java", "Microservice"], "years": 6, "experienced_role": True, "reason": "good fit"}, {}),
    "9000000003": ("C++ Systems Engineer", "Demo Works", "Otherton (On-site)", "2 days",
                   "C++ and Python services. [[3]]", {"score": 85, "skills": ["C++", "Python"], "years": 4, "experienced_role": True, "reason": "fits"}, {}),
    "9000000004": ("Python Architect", "Example Corp", "Placeville (On-site)", "1 day",
                   "Python. [[4]]", {"score": 95, "skills": ["Python"], "years": 8, "experienced_role": True, "reason": "x"}, {"tracker": True}),
    "9000000005": ("Python Developer", "Sample Labs", "Otherton (Hybrid)", "3 weeks", "Python. [[5]]", None, {}),
    "9000000006": ("Junior Python Developer", "Demo Works", "Otherton (Hybrid)", "1 day", "Python. [[6]]", None, {}),
    "9000000007": ("Java Engineer", "Example Corp", "Otherton (Hybrid)", "4 days", "Java. [[7]]", None, {"applied_card": True}),
    "9000000008": ("Accounts Manager", "Sample Labs", "Otherton (On-site)", "2 days", "Ledgers and audits only. [[8]]", None, {}),
    "9000000009": ("Python Engineer", "Demo Works", "Otherton (Hybrid)", "2 days",
                   "Python, entry friendly. [[9]]", {"score": 80, "skills": ["Python"], "years": 1, "experienced_role": True, "reason": "junior"}, {}),
    "9000000010": ("C Developer", "Example Corp", "Otherton (On-site)", "6 days",
                   "C work. [[10]]", {"score": 30, "skills": ["C"], "years": 4, "experienced_role": True, "reason": "weak"}, {}),
    "9000000011": ("Java Platform Engineer", "Sample Labs", "Otherton (Hybrid)", "2 days",
                   "Java. [[11]]", {"score": 88, "skills": ["Java"], "years": 5, "experienced_role": True, "reason": "x"}, {"page_applied": True}),
}


def card_html(job_id, spec, selected=False):
    title, company, loc, age, _d, _r, extra = spec
    lines = [("Selected, " if selected else "") + f"{title} (Verified job)", title, company, loc,
             "You’d be a top applicant", f"Posted {age} ago", f"{age} ago"]
    if extra.get("applied_card"):
        lines.insert(5, "Applied 4 days ago")
    inner = "".join(f"<p>{ln}</p>" for ln in lines)
    return f'<div role="button" componentkey="job-card-component-ref-{job_id}">{inner}</div>'


def job_page(job_id, spec):
    desc = spec[4]
    btn = "Applied" if spec[6].get("page_applied") else "Apply"
    return ('<html><head><title>job</title></head><body><div data-testid="expandable-text-box">short decoy</div>'
            f'<div data-testid="expandable-text-box">{desc}</div><button>{btn}</button><button>Save</button></body></html>')


class Site:
    """Fake LinkedIn Jobs. `log` records requested URLs."""

    def __init__(self, browser_ctx, jobs_map=J, per_page=None):
        self.log, self.jobs_map, self.per_page = [], jobs_map, per_page
        self.ctx = browser_ctx.new_context()
        self.ctx.route("https://www.linkedin.com/**", self._handle)
        self.page = self.ctx.new_page()

    def _handle(self, route):
        url = route.request.url
        self.log.append(url)
        if "/jobs/search-results/" in url:
            start = int(url.split("start=")[1].split("&")[0]) if "start=" in url else 0
            ids = list(self.jobs_map)
            chunk = ids[start:start + self.per_page] if self.per_page else (ids if start == 0 else [])
            body = "<html><head><title>search</title></head><body>" + "".join(
                card_html(i, self.jobs_map[i], selected=(n == 0)) for n, i in enumerate(chunk)) + "</body></html>"
        elif "/jobs-tracker/" in url:
            stage = url.split("stage=")[1]
            links = "".join(f'<a href="https://www.linkedin.com/jobs/view/{i}/">x</a>'
                            for i, s in self.jobs_map.items() if s[6].get("tracker") and stage == "applied")
            body = f"<html><body>{links}</body></html>"
        elif "/jobs/view/" in url:
            job_id = url.rstrip("/").split("/")[-1]
            body = job_page(job_id, self.jobs_map[job_id])
        else:
            body = "<html><body></body></html>"
        route.fulfill(status=200, content_type="text/html", body=body)

    def count(self, fragment):
        return sum(1 for u in self.log if fragment in u)


def llm_stub(calls):
    def complete(system, user, model):
        calls.append(user)
        marker = int(user.split("[[")[1].split("]]")[0])
        return json.dumps(J[f"90000000{marker:02d}"][5])
    return complete


def run_pipeline(site, tmp_path, prefs=PREFS, store=None, calls=None):
    calls = calls if calls is not None else []
    store = store or Store(tmp_path / "t.db")
    result = jobs.run(site.page, prefs, store, "m", complete=llm_stub(calls), say=lambda *_: None, geo_lookup=lookup)
    return {j.id: j for j in result}, result, store, calls


# ------------------------------------------------------------------ pure functions
def test_parse_card_variants():
    j = jobs.parse_card("Selected, Senior Dev (Verified job)\nSenior Dev\nExample Corp\nPlaceville, Region (Hybrid)\n"
                        "Posted 1 week ago\n1 week ago", "123")
    assert (j.title, j.company, j.location, j.workplace, j.posted_days) == (
        "Senior Dev", "Example Corp", "Placeville, Region", "Hybrid", 7)
    plain = jobs.parse_card("Backend Dev\nSample Labs\nExamplestan (Remote)\n2 days ago", "1")
    assert (plain.title, plain.company, plain.workplace, plain.posted_days) == ("Backend Dev", "Sample Labs", "Remote", 2)
    nowork = jobs.parse_card("Dev (Verified job)\nDev\nDemo Works\nOthertown\n5 hours ago", "2")
    assert nowork.workplace == "" and nowork.location == "Othertown" and abs(nowork.posted_days - 5 / 24) < 1e-9
    assert jobs.parse_card("", "3").title == ""


def test_applied_marker_is_not_fooled_by_titles():
    assert jobs.parse_card("Applied Scientist\nExample Corp\nPlaceville (Hybrid)\n2 days ago", "1").applied_on_card is False
    assert jobs.parse_card("Dev\nExample Corp\nPlaceville (Hybrid)\nApplied 3 days ago\n3 days ago", "1").applied_on_card is True
    assert jobs.parse_card("Dev\nExample Corp\nPlaceville\nApplied\n", "1").applied_on_card is True


def test_posted_days():
    assert jobs.parse_posted_days("Posted 2 weeks ago") == 14
    assert jobs.parse_posted_days("1 month ago") == 30
    assert jobs.parse_posted_days("3 minutes ago") == 3 / 1440
    assert jobs.parse_posted_days("nothing") is None


def test_tiers():
    mk = lambda loc, wp="": jobs.Job(id="1", location=loc, workplace=wp)
    assert jobs.tier_of(mk("Placeville, Region"), PREFS) == 1
    assert jobs.tier_of(mk("Examplestan", "Remote"), PREFS) == 1
    assert jobs.tier_of(mk("Otherton", "Hybrid"), PREFS) == 2
    assert jobs.tier_of(mk("Examplestan", "Remote"), {**PREFS, "include_remote": False}) == 2


def test_skill_matching_is_token_based():
    sk = PREFS["skills"]
    assert jobs.skills_in("Strong C/C++ and Python", sk) == ["C", "C++", "Python"]
    assert jobs.skills_in("Cloud native JavaScript and C#", sk) == []        # not C, not Java, not C++
    assert jobs.skills_in("designing microservices and 4G core", sk) == ["4G", "Microservice"]
    assert jobs.skills_in("lte radio and distributed systems", sk) == ["LTE", "Distributed System"]
    assert jobs.skills_in("c language", sk) == []                            # single letters are case-sensitive


def test_search_url_filters():
    url = jobs.search_url(PREFS, "1001")
    assert url.startswith("https://www.linkedin.com/jobs/search-results/?")
    assert "geoId=1001" in url and "f_TPR=r1296000" in url and "sortBy=DD" in url
    assert "location=" not in url and "AND%20remote" not in url and "start=" not in url
    assert "%22C%2B%2B%22" in url and "%22distributed%20systems%22" in url and "%20OR%20" in url
    assert "AND%20remote" in jobs.search_url(PREFS, "2002", remote_only=True)
    assert "start=25" in jobs.search_url(PREFS, "2002", start=25)
    assert jobs.search_url({**PREFS, "max_age_days": 7}, "1001").count("f_TPR=r604800") == 1
    assert "location=Placeville" in jobs.search_url(PREFS, "Placeville")      # fallback when no id was found


def test_geo_id_lookup_uses_first_geo_hit():
    class Resp:
        def __init__(self, ok, text):
            self.ok, self._t = ok, text

        def text(self):
            return self._t

    class Req:
        def __init__(self, resp):
            self.resp, self.urls = resp, []

        def get(self, url):
            self.urls.append(url)
            if isinstance(self.resp, Exception):
                raise self.resp
            return self.resp

    class Pg:
        def __init__(self, resp):
            self.request = Req(resp)

    hits = json.dumps([{"id": "555", "type": "GEO", "displayName": "Placeville"}, {"id": "666", "type": "GEO"}])
    pg = Pg(Resp(True, hits))
    assert jobs.geo_id(pg, "Place ville") == "555" and "query=Place%20ville" in pg.request.urls[0]
    assert jobs.geo_id(Pg(Resp(False, hits)), "x") is None
    assert jobs.geo_id(Pg(Resp(True, json.dumps([{"id": "7", "type": "COMPANY"}]))), "x") is None
    assert jobs.geo_id(Pg(RuntimeError("network")), "x") is None


def test_unresolved_place_falls_back_to_name(browser_ctx, tmp_path):
    site = Site(browser_ctx)
    jobs.run(site.page, {**PREFS, "max_jobs_to_score": 0}, Store(tmp_path / "t.db"), "m",
             complete=lambda *a: "", say=lambda *_: None, geo_lookup=lambda name: None)
    assert any("location=Placeville" in u for u in site.log if "/jobs/search-results/" in u)


def test_search_order_and_terms_default():
    assert jobs.searches(PREFS) == [("Placeville", False), ("Examplestan", True), ("Examplestan", False)]
    assert jobs.searches({**PREFS, "include_remote": False}) == [("Placeville", False), ("Examplestan", False)]
    assert jobs.search_terms({**PREFS, "search_terms": []})[:3] == ["C programming", "C++", "Python"]


def test_parse_score():
    ok = jobs.parse_score('<think>hmm</think> {"score": 140, "skills": ["Python"], "years": 5, "experienced_role": true, "reason": "good"}')
    assert ok["score"] == 100 and ok["years"] == 5.0 and ok["skills"] == ["Python"]
    assert jobs.parse_score('Sure! {"score": "72", "years": null, "reason": "r"}')["years"] is None
    assert jobs.parse_score("no json here") is None
    assert jobs.parse_score('{"nope": 1}') is None


def test_load_prefs(tmp_path):
    p = tmp_path / "jobs.yaml"
    with pytest.raises(SystemExit):
        jobs.load_prefs(p)                                                   # missing file -> no skills
    p.write_text("skills: [Python]\nmax_age_days: 7\n", encoding="utf-8")
    prefs = jobs.load_prefs(p)
    assert prefs["max_age_days"] == 7 and prefs["min_score"] == jobs.DEFAULTS["min_score"]   # defaults fill the rest


def test_repository_jobs_yaml_is_valid_and_complete():
    prefs = jobs.load_prefs()
    assert prefs["skills"] and prefs["max_age_days"] > 0 and prefs["fallback_location"]


# ------------------------------------------------------------------ pipeline against the fake site
def test_pipeline_decisions_and_ranking(browser_ctx, tmp_path):
    site = Site(browser_ctx)
    by, result, store, calls = run_pipeline(site, tmp_path)
    assert {i: by[i].decision for i in by} == {
        "9000000001": "recommended", "9000000002": "recommended", "9000000003": "recommended",
        "9000000004": "already applied",          # in the Job tracker
        "9000000005": "too old",                  # 3 weeks > 15 days
        "9000000006": "not experienced",          # junior title
        "9000000007": "already applied",          # card says Applied
        "9000000008": "no skill match",           # no skills in the posting
        "9000000009": "not experienced",          # AI: 1 year required
        "9000000010": "low match",                # AI score 30 < 50
        "9000000011": "already applied",          # job page shows Applied
    }
    ranked = [j.id for j in result if j.decision == "recommended"]
    assert ranked == ["9000000001", "9000000002", "9000000003"]              # preferred tier first, then score
    assert [by[i].tier for i in ranked] == [1, 1, 2]
    assert result[0].decision == "recommended"                                # recommended come first overall
    assert by["9000000001"].skills == ["C++", "Python", "LTE"] and by["9000000001"].reason == "strong fit"
    assert by["9000000001"].score == 85                                       # AI said 90; capped: 3 skills -> 40 + 45


def test_pipeline_saves_work(browser_ctx, tmp_path):
    site = Site(browser_ctx)
    _, _, _, calls = run_pipeline(site, tmp_path)
    assert site.count("/jobs/view/") == 7                 # 5 scored + 'no skill' + the page that said Applied
    assert len(calls) == 5                                # AI skipped for the no-skill job and applied jobs
    for skipped in ("9000000004", "9000000005", "9000000006", "9000000007"):
        assert not any(skipped in u for u in site.log if "/jobs/view/" in u)   # never even opened


def test_second_run_uses_cache_and_flags_seen(browser_ctx, tmp_path):
    site = Site(browser_ctx)
    store = Store(tmp_path / "t.db")
    first, _, _, _ = run_pipeline(site, tmp_path, store=store)
    assert not any(j.seen_before for j in first.values())
    views_before = site.count("/jobs/view/")
    second, _, _, calls = run_pipeline(site, tmp_path, store=store)
    assert calls == []                                    # no AI calls
    assert site.count("/jobs/view/") == views_before      # no job page re-opened: scores, "no skill" and "applied" are remembered
    assert {i: j.decision for i, j in second.items() if j.decision == "recommended"} == \
           {i: "recommended" for i in ("9000000001", "9000000002", "9000000003")}
    assert all(second[i].seen_before for i in ("9000000001", "9000000002", "9000000003"))


def test_manual_applied_is_excluded(browser_ctx, tmp_path):
    store = Store(tmp_path / "t.db")
    store.mark_jobs_applied(["9000000003"])
    by, _, _, _ = run_pipeline(Site(browser_ctx), tmp_path, store=store)
    assert by["9000000003"].decision == "already applied"
    assert by["9000000001"].decision == "recommended"


def test_budget_scores_preferred_locations_first(browser_ctx, tmp_path):
    by, _, _, calls = run_pipeline(Site(browser_ctx), tmp_path, prefs={**PREFS, "max_jobs_to_score": 2})
    assert len(calls) == 2
    assert by["9000000001"].decision == "recommended" and by["9000000002"].decision == "recommended"
    assert by["9000000003"].decision == "not scored (budget)"


def test_searches_and_pagination(browser_ctx, tmp_path):
    many = {f"91000000{i:02d}": ("Python Engineer", "Example Corp", "Otherton (Hybrid)", "1 day", f"Python [[{i}]]", None, {})
            for i in range(28)}
    site = Site(browser_ctx, jobs_map=many, per_page=25)
    found = jobs.run(site.page, {**PREFS, "pages_per_search": 3, "max_jobs_to_score": 0}, Store(tmp_path / "t.db"), "m",
                     complete=lambda *a: "", say=lambda *_: None, geo_lookup=lookup)
    assert len(found) == 28                               # 25 + 3 across two pages, no duplicates across searches
    searches = [u for u in site.log if "/jobs/search-results/" in u]
    assert any("geoId=1001" in u for u in searches)                       # the preferred place, by id
    assert any("geoId=2002" in u and "AND%20remote" in u for u in searches)   # remote search
    assert not any("location=" in u for u in searches)
    assert all("f_TPR=r1296000" in u for u in searches)
    assert any("start=25" in u for u in searches)


def test_tracker_collects_applied_interview_archived(browser_ctx):
    site = Site(browser_ctx)
    assert jobs.tracker_applied(site.page) == {"9000000004"}
    stages = [u.split("stage=")[1] for u in site.log if "jobs-tracker" in u]
    assert stages == ["applied", "interview", "archived"]


# ------------------------------------------------------------------ report + CLI helpers
def test_report_render_escapes_and_defaults_to_recommended():
    j1 = jobs.Job(id="1", title="Dev <script>x</script>", company="A&B", location="Placeville", workplace="Hybrid",
                  posted_days=3, tier=1, score=90, skills=["C++"], years=5, reason="fits", decision="recommended")
    j2 = jobs.Job(id="2", title="Other", decision="too old", tier=2)
    page = jobs_report.render([j1, j2], {"when": "now", "max_age_days": 15, "skills": "C++", "min_score": 50})
    assert "<script>x</script>" not in page and "&lt;script&gt;" in page and "A&amp;B" in page
    assert 'value="recommended" selected' in page and "Preferred / Remote: 1" in page and "too old: 1" in page
    assert "https://www.linkedin.com/jobs/view/1/" in page and "5+" in page and "3 d" in page
    assert "≤15 d" in page                                  # a job with no card age shows the filter window, not '?'
    assert jobs_report._age(0.5, 15) == "12 h" and jobs_report._age(None, 15) == "≤15 d"


def test_report_write(tmp_path):
    out = jobs_report.write([], {**PREFS}, tmp_path / "r" / "jobs.html")
    assert out.exists() and "Job report" in out.read_text(encoding="utf-8")


def test_cmd_applied_parses_ids_and_links(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")
    monkeypatch.setattr(cli, "Store", lambda: store)
    rc = cli.cmd_applied(["https://www.linkedin.com/jobs/view/9100000001/?trk=x", "9100000002", "junk"])
    assert rc == 0 and store.applied_job_ids() == {"9100000001", "9100000002"}
    assert cli.cmd_applied(["junk"]) == 1


def test_store_job_cache_roundtrip(tmp_path):
    s = Store(tmp_path / "t.db")
    assert s.job_cached("1") is None
    s.save_job("1", {"score": 5})
    s.mark_jobs_applied(["1", "2"])
    assert s.job_cached("1") == {"score": 5} and s.job_cached("2") is None
    assert s.applied_job_ids() == {"1", "2"}
    s.mark_jobs_reported(["1"])
    assert s.jobs_reported(["1", "2"]) == {"1"}


def test_score_is_capped_by_skills_really_in_the_text():
    job = jobs.Job(id="1", title="Backend Engineer", description="We build services in Python.")   # only 1 skill present
    ai = json.dumps({"score": 95, "skills": ["Python", "Java", "LTE"], "years": 5, "experienced_role": True, "reason": "r"})
    out = jobs.score_job(job, PREFS, "m", lambda *a: ai)
    assert out["skills"] == ["Python"]                    # claimed Java and LTE are not in the text
    assert out["score"] == 55                             # 40 + 15 * 1
    many = jobs.Job(id="2", title="Dev", description="Python Java C++ LTE 4G microservices")
    assert jobs.score_job(many, PREFS, "m", lambda *a: ai)["score"] == 95   # 5 skills: cap 100, AI's own 95 stands
    assert jobs.score_job(job, PREFS, "m", lambda *a: "garbage")["reason"].startswith("AI reply unreadable")


def test_same_posting_in_several_cities_is_listed_once(browser_ctx, tmp_path):
    base = J["9000000001"]
    twin = ("Senior C++ Engineer LTE", "Example Corp", "Otherton (On-site)", "3 days", base[4].replace("[[1]]", "[[1]]"), base[5], {})
    site = Site(browser_ctx, jobs_map={"9000000001": base, "9000000099": twin})
    result = jobs.run(site.page, PREFS, Store(tmp_path / "t.db"), "m", complete=llm_stub([]),
                      say=lambda *_: None, geo_lookup=lookup)
    by = {j.id: j for j in result}
    assert by["9000000001"].decision == "recommended"     # the Placeville (preferred) copy is kept
    assert by["9000000099"].decision == "duplicate"

from agent import choices
from conftest import COMPANY_NAME, COMPANY_SLUG, OTHER_SLUGS, REGIONAL_NAME, REGIONAL_SLUG


def scripted(*answers):
    it = iter(answers)
    return lambda prompt="": next(it)


def test_setup_company_keywords_connections_age():
    resolver = lambda name: [(COMPANY_SLUG, COMPANY_NAME + " "), (REGIONAL_SLUG, REGIONAL_NAME)]
    # companies, pick #1, keywords yes, extra keywords, connections yes, age 7
    ans = scripted("n", COMPANY_NAME, "1", "y", "SON, handover", "y", "7")
    c = choices.run_setup(ask=ans, say=lambda *_: None, resolver=resolver)
    assert c == {"jobs": False, "job_skills": [], "job_locations": [], "companies": [{"name": COMPANY_NAME, "slug": COMPANY_SLUG}], "keywords": True,
                 "connections": True, "extra_keywords": ["SON", "handover"], "max_post_age_days": 7}


def test_setup_blank_answers_use_defaults():
    # no company, keywords default yes, no extras, connections default no, age default
    c = choices.run_setup(ask=scripted("n", "", "", "", "", ""), say=lambda *_: None)
    assert c["companies"] == [] and c["keywords"] is True and c["connections"] is False
    assert c["extra_keywords"] == [] and c["max_post_age_days"] == 10


def test_company_can_be_a_url_without_lookup():
    c = choices.run_setup(ask=scripted("n", f"https://www.linkedin.com/company/{OTHER_SLUGS[0]}/posts/, https://linkedin.com/company/{OTHER_SLUGS[1]}",
                                       "n", "n", "30"), say=lambda *_: None)
    assert [x["slug"] for x in c["companies"]] == list(OTHER_SLUGS)
    assert c["keywords"] is False and c["max_post_age_days"] == 30


def test_company_name_not_found_is_skipped():
    msgs = []
    c = choices.run_setup(ask=scripted("n", "Some Unknown Co", "n", "y", ""), say=msgs.append, resolver=lambda name: [])
    assert c["companies"] == [] and c["connections"] is True      # still valid: connections enabled
    assert any("Could not find" in m for m in msgs)


def test_pick_zero_skips_candidate():
    two = lambda name: [("a", "A"), ("b", "B")]
    # pick 0 -> skipped, keywords off, connections yes (so a rule exists), default age
    c = choices.run_setup(ask=scripted("n", "X", "0", "n", "y", ""), say=lambda *_: None, resolver=two)
    assert c["companies"] == [] and c["connections"] is True


def test_nothing_enabled_reasks_then_falls_back_to_keywords():
    ans = scripted(*(["n", "", "n", "n"] * 3), "")      # three rounds of "nothing", then the fallback applies
    c = choices.run_setup(ask=ans, say=lambda *_: None)
    assert c["keywords"] is True


def test_apply_builds_scanner_settings():
    base = {"keywords": ["LTE"], "hashtags": ["LTE"], "searches": ["x"], "max_post_age_days": 10}
    only_companies = {"companies": [{"name": "M", "slug": "m"}], "keywords": False, "connections": False,
                      "extra_keywords": [], "max_post_age_days": 5}
    cfg = choices.apply(base, only_companies)
    assert cfg["companies"] == ["m"] and cfg["scan_feed"] is False
    assert cfg["use_keywords"] is False and cfg["hashtags"] == [] and cfg["searches"] == []
    assert cfg["max_post_age_days"] == 5
    kw = {"companies": [], "keywords": True, "connections": False, "extra_keywords": ["SON", "lte"], "max_post_age_days": 10}
    cfg = choices.apply(base, kw)
    assert cfg["keywords"] == ["LTE", "SON"] and cfg["scan_feed"] is True and cfg["hashtags"] == ["LTE"]
    conn = {"companies": [], "keywords": False, "connections": True, "extra_keywords": [], "max_post_age_days": 10}
    assert choices.apply(base, conn)["scan_feed"] is True and base["hashtags"] == ["LTE"]   # base not mutated


def test_save_load_roundtrip_and_missing(tmp_path):
    p = tmp_path / "choices.yaml"
    assert choices.load(p) is None
    c = {"jobs": True, "job_skills": ["C"], "job_locations": [],
         "companies": [{"name": COMPANY_NAME, "slug": COMPANY_SLUG}], "keywords": False,
         "connections": True, "extra_keywords": [], "max_post_age_days": 14}
    choices.save(c, p)
    assert choices.load(p) == c
    p.write_text("keywords: false\n", encoding="utf-8")                  # partial file: defaults fill the rest
    assert choices.load(p)["max_post_age_days"] == 10 and choices.load(p)["keywords"] is False


def test_summary_text():
    c = {"companies": [{"name": COMPANY_NAME, "slug": COMPANY_SLUG}], "keywords": True, "connections": True,
         "extra_keywords": ["SON"], "max_post_age_days": 10}
    assert choices.summary(c) == f"companies: {COMPANY_NAME} OR all posts by your connections OR keywords (+1 extra)"
    assert choices.summary({"companies": [], "keywords": False, "connections": False}) == "nothing"


def test_jobs_only_asks_no_post_questions_and_overrides_prefs():
    c = choices.run_setup(ask=scripted("y", "Python, LTE", "Pune", "", "n", "n"), say=lambda *_: None)
    assert c["jobs"] is True and c["job_skills"] == ["Python", "LTE"] and c["job_locations"] == ["Pune"]
    assert not choices.posts_enabled(c) and c["max_post_age_days"] == 10
    assert choices.summary(c) == "jobs"
    prefs = choices.apply_jobs({"skills": ["C"], "search_terms": ["x"], "preferred_locations": ["K"]}, c)
    assert prefs["skills"] == ["Python", "LTE"] and prefs["search_terms"] == [] and prefs["preferred_locations"] == ["Pune"]
    assert choices.apply_jobs({"skills": ["C"]}, None) == {"skills": ["C"]}


def test_old_choices_file_is_outdated(tmp_path):
    p = tmp_path / "choices.yaml"
    assert choices.is_outdated(p) is False
    p.write_text("keywords: true", encoding="utf-8")
    assert choices.is_outdated(p) is True
    choices.save({**choices.DEFAULTS}, p)
    assert choices.is_outdated(p) is False

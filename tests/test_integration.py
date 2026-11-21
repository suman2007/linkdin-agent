"""Integration tests: real Chromium against a mocked LinkedIn (no network)."""
from urllib.parse import quote

import pytest

from conftest import COMPANY_NAME, COMPANY_SLUG, OTHER_SLUGS, REGIONAL_NAME, REGIONAL_SLUG, URN_A, URN_B, URN_C

from agent import __main__ as cli
from agent import actions, browser, matcher, scraper
from agent.store import Store

CFG = {"scan_feed": True, "hashtags": ["LTE"], "scroll_rounds": 1,
       "model": "m", "max_likes_per_day": 20, "max_post_age_days": 10, "keywords": ["LTE", "4G", "eNodeB"]}


def test_scan_extracts_and_dedupes(page):
    posts = scraper.scan(page, CFG)  # feed + #LTE page return the same posts
    assert len(posts) == 5          # the non-post widget listitem is ignored
    by = {p.id: p for p in posts}
    assert by["urn:li:activity:1"].author == "Alice"
    assert "eNodeB" in by["urn:li:activity:1"].text
    assert by["urn:li:activity:3"].liked is True
    assert by["urn:li:activity:1"].liked is False
    # post without a URN link falls back to the componentkey as its id
    assert by["update-card-focusK4"].urn == ""


def test_like_clicks_once(page):
    post = scraper.Post("urn:li:activity:1", "Alice", "t", False, urn="urn:li:activity:1")
    assert scraper.like(page, post) is True
    # already-liked post stays liked, no toggle-off
    post3 = scraper.Post("urn:li:activity:3", "Carol", "t", True, urn="urn:li:activity:3")
    assert scraper.like(page, post3) is True


def test_like_without_urn_uses_card_key(page):
    post = scraper.Post("update-card-focusK4", "Dan", "t", False, key="update-card-focusK4",
                        source_url="https://www.linkedin.com/feed/")
    assert scraper.like(page, post) is True


def test_like_label_states():
    assert scraper.is_liked_label("Reaction button state: Like") is True
    assert scraper.is_liked_label("Reaction button state: no reaction") is False
    assert scraper.is_liked_label("Open reactions menu") is False
    assert scraper.is_liked_label(None) is False


def test_like_missing_button(page):
    post = scraper.Post("urn:li:activity:999", "X", "t", False, urn="urn:li:activity:999")
    assert scraper.like(page, post) is False


def test_login_detection(page):
    assert browser.is_logged_in(page) is True
    assert browser.is_checkpoint(page) is False


def test_checkpoint_detection(page):
    page.goto("https://www.linkedin.com/checkpoint/challenge")
    assert browser.is_checkpoint(page) is True


def _run(page, tmp_path, monkeypatch, approve, notify_only=False, llm=True):
    monkeypatch.setattr(matcher, "llm_match", lambda t, m: (llm, "reason"))
    asked = []
    monkeypatch.setattr(actions, "ask", lambda p, r: asked.append(p.id) or approve)
    store = Store(tmp_path / "t.db")
    pattern = matcher.build_pattern(CFG["keywords"])
    cli.run_cycle(page, CFG, store, pattern, notify_only)
    return store, asked


def test_cycle_approve_likes_only_relevant(page, tmp_path, monkeypatch):
    store, asked = _run(page, tmp_path, monkeypatch, approve=True)
    # Alice (LTE) and Dan (#LTE) pass keyword filter; Bob irrelevant; Carol already liked
    assert sorted(asked) == ["update-card-focusK4", "urn:li:activity:1"]
    assert store.likes_today() == 2
    assert store.seen("urn:li:activity:3")                 # already liked
    assert not store.seen("update-card-focusK2")           # keyword miss: not recorded, so new keywords apply later


def test_cycle_decline(page, tmp_path, monkeypatch):
    store, asked = _run(page, tmp_path, monkeypatch, approve=False)
    assert store.likes_today() == 0
    assert len(asked) == 2


def test_cycle_notify_only_never_likes(page, tmp_path, monkeypatch):
    store, asked = _run(page, tmp_path, monkeypatch, approve=True, notify_only=True)
    assert asked == [] and store.likes_today() == 0


def test_cycle_llm_rejects(page, tmp_path, monkeypatch):
    store, asked = _run(page, tmp_path, monkeypatch, approve=True, llm=False)
    assert asked == []


def test_second_cycle_does_not_reprompt(page, tmp_path, monkeypatch):
    store, asked = _run(page, tmp_path, monkeypatch, approve=False)
    asked.clear()
    cli.run_cycle(page, CFG, store, matcher.build_pattern(CFG["keywords"]), False)
    assert asked == []


def test_daily_cap(page, tmp_path, monkeypatch):
    cfg = dict(CFG, max_likes_per_day=1)
    monkeypatch.setattr(matcher, "llm_match", lambda t, m: (True, "r"))
    asked = []
    monkeypatch.setattr(actions, "ask", lambda p, r: asked.append(p.id) or True)
    store = Store(tmp_path / "t.db")
    cli.run_cycle(page, cfg, store, matcher.build_pattern(cfg["keywords"]), False)
    assert len(asked) == 1 and store.likes_today() == 1


def test_ask_prompt(monkeypatch):
    post = scraper.Post("urn:li:activity:1", "A", "text", False, urn="urn:li:activity:1")
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert actions.ask(post, "r") is True
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert actions.ask(post, "r") is False


def test_notify_only_does_not_consume_posts(page, tmp_path, monkeypatch):
    store, _ = _run(page, tmp_path, monkeypatch, approve=True, notify_only=True)
    assert not store.seen("urn:li:activity:1")          # dry run leaves matches unseen
    asked = []
    monkeypatch.setattr(actions, "ask", lambda p, r: asked.append(p.id) or False)
    cli.run_cycle(page, CFG, store, matcher.build_pattern(CFG["keywords"]), False)
    assert "urn:li:activity:1" in asked                 # the real run still prompts


LOGIN_FORM = """<html><body>
<input id="username"><input id="password" type="password">
<button type="submit" onclick="location = document.getElementById('password').value == 'good'
  ? 'https://www.linkedin.com/feed/' : 'https://www.linkedin.com/checkpoint/challenge'">Sign in</button>
</body></html>"""


def _login_page(browser_ctx):
    ctx = browser_ctx.new_context()

    def handler(route):
        url = route.request.url
        body = LOGIN_FORM if "/login" in url else "<html>feed</html>"
        route.fulfill(status=200, content_type="text/html", body=body)

    ctx.route("https://www.linkedin.com/**", handler)
    return ctx, ctx.new_page()


def test_auto_login_success(browser_ctx):
    ctx, pg = _login_page(browser_ctx)
    assert browser.auto_login(pg, "me@example.com", "good") is True
    ctx.close()


def test_auto_login_blocked_by_checkpoint(browser_ctx):
    ctx, pg = _login_page(browser_ctx)
    assert browser.auto_login(pg, "me@example.com", "wrong") is False
    ctx.close()


def test_credentials_from_env(monkeypatch):
    monkeypatch.delenv("LINKEDIN_EMAIL", raising=False)
    monkeypatch.delenv("LINKEDIN_PASSWORD", raising=False)
    assert browser.credentials() is None
    monkeypatch.setenv("LINKEDIN_EMAIL", " a@b.c ")
    assert browser.credentials() is None            # password still missing
    monkeypatch.setenv("LINKEDIN_PASSWORD", "pw")
    assert browser.credentials() == ("a@b.c", "pw")


def test_blank_credentials_mean_manual_login(monkeypatch):
    monkeypatch.setenv("LINKEDIN_EMAIL", "")
    monkeypatch.setenv("LINKEDIN_PASSWORD", "")
    assert browser.credentials() is None            # blank -> user logs in via the browser window
    monkeypatch.setenv("LINKEDIN_EMAIL", "a@b.c")
    assert browser.credentials() is None            # email without password is still manual


def test_scan_reads_post_age(page):
    by = {p.id: p for p in scraper.scan(page, CFG)}
    assert by["update-card-focusK4"].age_days == 3
    assert by["update-card-focusK5"].age_days == 90


def test_old_posts_are_filtered_before_llm(page, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(matcher, "llm_match", lambda t, m: calls.append(t) or (True, "r"))
    monkeypatch.setattr(actions, "ask", lambda p, r: True)
    store = Store(tmp_path / "t.db")
    cli.run_cycle(page, CFG, store, matcher.build_pattern(CFG["keywords"]), False)
    assert not any("Old but great" in t for t in calls)       # never sent to the model
    assert store.seen("update-card-focusK5")                  # but recorded as old


def test_no_age_limit_when_unset(page, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(matcher, "llm_match", lambda t, m: calls.append(t) or (True, "r"))
    monkeypatch.setattr(actions, "ask", lambda p, r: False)
    cfg = dict(CFG, max_post_age_days=None)
    cli.run_cycle(page, cfg, Store(tmp_path / "t.db"), matcher.build_pattern(cfg["keywords"]), False)
    assert any("Old but great" in t for t in calls)


def test_parse_age_days():
    now = 1_800_000_000.0
    assert scraper.parse_age_days("Alice  4h • Edited", now=now) == 4 / 24
    assert scraper.parse_age_days("Bob 3d •", now=now) == 3
    assert scraper.parse_age_days("Cy 2w", now=now) == 14
    assert scraper.parse_age_days("Di 6mo", now=now) == 180
    assert scraper.parse_age_days("Ed 1yr", now=now) == 365
    assert scraper.parse_age_days("no age here", now=now) is None
    # exact age from the URN timestamp beats the coarse label
    urn = "urn:li:activity:%d" % (int((now - 5 * 86400) * 1000) << 22)
    assert abs(scraper.parse_age_days("2mo", urn, now=now) - 5) < 0.01
    # implausible id (like test fixtures) falls back to the text label
    assert scraper.parse_age_days("7d", "urn:li:activity:1", now=now) == 7


def test_trace_rows_explain_every_decision(page, tmp_path, monkeypatch):
    monkeypatch.setattr(matcher, "llm_match", lambda t, m: (True, "about LTE"))
    monkeypatch.setattr(actions, "ask", lambda p, r: False)
    rows = []
    cli.run_cycle(page, CFG, Store(tmp_path / "t.db"), matcher.build_pattern(CFG["keywords"]), False, rows)
    by = {r["author"]: r for r in rows}
    assert len(rows) == 5
    assert by["Alice"]["decision"] == "skipped (by you)" and by["Alice"]["keywords"] == ["LTE", "eNodeB"]
    assert by["Alice"]["verdict"] == "about LTE"
    assert by["Bob"]["decision"] == "no keyword"
    assert by["Carol"]["decision"] == "already liked"
    assert by["Eve"]["decision"] == "too old" and by["Eve"]["age_days"] == 90


def test_dry_run_trace_and_seen_before(page, tmp_path, monkeypatch):
    monkeypatch.setattr(matcher, "llm_match", lambda t, m: (False, "no"))
    store = Store(tmp_path / "t.db")
    pat = matcher.build_pattern(CFG["keywords"])
    cli.run_cycle(page, CFG, store, pat, True, [])
    rows = []
    cli.run_cycle(page, CFG, store, pat, True, rows)
    assert any(r["decision"].startswith("seen before") for r in rows)   # AI-rejected posts are remembered


def test_report_html(tmp_path):
    from agent import report
    rows = [{"source": "https://www.linkedin.com/search/results/all/?keywords=%23lte", "author": "A<b>",
             "age_days": 0.5, "text": "x <script>", "keywords": ["LTE"], "verdict": "ok",
             "decision": "match (dry run)", "link": "https://www.linkedin.com/feed/update/urn:li:activity:1/"}]
    out = report.write(rows, {"max_post_age_days": 10, "keywords": ["LTE"], "model": "m"}, True, tmp_path / "r" / "latest.html")
    page_html = out.read_text(encoding="utf-8")
    assert "Search #lte" in page_html and "12 h" in page_html and "match (dry run)" in page_html
    assert "<script>" not in page_html.split("<tbody")[1].split("</tbody>")[0]   # post text is escaped
    assert "A&lt;b&gt;" in page_html
    assert report.source_label("https://www.linkedin.com/feed/") == "Home feed"


def test_matched_keywords_unique_and_ordered():
    pat = matcher.build_pattern(["LTE", "eNodeB"])
    assert matcher.matched_keywords("lte and LTE with ENODEB", pat) == ["lte", "ENODEB"]


def test_searches_are_scanned(page):
    cfg = dict(CFG, scan_feed=False, hashtags=[], searches=[f"{COMPANY_NAME} LTE"])
    posts = scraper.scan(page, cfg)
    assert posts and all("search/results/content" in p.source_url and quote(f"{COMPANY_NAME} LTE") in p.source_url
                         for p in posts)



OLD_PAGE = """<html><body>
<div class="feed-shared-update-v2" data-urn="__URN_A__">
  <div class="update-components-actor__title"><span aria-hidden="true">__COMPANY__</span></div>
  <span>2d &bull;</span>
  <div class="update-components-text">New LTE push-to-talk release for public safety</div>
  <button aria-label="React Like" onclick="this.setAttribute('aria-label','Unreact Like')">Like</button>
</div>
<div class="feed-shared-update-v2" data-urn="urn:li:inAppPromotion:8365"><div class="update-components-text">promo</div></div>
<div class="feed-shared-update-v2" data-urn="__URN_B__">
  <div class="update-components-actor__title"><span aria-hidden="true">__COMPANY__</span></div>
  <div class="update-components-text">Already liked 4G post</div>
  <button aria-label="Unreact Like">Like</button>
</div>
<div class="feed-shared-update-v2" data-urn="__URN_C__">
  <div class="update-components-actor__title"><span aria-hidden="true">__COMPANY__</span></div>
  <div class="update-components-text">Quarterly results are out for the full year</div>
  <button aria-label="React Like" onclick="this.setAttribute('aria-label','Unreact Like')">Like</button>
</div></body></html>""".replace("__COMPANY__", COMPANY_NAME).replace("__URN_A__", URN_A).replace("__URN_B__", URN_B).replace("__URN_C__", URN_C)


def _old_layout_page(browser_ctx):
    ctx = browser_ctx.new_context()
    ctx.route("https://www.linkedin.com/**", lambda r: r.fulfill(status=200, content_type="text/html", body=OLD_PAGE))
    return ctx, ctx.new_page()


def test_company_page_old_layout_scan(browser_ctx):
    ctx, pg = _old_layout_page(browser_ctx)
    posts = scraper.scan(pg, {"scan_feed": False, "companies": [COMPANY_SLUG], "scroll_rounds": 1})
    assert [p.id for p in posts] == [URN_A, URN_B,
                                     URN_C]
    first, second, _third = posts                # the inAppPromotion card is ignored
    assert first.origin == f"company:{COMPANY_SLUG}"
    assert first.author == COMPANY_NAME and "push-to-talk" in first.text
    assert first.liked is False and second.liked is True
    assert first.urn == first.id and first.source_url.endswith(f"/company/{COMPANY_SLUG}/posts/?feedView=all")
    ctx.close()


def test_company_page_old_layout_like(browser_ctx):
    ctx, pg = _old_layout_page(browser_ctx)
    post = scraper.Post(URN_A, COMPANY_NAME, "t", False,
                        urn=URN_A)
    assert scraper.like(pg, post) is True       # 'React Like' -> 'Unreact Like'
    ctx.close()


def test_old_layout_like_labels():
    assert scraper.is_liked_label("Unreact Like") is True
    assert scraper.is_liked_label("React Like") is False


def test_bot_check_page_is_skipped_not_bypassed(browser_ctx, capsys):
    ctx = browser_ctx.new_context()
    ctx.route("https://www.linkedin.com/**", lambda r: r.fulfill(
        status=200, content_type="text/html", body="<html><head><title>Just a moment...</title></head><body></body></html>"))
    pg = ctx.new_page()
    assert scraper.scan(pg, {"scan_feed": False, "companies": ["x"], "scroll_rounds": 1}) == []
    assert "bot-check" in capsys.readouterr().out
    ctx.close()


def test_scan_reads_connection_degree(page):
    by = {p.id: p for p in scraper.scan(page, CFG)}
    assert by["update-card-focusK2"].degree == 1          # Bob, a connection
    assert by["urn:li:activity:1"].degree == 3
    assert by["update-card-focusK4"].degree == 2


def test_parse_degree():
    assert scraper.parse_degree("Alex\n• 1st\nArchitect") == 1
    assert scraper.parse_degree("Sam likes this\nRiley\n• 2nd") == 2   # first badge = real author
    assert scraper.parse_degree("Jordan\n• 3rd+") == 3
    assert scraper.parse_degree("Follow\n5h") is None


def _cycle(page, tmp_path, monkeypatch, cfg, approve=False):
    llm_calls, asked = [], []
    monkeypatch.setattr(matcher, "llm_match", lambda t, m: llm_calls.append(t) or (True, "about LTE"))
    monkeypatch.setattr(actions, "ask", lambda p, r: asked.append((p.author, r)) or approve)
    rows = []
    cli.run_cycle(page, cfg, Store(tmp_path / "t.db"), matcher.build_pattern(cfg["keywords"]), False, rows)
    return {r["author"]: r for r in rows}, asked, llm_calls


def test_connections_rule_alone_needs_no_keyword_and_no_ai(page, tmp_path, monkeypatch):
    cfg = dict(CFG, use_keywords=False, use_connections=True)
    by, asked, llm = _cycle(page, tmp_path, monkeypatch, cfg)
    assert [a for a, _ in asked] == ["Bob"]                # pasta post by a 1st-degree connection
    assert "Posted by your connection" in asked[0][1]
    assert llm == []                                       # AI never called
    assert by["Alice"]["decision"] == "no rule matched"    # keyword post, but keywords are off
    assert by["Carol"]["decision"] == "already liked" and by["Eve"]["decision"] == "too old"


def test_keywords_or_connections(page, tmp_path, monkeypatch):
    cfg = dict(CFG, use_keywords=True, use_connections=True)
    by, asked, llm = _cycle(page, tmp_path, monkeypatch, cfg)
    assert sorted(a for a, _ in asked) == ["Alice", "Bob", "Dan"]   # Bob via connection, Alice/Dan via keywords
    assert len(llm) == 2                                   # AI only for the keyword posts, not for Bob


def test_connection_with_keyword_skips_the_ai(page, tmp_path, monkeypatch):
    cfg = dict(CFG, use_keywords=True, use_connections=True, keywords=["pasta"])
    by, asked, llm = _cycle(page, tmp_path, monkeypatch, cfg)
    assert [a for a, _ in asked] == ["Bob"] and llm == []   # already matched by the connection rule


def test_connections_off_by_default(page, tmp_path, monkeypatch):
    by, asked, _ = _cycle(page, tmp_path, monkeypatch, CFG)
    assert "Bob" not in [a for a, _ in asked]


def test_company_rule_matches_any_topic(browser_ctx, tmp_path, monkeypatch):
    ctx, pg = _old_layout_page(browser_ctx)
    cfg = {"scan_feed": False, "companies": [COMPANY_SLUG], "use_keywords": False, "use_connections": False,
           "scroll_rounds": 1, "model": "m", "max_likes_per_day": 20, "max_post_age_days": None, "keywords": ["LTE"]}
    by, asked, llm = _cycle(pg, tmp_path, monkeypatch, cfg)
    assert len(asked) == 2                                 # the two unliked posts, incl. the non-LTE results post
    assert all(f"[company] From company page {COMPANY_SLUG}" in r for _, r in asked)
    assert llm == []
    ctx.close()


def test_find_companies_parses_search_results(browser_ctx):
    base = "https://www.linkedin.com/company/"
    html_page = ("<html><body>"
                 f'<a href="{base}{COMPANY_SLUG}/">{COMPANY_NAME} </a>'
                 f'<a href="{base}{COMPANY_SLUG}/?x=1">{COMPANY_NAME}</a>'
                 f'<a href="{base}{REGIONAL_SLUG}/">{REGIONAL_NAME}</a>'
                 f'<a href="{base}{OTHER_SLUGS[0]}/events/">Events page</a>'
                 f'<a href="{base}{OTHER_SLUGS[1]}/"></a>'
                 "</body></html>")
    ctx = browser_ctx.new_context()
    ctx.route("https://www.linkedin.com/**", lambda r: r.fulfill(status=200, content_type="text/html", body=html_page))
    pg = ctx.new_page()
    assert scraper.find_companies(pg, COMPANY_NAME) == [(COMPANY_SLUG, COMPANY_NAME), (REGIONAL_SLUG, REGIONAL_NAME)]
    ctx.close()


def test_store_clear_stale_keeps_likes(tmp_path):
    s = Store(tmp_path / "t.db")
    for i, d in enumerate(["irrelevant", "old", "liked", "skipped"]):
        s.record(f"p{i}", d)
    assert s.clear_stale() == 2
    assert s.seen("p2") and s.seen("p3") and not s.seen("p0") and not s.seen("p1")


CONNECTIONS_HTML = """<html><body>
<a href="https://www.linkedin.com/in/me-self/">Me</a>
<main>
  <a href="https://www.linkedin.com/in/c1/">c1</a><a href="https://www.linkedin.com/in/c2/">c2</a>
  <a href="https://www.linkedin.com/in/c1/">c1 again</a><a href="https://www.linkedin.com/in/c3/?miniProfile=1">c3</a>
</main></body></html>"""


def _network_context(browser_ctx, visited):
    """Mock LinkedIn: a connections list, per-connection post pages, and the normal feed for anything else."""
    from conftest import page_for
    ctx = browser_ctx.new_context()

    def handler(route):
        url = route.request.url
        if "/mynetwork/invite-connect/connections" in url:
            body = CONNECTIONS_HTML
        elif "/recent-activity/all/" in url:
            visited.append(url.split("/in/")[1].split("/")[0])
            body = page_for("https://www.linkedin.com/feed/")
        else:
            body = page_for(url)
        route.fulfill(status=200, content_type="text/html", body=body)

    ctx.route("https://www.linkedin.com/**", handler)
    return ctx, ctx.new_page()


def test_store_rotation_covers_everyone(tmp_path):
    s = Store(tmp_path / "t.db")
    slugs = ["a", "b", "c", "d", "e"]
    assert s.due_connections(slugs, 2) == ["a", "b"]            # never-checked first, list order
    for x in ("a", "b"):
        s.mark_connection_checked(x)
    assert s.due_connections(slugs, 2) == ["c", "d"]
    for x in ("c", "d"):
        s.mark_connection_checked(x)
    assert s.due_connections(slugs, 2) == ["e", "a"]            # then the one checked longest ago
    assert s.due_connections(slugs, 10) == ["e", "a", "b", "c", "d"]


def test_list_connections_reads_main_only(browser_ctx):
    ctx, pg = _network_context(browser_ctx, [])
    assert scraper.list_connections(pg, rounds=1) == ["c1", "c2", "c3"]   # no duplicates, nav link excluded
    ctx.close()


def test_scan_connection_pages_marks_origin_keeps_real_degree(browser_ctx):
    ctx, pg = _network_context(browser_ctx, [])
    cfg = {"scan_feed": False, "connection_pages": ["c1"], "scroll_rounds": 1, "connection_scroll_rounds": 1}
    posts = scraper.scan(pg, cfg)
    assert posts and all(p.origin == "connection:c1" for p in posts)
    assert {p.author: p.degree for p in posts}["Alice"] == 3     # not forced to 1st: activity lists others' posts too
    assert all("/in/c1/recent-activity/all/" in p.source_url for p in posts)
    ctx.close()


def test_connection_rotation_in_cycle(browser_ctx, tmp_path, monkeypatch):
    visited, asked = [], []
    ctx, pg = _network_context(browser_ctx, visited)
    monkeypatch.setattr(actions, "ask", lambda p, r: asked.append((p.author, r)) or False)
    cfg = dict(CFG, scan_feed=False, hashtags=[], use_keywords=False, use_connections=True,
               connections_per_scan=2, connection_scroll_rounds=1)
    store = Store(tmp_path / "t.db")
    pattern = matcher.build_pattern(cfg["keywords"])
    cli.run_cycle(pg, cfg, store, pattern, False)
    assert visited == ["c1", "c2"]                                   # 2 of the 3 connections this scan
    assert [a for a, _ in asked] == ["Bob"]                          # only the post whose author badge says 1st
    cli.run_cycle(pg, cfg, store, pattern, False)
    assert visited[2:] == ["c3", "c1"]                               # next scan: never-checked c3, then oldest
    ctx.close()


def test_no_profile_visits_unless_configured(browser_ctx, tmp_path, monkeypatch):
    visited = []
    ctx, pg = _network_context(browser_ctx, visited)
    monkeypatch.setattr(actions, "ask", lambda p, r: False)
    cfg = dict(CFG, use_keywords=False, use_connections=True)         # no connections_per_scan
    cli.run_cycle(pg, cfg, Store(tmp_path / "t.db"), matcher.build_pattern(cfg["keywords"]), False)
    assert visited == []
    ctx.close()


def test_report_labels_connection_pages():
    from agent import report
    assert report.source_label("https://www.linkedin.com/in/jane-doe-123/recent-activity/all/") == "Connection jane-doe-123"


class _FlakyPage:
    """goto fails like Playwright does while LinkedIn is still redirecting, then succeeds."""

    def __init__(self, fail_times):
        from playwright.sync_api import Error
        self.fail, self.calls, self.url, self._err = fail_times, 0, "https://www.linkedin.com/feed/", Error

    def goto(self, url, **kw):
        self.calls += 1
        if self.calls <= self.fail:
            raise self._err('Page.goto: Navigation to "https://www.linkedin.com/feed/" is interrupted by another navigation to "https://www.linkedin.com/feed/"')

    def wait_for_load_state(self, *a, **kw):
        pass

    def wait_for_timeout(self, ms):
        pass


def test_is_logged_in_survives_a_redirect_in_progress():
    page = _FlakyPage(fail_times=1)
    assert browser.is_logged_in(page) is True and page.calls == 2          # retried once, then fine


def test_is_logged_in_gives_up_after_three_interruptions_but_other_errors_propagate():
    from playwright.sync_api import Error
    with pytest.raises(Error):
        browser.is_logged_in(_FlakyPage(fail_times=5))
    page = _FlakyPage(fail_times=0)
    page.goto = lambda *a, **k: (_ for _ in ()).throw(Error("net::ERR_CONNECTION_RESET"))
    with pytest.raises(Error):
        browser.is_logged_in(page)


def test_profile_lock_blocks_a_second_command_and_ignores_stale_locks(tmp_path, monkeypatch):
    import os
    lock = tmp_path / "agent.lock"
    monkeypatch.setattr(browser, "_pid_alive", lambda pid: pid == 4242)
    lock.write_text("4242")                                                  # another live command holds it
    with pytest.raises(SystemExit) as e:
        browser.acquire_lock(lock)
    assert "4242" in str(e.value)
    lock.write_text("999999")                                                # holder is gone: stale
    browser.acquire_lock(lock)
    assert lock.read_text() == str(os.getpid())
    browser.release_lock(lock)
    assert not lock.exists()
    lock.write_text("not a number")                                          # garbage is treated as stale
    browser.acquire_lock(lock)
    browser.release_lock(lock)


def test_release_lock_only_removes_its_own(tmp_path):
    lock = tmp_path / "agent.lock"
    lock.write_text("1")                                                     # someone else's
    browser.release_lock(lock)
    assert lock.exists()

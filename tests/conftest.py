"""Mock LinkedIn that mirrors the REAL feed markup (verified live, Nov 2026)."""
import os

os.environ.setdefault("AGENT_FAST", "1")   # skip the human-like pauses in new code (agent/pacing.py)

import pytest
from playwright.sync_api import sync_playwright

# Synthetic fixtures. Tests must never depend on real companies, people or post ids.
COMPANY_NAME = "Example Company"
COMPANY_SLUG = "example-company"
REGIONAL_NAME = f"{COMPANY_NAME} Regional"
REGIONAL_SLUG = f"{COMPANY_SLUG}-regional"
OTHER_SLUGS = ("sample-org", "demo-inc")
# Fake activity ids. Small numbers decode to an implausible date, so age falls back to the text label.
URN_A, URN_B, URN_C = (f"urn:li:activity:10000000000000000{i}" for i in (1, 2, 3))

NO_REACTION = "Reaction button state: no reaction"
LIKED = "Reaction button state: Like"


def card_html(key, author, text, liked=False, urn="", age="1d", degree="3rd"):
    label = LIKED if liked else NO_REACTION
    link = f'<a href="https://www.linkedin.com/feed/update/{urn}/">post</a>' if urn else ""
    return f"""
<div role="listitem" componentkey="update-card-focus{key}">
  <a href="https://www.linkedin.com/in/x/"></a>
  <a href="https://www.linkedin.com/in/x/">{author}</a>
  <span>&bull; {degree}</span>
  <span>{age} &bull; Edited</span>
  {link}
  <div data-testid="expandable-text-box">{text}</div>
  <button aria-label="{label}"
          onclick="this.setAttribute('aria-label','{LIKED}')">Like</button>
  <button aria-label="Open reactions menu">...</button>
</div>"""


# (componentkey, author, text, already_liked, urn, age, author's connection degree)
POSTS = [
    ("K1", "Alice", "Excited to share our LTE rollout and eNodeB tuning results", False, "urn:li:activity:1", "1d", "3rd"),
    ("K2", "Bob", "Best pasta recipe ever", False, "", "1d", "1st"),                       # connection, no keyword
    ("K3", "Carol", "4G coverage planning deep dive", True, "urn:li:activity:3", "1d", "1st"),  # already liked
    ("K4", "Dan", "Hiring! #LTE #5G #jobs apply now", False, "", "3d", "2nd"),             # no URN link
    ("K5", "Eve", "Old but great LTE deep dive", False, "", "3mo", "1st"),                  # too old
]
WIDGET = '<div role="listitem" componentkey="some-uuid-widget">Start a post</div>'


def page_for(url):
    if "/feed/update/" in url:
        urn = url.rstrip("/").split("/")[-1]
        cards = [card_html(*p) for p in POSTS if p[4] == urn]
    else:
        cards = [WIDGET] + [card_html(*p) for p in POSTS]
    return f"<html><body>{''.join(cards)}</body></html>"


@pytest.fixture(scope="session")
def browser_ctx():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser_ctx):
    ctx = browser_ctx.new_context()

    def handler(route):
        url = route.request.url
        body = "<html>login</html>" if "login" in url else page_for(url)
        route.fulfill(status=200, content_type="text/html", body=body)

    ctx.route("https://www.linkedin.com/**", handler)
    pg = ctx.new_page()
    yield pg
    ctx.close()

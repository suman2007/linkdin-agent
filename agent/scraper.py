"""Read-only scanning of the feed and hashtag pages, plus the single Like click.

All LinkedIn DOM selectors live here; LinkedIn changes them often.
Selectors verified against the live feed (Nov 2026): posts are
[role=listitem][componentkey^=update-card-focus]; there is no data-urn attribute.
"""
import random
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

from playwright.sync_api import Page

# Two layouts exist: the new feed/search cards, and the older cards still used on company pages.
CARD_SEL = ('[role="listitem"][componentkey^="update-card-focus"], '
            'div.feed-shared-update-v2[data-urn^="urn:li:activity"]')
TEXT_SEL = '[data-testid="expandable-text-box"], .update-components-text'
AUTHOR_FIRST_SEL = '.update-components-actor__title span[aria-hidden="true"], .update-components-actor__name'
AUTHOR_SEL = 'a[href*="/in/"], a[href*="/company/"], a[href*="/school/"]'
LIKE_SEL = ('button[aria-label^="Reaction button state"], '
            'button[aria-label="React Like"], button[aria-label="Unreact Like"]')
URN_LINK_SEL = 'a[href*="/feed/update/urn:li:"]'
URN_RE = re.compile(r"/feed/update/(urn:li:[A-Za-z]+:\d+)")


@dataclass
class Post:
    id: str            # URN when the card links to one, else the componentkey
    author: str
    text: str
    liked: bool
    key: str = ""      # componentkey, used to find the card again on the source page
    urn: str = ""
    source_url: str = ""
    age_days: float | None = None   # None when the age could not be determined
    degree: int | None = None       # author's connection degree: 1, 2 or 3 (None if not shown)
    origin: str = ""                # "company:<slug>" when found on that company's page

    @property
    def url(self) -> str:
        if self.urn:
            return f"https://www.linkedin.com/feed/update/{self.urn}/"
        return self.source_url


AGE_RE = re.compile(r"(?<![A-Za-z0-9])(\d+)\s?(m|h|d|w|mo|yr)(?![A-Za-z0-9])")
_UNIT_DAYS = {"m": 1 / 1440, "h": 1 / 24, "d": 1, "w": 7, "mo": 30, "yr": 365}


def parse_age_days(header: str, urn: str = "", now: float | None = None) -> float | None:
    """Post age in days. Exact from the URN's embedded timestamp when plausible,
    else from LinkedIn's relative label ('3d', '2w', '6mo') in the card header.
    Note '1w' means 7-13 days, so it counts as 7."""
    now = now if now is not None else time.time()
    if urn:
        ts_ms = int(urn.rsplit(":", 1)[-1]) >> 22      # LinkedIn ids embed ms-since-epoch
        if 1.2e12 < ts_ms < (now + 2 * 86400) * 1000:
            return max(0.0, (now - ts_ms / 1000) / 86400)
    m = AGE_RE.search(header)
    return int(m.group(1)) * _UNIT_DAYS[m.group(2)] if m else None


DEGREE_RE = re.compile(r"[•·]\s*(1st|2nd|3rd)")


def parse_degree(header: str) -> int | None:
    """Degree badge ('• 1st') right after the author's name. In 'X likes this' cards the first badge
    belongs to the real author, not to X, so the first badge is the right one."""
    m = DEGREE_RE.search(header)
    return int(m.group(1)[0]) if m else None


def is_liked_label(label: str | None) -> bool:
    """New layout: 'Reaction button state: no reaction' = not liked, any other state = liked.
    Old layout (company pages): 'React Like' = not liked, 'Unreact Like' = liked."""
    if not label:
        return False
    if label.startswith("Reaction button state"):
        return "no reaction" not in label
    return label.startswith("Unreact")


def _author(card) -> str:
    first = card.query_selector(AUTHOR_FIRST_SEL)
    if first and first.inner_text().strip():
        return first.inner_text().strip().split("\n")[0]
    for a in card.query_selector_all(AUTHOR_SEL):
        name = a.inner_text().strip().split("\n")[0]
        if name:
            return name
    return "?"


def _parse_card(card, source_url: str) -> Post | None:
    data_urn = card.get_attribute("data-urn") or ""        # old layout carries the URN directly
    key = card.get_attribute("componentkey") or data_urn
    link = card.query_selector(URN_LINK_SEL)
    m = URN_RE.search(link.get_attribute("href") or "") if link else None
    urn = m.group(1) if m else (data_urn if data_urn.startswith("urn:li:activity") else "")
    pid = urn or key
    if not pid:
        return None
    text_el = card.query_selector(TEXT_SEL)
    like_el = card.query_selector(LIKE_SEL)
    header = card.inner_text()
    return Post(
        id=pid,
        author=_author(card),
        text=(text_el.inner_text().strip() if text_el else ""),
        liked=is_liked_label(like_el.get_attribute("aria-label") if like_el else None),
        key=key, urn=urn, source_url=source_url,
        age_days=parse_age_days(header[:400], urn),
        degree=parse_degree(header[:500]),
    )


def _scan_page(page: Page, scroll_rounds: int) -> list[Post]:
    """Read the cards, scroll, read again. The feed scrolls inside <main>, not the window, so a plain
    window scroll loads nothing: bring the last card into view (what a person scrolling does), and
    read after every step because LinkedIn may drop cards that have scrolled far out of view."""
    found: dict[str, Post] = {}
    for _ in range(scroll_rounds + 1):
        cards = page.query_selector_all(CARD_SEL)
        for card in cards:
            post = _parse_card(card, page.url)
            if post:
                found.setdefault(post.id, post)
        if cards:
            try:
                cards[-1].scroll_into_view_if_needed(timeout=5000)
            except Exception:       # card was replaced while scrolling; the wheel below still moves things
                pass
        page.mouse.move(640, 450)
        page.mouse.wheel(0, random.randint(500, 900))
        page.wait_for_timeout(random.randint(1500, 3500))
    return list(found.values())


def scan(page: Page, cfg: dict) -> list[Post]:
    targets: list[tuple[str, str]] = []              # (url, origin)
    if cfg.get("scan_feed", True):
        targets.append(("https://www.linkedin.com/feed/", ""))
    for tag in cfg.get("hashtags") or []:
        targets.append((f"https://www.linkedin.com/feed/hashtag/{tag.lower()}/", ""))
    for slug in cfg.get("companies") or []:          # company page posts (needs a visible browser window)
        targets.append((f"https://www.linkedin.com/company/{slug}/posts/?feedView=all", f"company:{slug}"))
    for q in cfg.get("searches") or []:              # LinkedIn post search, newest first
        targets.append(("https://www.linkedin.com/search/results/content/?keywords="
                        + quote(q) + "&sortBy=%22date_posted%22", ""))
    for slug in cfg.get("connection_pages") or []:   # a connection's activity page
        targets.append((f"https://www.linkedin.com/in/{slug}/recent-activity/all/", f"connection:{slug}"))
    found: dict[str, Post] = {}
    for url, origin in targets:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(random.randint(2500, 4500))
        if "just a moment" in page.title().lower():   # bot-check page; never try to bypass it
            print(f"Skipped {url}: LinkedIn showed a bot-check page (try --show / a visible window).")
            continue
        is_conn = origin.startswith("connection:")
        rounds = cfg.get("connection_scroll_rounds", 2) if is_conn else cfg.get("scroll_rounds", 4)
        for post in _scan_page(page, rounds):
            post.origin = origin       # note: degree is NOT forced; "All activity" also lists posts a
            if post.id in found and origin and not found[post.id].origin:   # connection only liked/commented on
                found[post.id].origin = origin       # seen earlier in the feed, but also found via this source
            found.setdefault(post.id, post)
    return list(found.values())


PROFILE_HREF = re.compile(r"/in/([^/?#]+)/?$")


def list_connections(page: Page, rounds: int = 10) -> list[str]:
    """Profile slugs from your Connections page (most recently added first). Only links inside <main>,
    so your own profile link in the top bar is not included."""
    page.goto("https://www.linkedin.com/mynetwork/invite-connect/connections/", wait_until="domcontentloaded")
    page.wait_for_timeout(random.randint(3000, 5000))
    if "just a moment" in page.title().lower():
        print("Connections page showed a bot-check; skipping connection posts this scan.")
        return []
    js = ("() => { const root = document.querySelector('main') || document;"
          " return [...root.querySelectorAll('a[href*=\"/in/\"]')].map(a => a.getAttribute('href').split('?')[0]); }")
    slugs: list[str] = []
    for _ in range(rounds + 1):
        for href in page.evaluate(js):
            m = PROFILE_HREF.search(href)
            if m and m.group(1) not in slugs:
                slugs.append(m.group(1))
        page.mouse.move(640, 450)
        page.mouse.wheel(0, random.randint(900, 1400))
        page.wait_for_timeout(random.randint(1200, 2200))
    return slugs


COMPANY_HREF = re.compile(r"linkedin\.com/company/([^/?#]+)/?$")


def find_companies(page: Page, name: str, limit: int = 3) -> list[tuple[str, str]]:
    """Look a company up by name via LinkedIn's company search. Returns [(slug, display_name), ...]."""
    page.goto("https://www.linkedin.com/search/results/companies/?keywords=" + quote(name),
              wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    js = ("() => [...document.querySelectorAll('a[href*=\"/company/\"]')].map(a => "
          "[a.getAttribute('href').split('?')[0], a.innerText.trim().split(String.fromCharCode(10))[0]])")
    out: list[tuple[str, str]] = []
    for href, text in page.evaluate(js):
        m = COMPANY_HREF.search(href)
        if m and text.strip() and m.group(1) not in [s for s, _ in out]:
            out.append((m.group(1), text.strip()))
        if len(out) >= limit:
            break
    return out


def _click_like(page: Page, scope) -> bool:
    btn = scope.query_selector(LIKE_SEL)
    if not btn:
        return False
    if not is_liked_label(btn.get_attribute("aria-label")):
        btn.click()
        page.wait_for_timeout(1500)
    return is_liked_label(btn.get_attribute("aria-label"))


def like(page: Page, post: Post, max_scrolls: int = 8) -> bool:
    """Click Like once on this post. Returns True if it is now liked (never un-likes)."""
    if post.urn:  # dedicated post page: the first reaction button is the post's own
        page.goto(post.url, wait_until="domcontentloaded")
        page.wait_for_timeout(random.randint(2000, 3500))
        return _click_like(page, page)
    # no URN link: find the same card on the page it was scanned from
    page.goto(post.source_url, wait_until="domcontentloaded")
    page.wait_for_timeout(random.randint(2500, 4000))
    for _ in range(max_scrolls):
        for card in page.query_selector_all(CARD_SEL):
            if post.key in (card.get_attribute("componentkey"), card.get_attribute("data-urn")):
                return _click_like(page, card)
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(1500)
    return False

"""Persistent Chromium profile. The saved session is reused; optional auto-login uses .env credentials."""
import os
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright

PROFILE_DIR = Path(__file__).resolve().parent.parent / ".profile"


def launch(p: Playwright, headless: bool = False) -> BrowserContext:
    return p.chromium.launch_persistent_context(
        str(PROFILE_DIR), headless=headless, viewport={"width": 1280, "height": 900}
    )


def is_logged_in(page: Page) -> bool:
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    return "/feed" in page.url and "login" not in page.url


def is_checkpoint(page: Page) -> bool:
    return any(s in page.url for s in ("checkpoint", "challenge", "authwall", "login"))


def credentials() -> tuple[str, str] | None:
    """LinkedIn credentials from the environment (loaded from the gitignored .env). Never logged."""
    email = os.environ.get("LINKEDIN_EMAIL", "").strip()
    password = os.environ.get("LINKEDIN_PASSWORD", "")
    return (email, password) if email and password else None


def auto_login(page: Page, email: str, password: str) -> bool:
    """Fill and submit LinkedIn's login form. Returns False if a 2FA/CAPTCHA/checkpoint blocks it."""
    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    if page.query_selector("#username") is None:   # already signed in (redirected away)
        return is_logged_in(page)
    page.fill("#username", email)
    page.fill("#password", password)
    page.click('button[type="submit"]')
    page.wait_for_timeout(5000)
    return "/feed" in page.url and "login" not in page.url and "checkpoint" not in page.url

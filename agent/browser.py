"""Persistent Chromium profile. The saved session is reused; optional auto-login uses .env credentials."""
import atexit
import os
import subprocess
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright
from playwright.sync_api import Error as PlaywrightError

PROFILE_DIR = Path(__file__).resolve().parent.parent / ".profile"
LOCK_PATH = Path(__file__).resolve().parent.parent / ".agent.lock"


def _pid_alive(pid: int) -> bool:
    """Is a process with this id running? (Never signals it: on Windows os.kill would terminate it.)"""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, timeout=15).stdout
            return str(pid) in out.split()
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def acquire_lock(path: Path = LOCK_PATH) -> None:
    """Two browsers on one profile corrupt each other's navigation, so only one agent command may hold it."""
    if path.exists():
        try:
            pid = int(path.read_text().strip())
        except ValueError:
            pid = 0
        if pid and pid != os.getpid() and _pid_alive(pid):
            raise SystemExit(f"Another agent command (process {pid}) is using the browser profile. "
                             "Wait for it to finish, then run this again.")
    path.write_text(str(os.getpid()))
    atexit.register(release_lock, path)


def release_lock(path: Path = LOCK_PATH) -> None:
    try:
        if path.exists() and path.read_text().strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


def launch(p: Playwright, headless: bool = False) -> BrowserContext:
    acquire_lock()
    return p.chromium.launch_persistent_context(
        str(PROFILE_DIR), headless=headless, viewport={"width": 1280, "height": 900}
    )


def is_logged_in(page: Page) -> bool:
    """Open the feed and see whether LinkedIn lets us stay. Right after a manual login LinkedIn may still be
    redirecting to the feed, which makes our own navigation fail as 'interrupted': wait for it and retry."""
    for attempt in range(3):
        try:
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            break
        except PlaywrightError as e:
            if "interrupted by another navigation" not in str(e) or attempt == 2:
                raise
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1500)
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

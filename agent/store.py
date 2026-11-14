"""SQLite log of processed posts and likes (dedupe + daily cap)."""
import sqlite3
import time
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "seen.db"


class Store:
    def __init__(self, path: Path = DB_PATH):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS posts ("
            "urn TEXT PRIMARY KEY, decision TEXT, day TEXT)"
        )
        self.db.commit()

    def due_connections(self, slugs: list[str], n: int) -> list[str]:
        """The n connections checked longest ago (never-checked first). Rotating through them spreads the
        profile visits over several scans instead of hitting everyone at once."""
        self.db.execute("CREATE TABLE IF NOT EXISTS connections (slug TEXT PRIMARY KEY, last_checked REAL)")
        last = dict(self.db.execute("SELECT slug, last_checked FROM connections").fetchall())
        order = sorted(range(len(slugs)), key=lambda i: (last.get(slugs[i], -1.0), i))
        return [slugs[i] for i in order[:n]]

    def mark_connection_checked(self, slug: str) -> None:
        self.db.execute("CREATE TABLE IF NOT EXISTS connections (slug TEXT PRIMARY KEY, last_checked REAL)")
        self.db.execute("INSERT OR REPLACE INTO connections VALUES (?, ?)", (slug, time.time()))
        self.db.commit()

    def seen(self, urn: str) -> bool:
        return self.db.execute("SELECT 1 FROM posts WHERE urn=?", (urn,)).fetchone() is not None

    def decision(self, urn: str) -> str | None:
        row = self.db.execute("SELECT decision FROM posts WHERE urn=?", (urn,)).fetchone()
        return row[0] if row else None

    def record(self, urn: str, decision: str) -> None:
        """decision: 'irrelevant' | 'skipped' | 'liked' | 'notified'"""
        self.db.execute(
            "INSERT OR REPLACE INTO posts VALUES (?,?,?)",
            (urn, decision, date.today().isoformat()),
        )
        self.db.commit()

    def clear_stale(self) -> int:
        """Forget 'irrelevant' and 'old' verdicts (made under earlier rules). Likes/skips are kept."""
        n = self.db.execute("DELETE FROM posts WHERE decision IN ('irrelevant','old')").rowcount
        self.db.commit()
        return n

    def likes_today(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM posts WHERE decision='liked' AND day=?",
            (date.today().isoformat(),),
        ).fetchone()[0]

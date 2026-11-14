"""Human approval prompt."""
import webbrowser

from .scraper import Post


def ask(post: Post, reason: str) -> bool:
    """Return True to like, False to skip."""
    print("\n" + "=" * 70)
    print(f"Author: {post.author}\nWhy:    {reason}\nLink:   {post.url}")
    print("-" * 70)
    print(post.text[:500] + ("..." if len(post.text) > 500 else ""))
    while True:
        ans = input("Like this post? [y]es / [n]o / [o]pen link: ").strip().lower()
        if ans == "o":
            webbrowser.open(post.url)
        elif ans in ("y", "yes"):
            return True
        elif ans in ("n", "no", ""):
            return False

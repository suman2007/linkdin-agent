"""HTML scan report: shows, for every post the agent looked at, how it was found and what was decided."""
import html
import time
import webbrowser
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

_CLASS = [("match", "ok"), ("liked", "ok"), ("AI rejected", "warn"), ("skipped", "warn"),
          ("daily cap", "warn")]  # everything else is shown as muted


def source_label(url: str) -> str:
    if "/recent-activity/" in url:
        return "Connection " + url.split("/in/")[1].split("/")[0][:22]
    q = parse_qs(urlparse(url).query)
    if "keywords" in q:
        return "Search " + unquote(q["keywords"][0])
    return "Home feed" if "/feed" in url else (url[:40] or "?")


def _cls(decision: str) -> str:
    return next((c for prefix, c in _CLASS if decision.startswith(prefix)), "mute")


def _age(a) -> str:
    return "?" if a is None else (f"{a * 24:.0f} h" if a < 1 else f"{a:.1f} d")


def render(rows: list[dict], meta: dict) -> str:
    esc = html.escape
    by_dec = Counter(r["decision"].split(" (")[0] for r in rows)
    by_src = Counter(source_label(r["source"]) for r in rows)
    pills = "".join(f'<span class="pill {_cls(d)}">{esc(d)}: {n}</span>' for d, n in by_dec.most_common())
    srcs = "".join(f'<span class="pill mute">{esc(s)}: {n} posts</span>' for s, n in by_src.items())
    body = []
    for r in rows:
        chips = "".join(f'<span class="chip">{esc(k)}</span>' for k in r["keywords"]) or "-"
        link = f'<a href="{esc(r["link"])}" target="_blank" rel="noopener">open</a>' if r["link"] else ""
        body.append(
            f'<tr data-d="{esc(r["decision"].split(" (")[0])}">'
            f'<td>{esc(source_label(r["source"]))}</td><td>{esc(r["author"])}</td>'
            f'<td class="num">{_age(r["age_days"])}</td><td>{chips}</td>'
            f'<td class="txt">{esc(r["text"])}</td><td>{esc(r["verdict"]) or "-"}</td>'
            f'<td><span class="pill {_cls(r["decision"])}">{esc(r["decision"])}</span></td><td>{link}</td></tr>')
    opts = "".join(f"<option>{esc(d)}</option>" for d in by_dec)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Scan Report</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--mute:#6b7280;--line:#e5e7eb;--ok:#15803d;--warn:#b45309;--chip:#eef2ff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111418;--fg:#e8eaed;--mute:#9aa0a6;--line:#2a2f36;--ok:#4ade80;--warn:#fbbf24;--chip:#1e2433}}}}
body{{background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif;margin:0;padding:16px}}
h1{{font-size:20px;margin:0 0 4px}}.sub{{color:var(--mute);margin-bottom:12px}}
.pill{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 10px;margin:0 6px 6px 0;font-size:12px}}
.pill.ok{{color:var(--ok);border-color:var(--ok)}}.pill.warn{{color:var(--warn);border-color:var(--warn)}}.pill.mute{{color:var(--mute)}}
.chip{{background:var(--chip);border-radius:4px;padding:1px 6px;margin-right:4px;font-size:12px;white-space:nowrap}}
.wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:900px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mute);font-weight:600;font-size:12px}}.num{{white-space:nowrap}}.txt{{max-width:380px;color:var(--mute)}}
select{{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px}}
a{{color:inherit}}
</style></head><body>
<h1>Scan report</h1>
<div class="sub">{esc(meta["when"])} &middot; mode: {esc(meta["mode"])} &middot; rules: {esc(meta.get("rules", ""))} &middot; max age {esc(str(meta["max_age"]))} days &middot; {meta["n_keywords"]} keywords &middot; model {esc(meta["model"])}</div>
<div>{srcs}</div><div>{pills}</div>
<p>Show: <select id="f"><option value="">all decisions</option>{opts}</select></p>
<div class="wrap"><table><thead><tr><th>Found on</th><th>Author</th><th>Age</th><th>Keywords hit</th><th>Post text</th><th>AI verdict</th><th>Decision</th><th></th></tr></thead>
<tbody id="b">{"".join(body)}</tbody></table></div>
<script>document.getElementById('f').onchange=function(e){{var v=e.target.value;
document.querySelectorAll('#b tr').forEach(function(r){{r.style.display=(!v||r.dataset.d===v)?'':'none'}})}}</script>
</body></html>"""


def write(rows: list[dict], cfg: dict, notify_only: bool, path: Path, open_it: bool = False) -> Path:
    rules = []
    if cfg.get("companies"):
        rules.append("companies: " + ", ".join(cfg["companies"]))
    if cfg.get("use_connections"):
        rules.append("connections")
    if cfg.get("use_keywords", True):
        rules.append("keywords")
    meta = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "dry run" if notify_only else "interactive",
            "rules": " OR ".join(rules),
            "max_age": cfg.get("max_post_age_days"), "n_keywords": len(cfg.get("keywords", [])),
            "model": cfg.get("model", "?")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(rows, meta), encoding="utf-8")
    if open_it:
        webbrowser.open(path.resolve().as_uri())
    return path

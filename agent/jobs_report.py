"""HTML job report: recommended jobs ranked by location preference and AI score, plus why others were left out."""
import html
import time
import webbrowser
from collections import Counter
from pathlib import Path

TIER_LABEL = {1: "Preferred / Remote", 2: "Elsewhere in country"}


def _age(days, max_age) -> str:
    """Cards you have already viewed show 'Viewed' instead of a posting age. LinkedIn's date filter still
    applied to them, so they are within the limit."""
    if days is None:
        return f"≤{max_age:g} d"
    return f"{days * 24:.0f} h" if days < 1 else f"{days:.0f} d"


def render(jobs: list, meta: dict) -> str:
    esc = html.escape
    by_dec = Counter(j.decision for j in jobs)
    rec = [j for j in jobs if j.decision == "recommended"]
    tiers = Counter(j.tier for j in rec)
    pills = "".join(f'<span class="pill {"ok" if d == "recommended" else "mute"}">{esc(d)}: {n}</span>'
                    for d, n in by_dec.most_common())
    tier_pills = "".join(f'<span class="pill ok">{esc(TIER_LABEL[t])}: {tiers.get(t, 0)}</span>' for t in (1, 2))
    rows = []
    for j in jobs:
        skills = "".join(f'<span class="chip">{esc(s)}</span>' for s in j.skills) or "-"
        new = "" if j.seen_before else ' <span class="pill ok">new</span>'
        where = esc(j.location) + (f" ({esc(j.workplace)})" if j.workplace else "")
        rows.append(
            f'<tr data-d="{esc(j.decision)}"><td>{esc(TIER_LABEL[j.tier])}</td>'
            f'<td class="num">{"-" if j.score is None else j.score}</td>'
            f'<td><a href="{esc(j.url)}" target="_blank" rel="noopener">{esc(j.title)}</a>'
            f'{new if j.decision == "recommended" else ""}</td>'
            f'<td>{esc(j.company)}</td><td>{where}</td><td class="num">{_age(j.posted_days, meta["max_age_days"])}</td>'
            f'<td>{skills}</td><td class="num">{"-" if j.years is None else f"{j.years:g}+"}</td>'
            f'<td class="txt">{esc(j.reason) or "-"}</td>'
            f'<td><span class="pill {"ok" if j.decision == "recommended" else "mute"}">{esc(j.decision)}</span></td></tr>')
    opts = "".join(f'<option value="{esc(d)}"{" selected" if d == "recommended" else ""}>{esc(d)}</option>'
                   for d in by_dec)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Job Report</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--mute:#6b7280;--line:#e5e7eb;--ok:#15803d;--chip:#eef2ff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111418;--fg:#e8eaed;--mute:#9aa0a6;--line:#2a2f36;--ok:#4ade80;--chip:#1e2433}}}}
body{{background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif;margin:0;padding:16px}}
h1{{font-size:20px;margin:0 0 4px}}.sub{{color:var(--mute);margin-bottom:12px}}
.pill{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 10px;margin:0 6px 6px 0;font-size:12px}}
.pill.ok{{color:var(--ok);border-color:var(--ok)}}.pill.mute{{color:var(--mute)}}
.chip{{background:var(--chip);border-radius:4px;padding:1px 6px;margin:0 4px 4px 0;font-size:12px;white-space:nowrap;display:inline-block}}
.wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:1000px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mute);font-weight:600;font-size:12px}}.num{{white-space:nowrap}}.txt{{max-width:300px;color:var(--mute)}}
select{{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px}}a{{color:inherit}}
</style></head><body>
<h1>Job report</h1>
<div class="sub">{esc(meta["when"])} &middot; posted within {esc(str(meta["max_age_days"]))} days &middot; {esc(meta["skills"])} &middot; min score {esc(str(meta["min_score"]))} &middot; applied jobs excluded</div>
<div>{tier_pills}</div><div>{pills}</div>
<p>Show: <select id="f"><option value="">everything</option>{opts}</select></p>
<div class="wrap"><table><thead><tr><th>Location tier</th><th>Score</th><th>Job</th><th>Company</th><th>Location</th><th>Posted</th><th>Skills matched</th><th>Years</th><th>Why</th><th>Decision</th></tr></thead>
<tbody id="b">{"".join(rows)}</tbody></table></div>
<script>function ap(){{var v=document.getElementById('f').value;
document.querySelectorAll('#b tr').forEach(function(r){{r.style.display=(!v||r.dataset.d===v)?'':'none'}})}}
document.getElementById('f').onchange=ap;ap();</script>
</body></html>"""


def write(jobs: list, prefs: dict, path: Path, open_it: bool = False) -> Path:
    meta = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "max_age_days": prefs["max_age_days"],
            "skills": ", ".join(prefs["skills"]), "min_score": prefs["min_score"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(jobs, meta), encoding="utf-8")
    if open_it:
        webbrowser.open(path.resolve().as_uri())
    return path

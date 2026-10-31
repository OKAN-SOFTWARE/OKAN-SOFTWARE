#!/usr/bin/env python3
"""Refresh Ember-themed public commit ticker and recent original project cards."""
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import sys
import textwrap
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
START, END = "<!-- SHOWCASE:START -->", "<!-- SHOWCASE:END -->"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
AMBER = "#ffb56f"
BG = "#101b1c"


def api_get(path, token):
    request = Request(
        "https://api.github.com" + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "User-Agent": "okan-ember-profile",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError("GitHub data unavailable; keeping the previous showcase") from error


def original_projects(user, token, limit=6):
    """Select the most recently pushed public original repositories, not forks."""
    repos = []
    for page in range(1, 4):
        batch = api_get(
            "/users/" + quote(user, safe="")
            + "/repos?type=owner&sort=pushed&direction=desc&per_page=100&page="
            + str(page), token
        )
        if not isinstance(batch, list):
            raise ValueError("Invalid repository listing")
        for item in batch:
            name = item.get("name", "")
            owner = (item.get("owner") or {}).get("login", "")
            if (not SAFE_NAME.fullmatch(name)
                    or owner.lower() != user.lower()
                    or name.lower() == user.lower()
                    or item.get("fork") or item.get("private") or item.get("archived")
                    or not item.get("pushed_at")):
                continue
            repos.append(item)
        if len(batch) < 100:
            break
    repos.sort(key=lambda item: (item["pushed_at"], item["name"]), reverse=True)
    return repos[:limit]


def code_languages(repo, token, limit=3):
    full = repo["full_name"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full):
        raise ValueError("Unsafe repository name")
    result = api_get("/repos/" + full + "/languages", token)
    if not isinstance(result, dict):
        raise ValueError("Invalid language response")
    # GitHub Linguist returns source languages; exclude data/document formats.
    excluded = {"JSON", "Markdown", "XML", "YAML", "TOML", "Text", "Jupyter Notebook"}
    items = [(lang, size) for lang, size in result.items()
             if isinstance(lang, str) and isinstance(size, int) and size > 0
             and lang not in excluded and len(lang) <= 24]
    items.sort(key=lambda pair: (-pair[1], pair[0]))
    return [lang for lang, _ in items[:limit]] or ["Source code"]


def recent_commits(user, token, limit=9):
    """Public authored commits across original repositories and upstream PR branches."""
    query = quote("author:" + user, safe="")
    result = api_get(
        "/search/commits?q=" + query
        + "&sort=committer-date&order=desc&per_page=100", token
    )
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise ValueError("Invalid commit search result")
    commits, seen = [], set()
    for item in result["items"]:
        repository = (item.get("repository") or {}).get("full_name", "")
        sha = item.get("sha", "")
        message = ((item.get("commit") or {}).get("message") or "").splitlines()[0]
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
                or not re.fullmatch(r"[a-f0-9]{40}", sha)
                or repository.lower() == (user + "/" + user).lower()
                or sha in seen or not message
                or message.startswith(("Merge pull request", "Add files via upload",
                                       "Delete ", "Create "))):
            continue
        seen.add(sha)
        commits.append({
            "repo": repository,
            "sha": sha[:7],
            "message": " ".join(message.split())[:90],
        })
        if len(commits) >= limit:
            break
    if not commits:
        raise ValueError("No public authored commits found; keeping existing ticker")
    return commits


def render_ticker(commits):
    """A self-contained animated SVG; GitHub README does not execute JavaScript."""
    width, height, step = 1200, 132, 404
    period = step * len(commits)
    cards = []
    for index, item in enumerate(commits):
        x = index * step + 5
        name = html.escape(item["repo"][:37])
        message = html.escape(item["message"][:43])
        sha = html.escape(item["sha"])
        cards.append(
            f'<rect x="{x}" y="52" width="392" height="62" rx="10" '
            f'fill="#172625" stroke="#6d4934"/>'
            f'<rect x="{x+12}" y="63" width="4" height="39" rx="2" fill="{AMBER}"/>'
            f'<text x="{x+25}" y="74" class="repo">{name}</text>'
            f'<text x="{x+25}" y="96" class="msg">{message}</text>'
            f'<text x="{x+322}" y="74" class="sha">{sha}</text>'
        )
    inner = "".join(cards)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Scrolling recent authored GitHub commits">
<title>Latest public commits from original projects and upstream contributions</title>
<defs><clipPath id="viewport"><rect x="0" y="45" width="{width}" height="78"/></clipPath></defs>
<style>
.repo{{fill:#ffbd82;font:600 13px ui-monospace,SFMono-Regular,Consolas,monospace}}
.msg{{fill:#e2e5dd;font:12px ui-monospace,SFMono-Regular,Consolas,monospace}}
.sha{{fill:#bda7d5;font:11px ui-monospace,SFMono-Regular,Consolas,monospace}}
.head{{fill:#ffb56f;font:700 15px ui-monospace,SFMono-Regular,Consolas,monospace}}
.sub{{fill:#b5aaa0;font:11px ui-monospace,SFMono-Regular,Consolas,monospace}}
@keyframes glide{{from{{transform:translateX(0px)}}to{{transform:translateX(-{period}px)}}}}
.moving{{animation:glide {max(50, len(commits)*8)}s linear infinite}}
@media(prefers-reduced-motion:reduce){{.moving{{animation:none}}}}
</style>
<rect width="{width}" height="{height}" rx="13" fill="{BG}" stroke="#a36539" stroke-width="2"/>
<circle cx="20" cy="23" r="5" fill="#ff9d57"/>
<text x="34" y="29" class="head">LATEST COMMITS</text>
<text x="225" y="28" class="sub">ORIGINAL PROJECTS + UPSTREAM CONTRIBUTIONS</text>
<path d="M14 42 H1186" stroke="#574333"/>
<g clip-path="url(#viewport)"><g class="moving"><g>{inner}</g><g transform="translate({period} 0)">{inner}</g></g></g>
<text x="16" y="128" class="sub">PUBLIC COMMIT HISTORY  /  AUTO-REFRESHED</text>
</svg>
'''


def card_svg(repo, languages):
    """Readable, GitHub-compatible Ember project card with max three code tags."""
    name = repo["name"]
    desc = " ".join(str(repo.get("description") or "Open-source software project").split())
    lines = textwrap.wrap(desc, width=64, break_long_words=False)[:2]
    date = datetime.fromisoformat(repo["pushed_at"].replace("Z", "+00:00"))
    updated = date.strftime("%b %d, %Y").upper()
    tag_svg, x = [], 28
    for lang in languages[:3]:
        tag = html.escape(lang)
        tag_width = min(164, max(73, len(lang)*9+24))
        tag_svg.append(
            f'<rect x="{x}" y="216" width="{tag_width}" height="29" rx="7" '
            f'fill="#38291e" stroke="#8e603f"/>'
            f'<text x="{x+12}" y="235" class="tag">{tag}</text>'
        )
        x += tag_width + 9
    desc_svg = "".join(
        f'<text x="28" y="{155 + i*21}" class="desc">{html.escape(line)}</text>'
        for i, line in enumerate(lines)
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="580" height="265" viewBox="0 0 580 265" role="img" aria-label="{html.escape(name)} project">
<style>
.name{{fill:#fff1e2;font:700 28px ui-sans-serif,system-ui,Arial,sans-serif}}
.kicker{{fill:#ffad68;font:700 12px ui-monospace,Consolas,monospace;letter-spacing:1.5px}}
.date{{fill:#d4b69e;font:12px ui-monospace,Consolas,monospace}}
.desc{{fill:#c8cbc2;font:15px ui-sans-serif,system-ui,Arial,sans-serif}}
.tag{{fill:#ffcb9d;font:12px ui-monospace,Consolas,monospace}}
</style>
<rect width="580" height="265" rx="16" fill="#131f20" stroke="#87583c" stroke-width="2"/>
<path d="M18 18 V48 M18 18 H48" stroke="#ffad68" stroke-width="3" stroke-linecap="round"/>
<text x="28" y="53" class="kicker">ORIGINAL PROJECT</text>
<text x="28" y="104" class="name">{html.escape(name[:29])}</text>
<text x="28" y="126" class="date">LAST PUSH  {updated}</text>
{desc_svg}
{''.join(tag_svg)}
<path d="M534 34 h17 m-17 0 v17 m0 0 17-17" stroke="#ffad68" stroke-width="2" fill="none"/>
</svg>
'''


def showcase_markdown(repos):
    cells = []
    for repo in repos:
        name = repo["name"]
        url = "https://github.com/" + repo["full_name"]
        cells.append(
            f'<a href="{url}"><img src="./assets/showcase/{name}.svg" '
            f'width="100%" alt="{html.escape(name)}: project summary and source languages" /></a>'
        )
    rows = []
    for i in range(0, len(cells), 2):
        rows.append("| " + cells[i] + " | "
                    + (cells[i+1] if i+1 < len(cells) else " ") + " |")
    return (
        "_Automatically sorted by latest push · Original public projects only · "
        "Up to 3 source languages per card._\n\n"
        "| | |\n| :--- | :--- |\n" + "\n".join(rows) + "\n\n"
        "[Browse all repositories](https://github.com/OKAN-SOFTWARE?tab=repositories)"
    )


def replace_section(readme, rendered):
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README requires exactly one SHOWCASE marker pair")
    before, remainder = readme.split(START, 1)
    _, after = remainder.split(END, 1)
    return before + START + "\n" + rendered + "\n" + END + after


def run(user, token, root=ROOT):
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", user):
        raise ValueError("Invalid GitHub username")
    repos = original_projects(user, token)
    if not repos:
        raise ValueError("No public original projects found")
    languages = {item["name"]: code_languages(item, token) for item in repos}
    commits = recent_commits(user, token)
    target = root / "README.md"
    original = target.read_text(encoding="utf-8")
    updated = replace_section(original, showcase_markdown(repos))
    cards = {item["name"]: card_svg(item, languages[item["name"]]) for item in repos}
    ticker = render_ticker(commits)
    folder = root / "assets" / "showcase"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.svg"):
        if old.stem not in cards:
            old.unlink()
    for name, contents in cards.items():
        (folder / (name + ".svg")).write_text(contents, encoding="utf-8")
    (root / "assets" / "latest-commits.svg").write_text(ticker, encoding="utf-8")
    if updated != original:
        target.write_text(updated, encoding="utf-8")
    print("Ember showcase refreshed: " + str(len(repos))
          + " projects, " + str(len(commits)) + " commits.")


def main():
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ValueError("GH_TOKEN is required")
    run(os.environ.get("PROFILE_USER", "OKAN-SOFTWARE"), token)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, TypeError, KeyError) as error:
        print("Showcase refresh failed: " + str(error), file=sys.stderr)
        sys.exit(1)

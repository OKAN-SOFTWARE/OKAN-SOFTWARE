#!/usr/bin/env python3
"""Update GitHub-compatible Matrix SVGs and the recent-original-project grid.

No JavaScript or custom CSS is required in README.md. SVGs are embedded as images.
Existing ACTIVITY, RECENT, and JOURNEY generated sections remain untouched.
"""
from __future__ import annotations

from datetime import datetime
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
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
START = '<!-- MATRIX_PROJECTS:START -->'
END = '<!-- MATRIX_PROJECTS:END -->'
REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
USER_RE = re.compile(r'^[A-Za-z0-9-]{1,39}$')
IGNORED = {'Markdown', 'JSON', 'YAML', 'TOML', 'XML', 'Text', 'Jupyter Notebook', 'Dockerfile'}
ACCENT = '#39ff14'
TZ = ZoneInfo('America/New_York')


def api(path: str, token: str):
    request = Request('https://api.github.com' + path, headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': 'Bearer ' + token,
        'User-Agent': 'okan-readme-matrix-showcase',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    try:
        with urlopen(request, timeout=25) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError('GitHub API unavailable; leaving existing assets unchanged') from exc


def recent_original_repos(user: str, token: str, limit: int = 6) -> list[dict]:
    found = []
    for page in range(1, 4):
        batch = api('/users/' + quote(user, safe='') + '/repos?type=owner&sort=pushed&direction=desc&per_page=100&page=' + str(page), token)
        if not isinstance(batch, list):
            raise ValueError('Invalid repository listing')
        for repo in batch:
            if not isinstance(repo, dict):
                continue
            name, full = repo.get('name', ''), repo.get('full_name', '')
            owner = (repo.get('owner') or {}).get('login', '')
            if (owner.lower() != user.lower() or not REPO_RE.fullmatch(full)
                or name.lower() == user.lower() or repo.get('fork')
                or repo.get('private') or repo.get('archived') or not repo.get('pushed_at')):
                continue
            found.append(repo)
        if len(batch) < 100:
            break
    found.sort(key=lambda repo: (repo['pushed_at'], repo['name']), reverse=True)
    if not found:
        raise ValueError('No public original repositories found')
    return found[:limit]


def source_languages(repo: dict, token: str) -> list[str]:
    full = repo['full_name']
    if not REPO_RE.fullmatch(full):
        raise ValueError('Invalid repository path')
    values = api('/repos/' + full + '/languages', token)
    if not isinstance(values, dict):
        raise ValueError('Invalid language listing')
    langs = sorted(
        ((name, size) for name, size in values.items()
         if isinstance(name, str) and isinstance(size, int) and size > 0
         and name not in IGNORED and len(name) <= 20),
        key=lambda pair: (-pair[1], pair[0]),
    )
    return [name for name, _ in langs[:3]]


def latest_commits(user: str, token: str, limit: int = 10) -> list[dict]:
    query = quote('author:' + user, safe='')
    data = api('/search/commits?q=' + query + '&sort=committer-date&order=desc&per_page=100', token)
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('Invalid commit listing')
    result, seen = [], set()
    for item in data['items']:
        full = (item.get('repository') or {}).get('full_name', '')
        sha = item.get('sha', '')
        message = ((item.get('commit') or {}).get('message') or '').splitlines()
        message = ' '.join(message[0].split()) if message else ''
        if (not REPO_RE.fullmatch(full) or full.lower() == (user + '/' + user).lower()
            or not re.fullmatch(r'[0-9a-f]{40}', sha) or sha in seen
            or not message or message.startswith(('Merge pull request', 'Add files via upload'))):
            continue
        seen.add(sha)
        result.append({'repo': full, 'sha': sha[:7], 'message': message[:90]})
        if len(result) >= limit:
            break
    if not result:
        raise ValueError('No authored public commits found')
    return result


def x(value) -> str:
    return html.escape(str(value), quote=True)


def truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit-1].rstrip() + '…'


def project_svg(repo: dict, languages: list[str]) -> str:
    name = truncate(repo['name'], 26)
    desc = ' '.join((repo.get('description') or 'An original open-source software project.').split())
    lines = textwrap.wrap(desc, width=56, break_long_words=False, break_on_hyphens=False)[:2]
    pushed = (datetime.fromisoformat(repo['pushed_at'].replace('Z', '+00:00'))
              .astimezone(TZ).strftime('%b %d, %Y %Z').upper())
    line_nodes = ''.join(f'<text x="29" y="{155 + 23*i}" class="desc">{x(line)}</text>' for i, line in enumerate(lines))
    chips, cx = [], 29
    for lang in languages[:3]:
        width = min(150, max(65, len(lang)*9 + 25))
        chips.append(f'<rect x="{cx}" y="218" width="{width}" height="29" rx="7" fill="#102a1b" stroke="#196c39"/>'
                     f'<text x="{cx+12}" y="237" class="tag">{x(lang)}</text>')
        cx += width + 8
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="560" height="266" viewBox="0 0 560 266" role="img" aria-label="{x(name)} project card">
<defs><linearGradient id="panel" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#0b1d12"/><stop offset="1" stop-color="#040b08"/></linearGradient></defs>
<style>.small{{fill:#00e676;font:700 12px ui-monospace,Consolas,monospace;letter-spacing:1.2px}}.name{{fill:#e4ffe9;font:700 29px Arial,sans-serif}}.date{{fill:#83bd96;font:12px ui-monospace,Consolas,monospace}}.desc{{fill:#b8d9c3;font:15px Arial,sans-serif}}.tag{{fill:#79ff96;font:12px ui-monospace,Consolas,monospace}}</style>
<rect x="1" y="1" width="558" height="264" rx="16" fill="url(#panel)" stroke="#1a7942" stroke-width="2"/>
<path d="M24 27h22 M24 27v22" stroke="{ACCENT}" stroke-width="3" stroke-linecap="round"/>
<text x="29" y="60" class="small">ORIGINAL PROJECT</text><text x="29" y="107" class="name">{x(name)}</text>
<text x="29" y="127" class="date">LATEST PUSH · {pushed}</text>{line_nodes}
{''.join(chips)}<path d="M506 33h24v24 M530 33l-28 28" fill="none" stroke="{ACCENT}" stroke-width="2.5" stroke-linecap="round"/>
</svg>'''


def ticker_svg(commits: list[dict]) -> str:
    step, width, period = 406, 1200, 406*len(commits)
    items = []
    for i, commit in enumerate(commits):
        cx = i*step + 5
        items.append(f'<rect x="{cx}" y="51" width="394" height="66" rx="9" fill="#0a2112" stroke="#1b6c3a"/>'
                     f'<rect x="{cx+10}" y="64" width="3" height="40" rx="1.5" fill="{ACCENT}"/>'
                     f'<text x="{cx+22}" y="76" class="repo">{x(truncate(commit["repo"], 31))}</text>'
                     f'<text x="{cx+22}" y="99" class="msg">{x(truncate(commit["message"], 42))}</text>'
                     f'<text x="{cx+325}" y="76" class="hash">{x(commit["sha"])}</text>')
    group = ''.join(items)
    seconds = max(65, len(commits)*8)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="143" viewBox="0 0 {width} 143" role="img" aria-label="Animated recent authored GitHub commits">
<title>Latest commits · original projects and upstream contributions</title>
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#07150c"/><stop offset="1" stop-color="#050b08"/></linearGradient><clipPath id="clip"><rect x="10" y="47" width="1180" height="75"/></clipPath></defs>
<style>.header{{fill:{ACCENT};font:700 16px ui-monospace,Consolas,monospace;letter-spacing:1px}}.sub{{fill:#8bbd99;font:11px ui-monospace,Consolas,monospace}}.repo{{fill:#75ff9a;font:700 13px ui-monospace,Consolas,monospace}}.msg{{fill:#d2f3d9;font:12px ui-monospace,Consolas,monospace}}.hash{{fill:#68d4a4;font:11px ui-monospace,Consolas,monospace}}@keyframes scroll{{from{{transform:translateX(0)}}to{{transform:translateX(-{period}px)}}}}.move{{animation:scroll {seconds}s linear infinite}}@media(prefers-reduced-motion:reduce){{.move{{animation:none}}}}</style>
<rect x="1" y="1" width="1198" height="141" rx="12" fill="url(#bg)" stroke="#16723d" stroke-width="2"/>
<circle cx="22" cy="24" r="5" fill="#00e676"/><text x="35" y="30" class="header">LATEST COMMITS</text><text x="247" y="29" class="sub">ORIGINAL PROJECTS + UPSTREAM CONTRIBUTIONS</text>
<path d="M15 43h1170" stroke="#14592f"/>
<g clip-path="url(#clip)"><g class="move"><g>{group}</g><g transform="translate({period} 0)">{group}</g></g></g>
<text x="16" y="134" class="sub">PUBLIC COMMIT HISTORY  ·  REFRESHED BY GITHUB ACTIONS</text></svg>'''


def stats_svg(readme: str) -> str:
    activity = re.search(r'\*\*(\d+) opened · (\d+) merged · \d+ open now · (\d+) upstream repositories\*\*', readme)
    journey = re.search(r'<!-- ORIGINAL_REPOS:(\d+) -->', readme)
    if not activity or not journey:
        raise ValueError('Could not find existing generated GitHub counters')
    values = [activity.group(1), activity.group(2), activity.group(3), journey.group(1)]
    labels = ['UPSTREAM PRS', 'MERGED PRS', 'UPSTREAM REPOS', 'ORIGINAL PROJECTS']
    cells = []
    for i, (value, label) in enumerate(zip(values, labels)):
        xx = 2 + i*301
        cells.append(f'<rect x="{xx}" y="2" width="294" height="122" rx="13" fill="#0b1b12" stroke="#1b6338"/>'
                     f'<rect x="{xx}" y="15" width="3" height="90" rx="1.5" fill="{ACCENT}"/>'
                     f'<text x="{xx+21}" y="73" class="num">{x(value)}</text>'
                     f'<text x="{xx+21}" y="101" class="label">{x(label)}</text>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="128" viewBox="0 0 1200 128" role="img" aria-label="GitHub contribution snapshot: {', '.join(values)}">
<style>.num{{fill:{ACCENT};font:700 49px Arial,sans-serif}}.label{{fill:#9ac6a5;font:12px ui-monospace,Consolas,monospace;letter-spacing:1px}}</style>{''.join(cells)}</svg>'''


def stack_svg() -> str:
    languages = ['JAVA', 'PYTHON', 'JAVASCRIPT', 'TYPESCRIPT', 'C#', 'SQL', 'HTML', 'CSS', 'RUST', 'POWERSHELL', 'AWS']
    chips, xx, yy = [], 22, 42
    for lang in languages:
        ww = max(70, len(lang)*13 + 26)
        if xx + ww > 1180:
            xx, yy = 22, 94
        chips.append(f'<rect x="{xx}" y="{yy-24}" width="{ww}" height="34" rx="7" fill="#102b1b" stroke="#1c7240"/>'
                     f'<text x="{xx+13}" y="{yy-1}" class="chip">{x(lang)}</text>')
        xx += ww + 11
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="126" viewBox="0 0 1200 126" role="img" aria-label="Java, Python, JavaScript, TypeScript, C sharp, SQL, HTML, CSS, Rust, PowerShell and AWS">
<style>.chip{{fill:#a0ffb3;font:700 15px ui-monospace,Consolas,monospace}}</style><rect x="1" y="1" width="1198" height="124" rx="12" fill="#07150e" stroke="#1d7642" stroke-width="2"/>{''.join(chips)}</svg>'''


def terminal_svg() -> str:
    """Animated, accessible code-typing intro; GitHub README embeds the SVG."""
    return '''<svg xmlns="http://www.w3.org/2000/svg" width="490" height="300" viewBox="0 0 490 300" role="img" aria-label="Animated code editor typing an introduction: backend, web, automation, developer tools, open source, bug fixes and tests">
<defs>
  <linearGradient id="panel" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#102a18"/><stop offset="1" stop-color="#030a06"/></linearGradient>
  <clipPath id="r1"><rect class="typing t1" x="26" y="64" width="0" height="23"/></clipPath>
  <clipPath id="r2"><rect class="typing t2" x="26" y="95" width="0" height="23"/></clipPath>
  <clipPath id="r3"><rect class="typing t3" x="26" y="126" width="0" height="23"/></clipPath>
  <clipPath id="r4"><rect class="typing t4" x="26" y="157" width="0" height="23"/></clipPath>
  <clipPath id="r5"><rect class="typing t5" x="26" y="188" width="0" height="23"/></clipPath>
  <clipPath id="r6"><rect class="typing t6" x="26" y="219" width="0" height="23"/></clipPath>
  <clipPath id="r7"><rect class="typing t7" x="26" y="250" width="0" height="23"/></clipPath>
</defs>
<style>
  .head{fill:#8ec9a2;font:13px ui-monospace,Consolas,monospace}
  .code{font:14px ui-monospace,Consolas,monospace}
  .kw{fill:#39ff14}.string{fill:#b9ffd0}.comment{fill:#72b889}.plain{fill:#e4fff0}.fn{fill:#80e6a0}
  .typing{animation-duration:14s;animation-timing-function:steps(34,end);animation-iteration-count:infinite}
  .t1{animation-name:type1}.t2{animation-name:type2}.t3{animation-name:type3}
  .t4{animation-name:type4}.t5{animation-name:type5}.t6{animation-name:type6}.t7{animation-name:type7}
  @keyframes type1{0%,2%{width:0} 12%,96%{width:440px} 100%{width:0}}
  @keyframes type2{0%,13%{width:0} 24%,96%{width:440px} 100%{width:0}}
  @keyframes type3{0%,25%{width:0} 37%,96%{width:440px} 100%{width:0}}
  @keyframes type4{0%,38%{width:0} 50%,96%{width:440px} 100%{width:0}}
  @keyframes type5{0%,51%{width:0} 63%,96%{width:440px} 100%{width:0}}
  @keyframes type6{0%,64%{width:0} 75%,96%{width:440px} 100%{width:0}}
  @keyframes type7{0%,76%{width:0} 89%,96%{width:440px} 100%{width:0}}
  @keyframes blink{50%{opacity:0}}
  .cursor{animation:blink .9s steps(1,end) infinite}
  @media(prefers-reduced-motion:reduce){
    .typing{animation:none;width:440px}
    .cursor{animation:none}
  }
</style>
<rect x="1" y="1" width="488" height="298" rx="15" fill="url(#panel)" stroke="#1e7644" stroke-width="2"/>
<circle cx="25" cy="27" r="6" fill="#0b5d2b"/><circle cx="46" cy="27" r="6" fill="#16b85d"/><circle cx="67" cy="27" r="6" fill="#39ff14"/>
<text x="366" y="32" class="head">about.ts</text><path d="M20 49h450" stroke="#1b5533"/>
<g class="code">
  <text x="26" y="81" clip-path="url(#r1)" class="comment">// software built for real needs</text>
  <text x="26" y="112" clip-path="url(#r2)" class="kw">const craft = {</text>
  <text x="26" y="143" clip-path="url(#r3)" class="string">  build: ["backend", "web", "tools"],</text>
  <text x="26" y="174" clip-path="url(#r4)" class="string">  automate: "repeatable workflows",</text>
  <text x="26" y="205" clip-path="url(#r5)" class="string">  improve: "bugs + regression tests",</text>
  <text x="26" y="236" clip-path="url(#r6)" class="string">  contribute: "open source"</text>
  <text x="26" y="267" clip-path="url(#r7)" class="plain">}; <tspan class="fn">ship</tspan>(craft); <tspan class="cursor kw">▋</tspan></text>
</g>
</svg>'''


def projects_markdown(repos: list[dict]) -> str:
    cells = []
    for repo in repos:
        full, name = repo['full_name'], repo['name']
        cells.append(f'<a href="https://github.com/{full}"><img src="./assets/ember/projects/{name}.svg" width="100%" alt="{x(name)} project summary with up to three source languages" /></a>')
    rows = [f'| {cells[i]} | {cells[i+1] if i+1 < len(cells) else ""} |' for i in range(0, len(cells), 2)]
    return ('<sub>Automatically sorted by latest push · Original public repositories only · '
            'Up to three source languages per card</sub>\n\n'
            '| | |\n| :--- | :--- |\n' + '\n'.join(rows) + '\n\n'
            '<p align="center"><a href="https://github.com/OKAN-SOFTWARE?tab=repositories"><strong>Browse all original projects ↗</strong></a></p>')


def replace_section(readme: str, generated: str) -> str:
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError('README must have exactly one Matrix project marker pair')
    head, rest = readme.split(START, 1)
    _, tail = rest.split(END, 1)
    return head + START + '\n' + generated + '\n' + END + tail


def refresh(user: str, token: str, root: Path = ROOT) -> None:
    if not USER_RE.fullmatch(user):
        raise ValueError('Invalid GitHub username')
    repos = recent_original_repos(user, token)
    languages = {repo['name']: source_languages(repo, token) for repo in repos}
    commits = latest_commits(user, token)
    target = root / 'README.md'
    current = target.read_text(encoding='utf-8')
    updated = replace_section(current, projects_markdown(repos))
    output = root / 'assets' / 'ember'
    folder = output / 'projects'
    folder.mkdir(parents=True, exist_ok=True)
    for file in folder.glob('*.svg'):
        if file.stem not in languages:
            file.unlink()
    for repo in repos:
        (folder / (repo['name'] + '.svg')).write_text(project_svg(repo, languages[repo['name']]), encoding='utf-8')
    (output / 'latest-commits.svg').write_text(ticker_svg(commits), encoding='utf-8')
    (output / 'stats.svg').write_text(stats_svg(updated), encoding='utf-8')
    (output / 'stack.svg').write_text(stack_svg(), encoding='utf-8')
    (output / 'terminal.svg').write_text(terminal_svg(), encoding='utf-8')
    if updated != current:
        target.write_text(updated, encoding='utf-8')
    print(f'Matrix showcase updated: {len(repos)} original projects and {len(commits)} commits')


def main() -> None:
    token = os.environ.get('GH_TOKEN', '')
    if not token:
        raise ValueError('GH_TOKEN is required')
    refresh(os.environ.get('PROFILE_USER', 'OKAN-SOFTWARE'), token)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
        print(f'Matrix refresh failed: {exc}', file=sys.stderr)
        sys.exit(1)

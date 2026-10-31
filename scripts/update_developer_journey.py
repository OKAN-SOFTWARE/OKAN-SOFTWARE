#!/usr/bin/env python3
"""Public GitHub developer statistics rendered as selectable README text."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
START, END = "<!-- JOURNEY:START -->", "<!-- JOURNEY:END -->"
MATRIX_START, MATRIX_END = "<!-- CODE_MATRIX:START -->", "<!-- CODE_MATRIX:END -->"
TZ = ZoneInfo("America/New_York")
SAFE_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

SOURCE_EXTENSIONS = {
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".py": "Python", ".java": "Java", ".cs": "C#", ".rs": "Rust",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".go": "Go", ".rb": "Ruby", ".php": "PHP", ".sh": "Shell",
    ".sql": "SQL", ".kt": "Kotlin", ".kts": "Kotlin", ".c": "C",
    ".cc": "C++", ".cpp": "C++", ".h": "C", ".hpp": "C++",
    ".swift": "Swift", ".xml": "XML", ".vue": "Vue",
}
SKIP_DIRECTORIES = {
    ".git", "node_modules", ".next", ".turbo", ".cache", ".venv",
    "venv", "dist", "build", "target", "vendor", "coverage",
    "__pycache__", "out", "bower_components", ".pnpm", "third_party",
    "third-party", "generated",
}
COUNTING_RULE_VERSION = 2


def ignored_directory(name):
    """Ignore dependency folders, including copied node_modules directories."""
    lower = name.casefold().strip()
    return (
        lower in SKIP_DIRECTORIES
        or bool(re.fullmatch(r"node_modules\s*[-_]\s*copy(?:\s*\(\d+\))?", lower))
        or bool(re.fullmatch(r"node_modules\s*\(copy\)(?:\s*\(\d+\))?", lower))
    )


def lines_in_checkout(root):
    """Count nonblank lines in recognized tracked source files, including comments."""
    results = Counter()
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        subdirs[:] = [
            name for name in subdirs
            if not ignored_directory(name)
            and not (Path(directory) / name).is_symlink()
        ]
        for filename in filenames:
            path = Path(directory) / filename
            if path.is_symlink() or ".min." in filename.lower():
                continue
            language = (
                "Dockerfile" if filename.lower() == "dockerfile"
                else SOURCE_EXTENSIONS.get(path.suffix.lower())
            )
            if not language:
                continue
            with path.open("rb") as stream:
                if b"\x00" in stream.read(4096):
                    continue
                stream.seek(0)
                results[language] += sum(bool(line.strip()) for line in stream)
    return results


def count_source_lines(repos, token, previous=None):
    """Count current default-branch LOC; reuse a cache only for identical HEAD SHAs."""
    previous = previous or {}
    if not isinstance(previous, dict):
        raise ValueError("Invalid code matrix cache")
    totals, refreshed = Counter(), {}
    with tempfile.TemporaryDirectory(prefix="okan-github-loc-") as temporary:
        for index, repo in enumerate(repos):
            full = repo.get("full_name", "")
            if not SAFE_REPO.fullmatch(full) or repo.get("fork") or repo.get("private"):
                raise ValueError("Only public original repositories can be counted")
            # Newly created empty repositories have no commit yet, and no source lines.
            if repo.get("pushed_at") is None and repo.get("size") == 0:
                refreshed[full] = {"sha": None, "rules": COUNTING_RULE_VERSION, "lines": {}}
                continue
            branch = repo.get("default_branch")
            if not isinstance(branch, str) or not branch:
                raise ValueError("Repository default branch is missing")
            head = api_get(f"/repos/{full}/commits/{quote(branch, safe='')}", token)
            sha = head.get("sha") if isinstance(head, dict) else None
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                raise ValueError("Invalid default-branch commit SHA")
            prior = previous.get(full)
            if (isinstance(prior, dict) and prior.get("sha") == sha
                    and prior.get("rules") == COUNTING_RULE_VERSION):
                counts = prior.get("lines")
                if isinstance(counts, dict) and all(
                    isinstance(lang, str) and lang and
                    type(n) is int and n >= 0 for lang, n in counts.items()
                ):
                    totals.update(counts)
                    refreshed[full] = {"sha": sha, "rules": COUNTING_RULE_VERSION, "lines": counts}
                    continue
            checkout = Path(temporary) / f"repo-{index}"
            environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
            try:
                process = subprocess.run(
                    [
                        "git", "-c", "core.hooksPath=/dev/null",
                        "clone", "--quiet", "--depth=1", "--single-branch", "--",
                        f"https://github.com/{full}.git", str(checkout),
                    ],
                    env=environment, capture_output=True, timeout=60, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError("Public source checkout failed") from error
            if process.returncode != 0:
                raise RuntimeError("Public source checkout failed")
            counted = lines_in_checkout(checkout)
            totals.update(counted)
            refreshed[full] = {"sha": sha, "rules": COUNTING_RULE_VERSION, "lines": dict(sorted(counted.items()))}
    return totals, refreshed


def parse_time(value):
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("GitHub timestamp has no timezone")
    return moment.astimezone(TZ)


def api_get(path, token):
    request = Request(
        "https://api.github.com" + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "User-Agent": "okan-profile-journey",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 409 and "/commits?" in path:
            return []
        raise RuntimeError("GitHub request failed; retaining prior statistics") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError("GitHub request failed; retaining prior statistics") from error


def paged(path, token, max_pages=30, allow_limit=False):
    items = []
    for page in range(1, max_pages + 1):
        separator = "&" if "?" in path else "?"
        result = api_get(f"{path}{separator}per_page=100&page={page}", token)
        if not isinstance(result, list):
            raise ValueError("GitHub API returned an invalid page")
        items.extend(result)
        if len(result) < 100:
            return items
    if allow_limit:
        return items
    raise ValueError("GitHub history exceeds the configured page limit")


def public_original_repos(user, token):
    """List all current public non-fork repositories, excluding this profile."""
    name = quote(user, safe="")
    repos = paged(f"/users/{name}/repos?type=owner", token, max_pages=10)
    return [
        repo for repo in repos
        if SAFE_REPO.fullmatch(repo.get("full_name", ""))
        and repo["full_name"].split("/")[0].lower() == user.lower()
        and not repo.get("fork") and not repo.get("private")
        and repo["full_name"].lower() != f"{user}/{user}".lower()
    ]


def fetch_public_data(user, token):
    name = quote(user, safe="")
    originals = public_original_repos(user, token)
    languages, commits = Counter(), []
    for repo in originals:
        full = repo["full_name"]
        lang = api_get(f"/repos/{full}/languages", token)
        if not isinstance(lang, dict):
            raise ValueError("Invalid repository language data")
        for language, byte_count in lang.items():
            if not isinstance(language, str) or type(byte_count) is not int or byte_count < 0:
                raise ValueError("Invalid language byte count")
            languages[language] += byte_count
        authored = paged(f"/repos/{full}/commits?author={name}", token, max_pages=50)
        for item in authored:
            item["repository"] = {"full_name": full}
        commits.extend(authored)
    events = paged(f"/users/{name}/events/public", token, max_pages=3, allow_limit=True)
    return originals, languages, commits, events


def summarize(user, repos, languages, commits, events, now):
    today = now.astimezone(TZ).date()
    by_day = defaultdict(lambda: {
        "commits": 0, "repos": set(), "prs": 0, "issues": 0, "events": []
    })
    hours, weekdays, seen_shas = Counter(), Counter(), set()
    for item in commits:
        sha = item.get("sha")
        repo = (item.get("repository") or {}).get("full_name", "")
        raw = ((item.get("commit") or {}).get("committer") or {}).get("date")
        if not sha or not SAFE_REPO.fullmatch(repo) or not raw or sha in seen_shas:
            continue
        seen_shas.add(sha)
        when = parse_time(raw)
        hours[when.hour] += 1
        weekdays[when.weekday()] += 1
        day = by_day[when.date().isoformat()]
        day["commits"] += 1
        day["repos"].add(repo)
        day["events"].append((when, "Committed code", repo))
    seen_events = set()
    for event in events:
        event_id = event.get("id")
        actor = (event.get("actor") or {}).get("login", "")
        if not event_id or event_id in seen_events or actor.lower() != user.lower():
            continue
        seen_events.add(event_id)
        when = parse_time(event["created_at"])
        repo = (event.get("repo") or {}).get("name", "")
        if not SAFE_REPO.fullmatch(repo):
            continue
        day = by_day[when.date().isoformat()]
        kind, payload = event.get("type"), event.get("payload") or {}
        label = None
        if kind == "PullRequestEvent":
            action = payload.get("action")
            pr = payload.get("pull_request") or {}
            if action == "closed" and pr.get("merged"):
                label = "Merged PR"
            elif action == "opened":
                label = "Opened PR"
            elif action in ("reopened", "synchronize"):
                label = "Updated PR"
            if label:
                day["prs"] += 1
        elif kind == "IssuesEvent" and payload.get("action") == "opened":
            day["issues"] += 1
            label = "Opened issue"
        elif kind == "PushEvent":
            label = "Pushed code"
        if label:
            day["repos"].add(repo)
            day["events"].append((when, label, repo))
    for day in by_day.values():
        day["events"].sort(key=lambda value: value[0], reverse=True)
    rhythm = [
        ("🌞 Morning", sum(hours[h] for h in range(6, 12))),
        ("🌆 Daytime", sum(hours[h] for h in range(12, 18))),
        ("🌃 Evening", sum(hours[h] for h in range(18, 24))),
        ("🌙 Night", sum(hours[h] for h in range(0, 6))),
    ]
    current = by_day[today.isoformat()]
    return {
        "today": today, "now": now.astimezone(TZ), "repos": len(repos),
        "languages": languages, "rhythm": rhythm, "weekdays": weekdays,
        "total_commits": len(seen_shas), "by_day": by_day, "current": current,
    }


def bar(part, total, width=16):
    filled = round(part / total * width) if total else 0
    return "█" * filled + "░" * (width - filled)


def day_record(item):
    return {
        "commits": item["commits"], "repos": len(item["repos"]),
        "pr_events": item["prs"], "issues": item["issues"],
    }


def archive_updates(existing, summary):
    if not isinstance(existing, dict):
        raise ValueError("Chronicles archive must be an object")
    updated = dict(existing)
    today = summary["today"]
    for offset in range(30):
        day = (today - timedelta(days=offset)).isoformat()
        item = summary["by_day"].get(day)
        if item:
            updated[day] = day_record(item)
        elif day not in updated:
            updated[day] = {"commits": 0, "repos": 0, "pr_events": 0, "issues": 0}
    threshold = (today - timedelta(days=364)).isoformat()
    return {
        day: updated[day] for day in sorted(updated)
        if threshold <= day <= today.isoformat()
    }


LANGUAGE_ICONS = {
    "JavaScript": "javascript", "TypeScript": "typescript",
    "Java": "java", "Python": "python", "HTML": "html5",
    "CSS": "css3", "SCSS": "sass", "XML": "xml",
    "Rust": "rust", "Dockerfile": "docker", "C#": "csharp",
    "Go": "go", "Ruby": "ruby", "PHP": "php", "Shell": "bash",
    "SQL": "azuresqldatabase", "Kotlin": "kotlin", "C": "c",
    "C++": "cplusplus", "Swift": "swift", "Vue": "vuejs",
}


def matrix_bar(part, total, width=12):
    """GitHub-native text cells; percentages are the accurate values."""
    filled = round(part / total * width) if total else 0
    if part > 0:
        filled = max(1, filled)
    filled = min(width, filled)
    return "🟩" * filled + "⬛" * (width - filled)


def language_cell(name):
    """Keep the table selectable text, with only a tiny decorative language logo."""
    safe = re.sub(r"([\\|*_\[\]])", r"\\\1", name)
    icon = LANGUAGE_ICONS.get(name)
    if not icon:
        return f"💻 **{safe}**"
    url = (
        "https://raw.githubusercontent.com/devicons/devicon/master/"
        f"icons/{icon}/{icon}-original.svg"
    )
    return f'<img src="{url}" width="18" height="18" alt="" /> **{safe}**'


def matrix_markdown(locs, repo_count):
    """Native GitHub Markdown with accurate, descending source-line shares."""
    counts = [(name, n) for name, n in locs.items() if type(n) is int and n > 0]
    counts.sort(key=lambda item: (-item[1], item[0].casefold()))
    total = sum(n for _, n in counts)
    rows = [
        "### 🟢 OKAN'S CODE STATS // LIVE UPDATES",
        "",
        f"**TOTAL LINES OF CODE: {total:,}**",
        "",
        "| Language | Lines | Share | Distribution |",
        "| :--- | ---: | ---: | :--- |",
    ]
    for name, n in counts:
        share = n / total * 100
        pct = f"{share:.3f}%" if share < 0.01 else f"{share:.2f}%"
        rows.append(
            f"| {language_cell(name)} | {n:,} | **{pct}** | {matrix_bar(n, total)} |"
        )
    if not counts:
        rows.append("| No source code found yet | 0 | 0.00% | " + matrix_bar(0, 0) + " |")
    rows.extend([
        "",
        "<sub>Automatically checked every 15 minutes · Public original repositories · "
        "Nonblank source lines (including comments) · Dependencies, generated files, "
        "and binaries excluded · Tiny nonzero shares show one green cell</sub>",
    ])
    return "\n".join(rows)


def markdown(summary, archive):
    now = summary["now"]
    total = summary["total_commits"]
    rows = [
        f"**Developer stats refreshed:** {now:%b %d, %Y at %I:%M %p %Z}",
        "",
        "### Public GitHub History",
        "",
        f"**{total:,} authored commits · {summary['repos']} public original repositories**",
    ]
    return "\n".join(rows)

def replace_section(readme, generated):
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README must contain one journey marker pair")
    before, rest = readme.split(START, 1)
    _, after = rest.split(END, 1)
    return before + START + "\n" + generated + "\n" + END + after


def load_matrix_cache(root):
    path = root / "data" / "code-matrix-cache.json"
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("Code matrix cache must be a JSON object")
    return loaded


def replace_matrix_section(readme, generated):
    if readme.count(MATRIX_START) != 1 or readme.count(MATRIX_END) != 1:
        raise ValueError("README must contain exactly one Code Matrix marker pair")
    before, rest = readme.split(MATRIX_START, 1)
    _, after = rest.split(MATRIX_END, 1)
    return before + MATRIX_START + "\n" + generated + "\n" + MATRIX_END + after


def write_matrix_cache(root, cache):
    path = root / "data" / "code-matrix-cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_matrix_only(user, token, root=ROOT):
    """Fast polling path: update only native Markdown LOC table and per-repo cache."""
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", user):
        raise ValueError("Invalid GitHub username")
    repos = public_original_repos(user, token)
    locs, cache = count_source_lines(repos, token, load_matrix_cache(root))
    readme_path = root / "README.md"
    original = readme_path.read_text(encoding="utf-8")
    updated = replace_matrix_section(original, matrix_markdown(locs, len(repos)))
    # All remote reads, clones and validation complete before writing files.
    if updated != original:
        readme_path.write_text(updated, encoding="utf-8")
    write_matrix_cache(root, cache)
    print(f"Code Matrix refreshed: {sum(locs.values()):,} lines in {len(repos)} repositories.")


def run(user, token, root=ROOT, now=None):
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", user):
        raise ValueError("Invalid GitHub username")
    now = now or datetime.now(timezone.utc)
    repos, languages, commits, events = fetch_public_data(user, token)
    summary = summarize(user, repos, languages, commits, events, now)
    readme = root / "README.md"
    history_path = root / "data" / "chronicles.json"
    original = readme.read_text(encoding="utf-8")
    existing = json.loads(history_path.read_text(encoding="utf-8")) if history_path.is_file() else {}
    archive = archive_updates(existing, summary)
    summary["locs"], cache = count_source_lines(repos, token, load_matrix_cache(root))
    updated = replace_matrix_section(
        original, matrix_markdown(summary["locs"], summary["repos"])
    )
    archive_text = json.dumps(archive, ensure_ascii=False, indent=2) + "\n"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text(updated, encoding="utf-8")
    history_path.write_text(archive_text, encoding="utf-8")
    write_matrix_cache(root, cache)
    print(f"Developer journey refreshed: {summary['total_commits']} public attributed commits.")


def main():
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ValueError("GH_TOKEN is required")
    user = os.environ.get("PROFILE_USER", "OKAN-SOFTWARE")
    if "--matrix-only" in sys.argv:
        run_matrix_only(user, token)
    else:
        run(user, token)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        print(f"Developer journey refresh failed: {error}", file=sys.stderr)
        sys.exit(1)

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
TZ = ZoneInfo("America/New_York")
SAFE_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
STACK = [
    ("Backend", "Java · Python · C# · JavaScript/TypeScript (Node.js) · Express · Spring Boot"),
    ("Frontend", "JavaScript · TypeScript · React · HTML · CSS · Three.js"),
    ("Data & SQL", "SQL · PostgreSQL · SQL Server · SQLite · MongoDB · DynamoDB"),
    ("OS & DevOps", "Windows · Ubuntu/WSL · GitHub Actions · Docker"),
    ("Cloud", "AWS Lambda · SQS · SNS · EventBridge · EC2"),
]


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
    "__pycache__", "out",
}


def lines_in_checkout(root):
    """Count nonblank lines in recognized tracked source files, including comments."""
    results = Counter()
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        subdirs[:] = [
            name for name in subdirs
            if name not in SKIP_DIRECTORIES
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


def count_source_lines(repos):
    """Shallow-clone public original default branches without running repo code."""
    lines = Counter()
    with tempfile.TemporaryDirectory(prefix="okan-github-loc-") as temporary:
        for index, repo in enumerate(repos):
            full = repo.get("full_name", "")
            if not SAFE_REPO.fullmatch(full) or repo.get("fork") or repo.get("private"):
                raise ValueError("Only public original repositories can be counted")
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
            lines.update(lines_in_checkout(checkout))
    return lines


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


def fetch_public_data(user, token):
    name = quote(user, safe="")
    repos = paged(f"/users/{name}/repos?type=owner", token, max_pages=10)
    originals, languages, commits = [], Counter(), []
    for repo in repos:
        full = repo.get("full_name", "")
        if (
            not SAFE_REPO.fullmatch(full)
            or full.split("/")[0].lower() != user.lower()
            or repo.get("fork") or repo.get("private")
            or full.lower() == f"{user}/{user}".lower()
        ):
            continue
        originals.append(repo)
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
    # GitHub's public event feed exposes only recent events (maximum 300).
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


def markdown(summary, archive):
    now = summary["now"]
    total = summary["total_commits"]
    total_bytes = sum(summary["languages"].values())
    top = summary["languages"].most_common(1)
    dominant = top[0][0] if top else "a mix of technologies"
    rows = [
        f"**Developer stats refreshed:** {now:%b %d, %Y at %I:%M %p %Z}",
        "",
        "### 🧬 Code DNA · Public GitHub History",
        "",
        f"**{total:,} authored commits · {summary['repos']} public original repositories**",
        "",
        "#### 🕒 When I Commit",
        "",
        "| Time of day (Eastern) | Commits | Share | Activity |",
        "| :--- | ---: | ---: | :--- |",
    ]
    for name, count in summary["rhythm"]:
        share = count / total * 100 if total else 0
        rows.append(f"| {name} | {count:,} | {share:.1f}% | {bar(count, total)} |")
    rows.extend([
        "",
        f"#### 💬 I Mostly Code in {dominant}",
        "",
        "| Language | Line of Codes | Source bytes | Activity |",
        "| :--- | ---: | ---: | :--- |",
    ])
    for name, count in summary["languages"].most_common(8):
        safe = re.sub(r"([\\|*_\[\]])", r"\\\1", name)
        loc = f'{summary["locs"][name]:,}' if name in summary.get("locs", {}) else "—"
        rows.append(f"| {safe} | {loc} | {count:,} | {bar(count, total_bytes)} |")
    if not summary["languages"]:
        rows.append("| — | — | 0 | No language data available |")
    rows.extend([
        "",
        "### 🧩 My Development Stack",
        "",
        "| Area | Technologies |",
        "| :--- | :--- |",
    ])
    for area, tech in STACK:
        rows.append(f"| **{area}** | {tech} |")
    return "\n".join(rows)


def replace_section(readme, generated):
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README must contain one journey marker pair")
    before, rest = readme.split(START, 1)
    _, after = rest.split(END, 1)
    return before + START + "\n" + generated + "\n" + END + after


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
    summary["locs"] = count_source_lines(repos)
    updated = replace_section(original, markdown(summary, archive))
    archive_text = json.dumps(archive, ensure_ascii=False, indent=2) + "\n"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    # Validate all remote data and the complete outputs before writing either file.
    readme.write_text(updated, encoding="utf-8")
    history_path.write_text(archive_text, encoding="utf-8")
    print(f"Developer journey refreshed: {summary['total_commits']} public attributed commits.")


def main():
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ValueError("GH_TOKEN is required")
    run(os.environ.get("PROFILE_USER", "OKAN-SOFTWARE"), token)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        print(f"Developer journey refresh failed: {error}", file=sys.stderr)
        sys.exit(1)

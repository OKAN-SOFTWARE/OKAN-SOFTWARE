#!/usr/bin/env python3
"""Public GitHub developer statistics rendered as selectable README text."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
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
    repo_languages = {
        repo["full_name"]: repo.get("language")
        for repo in repos if repo.get("full_name")
    }
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
    active_languages = Counter(
        repo_languages.get(repo) for repo in current["repos"]
        if repo_languages.get(repo)
    )
    early_commit = any(
        when.hour < 10 and label == "Committed code"
        for when, label, _ in current["events"]
    )
    return {
        "today": today, "now": now.astimezone(TZ), "repos": len(repos),
        "languages": languages, "rhythm": rhythm, "weekdays": weekdays,
        "total_commits": len(seen_shas), "by_day": by_day, "current": current,
        "active_languages": active_languages, "early_commit": early_commit,
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
    today, now = summary["today"], summary["now"]
    total = summary["total_commits"]
    total_bytes = sum(summary["languages"].values())
    top = summary["languages"].most_common(1)
    dominant = top[0][0] if top else "a mix of technologies"
    rows = [
        f"**Developer stats refreshed:** {now:%b %d, %Y at %I:%M %p %Z}",
        "",
        "### 🧬 Code DNA · Public GitHub History",
        "",
        f"**I Mostly Code in {dominant}**",
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
        "#### 📅 Most Productive Commit Day",
        "",
        "| Day | Commits | Share |",
        "| :--- | ---: | ---: |",
    ])
    for index, name in enumerate(("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")):
        count = summary["weekdays"][index]
        share = count / total * 100 if total else 0
        rows.append(f"| {name} | {count:,} | {share:.1f}% |")
    rows.extend([
        "",
        "#### 💬 Programming Languages",
        "",
        "| Language | Source bytes | Share | Activity |",
        "| :--- | ---: | ---: | :--- |",
    ])
    for name, count in summary["languages"].most_common(8):
        safe = re.sub(r"([\\|*_\[\]])", r"\\\1", name)
        share = count / total_bytes * 100 if total_bytes else 0
        rows.append(f"| {safe} | {count:,} | {share:.1f}% | {bar(count, total_bytes)} |")
    if not summary["languages"]:
        rows.append("| — | 0 | — | No language data available |")
    rows.extend([
        "",
        "### 🧩 My Development Stack",
        "",
        "| Area | Technologies |",
        "| :--- | :--- |",
    ])
    for area, tech in STACK:
        rows.append(f"| **{area}** | {tech} |")
    current = summary["current"]
    rows.extend([
        "",
        f"### 🔥 Today's Coding Adventure · {today:%A, %B %d, %Y}",
        "",
        "| Authored commits | Repositories touched | PR activities | Issues opened |",
        "| ---: | ---: | ---: | ---: |",
        f"| **{current['commits']}** | **{len(current['repos'])}** | **{current['prs']}** | **{current['issues']}** |",
        "",
        "#### ⚡ Today's Public Activity Trail",
        "",
    ])
    if current["events"]:
        for when, label, repo in current["events"][:6]:
            rows.append(f"- **{when:%I:%M %p}** · {label} in [{repo}](https://github.com/{repo})")
    else:
        rows.append("No qualifying public GitHub events recorded yet today.")
    rows.extend(["", "#### 💻 Languages of Repositories Active Today", ""])
    if summary["active_languages"]:
        rows.append(" · ".join(
            f"**{name}** ({count} repo{'s' if count != 1 else ''})"
            for name, count in summary["active_languages"].most_common()
        ))
    else:
        rows.append("No language information for today's active original repositories.")
    badges = []
    if current["commits"] >= 5:
        badges.append("🔥 Commit Flame")
    if current["prs"]:
        badges.append("🚀 Upstream Pathfinder")
    if len(current["repos"]) >= 3:
        badges.append("🧭 Multi-Repo Adventurer")
    if summary["early_commit"]:
        badges.append("🌅 Early Commit")
    rows.extend([
        "",
        "**Achievements:** " + (" · ".join(badges) if badges else "Awaiting today's first unlock"),
        "",
        "### 📖 The Phoenix Chronicles",
        "",
        "<details>",
        "<summary>Explore recent daily adventures (text history)</summary>",
        "",
        "| Date (Eastern) | Commits | Repos | PR activity | Issues |",
        "| :--- | ---: | ---: | ---: | ---: |",
    ])
    for day in sorted(archive, reverse=True)[:14]:
        item = archive[day]
        rows.append(
            f"| {day} | {item['commits']} | {item['repos']} | {item['pr_events']} | {item['issues']} |"
        )
    rows.extend([
        "",
        "</details>",
        "",
        "<sub>All numbers, percentages, and bars above are selectable text, not images. Commit totals and timing cover commits attributed to this GitHub account on default branches of its current public original repositories; they do not measure hours spent coding or include all upstream commits. Language shares represent GitHub-reported source bytes, not coding time or skill level. Recent public activity is limited by GitHub's event feed and may lag. Development Stack is a curated skills list. The daily chronicle archive retains up to 365 days of snapshots.</sub>",
    ])
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

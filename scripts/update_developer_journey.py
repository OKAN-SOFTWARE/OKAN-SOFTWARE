#!/usr/bin/env python3
"""Public GitHub developer statistics rendered as selectable README text."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import html
import os
from pathlib import Path
import re
import sys
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
START, END = "<!-- JOURNEY:START -->", "<!-- JOURNEY:END -->"
WEEKLY_START, WEEKLY_END = "<!-- WEEKLY:START -->", "<!-- WEEKLY:END -->"
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



TOPIC_RULES = [
    ("React", r"\breact\b"),
    ("Node.js / Express", r"\b(?:nodejs|node\.js|express)\b"),
    ("Spring Boot", r"\bspring(?:[- ]boot)?\b"),
    ("JavaScript / TypeScript", r"\b(?:javascript|typescript|html|css|vite|vitest|angular)\b"),
    ("Python", r"\b(?:python|pytest|django|mypy|ruff|boto3)\b"),
    ("Java", r"\b(?:java|maven)\b"),
    ("Data & SQL", r"\b(?:sql|postgres|redis|mongodb|dynamodb|csv)\b"),
    ("Cloud & DevOps", r"\b(?:aws|lambda|cloudformation|docker|workflow|github.actions|ci)\b"),
    ("Testing & reliability", r"\b(?:test|tests|regression|lint|validation|diagnostic)\b"),
]


def safe_title(value):
    """Escape GitHub-supplied titles before inserting them into README Markdown."""
    value = " ".join(str(value).split())[:120]
    value = html.escape(value, quote=False)
    return re.sub(r"([\\*_\[\]()|])", r"\\\1", value).replace(chr(96), "\\" + chr(96))


def fetch_weekly_prs(user, token, today):
    """Find the author's public PRs active since the start of the Eastern week."""
    monday = today - timedelta(days=today.weekday())
    scope = f"is:pr is:public author:{user} updated:>={monday.isoformat()} sort:updated-desc"
    results, total, seen = [], None, set()
    for page in range(1, 11):
        url = "/search/issues?" + urlencode({
            "q": scope, "per_page": 100, "page": page
        })
        payload = api_get(url, token)
        if (
            not isinstance(payload, dict)
            or payload.get("incomplete_results") is not False
            or type(payload.get("total_count")) is not int
        ):
            raise ValueError("Incomplete weekly GitHub PR search")
        if total is None:
            total = payload["total_count"]
            if total > 1000:
                raise ValueError("Weekly PR search exceeds 1,000 results")
        elif total != payload["total_count"]:
            raise ValueError("Weekly PR search changed during pagination")
        for item in payload.get("items", []):
            match = re.fullmatch(
                r"https://api.github.com/repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
                item.get("repository_url", ""),
            )
            if not match or (item.get("user") or {}).get("login", "").lower() != user.lower():
                raise ValueError("Unexpected weekly PR result")
            repo = "/".join(match.groups())
            number = item.get("number")
            if type(number) is not int or number < 1:
                raise ValueError("Invalid weekly PR number")
            url = f"https://github.com/{repo}/pull/{number}"
            if item.get("html_url") != url or "pull_request" not in item:
                raise ValueError("Unexpected weekly PR link")
            if url in seen:
                raise ValueError("Duplicate weekly PR")
            seen.add(url)
            parse_time(item["created_at"])
            parse_time(item["updated_at"])
            merged_at = (item.get("pull_request") or {}).get("merged_at")
            if merged_at:
                parse_time(merged_at)
            # Closed and unmerged PRs are excluded by profile preference.
            if item.get("state") == "open" or merged_at:
                results.append(item)
        if len(seen) >= total:
            return sorted(results, key=lambda p: parse_time(p["updated_at"]), reverse=True)
        if not payload.get("items"):
            break
    raise ValueError("Weekly PR search did not return all results")


def weekly_topics(prs, commits, monday):
    samples = []
    for item in prs:
        samples.append(item.get("title", "") + " " + item.get("repository_url", ""))
    for item in commits:
        raw = ((item.get("commit") or {}).get("committer") or {}).get("date")
        if raw and parse_time(raw).date() >= monday:
            samples.append(((item.get("commit") or {}).get("message") or "").split("\n")[0])
    counts = Counter()
    for item in samples:
        for label, expression in TOPIC_RULES:
            if re.search(expression, item, re.IGNORECASE):
                counts[label] += 1
    return [label for label, _ in counts.most_common(4)]


def weekly_markdown(summary, commits, prs):
    today = summary["today"]
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    # These counters represent observed public actions, not a skill assessment.
    opened = [p for p in prs if monday <= parse_time(p["created_at"]).date() <= today]
    merged = [
        p for p in prs
        if (p.get("pull_request") or {}).get("merged_at")
        and monday <= parse_time(p["pull_request"]["merged_at"]).date() <= today
    ]
    week_days = [
        (monday + timedelta(days=offset)).isoformat()
        for offset in range((today - monday).days + 1)
    ]
    weekly_commits = sum(
        summary["by_day"].get(day, {}).get("commits", 0) for day in week_days
    )
    active_repos = set()
    for day in week_days:
        active_repos.update(summary["by_day"].get(day, {}).get("repos", set()))
    topics = weekly_topics(prs, commits, monday)
    rows = [
        "### 📅 Weekly Coding Adventure",
        "",
        f"**{monday:%b %d}–{sunday:%b %d, %Y} · Eastern Time**",
        "",
        (
            f"This week, I recorded **{weekly_commits} attributed commits** across "
            f"**{len(active_repos)} public repositories**. "
            f"I opened **{len(opened)} pull requests** and merged **{len(merged)}**."
        ),
        "",
    ]
    if topics:
        rows.extend([
            "**Topics appearing in my public work:** " + " · ".join(topics),
            "",
        ])
    candidates = opened + merged + prs
    # Prefer actions from this week, then other still-active authored PRs.
    # Keep upstream projects prominent ahead of profile maintenance.
    candidates.sort(key=lambda p: (
        p.get("repository_url", "").lower()
        == "https://api.github.com/repos/okan-software/okan-software",
        p not in opened and p not in merged,
        -(parse_time(p["updated_at"]).timestamp()),
    ))
    highlights, highlighted_repos = [], set()
    for item in candidates:
        repo = item["repository_url"].split("/repos/", 1)[-1]
        if repo in highlighted_repos:
            continue
        highlighted_repos.add(repo)
        highlights.append(item)
        if len(highlights) >= 4:
            break
    rows.extend(["### This Week's Work", ""])
    if highlights:
        for item in highlights:
            repo = item["repository_url"].split("/repos/", 1)[-1]
            title = safe_title(item["title"])
            if item in merged:
                action = "My PR was merged:"
            elif item in opened:
                action = "I opened"
            else:
                action = "My PR remained active:"
            rows.append(
                f"- **{repo}:** {action} [{title}]({item['html_url']})."
            )
    else:
        rows.append("No qualifying public pull requests are recorded for this week yet.")
    rows.extend([
        "",
        "<details>",
        "<summary>📅 Day-by-day development notes</summary>",
        "",
    ])
    for day in reversed(week_days):
        current = datetime.fromisoformat(day).date()
        data = summary["by_day"].get(day, {})
        count = data.get("commits", 0)
        pr_started = [p for p in opened if parse_time(p["created_at"]).date() == current]
        pr_merged = [
            p for p in merged
            if parse_time(p["pull_request"]["merged_at"]).date() == current
        ]
        snippets = []
        if count:
            snippets.append(f"{count} authored commit{'s' if count != 1 else ''}")
        if pr_started:
            snippets.append(f"opened {len(pr_started)} PR{'s' if len(pr_started) != 1 else ''}")
        if pr_merged:
            snippets.append(f"merged {len(pr_merged)} PR{'s' if len(pr_merged) != 1 else ''}")
        detail_pr = (pr_merged or pr_started)
        if detail_pr:
            first = detail_pr[0]
            snippets.append(f"including [{safe_title(first['title'])}]({first['html_url']})")
        if not detail_pr:
            messages = []
            for commit in commits:
                raw = ((commit.get("commit") or {}).get("committer") or {}).get("date")
                message = ((commit.get("commit") or {}).get("message") or "").split("\n")[0]
                if raw and parse_time(raw).date() == current and message:
                    messages.append(message)
            if messages:
                snippets.append("commit note: " + safe_title(messages[0]))
        rows.append(
            f"- **{current:%a, %b %d}:** " +
            ("; ".join(snippets) if snippets else "No matching public work recorded.")
        )
    rows.extend([
        "",
        "</details>",
        "",
        "<sub>This journal is rebuilt from attributed commits on current public original-repository default branches and public PR titles/dates. Technology topics are keyword matches in titles, repository names, and commit messages, not verified framework usage or an expertise rating. PR updates by other people are not described as personal actions. The current week runs Monday–Sunday in Eastern Time and refreshes with the profile workflow.</sub>",
    ])
    return "\n".join(rows)


def replace_weekly(readme, generated):
    if readme.count(WEEKLY_START) != 1 or readme.count(WEEKLY_END) != 1:
        raise ValueError("README must contain one weekly marker pair")
    before, rest = readme.split(WEEKLY_START, 1)
    _, after = rest.split(WEEKLY_END, 1)
    return before + WEEKLY_START + "\n" + generated + "\n" + WEEKLY_END + after



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
    current = summary["current"]
    rows.extend([
        "",
        f"### 🔥 Today's Coding Adventure · {today:%A, %B %d, %Y}",
        "",
        "Our daily development journey is here — a timeline of today's public coding and contribution activity.",
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
        "<sub>Statistics and bars are selectable text. Line of Codes counts nonblank lines in recognized source files on the default branches of current public original repositories; comments are included, while common generated/dependency directories and minified files are excluded. Source bytes come from GitHub's language API, so LOC and bytes have different inclusion rules. Commit timing does not measure hours spent coding or cover all upstream history. Recent event data can lag. Development Stack is a curated skills list.</sub>",
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
    summary["locs"] = count_source_lines(repos)
    weekly_prs = fetch_weekly_prs(user, token, summary["today"])
    updated = replace_weekly(
        replace_section(original, markdown(summary, archive)),
        weekly_markdown(summary, commits, weekly_prs),
    )
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

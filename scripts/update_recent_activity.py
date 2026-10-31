#!/usr/bin/env python3
"""Render a compact, selectable-text feed of recent public GitHub actions."""
from datetime import datetime
import html
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
START, END = "<!-- RECENT:START -->", "<!-- RECENT:END -->"
TZ = ZoneInfo("America/New_York")
SAFE_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def safe_text(value, limit=110):
    """Make untrusted GitHub labels safe for a Markdown table cell."""
    normalized = " ".join(str(value or "").split())[:limit]
    escaped = html.escape(normalized, quote=False)
    return re.sub(r"([\\\x60*_\[\]()|])", r"\\\1", escaped)


def parse_time(value):
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("GitHub event timestamp has no timezone")
    return timestamp.astimezone(TZ)


def fetch_events(user, token):
    events = []
    for page in range(1, 4):
        request = Request(
            "https://api.github.com/users/" + quote(user, safe="")
            + f"/events/public?per_page=100&page={page}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + token,
                "User-Agent": "okan-profile-recent-activity",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=25) as response:
                batch = json.load(response)
        except (HTTPError, URLError, TimeoutError) as error:
            raise RuntimeError("Recent GitHub events could not be loaded") from error
        if not isinstance(batch, list):
            raise ValueError("GitHub returned invalid recent events")
        events.extend(batch)
        if len(batch) < 100:
            break
    return events


def linked_item(item, repo, kind, fallback):
    number = item.get("number") if isinstance(item, dict) else None
    title = safe_text((item or {}).get("title") if isinstance(item, dict) else fallback)
    if type(number) is int and number > 0:
        return f"[{title or fallback}](https://github.com/{repo}/{kind}/{number})"
    return title or fallback


def describe(event):
    kind = event.get("type")
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    repo = (event.get("repo") or {}).get("name", "")
    if not SAFE_REPO.fullmatch(repo):
        return None
    detail = ""
    if kind == "PullRequestEvent":
        action = payload.get("action")
        pr = payload.get("pull_request") or {}
        if action == "opened":
            label = "Opened PR"
        elif action == "closed" and pr.get("merged"):
            label = "Merged PR"
        elif action in ("reopened", "synchronize"):
            label = "Updated PR"
        else:
            return None  # Never show closed-unmerged PRs.
        if not isinstance(pr, dict):
            return None
        pr = dict(pr)
        pr.setdefault("number", payload.get("number"))
        detail = linked_item(pr, repo, "pull", "Pull request")
    elif kind == "PushEvent":
        label = "Pushed code"
        branch = str(payload.get("ref") or "").removeprefix("refs/heads/")
        detail = ("Branch " + safe_text(branch, 45)) if branch else "Repository update"
        commits = payload.get("commits") or []
        if isinstance(commits, list) and commits:
            first = (commits[0] or {}).get("message", "")
            if first:
                detail += " · " + safe_text(str(first).splitlines()[0], 70)
    elif kind == "IssuesEvent" and payload.get("action") == "opened":
        label = "Opened issue"
        detail = linked_item(payload.get("issue") or {}, repo, "issues", "Issue")
    elif kind == "IssueCommentEvent" and payload.get("action") == "created":
        label = "Commented"
        detail = linked_item(payload.get("issue") or {}, repo, "issues", "Discussion")
    elif kind == "PullRequestReviewEvent" and payload.get("action") == "submitted":
        label = "Reviewed PR"
        detail = linked_item(payload.get("pull_request") or {}, repo, "pull", "Pull request")
    elif kind == "CreateEvent":
        label = "Created " + safe_text(payload.get("ref_type") or "ref", 15)
        detail = safe_text(payload.get("ref") or "New repository")
    elif kind == "ReleaseEvent" and payload.get("action") == "published":
        label = "Published release"
        detail = safe_text((payload.get("release") or {}).get("tag_name") or "Release")
    elif kind == "ForkEvent":
        label = "Forked repository"
        detail = "Public fork"
    else:
        return None
    return label, repo, detail


def activity_rows(events, user, limit=8):
    """Use the most recent relevant event for each distinct public repository."""
    candidates = []
    seen_ids = set()
    for event in events:
        event_id = event.get("id")
        actor = (event.get("actor") or {}).get("login", "")
        if not event_id or event_id in seen_ids or actor.lower() != user.lower():
            continue
        seen_ids.add(event_id)
        detail = describe(event)
        if detail is not None:
            candidates.append((parse_time(event["created_at"]), *detail))
    candidates.sort(key=lambda item: item[0], reverse=True)
    rows, seen_repos = [], set()
    for when, label, repo, detail in candidates:
        if repo.lower() in seen_repos:
            continue
        seen_repos.add(repo.lower())
        rows.append(
            f"| {label} in [{repo}](https://github.com/{repo})"
            f" | {detail or '—'} |"
        )
        if len(rows) >= limit:
            break
    return rows


def markdown(events, user):
    rows = [
        "| Recent activity | Details |",
        "| :--- | :--- |",
    ]
    entries = activity_rows(events, user)
    if entries:
        rows.extend(entries)
    else:
        rows.append("| No qualifying recent public activity | — |")
    return "\n".join(rows)


def replace_section(readme, generated):
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README requires one recent-activity marker pair")
    prefix, remainder = readme.split(START, 1)
    _, suffix = remainder.split(END, 1)
    return prefix + START + "\n" + generated + "\n" + END + suffix


def run(user, token, root=ROOT):
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", user):
        raise ValueError("Invalid GitHub account")
    events = fetch_events(user, token)
    target = root / "README.md"
    original = target.read_text(encoding="utf-8")
    updated = replace_section(original, markdown(events, user))
    if updated != original:
        target.write_text(updated, encoding="utf-8")


def main():
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ValueError("GH_TOKEN is required")
    run(os.environ.get("PROFILE_USER", "OKAN-SOFTWARE"), token)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, TypeError, KeyError) as error:
        print(f"Recent activity refresh failed: {error}", file=sys.stderr)
        sys.exit(1)

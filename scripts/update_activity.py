#!/usr/bin/env python3
"""Render public upstream PR activity; Python standard library only."""

import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import html
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
START = "<!-- ACTIVITY:START -->"
END = "<!-- ACTIVITY:END -->"
TZ = ZoneInfo("America/New_York")
def format_sync_time(value, *, compact=False):
    local = value.astimezone(TZ)

    hour = local.hour % 12 or 12
    clock = f"{hour}:{local:%M %p %Z}"

    if compact:
        return f"{local:%b %d} · {clock}"

    return f"{local:%b %d, %Y} at {clock}"
KEYS = ("opened", "merged", "open", "recent")
REPO_URL = re.compile(r"https://api\.github\.com/repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("GitHub timestamps must include a timezone")
    return parsed.astimezone(TZ)


def queries(user, today):
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", user):
        raise ValueError("Invalid GitHub username")
    first = today - timedelta(days=29)
    since = datetime.combine(first, time.min, TZ).astimezone(timezone.utc)
    since_text = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    scope = f"is:pr is:public author:{user} -user:{user}"
    return {
        "opened": f"{scope} created:>={since_text}",
        "merged": f"{scope} is:merged merged:>={since_text}",
        "open": f"{scope} is:open",
        "recent": f"{scope} is:open sort:updated-desc",
    }


def api_search(query, token, *, all_pages=True):
    items = []
    total = None
    for page in range(1, 11):
        params = urlencode({"q": query, "per_page": 100, "page": page})
        request = Request(
            f"https://api.github.com/search/issues?{params}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "okan-profile-activity",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except (HTTPError, URLError, TimeoutError) as error:
            # Do not echo request headers or credentials in Actions logs.
            raise RuntimeError("GitHub API request failed; keeping the previous snapshot") from error
        if payload.get("incomplete_results") is not False:
            raise ValueError("Incomplete GitHub search; keeping the previous snapshot")
        page_total = payload["total_count"]
        if total is None:
            total = page_total
        elif total != page_total:
            raise ValueError("Search changed during pagination; retry the refresh")
        if all_pages and total > 1000:
            raise ValueError("Search exceeds GitHub's 1,000-result limit; narrow the window")
        items.extend(payload["items"])
        if not all_pages or len(items) >= total:
            return {"total_count": total, "incomplete_results": False, "items": items}
        if not payload["items"]:
            break
    raise ValueError("GitHub search did not return the full result set")


def validate_item(item, user):
    match = REPO_URL.fullmatch(item["repository_url"])
    if not match:
        raise ValueError("Unexpected repository URL in search results")
    owner, repo = match.groups()
    if owner.lower() == user.lower():
        raise ValueError("Owner's repositories must not enter upstream counts")
    if item.get("repository", {}).get("private"):
        raise ValueError("Private repository in public activity results")
    if item["user"]["login"].lower() != user.lower():
        raise ValueError("Unexpected PR author")
    number = item["number"]
    if type(number) is not int or number < 1:
        raise ValueError("Invalid PR number")
    expected = f"https://github.com/{owner}/{repo}/pull/{number}"
    if item["html_url"] != expected or "pull_request" not in item:
        raise ValueError("Unexpected PR URL")
    for field in ("created_at", "updated_at"):
        timestamp(item[field])
    merged_at = item["pull_request"].get("merged_at")
    if merged_at:
        timestamp(merged_at)
    if item["state"] not in ("open", "closed"):
        raise ValueError("Unexpected PR state")
    if not isinstance(item["title"], str):
        raise ValueError("Unexpected PR title")
    return f"{owner}/{repo}"


def summarize(bundle, user, today, synced_at=None):
    days = [today - timedelta(days=offset) for offset in reversed(range(30))]
    opened, merged = Counter(), Counter()
    upstreams = set()
    validated = {}
    for key in KEYS:
        payload = bundle[key]
        if payload.get("incomplete_results") is not False:
            raise ValueError("Incomplete activity bundle")
        total = payload["total_count"]
        items = payload["items"]
        if type(total) is not int or total < 0:
            raise ValueError("Invalid result count")
        if key in ("opened", "merged") and len(items) != total:
            raise ValueError("A complete 30-day result set is required")
        if key in ("open", "recent") and len(items) != min(total, 100):
            raise ValueError("Expected a complete first page of results")
        seen = set()
        validated[key] = []
        for item in items:
            repo = validate_item(item, user)
            if item["html_url"] in seen:
                raise ValueError("Duplicate PR in search results")
            seen.add(item["html_url"])
            validated[key].append(item)
            if key not in ("opened", "merged"):
                continue
            raw = item["created_at"] if key == "opened" else item["pull_request"].get("merged_at")
            if not raw:
                raise ValueError("Merged PR is missing its merge date")
            day = timestamp(raw).date()
            if day not in days:
                raise ValueError("PR event falls outside the requested date window")
            (opened if key == "opened" else merged)[day] += 1
            upstreams.add(repo)
    if any(item["state"] != "open" for item in validated["open"]):
        raise ValueError("Closed PR in open search results")
    recent = sorted(
        (item for item in validated["recent"] if item["state"] == "open"),
        key=lambda item: timestamp(item["updated_at"]),
        reverse=True,
    )[:10]
    return {
        "days": days, "opened": opened, "merged": merged,
        "opened_count": sum(opened.values()), "merged_count": sum(merged.values()),
        "open_count": bundle["open"]["total_count"], "repos_count": len(upstreams),
        "recent": recent, "today": today,
        "synced_at": synced_at or datetime.combine(today, time.min, timezone.utc),
    }


def svg_dashboard(data):
    today = data["today"]   
    synced_at = format_sync_time(data["synced_at"], compact=True)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 460" role="img" aria-labelledby="title desc">',
        '<title id="title">Okan’s public upstream pull-request activity</title>',
        f'<desc id="desc">Last 30 days: {data["opened_count"]} PRs opened and {data["merged_count"]} merged in {data["repos_count"]} repositories. {data["open_count"]} PRs currently open. Cyan bars show openings, amber bars show merges. Not a commit or fork count.</desc>',
        '<rect x="1" y="1" width="1198" height="458" rx="16" fill="#0c141e" stroke="#304556"/>',
        '<g font-family="sans-serif">',
        '<circle cx="39" cy="36" r="4" fill="#64d8cd"/>',
        '<text x="55" y="41" font-family="monospace" font-size="13" letter-spacing="2" fill="#e9f1f8">OPEN SOURCE / FIELD NOTES</text>',
        f'<text x="1159" y="41" text-anchor="end" font-family="monospace" font-size="12" fill="#9db4c7">UPDATED · {synced_at}</text>',
    ]
    stats = [
        ("PRs opened", data["opened_count"], "last 30 days", "#64d8cd"),
        ("PRs merged", data["merged_count"], "last 30 days", "#f1bb68"),
        ("Open upstream PRs", data["open_count"], "current · includes drafts", "#e9f1f8"),
        ("Upstream repositories", data["repos_count"], "with an opening or merge", "#e9f1f8"),
    ]
    for index, (label, value, note, color) in enumerate(stats):
        x = 28 + index * 291
        parts.extend([
            f'<rect x="{x}" y="66" width="271" height="118" rx="10" fill="#13212e"/>',
            f'<text x="{x+17}" y="93" font-size="14" fill="#bdcfdd">{label}</text>',
            f'<text x="{x+17}" y="143" font-size="42" font-weight="700" fill="{color}">{value}</text>',
            f'<text x="{x+17}" y="167" font-family="monospace" font-size="11" fill="#91aabe">{note}</text>',
        ])
    parts.extend([
        '<text x="40" y="224" font-size="15" font-weight="600" fill="#e9f1f8">A month of upstream work</text>',
        '<rect x="873" y="214" width="10" height="10" rx="2" fill="#64d8cd"/>',
        '<text x="891" y="224" font-size="12" fill="#bdcfdd">Opened</text>',
        '<rect x="984" y="214" width="10" height="10" rx="2" fill="#f1bb68"/>',
        '<text x="1002" y="224" font-size="12" fill="#bdcfdd">Merged</text>',
    ])
    max_events = max(1, *(data["opened"][day] + data["merged"][day] for day in data["days"]))
    base, chart_height = 374, 118
    for index, day in enumerate(data["days"]):
        x = 42 + index * 37.3
        opened, merged = data["opened"][day], data["merged"][day]
        h_open, h_merge = opened / max_events * chart_height, merged / max_events * chart_height
        parts.append(f'<rect x="{x:.1f}" y="256" width="24" height="118" rx="3" fill="#13212e"/>')
        label = html.escape(f"{day.isoformat()}: {opened} opened, {merged} merged")
        parts.append(f'<g><title>{label}</title>')
        if opened:
            parts.append(f'<rect x="{x:.1f}" y="{base-h_open:.1f}" width="24" height="{h_open:.1f}" fill="#64d8cd"/>')
        if merged:
            parts.append(f'<rect x="{x:.1f}" y="{base-h_open-h_merge:.1f}" width="24" height="{h_merge:.1f}" fill="#f1bb68"/>')
        parts.append('</g>')
        if index in (0, 7, 14, 21, 29):
            anchor = "end" if index == 29 else "start"
            label_x = x + 24 if index == 29 else x
            parts.append(f'<text x="{label_x:.1f}" y="395" text-anchor="{anchor}" font-family="monospace" font-size="11" fill="#91aabe">{day.strftime("%b %d")}</text>')
    parts.extend([
        '<path d="M28 414H1172" stroke="#304556"/>',
        f'<text x="40" y="440" font-family="monospace" font-size="12" fill="#bdcfdd">TODAY / {data["opened"][today]} opened · {data["merged"][today]} merged</text>',
        '<text x="1160" y="440" text-anchor="end" font-family="monospace" font-size="11" fill="#91aabe">PUBLIC UPSTREAM ACTIVITY</text>',
        '</g></svg>\n',
    ])
    return "\n".join(parts)


def markdown_text(value):
    value = " ".join(value.split())
    if len(value) > 110:
        value = value[:107].rstrip() + "…"
    value = html.escape(value, quote=False)
    return re.sub(r"([\\`*_[\]{}|])", r"\\\1", value)


def markdown_section(data, user):
    synced_at = format_sync_time(data["synced_at"])
    summary = f'{data["opened_count"]} PRs opened, {data["merged_count"]} merged, {data["open_count"]} currently open, {data["repos_count"]} upstream repositories'
    rows = [
        f'![Public upstream activity: {summary}](./assets/activity.svg)',
        "",
        f'**Last profile update: {synced_at}**',
        "",
        f'**{data["opened_count"]} opened · {data["merged_count"]} merged · {data["open_count"]} open now · {data["repos_count"]} upstream repositories**',
        "",
        "### Latest upstream work",
        "",
        "| State | Contribution | Repository |",
        "| :--- | :--- | :--- |",
    ]
    for item in data["recent"]:
        repo = REPO_URL.fullmatch(item["repository_url"])
        name = "/".join(repo.groups())
        state = "⚙️ Draft" if item.get("draft") else "🟢 Open"
        title = markdown_text(item["title"])
        rows.append(f'| {state} | [{title} · #{item["number"]}]({item["html_url"]}) | [{name}](https://github.com/{name}) |')
    if not data["recent"]:
        rows.append("| — | No public upstream pull requests yet. | — |")
    query = urlencode({"q": f"is:pr is:public author:{user} -user:{user} is:open sort:updated-desc"})
    rows.extend([
        "", f'[Explore all open upstream contributions →](https://github.com/pulls?{query})', "",
        "<sub>The chart records PR openings and merges—not commits or fork creation. The open count includes drafts across all dates. Private work and my own repositories are excluded. Counts do not imply approval or passing CI. [How this updates](./PROFILE.md)</sub>",
    ])
    return "\n".join(rows)


def replace_section(readme, section):
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README must contain exactly one activity marker pair")
    before, rest = readme.split(START, 1)
    _, after = rest.split(END, 1)
    return before + START + "\n" + section + "\n" + END + after


def write_outputs(bundle, user, today, root=ROOT, synced_at=None):
    import hashlib

    data = summarize(bundle, user, today, synced_at)

    readme = root / "README.md"
    svg_path = root / "assets" / "activity.svg"

    original = readme.read_text(encoding="utf-8")

    # Create a fingerprint of the displayed information,
    # excluding the changing synchronization timestamp.
    fixed_data = {
        **data,
        "synced_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
    }

    content = (
        markdown_section(fixed_data, user)
        + "\n"
        + svg_dashboard(fixed_data)
    )

    fingerprint = hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()

    marker = f"<!-- ACTIVITY:SNAPSHOT:{fingerprint} -->"

    # Generate both outputs before writing either file.
    updated = replace_section(
        original,
        markdown_section(data, user) + "\n\n" + marker,
    )

    svg = svg_dashboard(data)

    # The synchronization time is visible data, even when PR activity is unchanged.
    # Skip writes only when both complete outputs already match this refresh.
    if updated == original and svg_path.is_file() and svg_path.read_text(encoding="utf-8") == svg:
        print("Profile snapshot already current.")
        return data

    readme.write_text(updated, encoding="utf-8")
    svg_path.write_text(svg, encoding="utf-8")

    print("Profile snapshot refreshed, including the synchronization time.")
    return data   


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--today", type=date.fromisoformat)
    parser.add_argument("--synced-at", help="Snapshot time in timezone-aware ISO 8601 format")
    args = parser.parse_args()
    user = os.environ.get("PROFILE_USER", "OKAN-SOFTWARE")
    today = args.today or datetime.now(TZ).date()
    search_queries = queries(user, today)
    if args.fixture:
        bundle = json.loads(args.fixture.read_text(encoding="utf-8"))
    else:
        token = os.environ.get("GH_TOKEN")
        if not token:
            parser.error("Set GH_TOKEN before fetching GitHub activity")
        bundle = {
            key: api_search(query, token, all_pages=key in ("opened", "merged"))
            for key, query in search_queries.items()
        }
    synced_at = timestamp(args.synced_at) if args.synced_at else datetime.now(timezone.utc)
    data = write_outputs(bundle, user, today, synced_at=synced_at)
    print(f'Activity updated: {data["opened_count"]} opened, {data["merged_count"]} merged; public upstream PRs only.')


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        print(f"Activity refresh failed: {error}", file=sys.stderr)
        sys.exit(1)


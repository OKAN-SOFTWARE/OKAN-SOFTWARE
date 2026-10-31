#!/usr/bin/env python3
"""Display merged upstream PR diff statistics separately from original-repo LOC.

PR additions/deletions describe entire merged pull-request diffs, not uniquely
authored source lines. These values must never be mixed into the Code Matrix.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

from update_activity import api_search
from update_developer_journey import api_get

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- UPSTREAM_CODE:START -->"
END = "<!-- UPSTREAM_CODE:END -->"
CACHE = Path("data/upstream-code-cache.json")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
API_PR = re.compile(
    r"^https://api\.github\.com/repos/([A-Za-z0-9_.-]+)/"
    r"([A-Za-z0-9_.-]+)/pulls/([1-9][0-9]*)$"
)


def upstream_items(user, token):
    """Get the complete set of public, merged PRs authored by this user."""
    query = f"is:pr is:merged is:public author:{user} -user:{user}"
    payload = api_search(query, token, all_pages=True)
    if payload.get("incomplete_results") is not False:
        raise ValueError("Incomplete upstream PR search")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != payload.get("total_count"):
        raise ValueError("Incomplete upstream PR results")
    results = {}
    for item in items:
        pr = item.get("pull_request") or {}
        url = pr.get("url", "")
        match = API_PR.fullmatch(url)
        if not match:
            raise ValueError("Unexpected upstream pull request URL")
        owner, name, number = match.groups()
        full = f"{owner}/{name}"
        if owner.casefold() == user.casefold():
            continue
        if item.get("repository_url") != f"https://api.github.com/repos/{full}":
            raise ValueError("PR and repository URL mismatch")
        if not pr.get("merged_at"):
            raise ValueError("Merged PR has no merge date")
        if url in results:
            raise ValueError("Duplicate merged PR in search")
        results[url] = {
            "repo": full, "number": int(number),
            "merged_at": pr["merged_at"],
        }
    return results


def collect(user, token, cached):
    """Reuse immutable merged PR diffs and fetch new merged PRs only."""
    if not isinstance(cached, dict):
        raise ValueError("Invalid upstream cache")
    discovered = upstream_items(user, token)
    result = {}
    for url, meta in discovered.items():
        prior = cached.get(url)
        if (isinstance(prior, dict)
                and all(prior.get(key) == value for key, value in meta.items())
                and type(prior.get("additions")) is int and prior["additions"] >= 0
                and type(prior.get("deletions")) is int and prior["deletions"] >= 0):
            result[url] = prior
            continue
        detail = api_get(
            url.removeprefix("https://api.github.com"), token
        )
        if (not isinstance(detail, dict)
                or type(detail.get("additions")) is not int
                or type(detail.get("deletions")) is not int
                or detail["additions"] < 0 or detail["deletions"] < 0
                or not detail.get("merged_at")):
            raise ValueError("Invalid upstream PR diff statistics")
        result[url] = {
            **meta,
            "additions": detail["additions"],
            "deletions": detail["deletions"],
        }
    return result


def render(data):
    """Render all-time merged PR diff totals and five recent PRs as real text."""
    items = sorted(
        data.items(),
        key=lambda entry: (entry[1]["merged_at"], entry[0]),
        reverse=True,
    )
    added = sum(item["additions"] for _, item in items)
    removed = sum(item["deletions"] for _, item in items)
    rows = [
        "### 🟢 UPSTREAM CONTRIBUTIONS // MERGED PRS",
        "",
        f"**{len(items):,} merged PRs** · **+{added:,} lines added** · "
        f"**−{removed:,} lines removed**",
        "",
        "| Upstream repository | Merged pull request | Added | Removed |",
        "| :--- | :--- | ---: | ---: |",
    ]
    for _, item in items[:5]:
        full, number = item["repo"], item["number"]
        link = f"https://github.com/{full}"
        rows.append(
            f"| [{full}]({link}) | [#{number}]({link}/pull/{number}) "
            f"| +{item['additions']:,} | −{item['deletions']:,} |"
        )
    if not items:
        rows.append("| No merged upstream PRs found | — | 0 | 0 |")
    rows.extend([
        "",
        "<sub>Automatically refreshed · Public merged PRs in other owners' "
        "repositories · Entire PR diff additions/deletions, not unique lines "
        "personally authored · May include tests, generated files, or other "
        "contributors' changes · Excluded from Code Stats language percentages</sub>",
    ])
    return "\n".join(rows)


def replace_section(readme, generated):
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README must contain exactly one upstream marker pair")
    before, rest = readme.split(START, 1)
    _, after = rest.split(END, 1)
    return before + START + "\n" + generated + "\n" + END + after


def run(user, token, root=ROOT):
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", user):
        raise ValueError("Invalid GitHub username")
    cache_file = root / CACHE
    old = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    data = collect(user, token, old)
    readme_file = root / "README.md"
    original = readme_file.read_text(encoding="utf-8")
    updated = replace_section(original, render(data))
    snapshot = json.dumps(dict(sorted(data.items())), indent=2, ensure_ascii=False) + "\n"
    # Fetch and validate everything before changing any output.
    if updated != original:
        readme_file.write_text(updated, encoding="utf-8")
    if not cache_file.exists() or cache_file.read_text(encoding="utf-8") != snapshot:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(snapshot, encoding="utf-8")
    print(f"Upstream code stats: {len(data)} merged PRs (diff totals; separate from LOC).")


if __name__ == "__main__":
    try:
        token = os.environ.get("GH_TOKEN")
        if not token:
            raise ValueError("GH_TOKEN is required")
        run(os.environ.get("PROFILE_USER", "OKAN-SOFTWARE"), token)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        print(f"Upstream code refresh failed: {error}", file=sys.stderr)
        sys.exit(1)

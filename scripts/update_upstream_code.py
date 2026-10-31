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



LANGUAGE_SUFFIXES = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript",
    ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java",
    ".kt": "Kotlin", ".cs": "C#", ".rs": "Rust", ".go": "Go",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
    ".c": "C", ".cpp": "C++", ".cc": "C++", ".h": "C",
    ".hpp": "C++", ".html": "HTML", ".css": "CSS",
    ".scss": "SCSS", ".vue": "Vue", ".sql": "SQL",
    ".sh": "Shell", ".ps1": "PowerShell", ".r": "R",
}
FRAMEWORK_PATTERNS = {
    "React": r"\bfrom\s+['\"]react(?:/[^'\"]*)?['\"]",
    "Next.js": r"\bfrom\s+['\"]next(?:/[^'\"]*)?['\"]",
    "Express": r"\bfrom\s+['\"]express['\"]",
    "Vitest": r"\bfrom\s+['\"]vitest['\"]",
    "Playwright": r"\bfrom\s+['\"]@playwright/test['\"]",
    "pytest": r"^\s*(?:import\s+pytest\b|from\s+pytest\b)",
    "Django": r"^\s*(?:import\s+django\b|from\s+django\b)",
    "FastAPI": r"^\s*from\s+fastapi\b",
    "Flask": r"^\s*(?:import\s+flask\b|from\s+flask\b)",
    "NumPy": r"^\s*(?:import\s+numpy\b|from\s+numpy\b)",
    "Pandas": r"^\s*(?:import\s+pandas\b|from\s+pandas\b)",
    "JUnit": r"^\s*import\s+org\.junit\b",
    "Spring Boot": r"^\s*import\s+org\.springframework\b",
}


def classify_changed_files(files):
    """Detect source languages and newly imported frameworks, not PR authorship."""
    if not isinstance(files, list):
        raise ValueError("Invalid PR files")
    languages, frameworks = set(), set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Invalid PR file")
        filename, added = item.get("filename"), item.get("additions")
        if not isinstance(filename, str) or type(added) is not int or added < 0:
            raise ValueError("Invalid PR file metadata")
        if added == 0 or item.get("status") == "removed":
            continue
        lower = filename.casefold()
        lang = ("Dockerfile" if Path(lower).name == "dockerfile"
                else LANGUAGE_SUFFIXES.get(Path(lower).suffix))
        if not lang:
            continue
        languages.add(lang)
        patch = item.get("patch")
        if isinstance(patch, str):
            for line in patch.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    for framework, pattern in FRAMEWORK_PATTERNS.items():
                        if re.search(pattern, line[1:]):
                            frameworks.add(framework)
    return sorted(languages), sorted(frameworks)


def fetch_pr_files(url, token, expected_count=None):
    """Fetch every page, rejecting truncated results."""
    if not API_PR.fullmatch(url):
        raise ValueError("Invalid PR URL")
    if expected_count is not None and (
        type(expected_count) is not int or not 0 <= expected_count <= 3000
    ):
        raise ValueError("Invalid changed-files count")
    files = []
    for page in range(1, 31):
        path = url.removeprefix("https://api.github.com")
        batch = api_get(f"{path}/files?per_page=100&page={page}", token)
        if not isinstance(batch, list):
            raise ValueError("Invalid PR files response")
        files.extend(batch)
        if len(batch) < 100:
            if expected_count is not None and len(files) != expected_count:
                raise ValueError("Incomplete PR files")
            return files
    raise ValueError("PR exceeds file listing limit")


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
                and type(prior.get("deletions")) is int and prior["deletions"] >= 0
                and isinstance(prior.get("languages"), list)
                and all(isinstance(v, str) for v in prior["languages"])
                and isinstance(prior.get("frameworks"), list)
                and all(isinstance(v, str) for v in prior["frameworks"])):
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
        languages, frameworks = classify_changed_files(
            fetch_pr_files(url, token, detail.get("changed_files"))
        )
        result[url] = {
            **meta,
            "additions": detail["additions"],
            "deletions": detail["deletions"],
            "languages": languages,
            "frameworks": frameworks,
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
        "| Upstream repository | Merged pull request | Added | Removed | Languages / Frameworks |",
        "| :--- | :--- | ---: | ---: | :--- |",
    ]
    for _, item in items[:5]:
        full, number = item["repo"], item["number"]
        link = f"https://github.com/{full}"
        technologies = item.get("languages", []) + item.get("frameworks", [])
        labels = ", ".join(technologies) if technologies else "Documentation / other"
        rows.append(
            f"| [{full}]({link}) | [#{number}]({link}/pull/{number}) "
            f"| +{item['additions']:,} | −{item['deletions']:,} | {labels} |"
        )
    if not items:
        rows.append("| No merged upstream PRs found | — | 0 | 0 | — |")
    rows.extend([
        "",
        "<sub>Automatically refreshed · Public merged PRs in other owners' "
        "repositories · Entire PR diff additions/deletions, not unique lines "
        "personally authored · May include tests, generated files, or other "
        "contributors' changes · Languages from added source files; frameworks "
        "only from added imports · Excluded from Code Stats language percentages</sub>",
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

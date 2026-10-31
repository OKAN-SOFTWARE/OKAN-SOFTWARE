from collections import Counter
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "update_developer_journey.py"
SPEC = importlib.util.spec_from_file_location("journey", SCRIPT)
journey = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(journey)

NOW = datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)


def commit(sha, at, repo="OKAN-SOFTWARE/example"):
    return {
        "sha": sha, "repository": {"full_name": repo},
        "commit": {"committer": {"date": at}},
    }


def event(number, kind, at, action=None, merged=False, actor="OKAN-SOFTWARE"):
    return {
        "id": str(number), "actor": {"login": actor},
        "created_at": at, "type": kind,
        "repo": {"name": "upstream/project"},
        "payload": {"action": action, "pull_request": {"merged": merged}},
    }


def sample():
    repos = [{
        "full_name": "OKAN-SOFTWARE/example",
        "language": "Python", "fork": False, "private": False,
    }]
    commits = [
        commit("abc", "2026-10-30T13:15:00Z"),
        commit("def", "2026-10-30T23:20:00Z"),
        commit("abc", "2026-10-30T13:15:00Z"),
    ]
    events = [
        event(1, "PullRequestEvent", "2026-10-30T20:00:00Z", action="opened"),
        event(2, "PullRequestEvent", "2026-10-30T21:00:00Z", action="closed", merged=False),
        event(3, "PullRequestEvent", "2026-10-30T21:30:00Z", action="closed", merged=True),
        event(4, "IssuesEvent", "2026-10-30T22:00:00Z", action="opened"),
        event(5, "PullRequestEvent", "2026-10-30T18:00:00Z", action="opened", actor="not-okan"),
    ]
    return repos, Counter({"Python": 800, "JavaScript": 200}), commits, events


class JourneyTests(unittest.TestCase):
    def test_real_counts_timezone_and_merge_rules(self):
        data = journey.summarize("OKAN-SOFTWARE", *sample(), NOW)
        self.assertEqual(2, data["total_commits"])
        self.assertEqual(1, data["repos"])
        self.assertEqual(2, data["weekdays"][4])
        self.assertEqual(1, data["rhythm"][0][1])
        self.assertEqual(1, data["rhythm"][2][1])
        self.assertEqual(2, data["current"]["prs"])
        self.assertEqual(1, data["current"]["issues"])

    def test_markdown_keeps_history_without_hidden_stack(self):
        data = journey.summarize("OKAN-SOFTWARE", *sample(), NOW)
        rendered = journey.markdown(data, journey.archive_updates({}, data))
        self.assertIn("Public GitHub History", rendered)
        self.assertNotIn("My Development Stack", rendered)
        self.assertIn("2 authored commits · 1 public original repositories", rendered)
        for excluded in ("When I Commit", "Activity |", "Source bytes",
                         "CODE MATRIX · Language Distribution", journey.MATRIX_START):
            self.assertNotIn(excluded, rendered)
        self.assertNotIn("<img", rendered)

    def test_development_stack_is_visible_before_footer(self):
        readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(
            encoding="utf-8"
        )
        heading = "### 🧩 My Development Stack"
        footer = "assets/phoenix-footer-animated.gif"
        self.assertEqual(1, readme.count(heading))
        self.assertLess(readme.index("<!-- UPSTREAM_CODE:END -->"), readme.index(heading))
        self.assertLess(readme.index(heading), readme.index(footer))
        self.assertIn("| **Backend** |", readme)
        self.assertIn("| **Cloud** |", readme)
        self.assertNotIn("Explore public GitHub history", readme)
        self.assertNotIn("### Public GitHub History", readme)
        self.assertNotIn(journey.START, readme)

    def test_loc_counts_nonblank_source_and_excludes_generated_dirs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "dist").mkdir()
            (root / "node_modules - Copy" / "three").mkdir(parents=True)
            (root / "node_modules - Copy" / "three" / "three.js").write_text(
                "third party\n" * 100, encoding="utf-8"
            )
            (root / "src" / "app.js").write_text(
                "const a = 1;\n\n// a comment\nconst b = 2;\n", encoding="utf-8"
            )
            (root / "src" / "types.ts").write_text(
                "type A = string;\n\n", encoding="utf-8"
            )
            (root / "dist" / "bundle.js").write_text(
                "generated\n", encoding="utf-8"
            )
            (root / "src" / "bundle.min.js").write_text(
                "minified\n", encoding="utf-8"
            )
            result = journey.lines_in_checkout(root)
            self.assertEqual(3, result["JavaScript"])
            self.assertEqual(1, result["TypeScript"])
            self.assertTrue(journey.ignored_directory("node_modules - Copy"))
            self.assertTrue(journey.ignored_directory("node_modules (copy)"))
            self.assertFalse(journey.ignored_directory("src"))

    def test_archive_keeps_previous_days_and_caps_to_a_year(self):
        data = journey.summarize("OKAN-SOFTWARE", *sample(), NOW)
        old = {
            "2026-10-29": {"commits": 3, "repos": 1, "pr_events": 0, "issues": 0},
            "2020-01-01": {"commits": 5, "repos": 1, "pr_events": 0, "issues": 0},
        }
        updated = journey.archive_updates(old, data)
        self.assertEqual(3, updated["2026-10-29"]["commits"])
        self.assertNotIn("2020-01-01", updated)
        self.assertEqual(2, updated["2026-10-30"]["commits"])

    def test_marker_validation_preserves_surrounding_content(self):
        original = f"header\n{journey.START}\nold\n{journey.END}\nfooter"
        updated = journey.replace_section(original, "new")
        self.assertEqual(
            f"header\n{journey.START}\nnew\n{journey.END}\nfooter", updated
        )
        with self.assertRaises(ValueError):
            journey.replace_section("missing markers", "new")

    def test_fetch_filters_forks_and_attaches_repo(self):
        repo = {"full_name": "OKAN-SOFTWARE/example", "language": "Python",
                "fork": False, "private": False}
        fork = {"full_name": "OKAN-SOFTWARE/fork", "fork": True, "private": False}
        profile = {"full_name": "OKAN-SOFTWARE/OKAN-SOFTWARE",
                   "fork": False, "private": False}

        def fake_get(path, token):
            if "/users/" in path and "/repos?" in path:
                return [repo, fork, profile]
            if path.endswith("/languages"):
                return {"Python": 800}
            if "/commits?" in path:
                return [commit("abc", "2026-10-30T13:15:00Z")]
            if "/events/public?" in path:
                return []
            raise AssertionError(path)

        with patch.object(journey, "api_get", side_effect=fake_get):
            repos, langs, commits, events = journey.fetch_public_data("OKAN-SOFTWARE", "token")
        self.assertEqual([repo], repos)
        self.assertEqual(800, langs["Python"])
        self.assertEqual("OKAN-SOFTWARE/example", commits[0]["repository"]["full_name"])
        self.assertEqual([], events)

    def test_failed_fetch_does_not_modify_readme(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readme = root / "README.md"
            readme.write_text("<!-- CODE_MATRIX:START -->\nold\n<!-- CODE_MATRIX:END -->", encoding="utf-8")
            with patch.object(journey, "fetch_public_data", side_effect=RuntimeError("API failure")):
                with self.assertRaises(RuntimeError):
                    journey.run("OKAN-SOFTWARE", "token", root, NOW)
            self.assertIn("old", readme.read_text(encoding="utf-8"))
            self.assertFalse((root / "data" / "chronicles.json").exists())


    def test_generation_keeps_code_dna_and_omits_daily_section(self):
        original = (f"intro\n{journey.MATRIX_START}\nold matrix\n{journey.MATRIX_END}\n"
                    "footer")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readme = root / "README.md"
            readme.write_text(original, encoding="utf-8")
            with (
                patch.object(journey, "fetch_public_data", return_value=sample()),
                patch.object(journey, "count_source_lines", return_value=(Counter({"Python": 5}), {})),
            ):
                journey.run("OKAN-SOFTWARE", "token", root, NOW)
            content = readme.read_text(encoding="utf-8")
            self.assertIn("OKAN\'S CODE STATS // LIVE UPDATES", content)
            self.assertNotIn("Public GitHub History", content)
            self.assertNotIn("Explore public GitHub history", content)
            self.assertNotIn("When I Commit", content)
            self.assertNotIn("My Development Stack", content)
            self.assertNotIn("Today's Coding Adventure", content)
            self.assertNotIn("Weekly Coding Adventure", content)
            self.assertIn("intro", content)
            self.assertIn("footer", content)


    def test_matrix_sorts_by_actual_loc_and_uses_small_logos(self):
        counts = Counter({"Python": 480, "Java": 520, "Rust": 0})
        rendered = journey.matrix_markdown(counts, 2)
        self.assertIn("OKAN'S CODE STATS // LIVE UPDATES", rendered)
        self.assertIn("**TOTAL LINES OF CODE: 1,000**", rendered)
        self.assertIn("| Language | Lines | Share | Distribution |", rendered)
        self.assertIn("java/java-original.svg", rendered)
        self.assertIn("python/python-original.svg", rendered)
        self.assertIn('width="18" height="18"', rendered)
        self.assertIn("**Java** | 520 | **52.00%** |", rendered)
        self.assertIn("**Python** | 480 | **48.00%** |", rendered)
        self.assertLess(rendered.index("**Java** | 520"), rendered.index("**Python** | 480"))
        self.assertNotIn("Rust", rendered)
        self.assertNotIn("Source bytes", rendered)
        self.assertNotIn("<svg", rendered)
        self.assertNotIn("LANGUAGE DISTRIBUTION", rendered)
        self.assertNotIn("**2 languages**", rendered)
        self.assertIn("🟩", rendered)
        self.assertIn("⬛", rendered)

    def test_matrix_only_preserves_other_readme_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readme = root / "README.md"
            readme.write_text(
                "hero\\n" + journey.MATRIX_START + "\\nold table\\n" +
                journey.MATRIX_END + "\\nOther statistics\\nfooter",
                encoding="utf-8",
            )
            repo = {"full_name": "OKAN-SOFTWARE/example", "fork": False,
                    "private": False, "default_branch": "main"}
            cached = {"OKAN-SOFTWARE/example": {
                "sha": "a" * 40, "rules": journey.COUNTING_RULE_VERSION,
                "lines": {"Java": 480, "Python": 520}
            }}
            with (
                patch.object(journey, "public_original_repos", return_value=[repo]),
                patch.object(journey, "count_source_lines",
                             return_value=(Counter({"Java": 480, "Python": 520}), cached)),
            ):
                journey.run_matrix_only("OKAN-SOFTWARE", "token", root)
            updated = readme.read_text(encoding="utf-8")
            self.assertIn("hero", updated)
            self.assertIn("Other statistics", updated)
            self.assertIn("footer", updated)
            self.assertNotIn("old table", updated)
            self.assertIn("**Python** | 520 | **52.00%** |", updated)
            self.assertIn("**Java** | 480 | **48.00%** |", updated)
            self.assertTrue((root / "data" / "code-matrix-cache.json").exists())

    def test_cached_repo_reuses_exact_head_sha_without_cloning(self):
        repo = {"full_name": "OKAN-SOFTWARE/example", "fork": False,
                "private": False, "default_branch": "main", "pushed_at": "2026-10-31"}
        sha = "a" * 40
        cache = {repo["full_name"]: {"sha": sha, "rules": journey.COUNTING_RULE_VERSION, "lines": {"Python": 480}}}
        with (
            patch.object(journey, "api_get", return_value={"sha": sha}),
            patch.object(journey.subprocess, "run",
                         side_effect=AssertionError("cache hit must not clone")),
        ):
            counts, refreshed = journey.count_source_lines([repo], "token", cache)
        self.assertEqual(480, counts["Python"])
        self.assertEqual(cache, refreshed)

    def test_cache_invalidated_when_counting_rules_change(self):
        repo = {"full_name": "OKAN-SOFTWARE/example", "fork": False,
                "private": False, "default_branch": "main", "pushed_at": "2026-10-31"}
        old_cache = {repo["full_name"]: {"sha": "a" * 40, "lines": {"JavaScript": 270000}}}
        with (
            patch.object(journey, "api_get", return_value={"sha": "a" * 40}),
            patch.object(journey.subprocess, "run") as cloned,
            patch.object(journey, "lines_in_checkout",
                         return_value=Counter({"JavaScript": 200})),
        ):
            cloned.return_value.returncode = 0
            counts, refreshed = journey.count_source_lines([repo], "token", old_cache)
        self.assertEqual(200, counts["JavaScript"])
        self.assertEqual(journey.COUNTING_RULE_VERSION,
                         refreshed[repo["full_name"]]["rules"])
        cloned.assert_called_once()

    def test_cache_invalidated_on_new_commit_sha(self):
        repo = {"full_name": "OKAN-SOFTWARE/example", "fork": False,
                "private": False, "default_branch": "main", "pushed_at": "2026-10-31"}
        cache = {repo["full_name"]: {"sha": "a" * 40, "rules": journey.COUNTING_RULE_VERSION, "lines": {"Python": 480}}}
        with (
            patch.object(journey, "api_get", return_value={"sha": "b" * 40}),
            patch.object(journey.subprocess, "run") as cloned,
            patch.object(journey, "lines_in_checkout",
                         return_value=Counter({"Python": 510})),
        ):
            cloned.return_value.returncode = 0
            counts, refreshed = journey.count_source_lines([repo], "token", cache)
        self.assertEqual(510, counts["Python"])
        self.assertEqual("b" * 40, refreshed[repo["full_name"]]["sha"])
        cloned.assert_called_once()

    def test_readme_omits_daily_and_exposes_stack(self):
        readme = (SCRIPT.parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("Today's Coding Adventure", readme)
        self.assertNotIn("<!-- WEEKLY:START -->", readme)
        self.assertNotIn("Weekly Coding Adventure", readme)
        self.assertNotIn("Activity sync", readme)
        self.assertIn("## Contribution Snake", readme)
        self.assertIn("## Languages & Toolkit", readme)
        self.assertIn("assets/ember/stack.svg", readme)
        self.assertIn("<!-- CODE_MATRIX:START -->", readme)
        self.assertNotIn("Source bytes", readme)
        self.assertNotIn("When I Commit", readme)
        self.assertIn("OKAN\'S CODE STATS // LIVE UPDATES", readme)
        self.assertIn("javascript/javascript-original.svg", readme)
        self.assertLess(readme.index(journey.MATRIX_START), readme.index("<details>"))
        self.assertIn("<!-- MATRIX_PROJECTS:START -->", readme)
        self.assertIn("Tools built around real needs", readme)
        self.assertIn("assets/phoenix-hero-animated.gif", readme)



if __name__ == "__main__":
    unittest.main()

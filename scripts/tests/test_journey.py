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

    def test_markdown_is_text_only_and_has_requested_sections(self):
        data = journey.summarize("OKAN-SOFTWARE", *sample(), NOW)
        data["locs"] = Counter({"Python": 120, "JavaScript": 45})
        archive = journey.archive_updates({}, data)
        rendered = journey.markdown(data, archive)
        for phrase in (
            "Code DNA", "When I Commit", "I Mostly Code in Python",
            "My Development Stack",
            "| Language | Line of Codes | Source bytes | Activity |",
            "| Python | 120 | 800 |",
        ):
            self.assertIn(phrase, rendered)
        for excluded in (
            "Most Productive Commit Day", "The Phoenix Chronicles",
            "Programming Languages", "Authored commits | Repositories touched",
            "Explore recent daily adventures", "| Language | Source bytes | Share",
            "Today's Coding Adventure", "Today's Public Activity Trail",
            "Languages of Repositories Active Today", "**Achievements:**",
        ):
            self.assertNotIn(excluded, rendered)
        self.assertNotIn("![", rendered)
        self.assertNotIn("<img", rendered)
        self.assertIn("█", rendered)
        self.assertIn("░", rendered)

    def test_loc_counts_nonblank_source_and_excludes_generated_dirs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "dist").mkdir()
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
            readme.write_text(f"{journey.START}\nold\n{journey.END}", encoding="utf-8")
            with patch.object(journey, "fetch_public_data", side_effect=RuntimeError("API failure")):
                with self.assertRaises(RuntimeError):
                    journey.run("OKAN-SOFTWARE", "token", root, NOW)
            self.assertIn("old", readme.read_text(encoding="utf-8"))
            self.assertFalse((root / "data" / "chronicles.json").exists())


    def test_generation_keeps_code_dna_and_omits_daily_section(self):
        original = f"intro\n{journey.START}\nold\n{journey.END}\nfooter"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readme = root / "README.md"
            readme.write_text(original, encoding="utf-8")
            with (
                patch.object(journey, "fetch_public_data", return_value=sample()),
                patch.object(journey, "count_source_lines", return_value=Counter({"Python": 5})),
            ):
                journey.run("OKAN-SOFTWARE", "token", root, NOW)
            content = readme.read_text(encoding="utf-8")
            self.assertIn("Code DNA", content)
            self.assertIn("My Development Stack", content)
            self.assertNotIn("Today's Coding Adventure", content)
            self.assertNotIn("Weekly Coding Adventure", content)
            self.assertIn("intro", content)
            self.assertIn("footer", content)


    def test_readme_omits_daily_and_exposes_stack(self):
        readme = (SCRIPT.parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("Today's Coding Adventure", readme)
        self.assertNotIn("<!-- WEEKLY:START -->", readme)
        self.assertNotIn("Weekly Coding Adventure", readme)
        self.assertNotIn("Activity sync", readme)
        self.assertIn("## Contribution Snake", readme)
        self.assertIn("## Languages & Toolkit", readme)
        self.assertIn("assets/ember/stack.svg", readme)
        self.assertIn("<!-- MATRIX_PROJECTS:START -->", readme)
        self.assertIn("Tools built around real needs", readme)
        self.assertIn("assets/phoenix-hero-animated.gif", readme)



if __name__ == "__main__":
    unittest.main()

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "update_recent_activity.py"
SPEC = importlib.util.spec_from_file_location("recent", SCRIPT)
recent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recent)


def event(number, kind, repo, at, payload=None, actor="OKAN-SOFTWARE"):
    return {
        "id": str(number), "type": kind,
        "actor": {"login": actor}, "repo": {"name": repo},
        "created_at": at, "payload": payload or {},
    }


class RecentActivityTests(unittest.TestCase):
    def test_latest_action_per_repo_and_eastern_times(self):
        events = [
            event(1, "PushEvent", "upstream/project", "2026-10-30T18:00:00Z",
                  {"ref": "refs/heads/main"}),
            event(2, "PullRequestEvent", "upstream/project", "2026-10-30T21:00:00Z",
                  {"action": "opened", "pull_request": {
                      "number": 17, "title": "fix: update tests"}}),
            event(3, "IssuesEvent", "upstream/another", "2026-10-30T20:00:00Z",
                  {"action": "opened", "issue": {"number": 8, "title": "Unexpected output"}}),
            event(4, "PushEvent", "upstream/other", "2026-10-30T22:00:00Z",
                  {"ref": "refs/heads/dev"}, actor="someone-else"),
        ]
        rows = recent.activity_rows(events, "OKAN-SOFTWARE")
        self.assertEqual(2, len(rows))
        self.assertIn("Opened PR", rows[0])
        self.assertIn("Oct 30, 05:00 PM EDT", rows[0])
        self.assertIn("/upstream/project/pull/17", rows[0])
        self.assertNotIn("Pushed code", "\n".join(rows))
        self.assertIn("Opened issue", rows[1])

    def test_closed_unmerged_pr_excluded_and_merged_included(self):
        entries = [
            event(1, "PullRequestEvent", "upstream/closed", "2026-10-30T23:00:00Z",
                  {"action": "closed", "pull_request": {"number": 1, "merged": False}}),
            event(2, "PullRequestEvent", "upstream/merged", "2026-10-30T22:00:00Z",
                  {"action": "closed", "pull_request": {
                      "number": 2, "merged": True, "title": "add coverage"}}),
        ]
        output = recent.markdown(entries, "OKAN-SOFTWARE")
        self.assertNotIn("upstream/closed", output)
        self.assertIn("Merged PR", output)
        self.assertIn("add coverage", output)

    def test_escapes_untrusted_titles(self):
        item = {"number": 5, "title": "Fix | [evil](https://example.com) <script>"}
        linked = recent.linked_item(item, "upstream/repo", "pull", "PR")
        self.assertIn(r"\|", linked)
        self.assertNotIn("<script>", linked)
        self.assertIn("https://github.com/upstream/repo/pull/5", linked)
        self.assertIn(r"\[evil\]", linked)

    def test_limits_rows_and_ignores_duplicate_event_ids(self):
        items = [
            event(i, "PushEvent", f"owner/repo{i}", f"2026-10-30T{i:02d}:00:00Z")
            for i in range(1, 11)
        ]
        items.append(items[-1])
        rows = recent.activity_rows(items, "OKAN-SOFTWARE", limit=4)
        self.assertEqual(4, len(rows))
        self.assertIn("owner/repo10", rows[0])

    def test_readme_embeds_text_recent_feed_and_snake_artwork(self):
        readme = (SCRIPT.parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertEqual(1, readme.count(recent.START))
        self.assertEqual(1, readme.count(recent.END))
        self.assertIn("Auto-checked every hour", readme)
        self.assertIn("assets/contribution-snake.svg", readme)
        self.assertIn("assets/contribution-snake-dark.svg", readme)
        self.assertIn("I Mostly Code in", readme)

    def test_empty_table_honest(self):
        result = recent.markdown([], "OKAN-SOFTWARE")
        self.assertIn("No qualifying recent public activity", result)

    def test_marker_safety_and_integrated_refresh(self):
        original = f"intro\n{recent.START}\nold\n{recent.END}\nfooter"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "README.md"
            target.write_text(original, encoding="utf-8")
            events = [event(1, "PushEvent", "example/repo",
                            "2026-10-30T18:00:00Z", {"ref": "refs/heads/main"})]
            with patch.object(recent, "fetch_events", return_value=events):
                recent.run("OKAN-SOFTWARE", "token", root)
            output = target.read_text(encoding="utf-8")
            self.assertIn("example/repo", output)
            self.assertIn("intro", output)
            self.assertIn("footer", output)
            self.assertNotIn("old", output)
            with patch.object(recent, "fetch_events", return_value=events):
                recent.run("OKAN-SOFTWARE", "token", root)
            self.assertEqual(output, target.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            recent.replace_section("no markers", "data")


if __name__ == "__main__":
    unittest.main()

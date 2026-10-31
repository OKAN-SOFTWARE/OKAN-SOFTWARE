import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "update_showcase.py"
SPEC = importlib.util.spec_from_file_location("showcase", SCRIPT)
showcase = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(showcase)


class ShowcaseTests(unittest.TestCase):
    def test_selects_recent_original_projects_only(self):
        def repo(name, pushed, fork=False):
            return {"name": name, "full_name": "OKAN-SOFTWARE/" + name,
                    "owner": {"login": "OKAN-SOFTWARE"}, "pushed_at": pushed,
                    "fork": fork, "private": False, "archived": False}
        repos = [
            repo("older", "2026-10-25T12:00:00Z"),
            repo("newer", "2026-10-29T12:00:00Z"),
            repo("forked", "2026-10-30T12:00:00Z", fork=True),
            repo("OKAN-SOFTWARE", "2026-10-31T12:00:00Z"),
        ]
        with patch.object(showcase, "api_get", return_value=repos):
            found = showcase.original_projects("OKAN-SOFTWARE", "token")
        self.assertEqual(["newer", "older"], [x["name"] for x in found])

    def test_language_tags_are_source_languages_max_three(self):
        with patch.object(showcase, "api_get", return_value={
                "Java": 3000, "JavaScript": 2500, "HTML": 1000,
                "CSS": 500, "Markdown": 9000}):
            found = showcase.code_languages({"full_name": "OKAN-SOFTWARE/Doc2FAQ"}, "token")
        self.assertEqual(["Java", "JavaScript", "HTML"], found)
        self.assertEqual(3, len(found))

    def test_ticker_is_animated_and_escapes_untrusted_text(self):
        svg = showcase.render_ticker([{
            "repo": "OKAN-SOFTWARE/Doc2FAQ",
            "message": "fix: escape <tag> & symbols",
            "sha": "abcdef0",
        }])
        self.assertIn("@keyframes glide", svg)
        self.assertIn("prefers-reduced-motion", svg)
        self.assertIn("abcdef0", svg)
        self.assertIn("&lt;tag&gt;", svg)
        self.assertNotIn("<tag>", svg)
        self.assertIn("LATEST COMMITS", svg)

    def test_card_is_ember_and_contains_languages_not_generic_tags(self):
        repo = {"name": "Doc2FAQ", "description": "Build useful FAQ drafts",
                "pushed_at": "2026-10-29T18:00:00Z"}
        card = showcase.card_svg(repo, ["Java", "JavaScript", "HTML"])
        self.assertIn("#ffad68", card)
        self.assertIn("JavaScript", card)
        self.assertIn("LAST PUSH", card)
        self.assertNotIn("Documents", card)

    def test_commit_filter_excludes_merge_and_profile_refresh(self):
        def item(repo, sha, message):
            return {"repository": {"full_name": repo}, "sha": sha * 40,
                    "commit": {"message": message}}
        results = {"items": [
            item("OKAN-SOFTWARE/OKAN-SOFTWARE", "a", "docs(profile): refresh"),
            item("OKAN-SOFTWARE/Doc2FAQ", "b", "Merge pull request #1"),
            item("pylint-dev/astroid", "c", "fix(builder): close stream"),
        ]}
        with patch.object(showcase, "api_get", return_value=results):
            found = showcase.recent_commits("OKAN-SOFTWARE", "token")
        self.assertEqual(1, len(found))
        self.assertEqual("pylint-dev/astroid", found[0]["repo"])

    def test_markers_and_readme_assets(self):
        readme = (SCRIPT.parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertEqual(1, readme.count(showcase.START))
        self.assertEqual(1, readme.count(showcase.END))
        self.assertIn("assets/latest-commits.svg", readme)
        self.assertIn("assets/ember-stack.svg", readme)
        self.assertIn("assets/contribution-snake-dark.svg", readme)
        self.assertIn("assets/phoenix-hero-animated.gif", readme)
        self.assertIn("assets/phoenix-footer-animated.gif", readme)
        updated = showcase.replace_section(
            "top\n" + showcase.START + "\nold\n" + showcase.END + "\nbottom",
            "new"
        )
        self.assertIn("top", updated)
        self.assertIn("new", updated)
        self.assertIn("bottom", updated)
        self.assertNotIn("old", updated)
        with self.assertRaises(ValueError):
            showcase.replace_section("no markers", "new")


if __name__ == "__main__":
    unittest.main()

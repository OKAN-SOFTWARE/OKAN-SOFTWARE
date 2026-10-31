from pathlib import Path
import importlib.util
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "update_upstream_code.py"
SPEC = importlib.util.spec_from_file_location("upstream_code", SCRIPT)
import sys
sys.path.insert(0, str(SCRIPT.parent))
upstream = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upstream)


def issue(owner="other", number=10):
    return {
        "repository_url": f"https://api.github.com/repos/{owner}/project",
        "pull_request": {
            "url": f"https://api.github.com/repos/{owner}/project/pulls/{number}",
            "merged_at": "2026-10-30T12:00:00Z",
        },
    }


class UpstreamCodeTests(unittest.TestCase):
    def test_only_other_owners_and_merged_prs(self):
        with patch.object(upstream, "api_search", return_value={
            "incomplete_results": False, "total_count": 2,
            "items": [issue(), issue("OKAN-SOFTWARE", 2)],
        }) as search:
            items = upstream.upstream_items("OKAN-SOFTWARE", "token")
        self.assertEqual(1, len(items))
        self.assertIn("/other/project/pulls/10", next(iter(items)))
        self.assertIn("is:merged", search.call_args.args[0])
        self.assertIn("-user:OKAN-SOFTWARE", search.call_args.args[0])

    def test_cache_reused_and_diff_counts_validated(self):
        url = "https://api.github.com/repos/other/project/pulls/10"
        meta = {"repo": "other/project", "number": 10,
                "merged_at": "2026-10-30T12:00:00Z"}
        old = {url: {**meta, "additions": 21, "deletions": 8}}
        with patch.object(upstream, "upstream_items", return_value={url: meta}), \
             patch.object(upstream, "api_get") as api:
            self.assertEqual(old, upstream.collect("OKAN-SOFTWARE", "token", old))
            api.assert_not_called()
        with patch.object(upstream, "upstream_items", return_value={url: meta}), \
             patch.object(upstream, "api_get", return_value={
                 "merged_at": meta["merged_at"], "additions": 11, "deletions": 2,
             }):
            result = upstream.collect("OKAN-SOFTWARE", "token", {})
        self.assertEqual(11, result[url]["additions"])

    def test_render_is_separate_and_links_to_real_prs(self):
        data = {
            "https://api.github.com/repos/other/project/pulls/10": {
                "repo": "other/project", "number": 10,
                "merged_at": "2026-10-30T12:00:00Z",
                "additions": 23, "deletions": 5,
            },
        }
        result = upstream.render(data)
        self.assertIn("UPSTREAM CONTRIBUTIONS // MERGED PRS", result)
        self.assertIn("**1 merged PRs**", result)
        self.assertIn("**+23 lines added**", result)
        self.assertIn("**−5 lines removed**", result)
        self.assertIn("[#10](https://github.com/other/project/pull/10)", result)
        self.assertIn("not unique lines personally authored", result)
        self.assertNotIn("<img", result)

    def test_validation_fails_closed_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = f"intro\n{upstream.START}\nold\n{upstream.END}\nfooter"
            (root / "README.md").write_text(before, encoding="utf-8")
            with patch.object(upstream, "collect", side_effect=RuntimeError("API")):
                with self.assertRaises(RuntimeError):
                    upstream.run("OKAN-SOFTWARE", "token", root)
            self.assertEqual(before, (root / "README.md").read_text(encoding="utf-8"))
            self.assertFalse((root / upstream.CACHE).exists())

    def test_readme_has_distinct_upstream_markers(self):
        readme = (SCRIPT.parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertEqual(1, readme.count(upstream.START))
        self.assertEqual(1, readme.count(upstream.END))
        self.assertLess(readme.index("<!-- CODE_MATRIX:END -->"), readme.index(upstream.START))
        self.assertIn("OKAN'S CODE STATS // LIVE UPDATES", readme)


if __name__ == "__main__":
    unittest.main()

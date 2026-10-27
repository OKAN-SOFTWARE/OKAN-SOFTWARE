from datetime import date, datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET


SCRIPT = Path(__file__).resolve().parents[1] / "update_activity.py"

SPEC = importlib.util.spec_from_file_location("update_activity", SCRIPT)
activity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activity)

TODAY = date(2026, 10, 27)


def pr(number=1, **changes):
    result = {
        "number": number,
        "title": "Fix a reproducible bug",
        "repository_url": "https://api.github.com/repos/upstream/project",
        "html_url": f"https://github.com/upstream/project/pull/{number}",
        "user": {"login": "OKAN-SOFTWARE"},
        "state": "open",
        "draft": False,
        "created_at": "2026-10-27T10:00:00Z",
        "updated_at": "2026-10-27T11:00:00Z",
        "pull_request": {"merged_at": None},
    }

    result.update(changes)
    return result


def payload(items):
    return {
        "total_count": len(items),
        "incomplete_results": False,
        "items": items,
    }


def bundle():
    new = pr()

    old_merge = pr(
        2,
        state="closed",
        created_at="2026-08-01T10:00:00Z",
        pull_request={
            "merged_at": "2026-10-27T12:00:00Z"
        },
    )

    return {
        "opened": payload([new]),
        "merged": payload([old_merge]),
        "open": payload([new]),
        "recent": payload([new, old_merge]),
    }


class ActivityTests(unittest.TestCase):

    def test_openings_and_old_pr_merges_are_independent(self):
        data = activity.summarize(
            bundle(),
            "OKAN-SOFTWARE",
            TODAY,
        )

        counts = (
            data["opened_count"],
            data["merged_count"],
            data["open_count"],
            data["repos_count"],
        )

        self.assertEqual((1, 1, 1, 1), counts)
        self.assertEqual(30, len(data["days"]))

    def test_new_york_boundary_and_dst_query(self):
        self.assertEqual(
            date(2026, 10, 26),
            activity.timestamp("2026-10-27T02:00:00Z").date(),
        )

        self.assertIn(
            "2026-09-28T04:00:00Z",
            activity.queries(
                "OKAN-SOFTWARE",
                TODAY,
            )["opened"],
        )

        self.assertIn(
            "2026-12-03T05:00:00Z",
            activity.queries(
                "OKAN-SOFTWARE",
                date(2027, 1, 1),
            )["opened"],
        )

    def test_public_upstream_scope_on_every_query(self):
        queries = activity.queries(
            "OKAN-SOFTWARE",
            TODAY,
        )

        for query in queries.values():
            self.assertIn("is:public", query)
            self.assertIn("-user:OKAN-SOFTWARE", query)

    def test_owner_private_author_and_link_are_rejected(self):
        cases = [
            {
                "repository_url":
                "https://api.github.com/repos/OKAN-SOFTWARE/fork"
            },
            {
                "repository": {"private": True}
            },
            {
                "user": {"login": "somebody-else"}
            },
            {
                "html_url": "javascript:alert(1)"
            },
        ]

        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    activity.validate_item(
                        pr(**changes),
                        "OKAN-SOFTWARE",
                    )

    def test_incomplete_duplicate_out_of_window_data_fails(self):
        cases = []

        bad = bundle()
        bad["opened"]["incomplete_results"] = True
        cases.append(bad)

        bad = bundle()
        bad["opened"] = payload([pr(), pr()])
        cases.append(bad)

        bad = bundle()
        bad["opened"]["items"][0]["created_at"] = (
            "2026-09-01T00:00:00Z"
        )
        cases.append(bad)

        bad = bundle()
        bad["merged"]["items"][0]["pull_request"]["merged_at"] = None
        cases.append(bad)

        bad = bundle()
        bad["opened"]["total_count"] += 1
        cases.append(bad)

        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    activity.summarize(
                        value,
                        "OKAN-SOFTWARE",
                        TODAY,
                    )

    def test_empty_activity_and_distinct_states_render(self):
        empty = {
            key: payload([])
            for key in activity.KEYS
        }

        data = activity.summarize(
            empty,
            "OKAN-SOFTWARE",
            TODAY,
        )

        ET.fromstring(activity.svg_dashboard(data))

        self.assertIn(
            "No public upstream",
            activity.markdown_section(
                data,
                "OKAN-SOFTWARE",
            ),
        )

        value = bundle()

        value["recent"] = payload([
            pr(1),
            pr(2, draft=True),
            pr(3, state="closed"),
            pr(
                4,
                state="closed",
                pull_request={
                    "merged_at": "2026-10-27T12:00:00Z"
                },
            ),
        ])

        rendered = activity.markdown_section(
            activity.summarize(
                value,
                "OKAN-SOFTWARE",
                TODAY,
            ),
            "OKAN-SOFTWARE",
        )

        expected_states = (
            "🟢 Open",
            "⚙️ Draft",
            "⚪ Closed",
            "🟣 Merged",
        )

        for state in expected_states:
            self.assertIn(state, rendered)

    def test_latest_upstream_work_shows_ten_prs(self):
        value = bundle()

        value["recent"] = payload([
            pr(
                number,
                updated_at=f"2026-10-27T12:{number:02d}:00Z",
            )
            for number in range(1, 13)
        ])

        data = activity.summarize(
            value,
            "OKAN-SOFTWARE",
            TODAY,
        )

        self.assertEqual(10, len(data["recent"]))

        expected_numbers = list(range(12, 2, -1))
        actual_numbers = [
            item["number"]
            for item in data["recent"]
        ]

        self.assertEqual(expected_numbers, actual_numbers)

    def test_untrusted_titles_cannot_break_table_or_insert_html(self):
        value = bundle()

        value["recent"]["items"][0]["title"] = (
            '<img src=x> | [click](javascript:x)\nnext'
        )

        data = activity.summarize(
            value,
            "OKAN-SOFTWARE",
            TODAY,
        )

        text = activity.markdown_section(
            data,
            "OKAN-SOFTWARE",
        )

        self.assertNotIn("<img", text)
        self.assertIn("&lt;img", text)
        self.assertIn(r"\|", text)
        self.assertIn(r"\[click\]", text)

    def test_sync_timestamp_changes_in_markdown_and_dashboard(self):
        snapshots = [
            datetime(
                2026,
                10,
                27,
                hour,
                42,
                tzinfo=timezone.utc,
            )
            for hour in (10, 11)
        ]

        for moment in snapshots:
            data = activity.summarize(
                bundle(),
                "OKAN-SOFTWARE",
                TODAY,
                moment,
            )

            text = activity.markdown_section(
                data,
                "OKAN-SOFTWARE",
            )

            svg = activity.svg_dashboard(data)

            self.assertIn(
                f"Last profile update: {activity.format_sync_time(moment)}",
                text,
            )

            self.assertIn(
                f"UPDATED · {activity.format_sync_time(moment, compact=True)}",
                svg,
            )

            self.assertNotIn("dates use New York", text)
            self.assertNotIn("Updated daily", text)
            self.assertNotIn("NEW YORK DATES", svg)

    def test_only_marker_contents_change_and_reruns_are_stable(self):
        original = (
            f"intro\n"
            f"{activity.START}\n"
            f"old\n"
            f"{activity.END}\n"
            f"projects\n"
        )

        result = activity.replace_section(
            original,
            "new",
        )

        expected = (
            f"intro\n"
            f"{activity.START}\n"
            f"new\n"
            f"{activity.END}\n"
            f"projects\n"
        )

        self.assertEqual(expected, result)

        self.assertEqual(
            result,
            activity.replace_section(result, "new"),
        )

        invalid_cases = (
            "missing markers",
            activity.START + activity.START + activity.END,
            activity.END + activity.START,
        )

        for invalid in invalid_cases:
            with self.assertRaises(ValueError):
                activity.replace_section(
                    invalid,
                    "new",
                )

    def test_unchanged_activity_skips_snapshot_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()

            readme = root / "README.md"
            svg = root / "assets" / "activity.svg"

            readme.write_text(
                f"{activity.START}\nold\n{activity.END}\n",
                encoding="utf-8",
            )

            data = bundle()

            first = datetime(
                2026,
                10,
                27,
                14,
                tzinfo=timezone.utc,
            )

            second = datetime(
                2026,
                10,
                27,
                15,
                tzinfo=timezone.utc,
            )

            # First refresh creates the snapshot.
            activity.write_outputs(
                data,
                "OKAN-SOFTWARE",
                TODAY,
                root,
                synced_at=first,
            )

            original_readme = readme.read_text(
                encoding="utf-8"
            )

            original_svg = svg.read_text(
                encoding="utf-8"
            )

            self.assertIn(
                "ACTIVITY:SNAPSHOT:",
                original_readme,
            )

            # Identical activity must not change either file.
            activity.write_outputs(
                data,
                "OKAN-SOFTWARE",
                TODAY,
                root,
                synced_at=second,
            )

            self.assertEqual(
                original_readme,
                readme.read_text(encoding="utf-8"),
            )

            self.assertEqual(
                original_svg,
                svg.read_text(encoding="utf-8"),
            )

            # A visible PR change must update the snapshot.
            data["recent"]["items"][0]["title"] = (
                "Updated bug fix"
            )

            activity.write_outputs(
                data,
                "OKAN-SOFTWARE",
                TODAY,
                root,
                synced_at=second,
            )

            updated_readme = readme.read_text(
                encoding="utf-8"
            )

            self.assertNotEqual(
                original_readme,
                updated_readme,
            )

            self.assertIn(
                "Updated bug fix",
                updated_readme,
            )

    def test_failed_data_keeps_both_outputs_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()

            readme = root / "README.md"
            svg = root / "assets" / "activity.svg"

            readme.write_text(
                f"{activity.START}\nold\n{activity.END}",
                encoding="utf-8",
            )

            svg.write_text(
                "old svg",
                encoding="utf-8",
            )

            invalid = bundle()
            invalid["merged"]["incomplete_results"] = True

            with self.assertRaises(ValueError):
                activity.write_outputs(
                    invalid,
                    "OKAN-SOFTWARE",
                    TODAY,
                    root,
                )

            self.assertIn(
                "old",
                readme.read_text(encoding="utf-8"),
            )

            self.assertEqual(
                "old svg",
                svg.read_text(encoding="utf-8"),
            )

    def test_api_paginates_and_rejects_partial_results(self):
        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps(
                    self.body
                ).encode()

        first = {
            "total_count": 101,
            "incomplete_results": False,
            "items": [{}] * 100,
        }

        second = {
            "total_count": 101,
            "incomplete_results": False,
            "items": [{}],
        }

        with patch.object(
            activity,
            "urlopen",
            side_effect=[
                Response(first),
                Response(second),
            ],
        ) as fetch:
            result = activity.api_search(
                "public query",
                "test-token",
            )

        self.assertEqual(
            101,
            len(result["items"]),
        )

        self.assertEqual(
            2,
            fetch.call_count,
        )

        first["incomplete_results"] = True

        with patch.object(
            activity,
            "urlopen",
            return_value=Response(first),
        ):
            with self.assertRaises(ValueError):
                activity.api_search(
                    "query",
                    "test-token",
                )


if __name__ == "__main__":
    unittest.main()

# Profile activity

This profile uses repository-owned SVGs and a small Python script to keep its
open-source section current. It needs no external stats-card service, package
installation, or personal access token.

## What updates

- Public pull requests authored by the profile owner in **other owners’ repositories**.
- PRs opened and PRs merged over the last 30 calendar days in `America/New_York`.
- Currently open upstream PRs, including drafts (an all-time count, not a 30-day count).
- Distinct repositories with an opening or merge during the same 30-day window.
- Ten most recently updated upstream PRs that are **open, draft, or merged**. Closed PRs that were not merged are excluded from the latest-work table. Historical chart and counts remain unchanged.
- A “Last profile update” timestamp from the latest successful refresh, shown in New York time. Both the README and chart update this time even when the PR counts and table are unchanged.

The chart counts PR openings and merges separately. It is **not** a commit count,
a count of working days, GitHub’s contribution calendar, or a count of forks.
An old PR merged this month contributes to merges even if it was opened earlier.
Fork creation and automated profile-refresh commits do not increase these counts.
Private repositories, PR bodies, and unpublished work are not included.
Search results can lag recent GitHub activity.

## Automation

Merge the profile PR into `main`. The merge triggers the first refresh.
After that, **Refresh profile activity** runs every four hours at minute 17 in
`America/New_York` (00:17, 04:17, 08:17, 12:17, 16:17, and 20:17). GitHub may delay a
scheduled run. To refresh sooner, use **Actions → Refresh profile activity → Run workflow**.

The workflow uses the repository’s built-in `GITHUB_TOKEN`. Only the refresh job
gets `contents: write`; pull-request runs execute the tests with read permission.
No token is written to the README or SVG. The updater validates all its data and
renders both outputs before writing either file. API errors or incomplete search
results fail the run instead of publishing zeroes. A push rejected by branch
protection fails visibly; no force push or rule bypass is attempted.

Scheduled workflows in public repositories can be disabled after 60 days without
repository activity. Re-enable the workflow from Actions if GitHub disables it.

## Local use

Requires Python 3.11+ (the workflow uses Python 3.12).

```bash
python -m unittest discover -s scripts/tests -v
```

To fetch a fresh snapshot, set `GH_TOKEN` to a GitHub token in your environment,
then run:

```bash
python scripts/update_activity.py
```

`PROFILE_USER` defaults to `OKAN-SOFTWARE`. `--fixture FILE` accepts a saved bundle
of the four search responses (`opened`, `merged`, `open`, and `recent`) for offline
validation. `--today YYYY-MM-DD` fixes the local calendar date when testing;
`--synced-at` sets an explicit timezone-aware timestamp for reproducible runs.
Do not commit raw API bundles; the generated section and SVG are the public outputs.

Keep the single `ACTIVITY:START` / `ACTIVITY:END` marker pair in `README.md`.
Everything outside those markers stays under manual control. The approved phoenix
and workbench illustration (`assets/phoenix-workbench.png`) appears at the top.
It is static artwork without illustrated navigation buttons. The image sits inside
a named anchor with no destination, so it is not an image-viewer or navigation link.
The real navigation links immediately below wrap animated GIF buttons. Contribution data
remains in the generated section, and stack details remain available as text.
The README needs no custom CSS or JavaScript.

To regenerate the four decorative assets locally, install Pillow and run
`python scripts/render_profile_assets.py`. Pillow is only needed when rebuilding
the decorative GIFs, not for the scheduled activity updater or its tests.

## References

- [GitHub issue and PR search](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests)
- [GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token)
- [Scheduled workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

# Profile activity

This profile uses text-based Markdown statistics and four Python standard-library scripts
to keep its public GitHub sections current. Decorative artwork remains separate;
statistics remain available as real text alongside automatically refreshed Matrix-green SVG cards. No external stats-card service,
package installation, or personal access token is needed.

## What updates

- Public pull requests authored by the profile owner in **other owners’ repositories**.
- PRs opened and PRs merged over the last 30 calendar days in `America/New_York`.
- Currently open upstream PRs, including drafts (an all-time count, not a 30-day count).
- Distinct repositories with an opening or merge during the same 30-day window.
- Ten most recently updated upstream PRs that are **open, draft, or merged**. Closed PRs that were not merged are excluded from the latest-work table. Historical chart and counts remain unchanged.
- A “Last profile update” timestamp from the latest successful refresh, shown in New York time.
- Text-based Code DNA: authored commit timing on default branches of current public original repositories. The **Code Matrix** is a native Markdown table with counted nonblank source lines, each language's percentage of total LOC, and descending LOC order. GitHub language bytes are not displayed.
- A curated, categorized Development Stack. The separate daily and weekly adventure sections have been removed at the owner's request.
- A separate selectable-text **Recent Activity** table, with one latest public event per repository (up to eight), updated by an hourly workflow.
- A Matrix-green **contribution-grid snake** (light and dark SVG variants), generated from GitHub's contribution calendar daily. The animation is decoration, not a substitute for text statistics.

The text table counts PR openings and merges separately. It is **not** a commit count,
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

**Refresh recent activity and contribution snake** runs every hour at minute 43
(Eastern Time), and the snake is regenerated daily at 04:29 Eastern Time.
The workflow also runs once when its files land on `main`; a manual run
can refresh both on demand.

**Refresh live Code Matrix** checks every 15 minutes (minutes 07, 22, 37, 52,
Eastern Time) and can also be started manually from Actions. It discovers new
public original repositories automatically and checks the latest default-branch
commit SHA of each repository. Unchanged repositories reuse their validated LOC
cache; changed or newly created repositories are shallow-cloned and recounted.
The result is committed as **real Markdown text**, not an image. Scheduled Actions
may run late, so this is eventual refresh rather than a guarantee of instant
updates on each push. Empty new repositories contribute zero lines until code
is pushed. The separate four-hour developer-statistics workflow also refreshes
the Code Matrix.

For near-immediate cross-repository updates, the Code Matrix workflow accepts
`repository_dispatch` with event type `source_code_changed`. That event must be
sent by a workflow or GitHub App in each source repository, using a token
permitted to dispatch to this profile repository. GitHub's built-in token from
an unrelated repository cannot dispatch there by default. No cross-repository
hooks or long-lived secrets are installed by this profile update. It commits only when the recent event table or
generated SVGs actually change. README writes from the two workflows share
a concurrency group to prevent simultaneous pushes.

The workflow uses the repository’s built-in `GITHUB_TOKEN`. Only the refresh job
gets `contents: write`; pull-request runs execute the tests with read permission.
No token is written to the README or SVG. The updater validates all its data and
renders the generated output before writing the README. The journey updater separately validates API data before updating the README and chronicle JSON. API errors or incomplete search
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
python scripts/update_developer_journey.py
python scripts/update_recent_activity.py
```

`PROFILE_USER` defaults to `OKAN-SOFTWARE`. `--fixture FILE` accepts a saved bundle
of the four search responses (`opened`, `merged`, `open`, and `recent`) for offline
validation. `--today YYYY-MM-DD` fixes the local calendar date when testing;
`--synced-at` sets an explicit timezone-aware timestamp for reproducible runs.
Do not commit raw API bundles. Generated Markdown and the public `data/chronicles.json` snapshots are the outputs.

Keep the single `ACTIVITY:START` / `ACTIVITY:END` marker pair in `README.md`.
The hourly updater owns only the `RECENT:START` / `RECENT:END` section.
The animated SVGs live in `assets/` and are referenced through a theme-sensitive picture element.
The journey updater uses a `JOURNEY:START` / `JOURNEY:END` marker pair, with nested `CODE_MATRIX:START` / `CODE_MATRIX:END` markers for frequent LOC-only updates; the former weekly journal markers were removed.
Everything outside the activity, recent, journey, and Matrix project marker pairs stays under manual control.
The animated Phoenix hero is followed immediately by the Matrix-green contribution snake, before About.
The animated Phoenix footer remains at the bottom. The terminal, stats, stack, latest-commit ticker,
and original-project SVG cards use a Matrix-green-on-black palette and are refreshed by
`scripts/update_ember_showcase.py`. The legacy `assets/ember/` directory name is retained to
avoid breaking existing image URLs. The hero, terminal, stats, stack, and footer images are plain
HTML images without surrounding links; GitHub's own image viewer may still offer access to
the underlying files. Project cards and the latest-commit ticker intentionally link to GitHub.
The README needs no custom CSS or JavaScript. All statistics remain available as selectable text.

To regenerate the four decorative assets locally, install Pillow and run
`python scripts/render_profile_assets.py`. Pillow is only needed when rebuilding
the decorative GIFs, not for the scheduled activity updater or its tests.

## Data scope and accuracy

- The Code DNA commit count includes attributed commits reachable from the default branches
  of current, public, non-fork repositories owned by OKAN-SOFTWARE. It does not include
  all upstream or private history. Times are commit timestamps in America/New_York,
  not time spent coding.
- Language shares in Code Matrix use **counted nonblank source lines**, not GitHub language bytes.
  Frameworks, OS names, and cloud tools in Development Stack are curated descriptions,
  not automatically inferred expertise scores.
- The hourly Recent Activity table uses public GitHub events (up to 300), keeps the
  most recent eligible event per repository, and includes PRs, pushes, issues,
  reviews, comments, and other supported actions. Closed-unmerged PRs are excluded.
  It is a snapshot of observable public events, not complete contribution history.
- The four-hour updater still observes public GitHub events to maintain its
  history archive. The separate daily README section and its upstream-language
  API lookups have been removed.
- The history archive remains in `data/chronicles.json` (up to 365 days), but no
  longer appears in the README. The refresh runs every four hours.
- LOC is a real count of nonblank lines in recognized source extensions from the
  default branch of each public original repository. Comments count; known generated,
  dependency, and minified files are excluded. The counts are a snapshot of code
  currently present, not a sum of lines authored in every historical commit.
  Forks, private repositories, and this profile repository are excluded. A
  repository's code may include third-party files outside known excluded paths.
  Matrix bars are illustrative; the percentages are computed from the exact LOC
  counts and sorted descending. Tiny nonzero shares receive a minimum green cell.
- API errors fail the job, preserving the previous committed snapshot.

## References

- [GitHub issue and PR search](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests)
- [GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token)
- [Scheduled workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

The Extra tools & interests stack table is always expanded. The Code Matrix heading and native Markdown language percentages are regenerated automatically. Recent Activity intentionally omits timestamps.

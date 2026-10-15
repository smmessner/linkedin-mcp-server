# Restricted job discovery: offline implementation and acceptance proposal

Personal fork: `smmessner/linkedin-mcp-server`; existing draft PR #1 and branch
`dev/job-discovery-read-only`. No authentication, live LinkedIn requests, external
integration, continuous monitoring, application automation, persistent deployment,
Google Drive/ledger edits, or merge is authorized or performed by this work.

## Startup and execution boundary

For future supervised discovery use **`linkedin-job-discovery`**, the dedicated
process-bound stdio launcher. It uses `JobDiscoverySettings`, which requires an
explicit `LINKEDIN_MCP_ACCESS_MODE=job-search-only` value. Missing, empty, invalid,
or `standard` settings fail before constructing a browser/container. HTTP transport
is rejected. Automatic login is forced off. This launcher neither attaches to a
shared runtime nor spawns a background service. The sample
[`mcp-client.example.json`](../packaging/job-discovery/mcp-client.example.json) is
configuration only; it has not been activated. Future discovery launchers must use
this settings contract rather than the general-purpose startup path.

The generic `linkedin-mcp serve` retains standard mode as its upstream default;
it must not be substituted for the dedicated discovery launcher. Restricted shared
runtimes still include the policy in their attachment fingerprint. The launcher
requirement prevents accidental default selection, not an authorized local operator
from executing different code or changing their own configuration.

Only these tools are exposed and executable:

- `linkedin.jobs.search`
- `linkedin.jobs.get`
- `linkedin.server.status`
- `linkedin.session.status`
- `linkedin.capabilities.list`

MCP registration, queue submission, and execution independently enforce the fixed
allowlist. All 19 excluded capabilities and unknown future names fail direct
invocation. Capability inventory contains only the two job operations. The evidence
resource is omitted, including for known source IDs; typed job results retain inline
evidence and source references. No application, messaging, posting, invitation,
generic browser, or unrelated data-access tool is added.

## Typed extraction: verified versus unsupported

All verification below is **synthetic-only**, not live acceptance. Real production
page objects parse in-memory HTML; browser requests are aborted. Official MCP
client tests verify nested structured output as well as the existing allowlist.

| Area | Verified behavior | Limits / deliberate unknowns |
| --- | --- | --- |
| Compensation | Separate `compensation` entries with decimal minimum/maximum, explicit currency, hour/day/week/month/year period, original quoted text, and uncertainty codes. Explicit ranges, K amounts, `from`, `up to`, point rates, multiple contextual ranges, and conflicts are tested | Empty tuple means unknown/unavailable, never zero salary. Bare `$` does not imply USD. No annualization, FX conversion, base-versus-total-pay inference, or merging geographic ranges. Unsupported number formats, mixed scales, missing currency/period, and estimates/bonus/equity language remain uncertain. Decimal amounts serialize as JSON strings |
| Employer destination | `application_url` is separate from canonical LinkedIn `job_url`. Read only visible primary-card Apply link hrefs; retain the safe original href, visible label evidence, and status | A visible external destination is not verified employer ownership. No clicks, DNS resolution, redirects, URL unwrapping, or submissions. Buttons without href stay unknown. Multiple destinations or competing Easy Apply controls are ambiguous. Non-HTTPS, credentials, literal IPs, local-style hosts, malformed hrefs, and LinkedIn redirects are rejected. Link safety is not a browsing sandbox |
| Washington employment eligibility | `eligible`, `excluded`, or `unknown` with exact description evidence and reason; tested explicit permission, prohibition, conflicts, incomplete descriptions, conditional language, Washington DC, and benefit-only notices | Deterministic, English patterns only; not a legal or employer eligibility determination. Remote/location filters and salary disclosure never prove eligibility. Unrecognized phrasing, implied closed state lists, conditions, or conflicting statements remain unknown and require human review |
| Description completeness | `complete`, `incomplete`, `unavailable`, or `unknown` with reason. Stable visible uncollapsed content, retained expansion controls, clipping, empty/loading state, missing container, explicit unavailability, and failed expansion are tested | `complete` describes the rendered UI only; it cannot prove that LinkedIn contains every paragraph from the employer. A stable snippet in an unrecognized layout can require live validation; absent or drifting UI never licenses invented content |
| Posting and freshness | Keep visible listed-time text. `posting` separates timezone-aware `posted_at`, date-only `posted_date`, and `reposted_at`; preserves repost and uncertainty evidence. Explicit ISO dates/timestamps, missing zones, conflicts, invalid timestamps, and relative-only labels are tested | Never subtract “3 hours ago” from capture time to invent a date. Reposts do not become new postings. A date without a time never becomes midnight. Unrecognized or conflicting timestamps stay unknown. Search windows request LinkedIn filtering, not independent freshness or repost guarantees |
| Geography and identity | Synthetic WA remote eligibility/exclusion/unknown text, Seattle hybrid/onsite, Sacramento, San Diego, 24-hour and 7-day filter serialization, employer/title/location, and descriptions | No live relevance, inventory, radius/geography, or employer availability claim. Missing employer is null; required search-card identity/location failure is parser drift |

Nested compensation, posting, application-label, and eligibility evidence is checked
against captured visible text. Washington quotes must additionally belong to the
description. Href and datetime values are explicit DOM attributes on visible controls,
not guessed text or fetched employer content. Outer source references bind capture
time and LinkedIn source URL. Application URLs and job descriptions are untrusted
input; a later workflow must not treat them as instructions or automatically browse
them.

## Inventory, cursors, and interruption semantics

Restricted production search enables strict inventory verification. Standard-mode
search retains its prior collection policy. Strict verification:

1. Uses the selected header, never a neighboring body's count; samples advertised
   counts before and after extraction and across visited pages. Counts that change,
   disappear, or use a lower bound are explicit issues, not exact inventories.
2. Tracks typed stable job IDs and repeated identities across pages. Same-page
   duplicate identities, invalid/malformed cards, and an exceeded 500-card raw DOM
   bound fail closed instead of silently discarding unclassified selected cards.
3. Checks post-extraction identities, loading markers, and untraversed tail controls.
   Empty results require a zero selected count and no residual selected cards; a
   transient zero count is sampled again rather than accepted immediately.
4. Claims `inventory_status=reconciled` only at a terminal traversal with a stable
   exact count equal to the unique typed inventory and no observed issues. Missing
   next-page controls alone are insufficient. Unknown counts remain uncertain.
5. Stores bounded prior inventory counts and observed-prefix identities in cursors.
   A changed count or lost identity between calls forces terminal truncation, even
   if a later page appears individually complete. Cursor pages remain disjoint and
   preserve existing account/client/filter binding, single-use, and replay rules.
6. Marks page bounds, interrupted partial searches, and unproven reconciliation as
   `safety_bound`, `pagination.truncated=true`, no continuation, with inventory issue
   codes. Ordinary page-size limits retain continuation with partial coverage.
   A stalled continuation also truncates rather than asserting completion.
7. Propagates first-page failures, authentication/checkpoint/restriction failures,
   and cancellation as errors. Cancellation releases reservations so an explicit
   retry can use the prior cursor; it never manufactures a completed search.

`status=completed` means the call finished, not that the inventory is complete.
Inspect `coverage.inventory_status`, `inventory_issues`, observed counts/IDs,
`pagination.truncated`, and `has_more` together. Coverage counts refer to the bounded
traversal; page result counts refer to the returned slice. Live-deduplicated scans
are not snapshots. Equal counts do not prove membership stability outside observed
prefixes, and deleted/reordered/reposted jobs can prevent complete coverage.

These checks implement an honest bound for the synthetic contract. Full collection
acceptance under `COLLECTION_VERIFICATION_PROCESS.md` remains unverified without a
separately authorized live observation; none was performed here.

## Future workflow and authoritative application ledger

No Google documents, career evidence, strategy, or ledger were opened or modified.
The separate future workflow would:

1. Read explicitly authorized, versioned strategy/evidence snapshots and confirm
   the authoritative Sheets schema. Determine whether a row represents discovery
   or an actual application; discovery must never mean “applied.”
2. Use an approved query matrix for the five geography/workplace buckets, a 24-hour
   run and overlapping 7-day catch-up. Follow bounded cursors and preserve coverage.
3. Stage normalized candidates outside the MCP, deduplicate by job ID/canonical URL
   against search overlap and the ledger, and flag possible reposts or cross-source
   duplicates for review. Preserve unknown eligibility, pay, dates, and destinations.
4. Explain strategy fit using only cited career evidence. Show incomplete scans,
   conditional state eligibility, conflicting pay, and uncertain descriptions.
5. Present a proposed shortlist and ledger diff. Re-read authoritative rows before
   any separately approved write, detect concurrent edits, use stable keys and exact
   column mappings, and verify the result. Never overwrite existing user statuses,
   dates, or notes, and never record an application based solely on job discovery.

Planning, ranking, scheduling, evidence retrieval, ATS ingestion, and a ledger
adapter remain outside this standalone typed server. No connector, scheduler,
notification, monitor, or ledger-writing permission is created by this PR.

## Remaining technical and account-security risks

- The generic shared loopback HTTP runtime has no per-client authentication. Trusted
  local processes share a security boundary; fingerprint checks are not protection
  against hostile local code. Do not expose, proxy, port-forward, or multi-tenant it.
  Dedicated stdio reduces listener exposure but does not sandbox the local user.
- Production URL validation is not a browser-wide egress firewall. Redirects,
  subresources, or visible controls can initiate traffic before final-URL checks.
  Extracted destination validation is separate and must not be used to auto-follow
  links. Preserve an independently reviewed network policy for live work.
- Persistent profiles and backup/failed siblings remain credentials. No profile was
  authenticated here. Standard mode retains upstream writes, account-scoped evidence
  resources, and attachment filesystem-race concerns from the initial audit.
- Visible reads can be observable to LinkedIn. Session expiry, checkpoint/restriction,
  selectors, layouts, count semantics, posting labels, and account/platform policies
  remain unvalidated in live use. No CAPTCHA/anti-bot bypass or private endpoints.
- Compensation, employment eligibility, and completeness are conservative evidence
  classifications, not guarantees. Unsupported languages, conditional logic, locale
  formats, inaccessible destinations, employer ownership, true original posting
  dates, and cross-source/repost matching still require review or development.
- No new dependency advisory scan or live penetration test was performed. Offline
  passing tests do not establish account safety, policy compliance, or live readiness.

## Proposed limited supervised acceptance (requires separate authorization)

Do not run this plan yet. Before authorizing it, review PR #1 and the aggregate
security limitations, confirm an approved method of LinkedIn access, and inspect
an isolated local environment with a private profile and reviewed browser egress
policy. The current development environment does not permit LinkedIn destinations;
this work does not change that policy or request credentials.

For a future one-off session:

1. The operator prepares the private profile directly; never put credentials or
   cookies in chat, source, logs, or a shared environment snapshot. Keep automatic
   login off. Use the dedicated stdio launcher, no persistent/shared public service.
2. Validate the five-tool list and repeat excluded direct-call checks before any
   browser reads. Confirm the explicit mode is required and no startup fallback.
3. Time-box to 30 minutes: one 24-hour search per geography/workplace bucket plus
   one approved 7-day catch-up, at most two cursor pages of five jobs per query
   (at most 60 returned cards), and at most ten job-detail reads. Preserve existing
   pacing; make no automatic retries or continuous schedule.
4. The supervisor compares a small sample to visible UI: exact pay/evidence,
   destination href without following it, state statement, rendered full description,
   original versus reposted time, selected counts, duplicates, and terminal metadata.
   A first page is not inventory acceptance; unresolved counts must stay truncated.
5. Stop immediately on checkpoint, restriction, expired authentication, permission
   failures, parser drift, unexpected navigation, unreviewed egress, or uncertain
   action effects. Do not widen access or add bypasses to finish the sample.
6. Retain only aggregate, private validation metrics: calls, buckets, extraction
   accuracy, unknown/truncated counts, pause state, and time spent. No DOM, screenshots,
   personal profiles, cookies, raw descriptions, or authentication material in Git.
   Close the process at the deadline; no ledger writes or application actions.

Success requires correct evidence on every reviewed returned field, no guessed
eligibility/dates, honest truncation on every unreconciled scan, and zero excluded
actions. Any failure returns the feature to offline development. This plan itself
is not authorization to authenticate or execute live calls.

## Investment decision: LinkedIn MCP versus employer ATS feeds

**Recommend employer ATS feeds/public job APIs as the primary acquisition path,
with LinkedIn MCP as a bounded optional discovery supplement.** This PR justifies
one supervised acceptance decision, not continuous LinkedIn investment or rollout.

Where employers offer public feeds/APIs (for example Greenhouse or Lever job-board
interfaces), they commonly provide direct application URLs and employer-source
job descriptions without a LinkedIn account. Structured IDs and fields usually cost
less to maintain than UI selectors. Availability, salary detail, original publication
times, and employer coverage still vary; feeds must be individually validated and
repost matching is not solved merely by using an API. No ATS connection is added.

LinkedIn may add discovery across employers not already in a target-company list,
but brings session/account risk, UI drift, incomplete inventories, indirect apply
buttons, relative/reposted dates, and recurring manual acceptance costs. Measure
incremental qualified opportunities against an independently approved ATS sample,
unknown-field frequency, truncation, and operator maintenance time. Continue only
if the supervised sample demonstrates useful incremental coverage with correct
critical fields and acceptable account behavior. Prioritize ATS coverage and stop
expanding LinkedIn if checkpoints, repeated drift, or unknown critical fields erase
that benefit. Keep all application tracking in the existing authoritative ledger.

## Verification record

Validated on Python 3.12.14 / Linux, 2026-10-15:

- **640 passed, 0 failed, 0 skipped**, in **159.94 seconds**. This is 97 more tests
  than the prior PR head's 543-test suite.
- **85.52% total coverage with branch measurement enabled**, above the unchanged
  85% gate (prior head: 85.12%). This is coverage.py's combined reported metric,
  not a claim that every branch or live UI state is covered.
- Ruff format: **122 files** already formatted. Ruff lint passed. Strict Pyright:
  **0 errors, 0 warnings**.
- Offline lockfile validation, diff whitespace checks, and source/wheel builds
  passed. No dependency or lockfile change was needed.
- An intermediate full run found only a packaging contract expecting one console
  script; the expectation now includes the dedicated discovery script. The final
  complete run above passed after that correction and the last bounded-DOM tests.
- Successful full output: `/tmp/linkedin-v2-full-offline.log` in the task workspace.
  GitHub Actions status for the published head is recorded in the PR description
  and completion report after querying GitHub; local success is not remote CI success.

Reproduce the complete offline suite in this managed environment using the installed
native browser cache (upstream bootstrap tests deliberately clear overrides):

```bash
XDG_CACHE_HOME=/workspace/.cache \
PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright \
LINKEDIN_MCP_BROWSER_CACHE_PATH=/workspace/.cache/ms-playwright \
UV_CACHE_DIR=/workspace/.cache/uv \
LINKEDIN_MCP_AUTO_LOGIN_ON_START=false \
uv run --offline pytest --cov=linkedin_mcp --cov-branch --cov-report=term-missing
```

No non-Linux run, authenticated acceptance, deployment, external integration,
continuous monitoring, Google Drive/ledger modification, or merge is claimed.

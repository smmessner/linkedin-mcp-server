# Restricted job discovery: offline validation and future integration

Scope: personal fork `smmessner/linkedin-mcp-server`, development branch
`dev/job-discovery-read-only`, based on `0027edc4eba3398a19173680f1063e9d7cdfa2a6`.
No LinkedIn authentication, live pages, external integration, job-document or ledger
edits, persistent deployment, or merge is part of this change.

## Server restriction

Configure `LINKEDIN_MCP_ACCESS_MODE=job-search-only`. Only these tools exist:
`linkedin.jobs.search`, `linkedin.jobs.get`, `linkedin.server.status`,
`linkedin.session.status`, and `linkedin.capabilities.list`.

The fixed allowlist excludes unrelated reads and every account-changing tool.
Unknown tools are excluded too. MCP registration rejects direct calls; the worker
and executor independently deny excluded capabilities, including with a broad
registry supplied by another composition root. Denial precedes queue submission,
read replay, persistence, and provider access. `access_denied` is the internal
safe error code. MCP calls to absent tools receive an unknown-tool error.

Automatic login is forced off. The mode participates in runtime identity so a
restricted client cannot silently reuse a differently configured standard runtime.
The captured-source resource is omitted to prevent unrelated stored-data access.
Inline job results and source references remain, but those references cannot be
resolved through the resource API in this mode. Standard mode retains upstream
registration, approval behavior, automatic-login configuration, and resources.

## Synthetic validation and capability gaps

Fixtures in `tests/fixtures/job_discovery/scenarios.json` contain invented employers,
roles, descriptions, and `example.org` application links. They are synthetic-only,
not live-accepted or recorded LinkedIn data. Tests use real Playwright against
in-memory HTML with browser requests aborted, production page objects, and the
restricted executor. Separate official MCP client tests verify all five approved
operations and denial of all 19 excluded operations. The existing suite also
exercises full standard-mode tools and offline simulated actions.

| Requirement | Offline evidence / existing support | Additional development or validation |
| --- | --- | --- |
| Remote jobs eligible for Washington | WA location plus remote filter; descriptions retain explicit WA permission, unspecified states, and WA exclusion | No typed eligibility decision. Review employer state restrictions; remote and geography filters alone do not prove eligibility |
| Seattle hybrid and onsite | Both workplace filters and Seattle location serialize and extract correctly | Validate real radius/geography behavior only in a separately authorized stage |
| Sacramento and San Diego | Each city serializes and extracts with synthetic roles | No live inventory or relevance claim |
| Past 24 hours / 7 days | Both windows exercised across all seven scenarios; query values `r86400` and `r604800` | Filters request a window; no independent absolute posting-time verification or guarantee about reposts |
| Complete descriptions | Exact full synthetic descriptions survive detail extraction; existing tests cover expand-button behavior and delayed rendering | No typed description-completeness indicator; missing description can be null. Live layouts remain unvalidated |
| Employer, title, location | Typed fields and evidence; employer can be null; missing search-card location rejects with parser drift | A missing field must remain unknown; do not infer employer or eligibility |
| Compensation when present | Salary text survives in description and can appear in search insights | No structured currency, pay range, or pay-period fields; no compensation normalizer |
| Application URL | Canonical LinkedIn job URL is returned; application method distinguishes external/Easy Apply/unavailable | External employer destination is not returned even when a visible link exists. No new clicks or navigation were added |
| Pagination | Existing cursor binding/replay tests plus synthetic disjoint pages and terminal metadata | A changing live inventory is not a stable snapshot |
| Duplicates | Duplicate identities within a rendered page reject with parser drift; repeated identities across rescans/cursor pages are suppressed | No cross-run persistence or cross-source/repost matching |
| Incomplete results | Restricted executor converts a terminal advertised-count shortfall into `safety_bound`, `truncated=true`, no continuation | Upstream page collector still reports completion on absent next controls; standard behavior is preserved. Unknown or inaccurate counts and live terminal proof require further work |
| Missing information | Optional employer preserved as null; required location absence rejects; existing fixtures cover unavailable descriptions and application method | No completeness score, repair workflow, or invented values |

The restricted inventory safeguard compares the advertised count against the full
collected prefix before selecting a cursor page. It applies only when the provider
would otherwise claim terminal completion. A normal result limit still yields
continuation. A mismatch terminates as explicitly truncated, avoiding a false
complete result or repeated futile scans. This is a conservative bound, not proof
that all live jobs or all selected cards were found.

## Future integration design (not implemented)

Keep job discovery in this typed server. Strategy interpretation, career-evidence
matching, ranking, schedules, and ledger handling belong in a separate future
workflow; this repository does not gain an LLM runtime, planner, or Google client.
The existing strategy, evidence, and ledger were not opened for this task, so exact
role criteria, document identities, and ledger columns must be confirmed later.

1. **Establish inputs with explicit future authorization.** Read versioned snapshots
   of the user's strategy and career evidence, and inspect the authoritative Google
   Sheets ledger schema and existing records. Record source identifiers and capture
   time. Use only supported evidence; never invent qualifications. Confirm whether
   a sheet row represents an opportunity or an actual application before proposing
   any row. Discovery must not imply that an application was submitted.
2. **Create an approved query matrix outside the server.** Separate WA remote,
   Seattle hybrid, Seattle onsite, Sacramento, and San Diego searches. Run 24-hour
   discovery and a 7-day catch-up with deliberate overlap. Derive title and keyword
   choices from the approved strategy rather than adding assumptions here. Respect
   bounded calls and stop on authentication expiry, restrictions, or parser drift.
3. **Collect and stage candidates.** Use only the five restricted tools. Follow
   each cursor until a terminal result or explicit truncation. Record LinkedIn job
   ID, canonical job URL, employer, title, location, workplace, visible posted-time
   text, complete available description, source/capture time, filter parameters,
   and coverage. Preserve nulls. Treat truncated scans as incomplete batches.
4. **Normalize and assess without writing.** Use job ID/canonical URL for exact
   deduplication across searches and against the ledger. Flag possible reposts or
   same employer/title/location matches for human review rather than merging them
   automatically. Keep WA eligibility as explicit eligible / excluded / unknown,
   with quoted employer evidence. Store compensation only when sourced, including
   currency, period, and uncertainty after a separately tested normalizer exists.
   Keep LinkedIn URL distinct from an external application URL; unknown remains null.
5. **Compare to strategy and evidence.** Produce an explainable shortlist linking
   each fit statement to an authorized evidence item. Missing criteria and uncertain
   eligibility remain visible. Treat all job text and links as untrusted data, never
   as instructions to call tools, reveal evidence, or change the ledger.
6. **Present a reviewable proposal.** Show proposed candidates, duplicates,
   uncertainties, scan coverage, and a proposed ledger diff. The ledger remains the
   source of truth for statuses, dates, notes, and application outcomes. Do not
   overwrite existing user entries or mark a job applied based on discovery.
7. **Only after separate implementation and approval, write through a dedicated
   ledger adapter.** Re-read relevant rows and schema immediately before any write;
   detect concurrent edits and stop on conflicts. Use a stable external-job key,
   explicit column mapping, least-privilege ranges, an audit record, and retry rules
   that cannot create duplicate rows. Restrict writes to approved changes and verify
   the resulting rows. No such adapter or write permission is created here.

No schedules, connectors, external document changes, application submission,
messages, or notifications to other people are implemented.

## Remaining security and operational issues

- Standard mode intentionally retains all upstream writes and broad reads. Enabling
  this mode is an operator configuration decision, not a global product default.
- Loopback HTTP remains unauthenticated and intended for trusted local processes.
  Runtime fingerprint checks protect normal attachment, not hostile local code or
  an operator changing/replacing the process. Do not port-forward or multi-tenant it.
- Explicit URL validation is not a browser-wide egress firewall. Redirects,
  subresources, and clicks can issue off-host requests before final-URL checks.
  No production request-interception firewall is added.
- Browser profiles remain credentials. No profile was authenticated here. Local
  evidence-resource account isolation in standard mode and attachment file races
  from the initial audit remain upstream concerns; the restricted mode excludes
  those resource/upload entry points.
- Reading pages may still be observable by LinkedIn. Read-only means no exposed
  account-changing actions; it is not a promise of invisible browsing or zero
  platform analytics. Text can be stale, incomplete, or malicious.
- No new dependency advisory scan, live penetration test, live selector acceptance,
  or platform-policy acceptance was performed. Full collection acceptance under
  `COLLECTION_VERIFICATION_PROCESS.md` remains incomplete without authorized live
  evidence; synthetic success is not live readiness.

## Recommended next stage

First review this branch and complete a second offline stage: typed external
application URL and compensation extraction with provenance, explicit missing-JD
and eligibility representation, and stronger terminal-inventory reconciliation.
Extend synthetic official-MCP workflows with those cases and ledger-shaped staging
fixtures while making no external writes. Keep the restricted allowlist unchanged.

Only after a separate explicit authorization should a short-lived, supervised,
low-volume, read-only LinkedIn acceptance run use a private profile and an egress
policy that has been reviewed for that stage. Verify the five-tool inventory and
negative direct calls before browser access, test each geographic/time bucket,
reconcile every cursor scan or record truncation, stop on checkpoints, and retain
only aggregate private validation evidence. Do not enable account-changing tools
or a persistent service. Ledger integration needs its own later approval.

## Verification record

Validated on Python 3.12.14 / Linux, 2026-10-15:

- Complete offline suite: **543 passed**, no failures or skips, in 137.53 seconds.
  This includes 28 new focused checks over the 515-test baseline.
- Branch coverage: **85.12%**, satisfying the unchanged 85% threshold
  (baseline: 85.06%).
- Ruff format: 118 files formatted; lint passed.
- Strict Pyright: zero errors or warnings.
- `uv lock --offline --check`, `git diff --check`, and offline sdist/wheel builds
  passed. No dependency or lockfile changes were needed.
- Full successful output: `/tmp/linkedin-job-discovery-offline.log` in the task
  workspace. No remote CI or non-Linux validation is claimed by these results.

The managed environment stores installed Chromium in the workspace cache.
Some upstream bootstrap tests deliberately remove the Playwright override, so
set the native XDG cache too when reproducing the complete suite here:

```bash
XDG_CACHE_HOME=/workspace/.cache \
PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright \
LINKEDIN_MCP_BROWSER_CACHE_PATH=/workspace/.cache/ms-playwright \
UV_CACHE_DIR=/workspace/.cache/uv \
LINKEDIN_MCP_AUTO_LOGIN_ON_START=false \
uv run --offline pytest --cov=linkedin_mcp --cov-branch --cov-report=term-missing
```

Tests remain network-isolated and use only synthetic browser/provider fixtures.
No LinkedIn authentication, live traffic, credentials, external integrations,
strategy/evidence-document or ledger changes, persistent deployment, or merge
occurred.

# Confiant -> GAM blocklist

Weekly job: pull Confiant's `issue_type_by_domain` report via their REST API,
pick the Google-served Security-category creatives, append their landing-page
domains to a named GAM Protection's "Advertiser URLs" field.

A manual `--csv` fallback is supported for backfills or for the rare case
when the Confiant API is down.

## Why this is a local-only Playwright job, not a GitHub Actions cron

GAM does not expose Protections (the resource that holds advertiser-URL block
rules) through any API surface — not the modern `admanager_v1` REST client, not
the legacy `googleads` SOAP client, not the Authorized Buyers RTB API. This is
a long-standing gap that Google has not closed; see the [community thread][1].
The only programmatic path is to drive the web UI.

Driving the web UI requires a logged-in Google session with 2FA. That can't be
done from a headless GitHub Actions runner without violating Google's ToS
(stored credentials, automated 2FA), so the job runs locally via launchd on a
Mac that's already authenticated to GAM through a normal browser login.

Tradeoffs you're accepting:
- The Mac must be on (or wake up via Power Nap) for the scheduled run to fire.
- Google occasionally invalidates the saved session; you'll get a failure
  email and need to re-run with `--inspect` once to log in again.
- When Google ships a Protections UI change, the selectors in
  `gam_blocklist_ui.py` break; the script aborts loudly (won't paste into the
  wrong field) and you update `_SELECTORS` and re-run.

[1]: https://support.google.com/admanager/thread/7512693?hl=en

## First-time setup

```bash
cd ~/code/yield-dashboard
pip install -r requirements.txt
python -m playwright install chromium

# Required env vars — add to .env (the scripts auto-load it):
#   GAM_NETWORK_ID                  your GAM network id
#   CONFIANT_API_KEY                from app.confiant.com Settings
#   AGENTMAIL_API_KEY               for outbound summary emails (daily + weekly)
#   AGENTMAIL_INBOX_ID              e.g. newsweek@agentmail.to
#   CONFIANT_REPORT_TO_EMAIL        recipient of the daily post-run summary
#
# Don't add these as empty strings to the launchd plist EnvironmentVariables
# dict — empty values defeat the script's _load_dotenv() (setdefault won't
# overwrite an empty value already in os.environ). Plists should only carry
# scheduling + paths.

# 1. Open a browser, log into GAM manually, complete 2FA.
#    This populates ~/.confiant-blocklist/playwright-profile/ with cookies.
python confiant_blocklist.py --inspect

# 2. Identify the target Protection ID. In GAM > Delivery > Protections,
#    click into the Protection — the URL ends with .../protection_id=<id>.
#    For Newsweek today: 28044902 ("Everything", prod catch-all).

# 3. Sanity-check what's already in that Protection BEFORE we modify it.
#    --print-existing opens the browser, reads the Advertiser URLs textarea,
#    prints it to stdout, and exits. No data pulled, no writes.
python confiant_blocklist.py --protection-id 28044902 --print-existing \
    > existing_urls_before.txt
wc -l existing_urls_before.txt

# 4. Dry-run against the live Confiant API (no GAM modification).
python confiant_blocklist.py \
    --protection-id 28044902 --protection-label Everything --dry-run

# 5. Real run (browser will open). Add --debug for screenshots.
python confiant_blocklist.py \
    --protection-id 28044902 --protection-label Everything --debug
```

## Categories

By default the script blocks `Security`-category issues only (Phishing, Cloaked,
Forced Redirect, etc.) — matches what was historically in the weekly outreach
email. Pass `--categories "Security,Quality"` to also block Quality issues
(pop-ups, auto-play video, heavy ads). Quality issues are noisier and less
clearly malicious; consider per-issue-type review before turning them on.

## Protection target

The script navigates *directly* to the Protection detail page via its ID, not
by clicking on a name link. This avoids the most fragile selector in the flow.
If GAM ever ships a routing change that breaks the URL format, override it
without code changes:

```bash
export GAM_PROTECTION_DETAIL_URL_FMT='https://admanager.google.com/{network_id}#delivery/protections/<new format>/{protection_id}'
```

`--protection-label` is for emails and the state table only — useful to make
weekly notifications read "Confiant -> GAM blocklist (Everything)" instead of
"(Protection #28044902)". The script never searches by label.

## Wiring up the weekly launchd cron

1. Copy the plist template and fill in the `REPLACE_ME` values:
   `GAM_NETWORK_ID`, `CONFIANT_API_KEY`, `AGENTMAIL_API_KEY`,
   `AGENTMAIL_INBOX_ID`, `CONFIANT_REPORT_TO_EMAIL`. (Agentmail vars are for
   the outbound summary email only — no inbound CSV fetching anymore.)
   ```bash
   cp ~/code/yield-dashboard/.launchd/com.newsweek.confiant-blocklist.plist \
      ~/Library/LaunchAgents/
   # edit the copy in ~/Library/LaunchAgents
   ```
2. Load:
   ```bash
   launchctl load -w ~/Library/LaunchAgents/com.newsweek.confiant-blocklist.plist
   ```
3. Trigger a one-off run to verify:
   ```bash
   launchctl start com.newsweek.confiant-blocklist
   tail -f ~/.confiant-blocklist/launchd.err.log
   ```

## What gets emailed daily (per-run summary)

A run summary that includes:
- Counts: total Google rows, blockable domains, already-in-state, new
  domains pushed to GAM, cloaked rows skipped.
- Full list of new domains added to the Protection, with issue type.
- Cloaked rows (Confiant-internal IDs like `ID 17830`) grouped by issue type
  with their adtrace URLs, for manual review.

Failed runs are flagged in the subject line and include the exception in the
body. Dry-run summaries say "(DRY RUN)" in the subject and show what *would*
have been pushed.

## Weekly RevOps summary

`confiant_blocklist_weekly_report.py` rolls up the last 7 days of pushes
into a single digest emailed to RevOps every Monday at 09:00 ET. It reads
only from `state.sqlite` — no Confiant or GAM API calls — so it's cheap
and always agrees with what actually landed in the GAM Protection.

Contents:
- Per-day count of URLs added (so it's obvious whether activity is flat,
  spiking, or falling off).
- All new URLs grouped by issue type, ordered by descending count.
- Cumulative blocklist size + a deep link into the GAM Protection (when
  `GAM_NETWORK_ID` is set).

Default recipient: `revops@newsweek.com`. Override with `--to`, env var
`CONFIANT_BLOCKLIST_WEEKLY_TO`, or add CCs via `CONFIANT_BLOCKLIST_WEEKLY_CC`.

One-off run for a different window or recipient:

```bash
# preview without sending
python confiant_blocklist_weekly_report.py --days 14 --dry-run

# emit the HTML to stdout (useful for piping to a file or pbcopy)
python confiant_blocklist_weekly_report.py --print-html

# send to a different recipient
python confiant_blocklist_weekly_report.py --to someone@newsweek.com
```

Install the cron the same way as the daily one:

```bash
cp ~/code/yield-dashboard/.launchd/com.newsweek.confiant-blocklist-weekly.plist \
   ~/Library/LaunchAgents/
# fill in REPLACE_ME for AGENTMAIL_API_KEY, AGENTMAIL_INBOX_ID, GAM_NETWORK_ID
launchctl load -w ~/Library/LaunchAgents/com.newsweek.confiant-blocklist-weekly.plist
# one-off trigger to verify
launchctl start com.newsweek.confiant-blocklist-weekly
tail ~/.confiant-blocklist/launchd.weekly.err.log
```

## High Risk Ad Platforms (HRAPs) — seed + SSP forward

Confiant publishes a periodic notice of **High Risk Ad Platforms** — bidding
or serving intermediaries with persistent abnormal volumes of malicious
campaigns. Their standing recommendation is to block at every layer:
publisher Protection AND upstream at SSP partners.

We persist the canonical list at `data/confiant_hraps.json` and run two
manual scripts against it whenever Confiant ships an update.

### `confiant_blocklist_seed_hraps.py` — push HRAPs into the GAM Protection

```bash
# Preview the diff against current state (no browser, no GAM, no state writes)
python confiant_blocklist_seed_hraps.py --dry-run

# Push new HRAPs to the prod Protection. Browser opens.
python confiant_blocklist_seed_hraps.py \
    --protection-id 28044902 --protection-label Everything
```

- Reads `data/confiant_hraps.json`, diffs against `state.sqlite`, pushes
  only the delta (idempotent — re-run after editing the JSON).
- **Strips Confiant's `*.` wildcard prefix before push.** GAM's Advertiser
  URLs field rejects entries with a leading asterisk; the bare-domain form
  blocks all subdomains automatically. JSON stays faithful to Confiant's
  notation so the SSP-forward email reads the way SSPs index HRAPs. The
  normalization is push-time only (`_normalize_for_gam` in the seeder).
- **Pushes in batches of `--batch-size` (default 30).** GAM's modal-input
  validation step is ~linear in the number of entries pasted; somewhere
  around 40-50 it starts to exceed the modal-Update-button enable wait
  (raised to 30s in `gam_blocklist_ui.py` as a separate safety bump).
  Each batch records its own `runs` row in `state.sqlite` so partial
  successes don't roll back.
- Records each push with `issue_type = "HRAP — <platform>"` so the weekly
  RevOps digest can distinguish platform-blocks from per-creative blocks
  at a glance.

### `confiant_hrap_forward.py` — Outlook drafts to every SSP partner

```bash
# Preview without creating drafts
python confiant_hrap_forward.py --dry-run

# Create one draft per SSP in settings.json -> ssp_contacts
python confiant_hrap_forward.py

# Only one SSP (testing)
python confiant_hrap_forward.py --provider 'Index Exchange'
```

- Reuses `confiant_outreach_drafts.get_token` (same Microsoft Graph token
  cache at `~/.confiant-outreach/msal_cache.json`).
- Reuses `confiant_outreach._load_settings` (same SSP contact distro and
  the `cc_emails` field — drafts CC `revops@newsweek.com` automatically).
- Reuses `confiant_outreach_drafts.create_graph_draft` (handles RFC 5322
  display names — `"Tristen Fabricant <tfabricant@zetaglobal.com>"`).
- Subject: `<SSP>//Newsweek — Confiant HRAP notice: N high-risk ad
  platforms to block`. Body: branded layout matching the weekly RevOps
  digest, full HRAP table grouped by platform, "additions this update"
  highlighted at top.

### Updating the list

When Confiant ships their next notice email:

1. Edit `data/confiant_hraps.json` — add new entries to `platforms`,
   list them in `additions_this_update`, bump `updated_at`.
2. `python confiant_blocklist_seed_hraps.py` — idempotent re-push, only
   new ones go through.
3. `python confiant_hrap_forward.py` — fresh drafts to all 35 SSPs.

## Confiant issue types — which layer enforces what

Confiant flags fall into two broad categories that need different
enforcement layers. Picking the wrong layer is the most common SSP-
outreach mistake; the per-creative ask is right for some flags and
deeply wrong for others.

### Security-category — destination-based, GAM Protection works

`Cloaked`, `Phishing`, `Forced Redirect`, `Investment Scam`,
`Pixel Stuffing`, `Unsafe/malware landing page`, `Misleading Claims (Health)`.

These are tied to specific malicious destinations. The daily blocklist
cron pushes the destination domains into GAM Protection 28044902. The
per-creative outreach to SSPs is also the right ask — "block this
specific advertiser/seat at source so it doesn't bid anywhere."

### Format-compliance — behavior-based, needs SSP format enforcement

`Video (Click)`, `Video (Mouse hover)`, `Video (Automatic)`,
`Expandable (Automatic)`, `Expandable (Click)`, `Pop-up (Automatic)`,
`Heaviness`, `Skin detection`.

These describe **behaviors** of the creative, not malicious destinations.
Examples:

- **Video (Click)** — on click, the creative triggers a redirect chain
  through undeclared domains, opens a new tab without proper user-intent
  semantics, or triggers sound on a unit configured silent.
- **Video (Mouse hover)** — same behaviors but pre-click, on hover.
- **Video (Automatic)** — same behaviors fired without any user
  interaction (auto-play with sound, auto-redirect, auto-expand).

The flagged creative's destination is almost always a legitimate ad-
serving CDN: `cache-ssl.celtra.com`, `chunk-oci-us-…edgemv.mux.com`,
`html5.adsrvr.org`, `swf.mixpo.com`. **Blocking those URLs in GAM
Protection would block every legitimate video creative on those
platforms.** The pipeline we built is the wrong tool for this class.

The correct enforcement layers, in priority order:

1. **SSP format-enforcement settings** — VAST/VPAID/MRAID click-through
   validation, sound-on-hover prevention, redirect-chain audits at the
   seat/exchange level. Reject the creative before it bids. The SSP owns
   this and it's standard infrastructure for them.
2. **Confiant Real-Time Blocking** — behavior-based blocks at render
   time on Newsweek's pages. Separate from the `issue_type_by_domain`
   API our daily cron reads — Active Blocking is a different Confiant
   product config, tuned at app.confiant.com.
3. **GAM Protection URL list** — destination-based, last-resort, only
   makes sense for Security-category.

### Reply pattern when an SSP follows up

When an SSP rep (Yahoo, Amazon TAM, etc.) asks for more context on a
weekly outreach, **lead with the platform-level ask** before the per-
creative details:

> "Can your team check whether \<SSP\> has format-enforcement settings
> that catch \<issue type\>? If so, why aren't these creatives being
> caught? If not, can they be turned on? If your enforcement catches
> it, you protect every publisher in your network, not just us."

Then provide the Confiant taxonomy reference, the IAB/MRC + Newsweek
policy mapping, and the per-creative specifics as supporting evidence.
The per-creative trace is the fallback path if they decline the
platform-level option, not the lead. This applies only to format-
compliance flags; for Security-category flags the per-creative ask
remains the right lead.

## State

- `~/.confiant-blocklist/state.sqlite` — `blocked_domains` (one row per domain
  ever pushed, tracks protection_id + protection_label + first_seen date) +
  `runs` (one row per invocation, tracks source/categories/counts/status).
  HRAP pushes use `source = "file://<path>#batch=N/M"` and `issue_type =
  "HRAP — <platform>"`.
- `~/.confiant-blocklist/playwright-profile/` — Chromium profile with the
  logged-in Google session.
- `~/.confiant-blocklist/launchd.{out,err}.log` — daily cron stdio.
- `~/.confiant-blocklist/launchd.weekly.{out,err}.log` — weekly digest stdio.

None of these are committed; the scripts auto-create them.

## Credentials live in `.env`, not in the launchd plist

All four scripts (`confiant_blocklist.py`, `confiant_blocklist_weekly_report.py`,
`confiant_blocklist_seed_hraps.py`, `confiant_hrap_forward.py`) call the same
`_load_dotenv()` helper at startup, which reads `~/code/yield-dashboard/.env`
via `os.environ.setdefault`. Required keys:

```
GAM_NETWORK_ID
CONFIANT_API_KEY
AGENTMAIL_API_KEY
AGENTMAIL_INBOX_ID         # newsweek@agentmail.to
CONFIANT_REPORT_TO_EMAIL   # daily summary recipient — comment out to pause
CONFIANT_ALERT_TO_EMAIL    # failure-only alert recipient (see "Email routing")
```

**Do NOT redeclare any of these in the launchd plist `EnvironmentVariables`
dict** — not even as empty `<string></string>` placeholders. `setdefault`
treats them as "already set" (Python doesn't distinguish "empty" from
"unset" once a key is present in `os.environ`) and the script silently
skips its outbound email. The daily blocklist post-run summary lost ~10
days of emails to exactly this bug between 2026-05-26 and 2026-06-08 — see
PR #133 for the fix. The plist should carry only `PATH`, paths
(`CONFIANT_BLOCKLIST_PROFILE_DIR`, `CONFIANT_BLOCKLIST_STATE`), and
process-control keys (`AbandonProcessGroup`, etc.).

## Phase 2 integration: ARC blocks happen in the daily cron too

As of PR #277 (2026-06-18) the daily `confiant_blocklist.py` cron has a
Phase 2 — after pushing destination-URL flags to GAM Protection, it walks
the same Cloaked-by-ID rows the standalone script handles, opens each
Confiant adtrace, extracts the GPT Ad Response ID, and runs the ARC
filter + block flow. So most Cloaked-by-ID rows get blocked overnight
without anyone running the manual script.

Failure isolation: Phase 2 runs in a `try/except` that captures errors
into `ArcStats.error_msg`. URL push results (Phase 1) stay valid even
if ARC fails entirely. The daily summary email shows ARC results in
its own section + a red callout when Phase 2 errored.

Skip-already: Phase 2 reads existing `gam-arc:<gpt_id>` rows from
`state.sqlite` and skips IDs we've already blocked. So a Confiant ID
that re-appears in the 7-day rolling window doesn't trigger a duplicate
Playwright run.

Open-Bidding rows correctly report `not-in-arc` — those creatives are
served by external SSPs via Google's Open Bidding mechanism and don't
surface in GAM's Ad Review Center at all. Confiant's Active Blocking
catches them upstream (verified via `providers_by_day -> Blocking
Status = Active Blocking`).

Disable with `--no-arc` if you need a URL-only run for some reason.

## Manual blocks in GAM Ad Review Center (Cloaked / no-destination case)

The daily `confiant_blocklist.py` cron handles Cloaked rows that have a
landing-page domain (most of them: see "Confiant issue types" above for
the `*.rtbrain.app` / `*.walnutplate.online` etc. pattern). What it can't
handle is the **`Detail = ID xxxxx`** subset — cloaked creatives where
Confiant's `issue_type_by_domain` API doesn't expose a destination URL.
For those, the publisher-side action is a per-creative block in **GAM's
Ad Review Center**, filtered by the GPT Ad Response ID.

`scripts/confiant_gam_arc_block.py` automates this:

```bash
# JSON input — the same shape confiant_blocklist.py emits for cloaked review
python scripts/confiant_gam_arc_block.py /tmp/cloaked_review_queue.json

# …or pass adtrace URLs directly
python scripts/confiant_gam_arc_block.py \
    https://app.confiant.com/adtrace/<hash1> \
    https://app.confiant.com/adtrace/<hash2>

# Dry-run: extract GPT IDs only, don't drive GAM
python scripts/confiant_gam_arc_block.py --dry-run /tmp/queue.json
```

Two-phase flow:

1. **Pull the GPT Ad Response ID from Confiant** — open each adtrace page
   in the existing `~/.confiant-blocklist/playwright-profile/`, regex out
   `GPT Ad Response ID\s+(\S+)` from the page body.
2. **Block in GAM Ad Review Center** — navigate to
   `https://admanager.google.com/22541732127#brand_safety/ad_review_center`
   (the new URL — Google moved ARC from `#creatives/ad_review_center` to
   the Brand safety section), apply the **`Ad response ID` filter** (NOT
   the generic `Text search`), click the Block button on the `Ad match`
   card.

### Gotchas captured from the 2026-06-17 run

- **`Ad response ID` autocomplete is value-conditional.** Typing
  characters in the filter box doesn't show "Ad response ID" as an
  option until you type a value that looks like one. Script types the
  value first, then clicks the `[role="menuitem"]:has-text("Ad response ID:")`
  option.
- **Low-impression creatives stick in skeleton-render state.** Cards for
  2-impression and lower creatives can hang as gray placeholders forever
  — the `Block ad` button never appears. The fix that worked: after
  applying the filter, **reload the page**. GAM persists the filter as
  `&as=<base64-blob>` in the URL hash, and the post-reload hydration
  renders the card properly. The script falls back to this automatically
  when the Block button isn't visible after 15s.
- **`Couldn't find matching ad` is a real signal, not always a bug.**
  Confiant's Real-Time Blocking catches ~99% of Cloaked Google flags
  before they serve. The handful Confiant flags in `issue_type_by_domain`
  but ARC says "Couldn't find" are the ones RTB caught upstream — they
  literally never reached GAM. No action needed; the script reports
  `not-in-arc` and moves on.

### What lands in state.sqlite

Each successful block writes one row to `blocked_domains` with:

- `domain = "gam-arc:<gpt_ad_response_id>"` (tagged differently from
  destination-URL blocks so the weekly digest can distinguish them)
- `issue_type = "Manual block in GAM ARC — Confiant ID xxxxx → GPT ..."`
- `protection_label = "ARC manual block"`

In the weekly RevOps digest these surface under their own issue-type
card, separate from the URL-based GAM Protection blocks.

## Email routing — success vs failure recipients

The daily script splits the post-run email by outcome. Two env vars:

| Var | Sends on | What it's for |
|---|---|---|
| `CONFIANT_REPORT_TO_EMAIL` | Success + dry-run | "Here's what we blocked today" daily drumbeat. Goes to a brand-safety alias or whoever wants the steady cadence. Comment out / unset to pause. |
| `CONFIANT_ALERT_TO_EMAIL` | Failure only | One-line "the cron broke, here's the error" to whoever's on-call for fixing it (refreshing the Google session, repointing a selector). |

Resolution order on each run:

| Outcome | Recipient picked |
|---|---|
| Success / dry-run | `CONFIANT_REPORT_TO_EMAIL` (if set; otherwise silent) |
| Failure | `CONFIANT_ALERT_TO_EMAIL` if set, else falls back to `CONFIANT_REPORT_TO_EMAIL` for back-compat, else silent with a `Skipping FAILURE email…` warning to stderr |

The four useful combinations:

| `REPORT` | `ALERT` | Behaviour |
|---|---|---|
| set | set | Daily summaries + loud failures — different recipients possible |
| set | unset | Daily summaries; failures inherit the summary recipient (original behaviour) |
| **unset** | **set** | **Quiet-on-success, loud-on-failure** ("monitoring mode" — what the laptop has been on since 2026-06-10) |
| unset | unset | Silent. Failure prints a warning to launchd.err.log so the gap is visible |

The weekly RevOps digest (`confiant_blocklist_weekly_report.py`) is its
own script with its own `--to` arg — it doesn't read these env vars.
Pause it by unloading the plist:

```bash
launchctl unload ~/Library/LaunchAgents/com.newsweek.confiant-blocklist-weekly.plist
```

The plist file stays on disk (just dormant). Resume with `launchctl load -w`.

## Data sharing & Claude — what's connected

This section exists to answer the legal-team question "what's connected to
Claude and what data flows through it?" without re-deriving it from the
codebase each time. Keep current when the pipeline changes.

### What flows where

**Production cron jobs do NOT call Claude / Anthropic.** Both
`confiant_blocklist.py` (daily push to GAM) and `confiant_outreach_drafts.py`
(weekly Outlook drafts to SSP partners) run as local Python on the laptop
via launchd. They pull from Confiant's API and write to either:

- **Outlook** via Microsoft Graph — drafts land in Roger's mailbox; nothing
  auto-sends.
- **GAM Protection 28044902 ("Everything")** via Playwright UI automation
  (GAM Protections has no API). This is a publisher-side blocklist; the
  data stays inside our Google Ad Manager network.

No Anthropic API call happens during these runs.

### Claude's three roles

| When | What Claude sees | Where output goes |
|---|---|---|
| **Production runtime** | Nothing — no LLM call | n/a |
| **Development** | Code, error messages, design discussions | The committed Python source (no LLM dep at runtime) |
| **Interactive drafting** (Claude Code chat) | What Roger pastes — SSP rep replies, Confiant fields, vendor xlsx contents | Outlook draft HTML, repo edits, terminal commands — all reviewed by Roger before any external action |

### Per-SSP data-sharing rule

In every per-SSP outreach email the pipeline generates, the only data we
share with a given SSP is **that SSP's own demand data** — creative IDs
they served and that Confiant flagged on Newsweek's inventory. We never
include another SSP's data in a third SSP's outreach. This fits within
the "aggregated/anonymized end-user interaction data" allowances we've
seen in the OpenX, TTD, DV, Pubmatic, GAM 360, IX, and Magnite supply
agreements; legal has the contracts.

### Vendors worth listing on a data-sharing audit chart

| Vendor | Role | What we send/receive |
|---|---|---|
| Confiant | Data source | Pull only — our property's flagged creatives (IDs, destinations, imps, timestamps) |
| Microsoft Graph (Outlook) | Draft creation | Email draft bodies stored in Roger's mailbox; standard M365 terms apply |
| agentmail.to | Outbound MTA | Internal status emails only (daily/weekly summaries) — no advertiser data flows through |
| Anthropic (Claude Code) | Code-gen + interactive drafting | Per Anthropic's enterprise terms for the Claude account |

### Reference

Legal-audit email drafted 2026-06-11 covers the above in plain English plus
a 34-SSP partner list and the policy framing for legal counsel. Preview at
`/tmp/legal_audit_reply_preview.html`; Outlook draft is in Roger's mailbox
addressed to himself as a placeholder until the legal contact is filled in.

## When Google changes the Protections UI

The script aborts with a `RuntimeError` and a hint about which selector
failed. To fix:

```bash
python confiant_blocklist.py --inspect
# Open dev tools, find the new selector for the failing element
# Edit gam_blocklist_ui.py:_SELECTORS, then re-run
```

The `--debug` flag saves `debug-pre-save.png` and `debug-final.png` in the
profile dir, which is the fastest way to see what state the page was in when
something broke.

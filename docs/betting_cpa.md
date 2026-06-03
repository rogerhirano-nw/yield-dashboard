# Spinfinite betting CPA optimization — runbook

**Owner:** Roger Hirano. **Order:** `4068491190` (GAM). **IO:** `IO1109`.
**Status:** Live · flight 2026-05-12 → 2026-06-13 · $15K · 1.875M imp goal.

Optimization loop for a Direct-sold betting/gambling campaign where the
advertiser (Spinfinite, tracker `trk.spnfnt.com`) pays Newsweek on a CPA
basis (per first-time purchase / FTP). Target: **$150 / FTP** blended.

The advertiser emails a daily Improvado report listing clicks, registrations,
FTPs, and Net Cash by date and sub-id. We ingest the report, join to GAM
delivery, surface CPA per LI, and add/retire audience segments to drive
toward the target.

---

## Architecture

```
GAM AudienceSegmentService  ─►  segment_engine (planned)   ─►  proposal email
GAM delivery (gam_campaigns) ─►        │                          │ approve
betting_conversions          ─►        │                          ▼
                                       │                   gam_apply (planned)
                                       ▼                          │
                                proposal diff ───────────────────►LineItemService
                                                                  CreativeService

Spinfinite tracker  ─► daily Improvado email
                   ─► newsweek@agentmail.to (forwarded by AE)
                   ─► refresh_improvado()  ─►  betting_conversions
                                                       │
                                                       ▼
                                          betting_daily_update.py  ─► email digest
```

Shipped today (PRs #52, #53): intake + digest. Still to come: segment engine
+ approval app, gated on real conversion data.

---

## Source LI (the control)

| | |
|---|---|
| LI id | `7306352098` |
| Order | `4068491190` |
| Name | `Newsweek_Direct_Gambling_NA_NA_NA_NA_Spinfinite_Spinfinite-Digital-Campaign_US_Display_IO1109_1_Team-USA_RShore` |
| Type / priority | STANDARD · 8 |
| Cost | CPM **$8.00** |
| Budget / goal | $15,000 · 1,875,000 impressions LIFETIME |
| Flight | 2026-05-12 → 2026-06-13 |
| Sizes | 728×90, 970×250, 320×50, 300×250 |
| Geo | US, **excluding** 13 restricted-betting states (Alabama, California, ...) |
| Inventory | ad unit `23207092721` + descendants |
| Audience targeting | **none** (wide open by audience — the test LIs will narrow) |
| Advertiser id | `6069130066` (Spinfinite) |

The 14-field naming convention is followed (see "GAM line-item 14-field
naming convention" in user memory). Test LIs swap the `Team-USA` creative
slot for `Aud-<handle>` (e.g. `Aud-Basketball`).

### Source creatives (4 ImageCreatives)

| id | size | name | asset id |
|---|---|---|---|
| 138556802703 | 320×50  | `Newsweek - 320x50`  | 6218609596 |
| 138556997792 | 300×250 | `Newsweek - 300x250` | 6218611513 (⚠ destinationUrl modified — see "In-flight experiments") |
| 138556998398 | 970×250 | `Newsweek - 970x250` | 6218857974 |
| 138557772955 | 728×90  | `Newsweek - 728x90`  | 6218857752 |

All four use the same impression-tracker URLs (ML314 + ActiveMetering).
Each tracker URL uses `%ecid!%` / `%eaid!%` style macros — these **do** expand
in impression trackers but **not** in click destinationUrls (see "Macro test
result" below).

---

## The sub_id contract (with Spinfinite / Improvado)

The Spinfinite click URL accepts arbitrary `sub_id1..N` query params and
echoes them back in the daily Improvado report.

| param | meaning | example |
|---|---|---|
| `sub_id1` | **creative size**, hardcoded per creative | `320x50` |
| `sub_id2` | **line item id**, hardcoded as `li<id>` per creative | `li7306352098` |

Sub_id_2 was negotiated with the AE after we discovered GAM's macro
limitations. The advertiser confirmed they can capture and report it.

Once test LIs are live, every test creative's destinationUrl has the form:

```
https://trk.spnfnt.com/click?o=1&a=236&c=1&link_id=5&sub_id1=<size>&sub_id2=li<new_LI_id>
```

The parser in `improvado_client.py` auto-detects whether `Sub ID 2` is
present in the report header row — same code path handles both shapes.
Joining `sub_id2` to `gam_campaigns.line_item_id` gives per-LI CPA;
parsing the LI's 14-field name yields the audience segment handle.

---

## Macro test result (hard-earned learning)

**`%eaid!%` (and any other `%`-prefixed GAM macro) does NOT expand in
creative `destinationUrl`.** Verified empirically:

- Attempt 1 (`...&sub_id1=320x50_li%eaid!%`): GAM rejected the create with
  `InvalidUrlError.ILLEGAL_CHARACTERS @ [0].destinationUrl; trigger:'...&sub_id1=320x50_li%'`.
- Attempt 2 (`...&sub_id1=320x50_li%25eaid!%25`, URL-encoded): GAM accepted,
  but 3 days of Spinfinite reporting confirmed the literal string came back
  in `sub_id1` — macro never expanded server-side.

These macros work in **impression-tracker URLs** (proved by the existing
ML314 / ActiveMetering trackers on the source creatives) but not in
**destinationUrl**. Consequence: each test LI must have its own dedicated
creatives with the LI id hardcoded into the URL at creation time.

Failed test creative `138559273952` was LICA-deactivated on 2026-05-25.
Tip: `DeactivateCreatives` itself is gated behind the GAM premium feature
`ACTIVATE_AND_DEACTIVATE_CREATIVES` which this network doesn't have;
deactivating the LICA achieves the same effect.

---

## Audience segment picks (current testing slate)

3 high-intent betting segments selected from a catalog of 1,945 keyword
matches, filtered for in-market / behavioral / brand-affinity signals and
sized for ~$75/day delivery per test LI:

| segment id | provider | name | size |
|---|---|---|---|
| `9385007833` | LiveRamp DDP | AudienceMix > Behavioral Intent > Sports Betting > **Basketball** | 770K |
| `9168610732` | LiveRamp DDP | Audience Mix > Enthusiast > Entertainment > **Sports Betting Enthusiast** | 3.0M |
| `9333427967` | LiveRamp DDP | Delivr.AI > Intent > Gambling-Casino > **Online Casinos > Regulated Consumer** | 2.7M |

Picked for vertical diversification (basketball-specific, all-sports umbrella,
casino) on a single provider for clean data-fee comparability. Alternatives
considered: Facteus FANDUEL Frequent (3.1M, transactional), Eyeota DraftKings
Big Spender (48M, broader provider), Sports Betting > American Football
(off-season). Reranked the raw catalog by anti-noise + size sweet-spot via
`/tmp/list_betting_segments.py` (regex `bet|gambl|casino|sportsbook|poker|wager|fantasy|igaming|lottery|horse racing|...`); the saved JSON is at
`/tmp/betting_segments_ranked.json` (~760 betting-only rows after filtering).

---

## Test-LI plan (gated on sub_id_2 validation)

Half of the remaining control-LI budget goes to a 3-LI test group, with the
control's goal trimmed accordingly:

| LI | name suffix | goal (imps) | media $ |
|---|---|---|---|
| `7306352098` (control) | `Team-USA` | 1,875,000 → **1,230,000** | $15,000 → $9,840 |
| new | `Aud-Basketball` | 215,000 | $1,720 |
| new | `Aud-SBEnthusiast` | 215,000 | $1,720 |
| new | `Aud-OnlineCasino` | 215,000 | $1,720 |

All four sum to the unchanged contractual 1.875M / $15K. Test LIs are
cloned from the control's targeting + add `audienceSegmentIds = [<segment>]`
positively. Each test LI gets its own 4 creatives (one per size) with the
URL hardcoded as `...&sub_id1=<size>&sub_id2=li<new_LI_id>`. Total writes:
3 createLineItems · 12 createCreatives · 12 createLineItemCreativeAssociations
· 1 updateLineItems (control goal reduction).

New LIs land in DRAFT status — manual approval in GAM UI is required
before they deliver. The service-account auto-approve flow is left
unwired because granting trafficker permission to a programmatic key is
a bigger blast radius than the win justifies.

**Hostile-math note.** At current observed FTP rates (1 in 14 days
across the whole campaign at full budget), splitting volume across 3 test
LIs over the remaining ~23 days will not produce statistically-significant
per-segment CPA winners. This flight is **directional**, not conclusive —
the engine collects signal, the next IO acts on it.

### Decision rules (for the future engine)

Calibrated for this campaign's volume:

- **Add** per 2-day cycle: top N new segments (default 3) not previously
  tested, ranked by size × category-fit × anti-noise.
- **Hold** (the default): any segment without enough data — eval window
  minimum 7 days regardless of the 2-day cadence.
- **Kill**: segment-CPA > $200 with ≥ $500 spent, OR zero FTPs after $500
  spent. (Originally drafted at $300 spent / $200 CPA but recalibrated
  upward given the low FTP rate.)
- **Promote / keep**: CPA < $150 with ≥ 2 FTPs.

All decisions go through the approval app (planned PR #55) — no autonomous
GAM writes until calibrated against real signal.

---

## Scripts (under `scripts/`)

| script | what it does | when to run |
|---|---|---|
| `betting_test_lis_batch.py` | The whole test-LI batch: control-goal reduction, 3 new LIs (DRAFT), 12 new creatives, 12 LICAs, macro-test cleanup (defensive). Dry-run default; `--apply` to execute. Logs all created IDs to `/tmp/test_lis_log.json` for rollback. | Once sub_id_2 column surfaces in the Improvado report (tomorrow's expected). |
| `betting_snapshot_source.py` | One-shot diagnostic: dumps LI `7306352098` + its 4 source creatives to JSON. The batch script depends on the output. | Run before the batch if the snapshot is stale. |

Both are read-and-stage scripts — they don't modify yield-dashboard tables.
GAM writes happen only with explicit `--apply` flags.

---

## In-flight experiments (state to remember)

| experiment | object | state | revert |
|---|---|---|---|
| Macro test (failed) | creative `138559273952` LICA on LI `7306352098` | INACTIVE since 2026-05-25 | re-activate via `LineItemCreativeAssociationService.ActivateLineItemCreativeAssociations` |
| sub_id_2 mini-test | creative `138556997792` (control 300×250) | destinationUrl was modified 2026-05-23 from `…&sub_id1=300x250` to `…&sub_id1=300x250&sub_id2=li7306352098` | restore original URL via `CreativeService.updateCreatives` |

The sub_id_2 mini-test stays live until 24h of Improvado reporting confirms
the Sub ID 2 column surfaces. After that it can either be reverted (if we
choose to keep the control free of sub_id_2 for cleaner "control vs test"
attribution) or left as-is (the control's clicks then attribute to itself
via `sub_id_2=li7306352098`).

---

## Operational hazards

1. **`/tmp` is volatile.** The first version of the batch lived only in
   `/tmp/test_lis_batch.py`. Moved into `scripts/` in this PR. If you
   re-encounter scripts only in `/tmp`, they're transient — promote them.

2. **Auto-sync across laptops can clobber uncommitted work.** This repo
   has aggressive cross-machine syncing (see user memory: "yield-dashboard
   — commit edits immediately"). For durable edits, batch
   edit+commit+push in one Bash call. Avoid leaving working-tree changes
   uncommitted for any length of time.

3. **GAM premium features.** Some operations (`DeactivateCreatives`,
   possibly others) are gated behind license tiers this network doesn't
   have — symptom: `FeatureError.MISSING_FEATURE`. Use the LICA layer for
   "stop this creative on this LI" operations; it's always available.

4. **Forwarded report subjects.** Reports arrive as `Fwd: Newsweek -
   Daily report` because the AE forwards rather than CC's. The parser
   substring-matches and verifies provenance via the `Generated by
   Improvado AI Agent` body footer instead of trusting the From header.

5. **Data fees stack on $8 CPM.** LiveRamp DDP segments carry per-impression
   data fees (~$0.50–$2.00 CPM). Check each segment's data CPM in GAM UI
   before approving its test LI. Effective CPM with data > $10 may require
   trimming the test LI's impression goal.

6. **STANDARD LIs need approval to deliver.** New LIs are created in
   DRAFT status. GAM UI: Delivery → Orders → 4068491190 → select new LIs
   → Approve. ~30 seconds of manual work; intentional gate.

---

## Recipients and variables

| name | scope | value |
|---|---|---|
| `BETTING_DIGEST_TO` | repo variable | `roger.hirano@newsweek.com` (default) |
| `BETTING_DIGEST_CC` | repo variable | empty by default |
| `BETTING_CPA_TARGET` | repo variable | `150` (USD per FTP) |
| `AGENTMAIL_API_KEY` | repo secret | shared with DV intake + apple-news |
| `AGENTMAIL_INBOX_ID` | repo secret | `newsweek@agentmail.to` |

The digest workflow (`.github/workflows/betting_daily_digest.yml`) is
`workflow_dispatch`-only. cron-job.org triggers it daily at 09:30 ET —
30 min after the 09:00 refresh sweep, so the freshest Improvado report
is already in `betting_conversions`.

---

## Live LI roles (current state — keep this current!)

`sub_id2 = li<id>` in the Improvado report maps to these LIs. **The LI id is
stable but its ROLE changes** as we rotate audiences through the challenger
slot — always cross-check this table before reading a report.

| LI id | role (as of 2026-06-02) | audience | formats | status |
|---|---|---|---|---|
| `7306352098` | Control / volume arm | none (broad) | all 4 sizes | delivering, goal 1.66M |
| `7319884497` | Conversion arm | OnlineCasino `9333427967` | **large only** (728×90/300×250/970×250) | delivering |
| `7319885244` | **Challenger slot** | **FANDUEL Frequent `9363363991`** (was Basketball `9385007833`) | all 4 sizes | delivering |
| `7322268934` | Halted (was SBEnthusiast) | — | — | LICAs deactivated |

## Sequential audience exploration (the screen)

Conversions are too sparse to measure per-segment this flight (~0.08% click→FTP
⇒ ~1,250 clicks per expected FTP; we get ~10–40/day per LI). So we **screen
audiences on CTR + DV Attention**, not conversions — the same leading
indicators that earned OnlineCasino its budget off 17.8K imps.

Method (`scripts/betting_challenger.py`):
- **One** challenger audience at a time through the repurposed Basketball LI
  (`7319885244`), running **all 4 sizes** for max volume + a format-matched CTR
  comparison against the all-sizes no-audience control.
- Repurpose = update audience + rename (`Aud-<handle>` slot) + reactivate the
  existing LICAs. No new creatives/LIs. `sub_id2=li7319885244` is the stable
  join key; the role table above records which audience it currently carries.
- **Promote** an audience to a dedicated large-format conversion line if it
  beats control's ~0.094% CTR at >5K imps. **Demote** fast otherwise (~3–4 day
  windows). Mostly this builds the ranked audience playbook for IO1110.

Challenger queue (vetted, ACTIVE, sized for delivery): FANDUEL Frequent
`9363363991` (3.4M, **running now**) → Online Sportsbooks Regulated `9333586319`
(2.0M) → DraftKings Big Spenders `9262197606` (69M, Eyeota) → Card Games Poker
`9385104862` (415K).

## When this is done

The optimization loop is "complete" (for this IO) when:

- Daily digest emails are landing at 09:30 ET every weekday.
- Each digest shows per-LI CPA with `sub_id_2 = li<id>` attribution.
- Segment-level CPA is being read by a human and either confirmed (no
  changes) or fed into a proposal email for approval.
- We've collected enough conversions across the test slate to inform
  segment selection for IO1110.

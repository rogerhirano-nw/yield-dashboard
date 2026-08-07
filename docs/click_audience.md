# Click-audience: "users who clicked the Apple at Work interstitial"

**Segment 9443004817** — `[nw] Apple at Work Q426 - ad clickers (LI 7384069597)`
(first-party, Signals → Audience). Created 2026-08-04 via API (PR #351).
Membership: pageViews=1, recency=1d, expiration **90d**. Rule (cloned from the
Subscription pixel segments): inventory = `DFPAudiencePixel` unit 23277289521
(+descendants) AND `dc_seg = 9443004817` (key 14515842, value 453691490776).

There is **no retroactive path** — GAM reporting carries no user ids, so the
segment fills only from click-time pixel fires after the capture went live.

## How it populates (publisher-side, no agency dependency)

A **GAM Creative Wrapper** carries the capture code since 2026-08-05: label
391280066 (`[nw] click-audience wrapper - Apple at Work clickers`, type
CREATIVE_WRAPPER) is applied to the **`interstitial` ad unit** (23295929518 —
wrapper labels are unit-scoped per the API), and wrapper 391280066 (ACTIVE)
injects a marker-wrapped `<script>` footer (`nw-click-audience … via=wrapper`)
into every creative served there. Because the whole unit gets wrapped, the
block is **LI-gated**: it resolves the serving line item via GPT response info
(the slot whose container holds `window.frameElement`) and returns inert
unless the LI is allow-listed (7384069597; preview URLs naming
`lineItemId=7384069597` also pass, for testing). Innovid snippets are
byte-identical originals — creative swaps/additions are wrapped automatically.

(History: 2026-08-04→05 the same block, ungated, was appended directly to the
three Innovid `ThirdPartyCreative` snippets — `add_click_capture_to_creatives.py`
stripped them back once the wrapper was verified serving.)

The block fires the segment's activity tag

    https://pubads.g.doubleclick.net/activity;dc_iu=/22541732127/DFPAudiencePixel;ord=<ts>;dc_seg=9443004817

only on click-through-shaped signals:

- **A — anchor click**: a real click on an `http(s)` anchor inside the ad
  overlay (exact, when Innovid renders reachable DOM).
- **B — same-tab navigation**: `pagehide` while the interstitial overlay is
  up. The overlay covers the page, so navigation-while-open ≈ click-through.
- **C — new-tab navigation**: focus drops into the ad iframe (top-window
  `blur` with `activeElement` = ad iframe, or pointer recently over it) AND
  the tab goes hidden within 1.6s.

An interstitial **close** produces none of these (no navigation, tab stays
visible) and never fires. Overlay detection requires a ≥50%-viewport
fixed/absolute element that IS or CONTAINS an ad iframe
(innovid/doubleclick/googlesyndication src), so consent/paywall overlays
can't arm it. All errors are swallowed; the Innovid tag itself is untouched;
consent: a CCPA string is attached via `__uspapi` when present.

Known edge: close + immediate tab-switch (<1.6s) over-counts; new-tab
click-throughs where the browser doesn't hide the opener are caught by C only
if the tab fronts. Population also requires the Google 3P-cookie context, so
Safari/Firefox clickers don't join (~half of clicks convert to members).

## Operate

**Kill/revert (instant):** Delivery → Creative wrappers → deactivate wrapper
391280066, or remove the label from the interstitial unit's Settings → Labels.
(The SA can create labels/wrappers but is PERMISSION_DENIED on AdUnit
updates — label add/remove on the unit is a UI step.)

**Setup / re-check:** `scripts/setup_click_capture_wrapper.py` via
`setup_click_capture_wrapper.yml` (`target=dryrun|apply`, idempotent at every
step). To reuse for a future campaign: new segment (create_click_audience.yml),
clone the wrapper script with the new seg id + LI allow-list, new label, apply
to the relevant unit.

**Legacy per-creative path** (`add_click_capture_to_creatives.py` +
`add_click_capture.yml`, stages dryrun|canary|all|rollback): retired 2026-08-05
after the wrapper went live — kept as the fallback if the wrapper ever has to
come off. Creative swaps need no action while the wrapper carries the code.

**Monitoring:** segment size in Signals → Audience (30min–48h membership lag;
line items can target it once ≥~100 members) or the recon workflow
(`inspect_click_audience.yml`), which lists segment sizes and checks the
creatives + pixel plumbing. GAM report ops from this SA can outlive 10-minute
jobs — the recon workflow runs with a 30-minute cap; keep pixel-unit request
reports to single-dimension/single-day shapes.

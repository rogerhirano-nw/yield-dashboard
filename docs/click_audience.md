# Click-audience: "users who clicked the Apple at Work interstitial"

**Segment 9443004817** — `[nw] Apple at Work Q426 - ad clickers (LI 7384069597)`
(first-party, Signals → Audience). Created 2026-08-04 via API (PR #351).
Membership: pageViews=1, recency=1d, expiration **90d**. Rule (cloned from the
Subscription pixel segments): inventory = `DFPAudiencePixel` unit 23277289521
(+descendants) AND `dc_seg = 9443004817` (key 14515842, value 453691490776).

There is **no retroactive path** — GAM reporting carries no user ids, so the
segment fills only from click-time pixel fires after the capture went live.

## How it populates (publisher-side, no agency dependency)

The three Innovid `ThirdPartyCreative` snippets on LI 7384069597
(138568851141 / 138568952930 / 138569841328) carry an appended, marker-wrapped
`<script>` block (`nw-click-audience`) that fires the segment's activity tag

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

`scripts/add_click_capture_to_creatives.py` (dry-run default, marker-
idempotent, updates creatives one at a time) driven by
`.github/workflows/add_click_capture.yml`:

- post-merge: `gh workflow run add_click_capture.yml -f target=<stage>` —
  `dryrun` | `canary` (138568851141 only) | `all` | **`rollback`** (strips the
  block from every creative; the instant-revert lever).
- pre-merge: the workflow's `TARGET` env on the branch drives push-triggered
  runs (rolled dryrun → canary → all on 2026-08-04).

If OMD/Innovid ever **swap or add creatives** on the LI, the block is gone
from the new ones — re-run `target=all` (the script picks up all LICAs).
Monitoring: segment size in Signals → Audience (30min–48h lag; line items can
target it once ≥~100 members) or re-run the recon workflow
(`inspect_click_audience.yml`), which lists first-party segments with sizes.

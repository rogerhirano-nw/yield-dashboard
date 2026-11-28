# Video clicks: why Innovid-tagged video reads 0 in GAM

**Short version:** every Innovid VAST redirect in network 22541732127
records **zero** GAM clicks — across four advertisers and 184,686
impressions in a 30-day window — while every other video redirect vendor
on the *same* ad unit records 0.18–1.26%. The click never reaches GAM's
click server. GAM's own instrumentation is present and working, so the
break is downstream of GAM, in the player↔Innovid click chain.

Do not grade an Innovid-tagged video flight on GAM CTR. It is
structurally 0, not a performance read.

First raised 2026-09-22 for LI 7415150292 (order 4181118888,
`Newsweek_PG_Tech_ADX_DV360_Omnicom_OMD_Apple-Trophy-Q426_Q127_US_FITO-Video-Flight1_$35_Team-USA_ILee`).

## Reproduce

`.github/workflows/diagnose_video_clicks.yml` → `scripts/diagnose_video_clicks.py`
(read-only; dispatch with line item ids + a window). It dumps the line
item, every creative's click-relevant fields, a live fetch of **both**
the vendor tag and GAM's own served wrapper, and the report cuts below.

## What the evidence rules in and out

| hop | finding | verdict |
|---|---|---|
| creative carries a click-through | Innovid InLine has `<VideoClicks>` → `<ClickThrough>` to `dts.innovid.com/clktru/…&click=https://www.apple.com/…` | present |
| GAM is in the click chain | GAM serves a **Wrapper** whose `<ClickTracking>` points at `pubads.g.doubleclick.net` | **present — GAM is trying to count** |
| the player talks to GAM's wrapper | mute / pause / resume / start / complete events on the line are all counted by GAM | wrapper tracking reaches GAM |
| VPAID/SIMID swallowing the click | `apiFramework` is **omid** only, no `InteractiveCreativeFile`, no `AdParameters`, 13 progressive `video/mp4` MediaFiles | **ruled out** — plain linear |
| "GAM can't count third-party VAST" | DCM 1.26%, Rubicon 0.61%, amazon-adsystem 0.63%, adsrvr.org 0.18% — all redirects, same unit | **ruled out** |
| "it's this flight / this placement" | the same 0 on a Jeep **Direct** pre-roll (different advertiser, agency, buy type) at 180,771 impressions | **ruled out** |

`AD_SERVER_CLICKS`, `CLICKS`, `AD_EXCHANGE_CLICKS` and
`AD_SERVER_UNFILTERED_CLICKS` were each checked separately (they are
mutually incompatible in one report) — the line returns no click rows in
any metric family, so this is not the dashboard reading the wrong metric.

Delivery itself is healthy: 3,723 impressions, video starts ~1:1 with
impressions, ~75% completion. Only the click is missing.

### Every Innovid line in the window

| line item | impressions | clicks |
|---|---|---|
| Jeep-Unconventional-Pre-roll-August (Direct, Stellantis/Publicis) | 180,771 | 0 |
| Apple-Trophy FITO Video Flight1 (PG, OMD) | 3,723 | 0 |
| Apple-Trophy FITO Flight1 AddedValue | 146 | 0 |
| Citi Q1 2025 FITO Video Test | 24 | 0 |
| Apple preroll health sponsorship test pages (×3) | 22 | 0 |

## What is NOT yet established

Which side drops the click. Two candidates survive:

1. **The ads aren't clickable as rendered.** Every Innovid buy here is a
   FITO / "unconventional pre-roll" injected player; if a tap toggles
   sound rather than navigating, there is no click to count anywhere.
2. **The player fires only the InLine's click trackers**, not the
   serving wrapper's — Innovid records the click, GAM never sees it.

The interaction counts do **not** discriminate between these, despite
looking like they should: 7,529 mutes against **5** unmutes on 3,723
impressions is ~2 mute events per impression, which reads as automatic
muted-autoplay firing, not user taps.

Two cheap ways to settle it:

- **On-page forensics** — render the unit, tap the ad, watch whether a
  `pubads.g.doubleclick.net/pcs/click` request fires and whether
  navigation happens. Same pattern as `scripts/prebid_render_forensics.py`
  / `preview_mobkoi_dom.yml`.
- **Ask the buyer** — OMD/Innovid for their click count on creatives
  07391863/64/65. Innovid reporting clicks while GAM reports zero proves
  case 2; Innovid also near-zero proves case 1.

## Related

This is a *click* analogue of the Active View artifact in
`docs/mobkoi_viewability.md` and the bidder gap in
`docs/prebid_viewability.md`: the metric is 0 because of where the
measurement sits in the chain, not because the ad performed that way.

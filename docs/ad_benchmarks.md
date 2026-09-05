# Ad performance benchmarks

`docs/Newsweek_Digital_Ads_Benchmarks_Master.xlsx` is the performance-benchmark
companion to the sales team's `Newsweek_Digital_Ads_Specs_Master.xlsx`. Built by
`scripts/build_ad_benchmarks.py`; the workbook is a build artifact, the script is
the source of truth.

## Why it exists

The specs master carries the benchmark per placement as **free text in one
column** — `"0.15% CTR, 40% VCR, 65% Engagement, 70% Viewability"`. That can't be
sorted, averaged, filtered or graded against, so nobody can answer "what should I
promise for a Video line?" or "did this line hit its target?" without reading 56
strings by eye. This workbook splits those strings into numeric per-metric
targets and adds the grading machinery.

It is **not** a second copy of the specs. Sizes, file weights, lead times and tag
rules stay in the specs master; this file only covers what a placement is
expected to *deliver*.

## The four sheets

| Sheet | What it is |
|---|---|
| **Benchmarks** | One row per sellable placement (56), in the specs master's section order. Numeric CTR / viewability / VCR / engagement targets, the canonical dashboard format, a unique **Benchmark Key**, a measurement column, and a review flag. |
| **Performance Tracker** | Paste GAM delivery into the amber cells, pick a placement from the dropdown, and formulas return actual rates, the target, the gap in pp, and a PASS / WATCH / MISS / THIN verdict per metric plus an overall. |
| **Thresholds** | The banding levers (below), target rollups by canonical format, and the dropdown's validation list. |
| **Definitions** | Metric definitions, the measurement caveats, and every open question in one place. |

## Grading rules

Two levers on the **Thresholds** sheet, and nothing else:

- **WATCH band = 0.90.** At or above target is `PASS`; at or above `target × 0.90`
  is `WATCH`; below that is `MISS`. So 63% viewability against a 70% target is
  WATCH, 62% is MISS.
- **Minimum impressions for a valid read = 10,000.** Below that the verdict is
  `THIN`, so a line in its first hours isn't escalated on noise.

A metric with **no target** in the specs master is not graded at all — the
verdict cell stays blank. This is deliberate and was a real bug during the build:
`INDEX` on an empty cell returns `0`, and a 0% target silently grades every line
`PASS`. The lookup wraps the blank check for that reason; don't simplify it back.

## Conventions

- Percentages are **stored as fractions** (`0.001` = 0.10%), like everywhere else
  in this repo.
- **Benchmark Key** is `Section · Ad Product · Ad Size`, built by formula. It's
  the join key the tracker looks up on, and it's unique across all 56 rows
  (product names alone are not — "Medium Rectangle" appears in four sections).
- The **Source** column preserves the verbatim string from the specs master, so a
  normalised target can always be checked against what was actually written.

## Regenerating

```bash
pip install openpyxl
python3 scripts/build_ad_benchmarks.py            # writes docs/…Benchmarks_Master.xlsx
python3 scripts/build_ad_benchmarks.py --out /tmp/preview.xlsx
```

Edit the `ROWS` table at the top of the script when a target changes, then re-run.
openpyxl writes formulas without cached values, so the file reads back as `None`
to pandas until something recalculates it — Excel does that on open; headless,
run it through LibreOffice (`soffice --headless --convert-to xlsx`, or the xlsx
skill's `recalc.py`). The committed copy is recalculated: 425 formulas, 0 errors.

**If you add a placement row, also add its Benchmark Key to the list on the
Thresholds sheet** (the script does this automatically; it only bites if someone
edits the workbook by hand). The dropdown reads a static range because Excel's
list validation can't skip the blank category-band rows in the Benchmarks column.

## Open questions carried from the specs master

30 rows carry a review flag. These are the ones that change a number rather than
just noting a gap:

- **Video Interscroller viewability** reads `0.70% viewability (30 seconds) /
  0.40% (60 seconds)`. Entered as **70% / 40%** — a 0.70% viewability target
  isn't plausible. Flagged `INTERPRETED`; confirm at source.
- **Newsletter Medium Rectangle CTR** reads `0.0006%`, ~370× below the Long
  Banner target in the same newsletter. Almost certainly a decimal slip.
- **Apple News and Interstitial rows** read `0.20 CTR` / `0.30 CTR` / `0.80 CTR`
  with no `%` sign. Entered as percentages.
- **Six video units carry no VCR target**: Centerstage Video Takeover (desktop +
  mobile), FITO Video Desktop Top Banner, LinkedIn Video, Apple News PreRoll.
- **Four placements have no benchmark at all**: IG/FB Video, TikTok Display,
  TikTok Video, Podcast Audio.
- **Two internal inconsistencies**: Centerstage Ribbon and Mobile Sticky target
  1% CTR while Large Banner units in the same family target 0.10%; Facebook
  targets 0.90% against Instagram's 0.10%.

## Measurement caveats baked into the sheet

The "Viewability measurable?" column exists because a viewability *target* is
meaningless on a placement whose viewability can't be measured. Carried from
`CLAUDE.md` and `docs/mobkoi_viewability.md`:

- **Breakout / parent-DOM renders read ~0% viewable** in Active View, and DV
  agrees, because both instrument the hidden iframe. Never sell a vCPM goal on
  one. The tell is healthy CTR with more clicks than "viewable" impressions.
- **Interscrollers are marked "iframe mirror required"** — the mirror creative
  took the same LI from 0.51% to 56.81% viewable. Grade viewability on these only
  once the mirror is confirmed live.
- **Apple News 100% viewability is Apple's guaranteed-view model**, reported by
  Apple. It is not an Active View measurement and shouldn't be averaged with
  on-site viewability.
- **Social and newsletter** are platform-/ESP-reported. No Active View, no DV.
- Rows served through the **placement-injection carrier slot** (FITO top banners)
  are marked *verify* rather than yes — confirm Active View reads real geometry
  on the live creative before grading them.

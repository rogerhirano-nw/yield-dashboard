# deal_health

Weekly Newsweek "deal health" email — surfaces PMP/PG deals that received
bid requests but no bids over the last 7 days, grouped by seller and broken
out **SSP → DSP → Agency → Advertiser → deal** for fast triage.

## What it does

- Pulls unhealthy deals from the existing Postgres cache (`magnite_deal_daily`,
  `pubmatic_deals`, `gam_deal_bid_daily`).
- Joins each deal to its per-SSP metadata table (`gam_pa_metadata`,
  `gam_pd_metadata`, `magnite_deal_metadata`, `pubmatic_deal_metadata`) for a
  reliable **deal age** anchor; drops anything younger than 90 days so the
  email isn't flooded with deals buyers haven't had time to wire up.
- Parses each deal name into structured fields (`deal_type`, `vertical`, `ssp`,
  `dsp`, `agency_holdco`, `agency`, `advertiser`, `geo`, `format`, `floor`,
  `seller`). The parser tolerates 13/14/15-field length variations and
  N/A/NA/blank in any slot.
- Renders one HTML email — table-based, Outlook-safe — listing every seller
  with non-empty results, sorted by deal count descending. House- and
  Unknown-attributed deals are excluded from per-seller breakouts but counted
  in the top-line KPIs.
- Writes a CSV alongside (full data internally, redacted version for the
  public repo), commits + pushes it, then HEADs the
  `raw.githubusercontent.com` URL up to 3× to confirm it actually went live.

## Recipients

One email goes to the full sales team (sellers + ad ops + revops) — all
sellers visible to each other. To change the list, edit the `MAIL_TO`
repo secret in GitHub. To switch to **per-seller emails later**, see the
note at the bottom of this README — the renderer already accepts a payload
subset, so the rewrite is one extra loop in `__main__.py`.

## Running it locally

```sh
# Generates HTML + CSV under reports/, does NOT commit.
python -m deal_health --output-dir reports --dry-run

# Generates and skips the public-safe redaction (internal-only mode).
python -m deal_health --output-dir reports --no-redact --dry-run

# Full path the GitHub Action takes:
python -m deal_health --output-dir reports
```

`DATABASE_URL` must be set (in `.env` or your shell). For the
publish step (commit + push) you need a writable git remote and
`GH_ORG_REPO` / `GH_BRANCH` / `REPORTS_PATH` env vars (defaults:
`rogerhirano-nw/yield-dashboard`, `main`, `reports`).

## Environment variables

| Variable          | Default                       | Purpose                              |
|-------------------|-------------------------------|--------------------------------------|
| `DATABASE_URL`    | —                             | Postgres connection (required)       |
| `PUBLIC_SAFE`     | `true`                        | Redact CSV before publish?           |
| `GH_ORG_REPO`     | `rogerhirano-nw/yield-dashboard` | Used to build the raw URL          |
| `GH_BRANCH`       | `main`                        | Same                                 |
| `REPORTS_PATH`    | `reports`                     | Path inside repo where CSVs live     |

## Known data-quality issues to chase with AdOps

These surface in the email's "Unknown SSP" bucket and are real defects we
shouldn't paper over in code:

1. **DSP in the SSP slot** — e.g. `Newsweek_PA_Multi_DV360_TTD_...` (position 3
   should be the SSP). Currently surfaces as "Unknown SSP" in the report.
2. **`JP Morgan Chase` vs `JPMorganChase`** — two spellings of the same
   advertiser ranked separately in "Top dark advertisers".
3. **`Pol` as the advertiser** — `Pol` is the *vertical* (political); the
   advertiser slot for political deals should be the agency or candidate name.

The methodology callout in the email links AdOps directly to these issues.

## Switching to private hosting

The CSV currently lives in this public repo under `reports/`. Redaction
(strips `Floor`, `Bid Requests`, `Raw Deal Name`) keeps it boring. When that
trade-off stops working — e.g. you want the full data downloadable and the
repo can't stay public — flip to S3 / R2:

1. Add an S3 step in `weekly_report.yml` after the CSV is generated, uploading
   to a private bucket with a presigned URL.
2. Replace `build_csv_url(filename)` in `deal_health/publish.py` to emit the
   presigned URL instead of `raw.githubusercontent.com/...`.
3. Set `PUBLIC_SAFE=false` (no redaction needed since the bucket is private).

Email rendering doesn't change — the CTA card's "Download CSV" anchor just
points elsewhere.

## Switching to per-seller emails

The renderer takes a `Payload`; `Payload.deals` is a tuple. To send one
email per seller, in `__main__.py` after `build_payload(...)` is called:

```python
for seller, seller_deals in groupby_seller(payload.deals):
    per_seller_payload = dataclasses.replace(payload, deals=tuple(seller_deals))
    html = render_email(per_seller_payload)
    send_to(seller_email_for(seller), html)
```

The hierarchy and styling are identical; only the deal set differs. The
top-line KPIs (`total_deals`, `total_bid_requests`, `seller_count`) will
recompute correctly because they're plain counts off the input tuple.

## Module map

```
deal_health/
  __main__.py    # CLI
  models.py      # frozen dataclasses (ParsedDeal, UnhealthyDeal, Payload, ...)
  colors.py      # all hex codes, thresholds, SSP/DSP/seller alias tables
  parser.py      # parse_deal(raw) -> ParsedDeal (13/14/15-field tolerant)
  data.py        # SQL → list[UnhealthyDeal] with age filter applied
  aggregate.py   # list[UnhealthyDeal] -> Payload (pre-computes KPIs/rollups)
  render.py      # Payload -> HTML string (PURE; no datetime.now())
  publish.py     # CSV write, git commit + push, raw URL verify
  redact.py      # strip Floor / Bid Requests / Raw Deal Name → public CSV
```

## Tests

```sh
pytest tests/
```

To regenerate the snapshot after intentional rendering changes:

```sh
REGENERATE_SNAPSHOT=1 pytest tests/test_render_snapshot.py
```

The Outlook-compat tests assert structural patterns (every colored `<td>`
has both `bgcolor=` AND inline `background-color`, etc.); **actual visual
checks in Outlook PWA on macOS remain manual** — send yourself a test from
the `weekly_report.yml` workflow_dispatch button before broadcasting.

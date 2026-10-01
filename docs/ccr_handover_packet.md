# Comscore CCR setup forms: handover packet

A self-contained brief on building Comscore **Campaign Ratings (CCR) setup
forms**, written so a new Claude account (or a new person) can do it cold.
Load it as Project knowledge, or give the new account access to this repo.

Compiled 2026-10-01 from `scripts/build_ccr_form.py`,
`.github/workflows/build_ccr_form.yml`, `tests/test_build_ccr_form.py`,
`CLAUDE.md` and `docs/changelog.md` (#390). If this packet and the script
disagree, the script wins.

---

## 1. Why this exists

Comscore (**Kristie Chesebro**, Technical Customer Success) needs a CCR setup
form for **every Direct campaign that carries Comscore tags**. When tag
activity shows up for a campaign ID before its form has arrived, she chases
it. That happened four times in Aug–Sep 2026 (orders 4057788230, 4159204943,
4171515326, 4183464375). Kael Rabelo used to fill the form by hand. It's now
generated from GAM.

**Rule of thumb:** send the form when the order is trafficked, not after the
tags start firing.

## 2. Doing it: order id in, `.xlsx` out

1. Dispatch **`build_ccr_form.yml`** (Actions → "Build Comscore CCR setup
   form") with the `orders` input:
   - `4183464375`: one form.
   - `4183464375,4187974224` (**commas**): **one combined form** covering
     both orders.
   - `4183464375 4187974224` (**spaces**): **one form per order**.
   - Optional overrides: `advertiser`, `brand`, `product`, `category`, `kpis`.
2. Read the run's **step summary**. It prints what was filled, which LIs were
   included or excluded and why, and any warnings.
3. Download the **`ccr-form`** artifact. The file is named
   `CCR_Setup_<product>_<orderids>.xlsx`.
4. **Open it and review** the advertiser, brand, product and category. These
   come from name tokens, which are shorthand (see §4). Re-run with overrides
   if something reads wrong.
5. Attach it to the email to Comscore.

From a cloud session without `gh`, dispatch through the GitHub MCP tools and
pull the artifact through the REST API (`GET …/actions/runs/<id>/artifacts`,
then `…/artifacts/<id>/zip`). GAM credentials are repo Actions secrets
(`GAM_SERVICE_ACCOUNT_JSON`, `GAM_NETWORK_ID`). The workflow is
**read-only against GAM**.

Running it locally (needs those two env vars in `.env`):
```bash
python scripts/build_ccr_form.py --order 4183464375 [--order …] \
  [--advertiser "Apple TV"] [--brand …] [--product …] [--category …] \
  [--kpis "CTR, Viewability"] [--campaign-name …] [--out ccr.xlsx]
```

## 3. What gets filled (template `templates/comscore_ccr_template.xlsx`)

**Study Details** sheet:

| Cell | Value |
|---|---|
| C3 | Campaign name = the GAM **order name**, capped at **150 chars** (Comscore's limit; a warning prints if truncated) |
| C4 | Flight, e.g. "September 23, 2026 to October 7, 2026" (earliest included LI start → latest included LI end) |
| C5 / C6 | "National Advertiser" / "National Study" |
| C10–C14 / E / G | Custom periods: **"End of campaign report"** = the flight, split into **≤92-day** chunks ("… (part N)") because 92 days is Comscore's max. The form holds 5 rows, so a flight over ~460 days errors out. |
| C17–C20 | Advertiser / Brand / Product / Category |
| C23 | KPIs: **"VCR, Viewability"** if any included LI is video (VIDEO_PLAYER, or "video"/"pre-roll" in the name), else **"CTR, Viewability"** |

**Media Details** sheet: B4/B5 flight start/end, B6 the order id(s), B7
`GAM`. **The Digital/CTV partner impression breakdown (rows 9–18) is left
blank on purpose** (Roger, 2026-09-23). Don't fill it.

## 4. How the names are parsed

From the Newsweek order-name convention (underscore tokens, 0-indexed):

- **Category** = token 2 (vertical). `Tech` becomes Technology, `Auto`
  becomes Automotive, `NA` becomes blank.
- **Advertiser = Brand** = token 7. **Product** = token 8. Dashes become spaces.
- **PG/PD and AppleTV-style names** pack advertiser and title into token 7,
  with token 8 holding a period code or geo (`…_AppleTv-Slow-Horses-S6_Q426_US_…`).
  In that case product = token 7 minus any trailing period code, and
  advertiser = its first dash-word (CamelCase split). `appletv` becomes
  "Apple TV".
- If the name doesn't follow the convention, the advertiser falls back to the
  GAM advertiser company with a leading `[nw] ` stripped. Use the overrides.

These tokens are shorthand, which is why the human review in step 4 matters.

## 5. What gets excluded

Line items Comscore doesn't measure are left off the form, and the step
summary lists each one with its reason:

- **Apple News** lines (name matches `apple[-_ ]?news`). Not Comscore-tagged
  (Kael, 2026-08-28).
- **Newsletter** lines. Not Comscore-tagged.
- **Canceled** and **archived** lines.
- **DRAFT** lines are *included* with a warning ("confirm it will run").
- An order with nothing measurable left is refused with "no
  Comscore-measured line items on the order(s)", and no form is written.

## 6. Hard rules (each one cost something)

- **The template must carry no hidden sheets except "Data Validation".** The
  original template had a hidden **"Q4 2024 - $128k"** sheet: a Verizon media
  plan with **another client's pricing**. It went to Comscore attached to
  every form until it was found. It's gone from the repo template. The script
  **refuses to write** if a hidden sheet reappears, and
  `test_template_has_no_leftover_sheets` pins it. If Comscore sends a new
  template, inspect every sheet (including hidden ones) before committing it.
- **Pillow must be installed** wherever the script runs (the workflow
  installs it). Without it, openpyxl **silently drops the Comscore logos**.
  The script now errors instead.
- Dates are read straight from SOAP y/m/d (already network-tz ET). Don't
  convert through UTC.

## 7. Verification and tests

- `pytest tests/test_build_ccr_form.py` covers name parsing (Direct + PG),
  exclusions, 92-day chunking, KPI choice, overrides, the name cap, the
  nothing-measurable refusal, the hidden-sheet guard, and an end-to-end
  template fill.
- Verified live on 4183464375, 4187974224 and 4199964192 (#390).

## 8. People

| Who | Role |
|---|---|
| Kristie Chesebro (Comscore) | Receives forms and chases missing ones |
| Kael Rabelo (Newsweek) | Previously sent the forms by hand. Source of the Apple News/newsletter exclusion rule |
| Roger Hirano | Owner. Decided to leave the partner breakdown blank |

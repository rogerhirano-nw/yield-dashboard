# Video ad request: forward selected page key-values into `cust_params`

**Owner:** Ad Ops (Roger Hirano) → Web Engineering
**Scope:** one function in the video ad module. No trafficking changes required.
**Priority:** blocks the FITO Fluid takeover product (Cognizant Q4/Q1 plan).

---

## 1. Problem

The video player's VAST ad request is built by the site's video ad module, which
assembles its own `cust_params` object. It currently sends a good set of page
signals (`cat`, `sitecat`, `group_cat`, `content`, `vidcontent`, `topics`,
`pageurl`, `title`, `adexclusion`, `trsource`, `brtype`, `video_type`, …) but it
**does not read live GPT page-level targeting**, and it drops two keys the
display side does send.

Two consequences:

**(a) Runtime page state never reaches video.**
Display line items can be gated on a key-value set at runtime — our takeover
anchor creative calls `googletag.pubads().setTargeting('nwdemocr','fitolive')`
when it renders, and every later *display* request inherits it. The video
request cannot see it, because:

- the module reads `window.nwdemocr` (a plain global), not GPT targeting; and
- `initializeNwdemocr()` runs **at the top of the request builder** and
  re-derives that global from the URL query string, erasing any runtime value
  microseconds before it is read.

So a video ad cannot currently be gated on "the display takeover rendered on
this pageview."

**(b) `categories` is discarded.**
`categories` is the only key that reflects *every* section an article is filed
under (e.g. an article with `cat = nwus-life` still carries
`categories = "life,personal/finance"`). Display sends it; the video module
reads `e.categories` only to take `.split(",")[0]` as a fallback for `cat`, then
throws the rest away. Without it, video cannot be targeted to a section the way
display can — verified: only ~34% of articles listed under /personal-finance and
/business have those as their *primary* category.

---

## 2. Change

**Module:** the video ad chunk — in the current build
`/_next/static/chunks/102l587676ixg.js`. Identify by content, not filename
(hash changes per build): it references `openads_video_cust` and
`initializeNwdemocr`, and builds the `cust_params` object containing
`ts / content / cat / sitecat / group_cat / nwnet_section / vid / vidcontent /
topics / pageurl / video_type`.

**Function:** the async ad-request builder. It opens with

```js
initializeNwdemocr();
window.openads_video_cust = "";
```

and ends by returning the assembled params object:

```js
return i && Object.assign(ee, i), ee;   // `ee` = the cust_params object
```

**Insert immediately before that return:**

```js
// Forward a small, explicit set of live GPT page key-values into the video
// ad request. Must run at REQUEST-BUILD time (not module init) so values set
// at runtime by a rendered creative are picked up.
const VIDEO_KV_ALLOWLIST = ["nwdemocr", "categories", "adunit", "article_id"];
const pa = window.googletag?.pubads?.();
if (pa) {
  for (const k of VIDEO_KV_ALLOWLIST) {
    const v = (pa.getTargeting(k) || []).join(",");
    if (v && !(k in ee)) ee[k] = v;   // never overwrite module-owned keys
  }
}
```

### Alternative insertion (equivalent, if preferred)

`window.openads_video_cust` already exists as an injection point for extra
`cust_params` — the builder appends its contents to the serialized string — but
it is cleared at the top of the builder and never repopulated. Populating it
with the serialized allowlist instead of clearing it achieves the same result.
Merging into `ee` is preferred because it de-duplicates against module-owned
keys.

---

## 3. The allowlist is mandatory, not stylistic

**Do not merge all GPT targeting.** Page-level targeting on article pages
includes very large values:

- `vnd_prx_segments` — several thousand characters (hundreds of Proximic IDs)
- `ias-kw` — ~200+ characters
- `ABS` / `BSC` / `CBS` — ID lists

Merging these would exceed practical ad-tag URL limits and silently break video
fill. Keep the allowlist short. If more keys are needed later, add them
explicitly and re-check total URL length.

---

## 4. Notes / already verified

- **Serialization is safe.** The existing serializer drops empty/null values
  (except `ref`), so allowlisted keys that are absent on a page cost nothing.
- **Multi-value keys work.** GAM matches comma-joined lists: a line item
  targeting `topics = "Dare to Dream"` was confirmed to match a request sending
  `topics=American dream,United States,Society,Dare to Dream`. So
  `categories="life,personal/finance"` will match a line item targeting
  `personal/finance`.
- **Master → pod propagation already works.** `vid.newsweek` uses ad rules; the
  master playlist request's `cust_params` is inherited by the pod request. No
  change needed there.
- **Do not change `initializeNwdemocr()`'s URL behaviour.** The `?nwdemocr=`
  test-campaign path must keep working as-is.

---

## 5. Acceptance criteria

1. On an article page, after the takeover anchor creative renders (page-level
   targeting shows `nwdemocr` containing `fitolive`), playing the video produces
   a request to `https://pubads.g.doubleclick.net/gampad/ads?...iu=/22541732127/vid.newsweek...`
   whose `cust_params` contains `nwdemocr=...fitolive...`.
2. The same request contains `categories=` with the page's full category list
   (e.g. `life,personal/finance`).
3. On a page where the anchor did **not** render, `cust_params` contains no
   `fitolive` value.
4. Total ad-tag URL length stays within normal bounds (no truncation).
5. Existing video delivery is unchanged on non-takeover pages.

A passing end-to-end test: with (1) satisfied, GAM returns the already-trafficked
Apple pre-roll (line item `7386773208`). It is confirmed to serve when the
request carries the matching key-value and to return nothing when it does not.

---

## 6. Rollout

- Put it behind a flag; canary one article template first.
- **Watch for one day:** video fill rate, video CPM, VAST error rate.
- **Known regression risk:** adding keys that were previously absent can change
  which *existing* video line items match — particularly any using `IS_NOT`
  conditions or brand-safety exclusions. This is the main thing to watch.
- Rollback is a flag flip. Nothing in trafficking depends on this change; the
  current contextual targeting keeps working meanwhile, so shipping late or
  reverting breaks nothing.

---

## 7. Out of scope

This change makes video targeting *correct and durable*. It does not create
video inventory — only ~8% of articles have a real player, so a takeover that
requires display and video together is still capped by player availability.
That is a separate product decision (outstream unit vs. instream pre-roll) and
does not block this ticket.

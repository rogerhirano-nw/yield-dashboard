# Video ad request: forward selected page key-values into `cust_params`

**Owner:** Ad Ops (Roger Hirano) → Web Engineering
**Scope:** one function in the video ad module. No trafficking changes required.
**Priority:** blocks the FITO Fluid takeover product (Cognizant Q4/Q1 plan).

**Prerequisite (Ad Ops, one-time):** create the custom targeting key `fito`
with value `live` in GAM. The takeover creative sets it via
`googletag.pubads().setTargeting('fito','live')`; this change forwards it to the
video request.

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
Display line items can be gated on a key-value set at runtime — the takeover
anchor creative calls `googletag.pubads().setTargeting('fito','live')` when it
renders, and every later *display* request inherits it. The video request never
sees it, because the module builds its `cust_params` from its own object plus a
few named globals and **does not read GPT page-level targeting at all**.

So a video ad cannot currently be gated on "the display takeover rendered on
this pageview."

> **Why a new `fito` key rather than reusing `nwdemocr`.** `nwdemocr` is the
> demo/test-campaign parameter, and a non-empty value changes real ad behaviour:
> three branches gate on `window.nwdemocr` together with DoubleVerify's `IDS`
> signal, and a non-empty value causes `NoPassFQ`/`keyEx` to be skipped,
> `googletag.display()` to be called on `IDS=1` traffic, and APS bids to be
> requested on `IDS=1` traffic. Using it as a production signal would bypass the
> site's invalid-traffic gate on every takeover pageview. `nwdemocr` stays
> demo-only; `fito` is the production signal. Both are forwarded — `fito` for
> production targeting, `nwdemocr` so QA/demo pages also work for video.

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

**Function:** the async ad-request builder — identifiable as the function that
calls `initializeNwdemocr()` on its first lines. It ends by returning the
assembled params object:

```js
return i && Object.assign(ee, i), ee;   // `ee` = the cust_params object
```

**Insert immediately before that return:**

```js
// Forward a small, explicit set of live GPT page key-values into the video
// ad request. Must run at REQUEST-BUILD time (not module init) so values set
// at runtime by a rendered creative are picked up.
// Keep this list SHORT and explicit — do not merge all GPT targeting. Page
// targeting includes multi-kilobyte values (vnd_prx_segments, ias-kw,
// ABS/BSC/CBS) that would blow the ad-tag URL limit and break video fill.
const VIDEO_KV_ALLOWLIST = ["fito", "nwdemocr", "categories", "adunit", "article_id"];
const pa = window.googletag?.pubads?.();
if (pa) {
  for (const k of VIDEO_KV_ALLOWLIST) {
    const v = (pa.getTargeting(k) || []).join(",");
    if (v && !(k in ee)) ee[k] = v;   // never overwrite module-owned keys
  }
}
```

This change writes only to the `ee` object, and only for keys not already
present.

# Pass page targeting values into the video ad request

## What's wrong today

Every article page keeps a small set of facts about itself for advertising
purposes — which sections the article belongs to, which article it is, and
(on takeover campaigns) a flag saying "the takeover is running on this
pageview."

**Our display ads automatically receive these facts. Our video player does not.**

The video player builds its own ad request and never looks at the page's
advertising values. So when the video ad request goes out, it is missing
information the rest of the page already has.

## Why that's a problem

Two things we cannot currently do:

1. **We cannot tie a video ad to a takeover.** When we sell a homepage-style
   takeover, we need the video ad to play only on pageviews where the takeover
   display ads actually appeared. The page knows this. The video request doesn't
   receive it, so the ad server can't act on it.

2. **We cannot target video ads by section properly.** Advertisers buy sections
   ("personal finance", "business"). The page knows every section an article
   belongs to. The video request doesn't receive that either, so video can only
   be targeted by an article's single main category — which matches the way
   articles are actually listed in a section only about a third of the time.

This is not specific to one campaign. Right now **no** video ad on the site can
be targeted using page-level values, because none of them reach the video
request.

## What we need done

When the video player builds its ad request, copy a short, fixed list of values
from the page into that request.

That's the whole change. No new data is created, nothing is calculated, nothing
is sent anywhere new — existing values are copied into a request that currently
goes out without them.

**Where:** the video ad module, in the function that assembles the ad request's
`cust_params`. It must happen at the moment the request is built (not when the
page or module first loads), because one of the values is set by an ad that
renders while the page is already open.

**The values to copy:** `fito`, `nwdemocr`, `categories`, `adunit`,
`article_id`.

**Suggested implementation** — insert just before the params object is returned:

```js
// Copy a small, explicit set of page-level ad values into the video ad request.
// Must run when the REQUEST IS BUILT (not at module init) so values set at
// runtime by an ad that has already rendered are picked up.
// Keep this list SHORT and explicit — do NOT copy all page targeting. Some
// page values are several thousand characters long (vnd_prx_segments, ias-kw,
// ABS/BSC/CBS) and would make the ad request URL too long, breaking video ads.
const VIDEO_KV_ALLOWLIST = ["fito", "nwdemocr", "categories", "adunit", "article_id"];
const pa = window.googletag?.pubads?.();
if (pa) {
  for (const k of VIDEO_KV_ALLOWLIST) {
    const v = (pa.getTargeting(k) || []).join(",");
    if (v && !(k in ee)) ee[k] = v;   // never overwrite values the module already set
  }
}
```

Notes on why it's written this way:

- If the ad library isn't loaded, the block does nothing and the request goes
  out exactly as it does today.
- If a value is missing on a page, it's skipped.
- If the module already sets a value itself, that one wins — we never overwrite.
- The five values together measure about 80 characters on a real page, so there
  is no risk to the request URL length. Copying *everything* would break it.

## Before this ships

Ad Ops needs to create one new targeting key, `fito`, with the value `live`, in
Google Ad Manager. Without it the code still works, it just has nothing to copy
for that key. **Roger will do this** — please confirm with him before release.

## How to check it worked

1. Open an article that is running the takeover and play the video.
2. Look at the video ad request in the network tab (it goes to
   `pubads.g.doubleclick.net/gampad/ads` and includes `vid.newsweek`).
3. Its `cust_params` should now contain `fito=live` and a `categories=` value
   listing the article's sections.
4. On an article that is *not* running the takeover, there should be no `fito`
   value.
5. Video ads on normal articles should behave exactly as before.

## Risk

Low — the change only adds values to a request. The one thing to watch is that
adding values can change which existing video campaigns match, so please put it
behind a flag, release to one article template first, and keep an eye on video
fill rate and errors for a day before rolling out. Rolling back is turning the
flag off; nothing on the ad-serving side depends on it.

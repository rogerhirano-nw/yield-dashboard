# Index Exchange — server-side (Prebid Server) activity drop-off, Aug 2026

**Thread:** `Index//Newsweek - Drop off on Server side activity`
(`thread::yqF5v3TDJAGEMpin9Mq6SiI::`) — Renee Kujawski ↔ Dayna Mistry /
Daisy Cheng (Index Exchange), CC RevOps. Opened 2026-08-10, still open at
2026-08-19.

**Symptom:** near-zero Index Exchange activity on the **server-side** ad units
since the start of August. Index's **TAM** and **client-side** paths are
unaffected. IX's read (2026-08-17): *"The Index cookie is no longer being
passed in the PB Server path."*

**IX's latest ask (2026-08-19):** *"Have the publisher check their ix.yaml file
and send us what user sync pixel they are using — it should be either a
redirect or iframe."*

---

## 1. The ask is misrouted — we don't have an ix.yaml

`static/bidder-info/ix.yaml` is a file in the **Prebid Server (Go) source
tree**. It belongs to whoever *operates the PBS host*, not to the publisher
whose pages call it. Newsweek runs the **Magnite RDM** managed server-side
wrapper (Renee, 2026-08-18), so **Magnite operates the PBS instance and owns
the ix.yaml** — endpoint, `userSync` block, publisher/site ids and all.

So there is no file on our side to open and send. Two consequences:

- The bidder-info question has to go to **Magnite RDM**, not to our dev team.
- We can still answer IX's actual question — *redirect or iframe?* — **from the
  browser**, without the file. See §3; it's the fastest path and it doesn't
  block on either vendor.

Worth confirming with Magnite as step zero, because everything below assumes
it: is our PBS instance Magnite-hosted RDM, or did anyone stand up a
self-hosted PBS? The former is what Renee described; the latter would make the
ix.yaml ours after all.

## 2. What that file controls, and where the cookie actually breaks

The chain that puts an Index user id on a server-side bid request:

1. Prebid.js on the page calls PBS **`/cookie_sync`** (configured via
   `s2sConfig.syncEndpoint`), passing the s2s bidder list.
2. PBS answers with a per-bidder sync URL. For `ix` it serves either the
   **`redirect`** (image pixel) or the **`iframe`** variant — **this is exactly
   what ix.yaml's `userSync` block decides**, and exactly what Daisy is asking
   for.
3. The browser calls IX's sync endpoint; IX 302s back to
   `<pbs-host>/setuid?bidder=ix&uid=<IX_UID>`.
4. PBS stores that uid in its own `uids` cookie, on the **PBS host domain**.
5. On the next auction PBS sets `user.buyeruid` on the `ix` bid request.

Break any link and IX sees server-side requests with no user id — which is
precisely what they reported. The `userSync` block in ix.yaml looks like this
(shape only — the real hosts, macros and ids must come from Magnite / IX's
onboarding, and IX now issues **regional** endpoints):

```yaml
endpoint: "https://<IX_REGIONAL_ENDPOINT>?s=<SITE_ID>"
userSync:
  redirect:
    url: "https://ssum.casalemedia.com/usermatchredir?s=<PUB_ID>&cb={{.RedirectURL}}"
    userMacro: "$UID"
  iframe:
    url: "https://ssum.casalemedia.com/usermatch?s=<PUB_ID>&cb={{.RedirectURL}}"
    userMacro: "$UID"
```

The two failure modes that would produce a clean step-change on ~Aug 1:

- **A PBS version bump on Magnite's side** that reset or dropped ix's
  `userSync` block (or re-added `disabled: true`), so `/cookie_sync` stopped
  returning an ix entry.
- **A sync-type mismatch**: ix.yaml serves an *iframe* sync while the page's
  `userSync.filterSettings` only permits *image*, or vice versa. The sync URL
  is returned but never fires. This is the specific thing Daisy's
  redirect-vs-iframe question is probing.

Lower-probability but cheap to rule out: a stale or wrong `s=` publisher id in
the sync URL (syncs land in the wrong bucket); `syncsPerBidder` / `coopSync`
caps starving ix; consent (GDPR / GPP / USP) strings not forwarded on the sync,
which makes IX drop it silently.

**Chronic third-party-cookie loss is *not* the explanation.** The PBS host is a
third-party domain, so Safari/Firefox match rates are always poor — but that's
a steady baseline, not a cliff on a specific date. A step-change points at
config or a version bump.

### The discriminating question nobody has asked yet

**Did the other PBS bidders' match rates drop at the same time, or only Index?**

- **All s2s bidders dropped** → the `/cookie_sync` call itself is broken or no
  longer firing (wrapper-level: `syncEndpoint`, `filterSettings`, a Prebid.js
  upgrade). Newsweek/Magnite wrapper problem, nothing to do with ix.yaml.
- **Only Index dropped** → it's ix-specific: the ix.yaml `userSync` block, the
  ix `s=` id, or something on IX's side. Magnite + IX problem.

This single answer halves the search space, and Magnite can pull it from RDM
reporting today. It should be in the first line of the note to them.

## 3. What we can check ourselves, right now (and it answers Daisy directly)

On a live newsweek.com article page, DevTools open:

1. Console — `pbjs.version`, then:
   - `pbjs.getConfig('s2sConfig')` → confirm `bidders` includes `ix`; note
     `syncEndpoint`, `endpoint`, `accountId`, `defaultVendor`.
   - `pbjs.getConfig('userSync')` → `syncEnabled`, `filterSettings` (does it
     allow `iframe`? `image`?), `syncsPerBidder`, `aliasSyncEnabled`.
2. Network, filter **`cookie_sync`** → open the response JSON. Look for the
   `ix` entry: `{"bidder":"ix","usersync":{"url":"…","type":"redirect"|"iframe"}}`.
   - **`type` is literally the answer to Daisy's question**, and the `url`
     carries the `s=` id IX wants to verify. Screenshot it and send it.
   - If there is **no `ix` entry**, or its status is `error`, that is the bug —
     PBS isn't offering an Index sync at all, and it's Magnite's to fix.
3. Network, filter **`casalemedia`** → does the `usermatch` call fire? Does it
   302 to `<pbs-host>/setuid?bidder=ix&uid=…`? Does `/setuid` return 200?
4. Application → Cookies → the **PBS host domain** → is there a `uids` cookie,
   and does it contain an `ix` entry?

Test in a browser that still allows third-party cookies (Chrome, non-Incognito,
no blocking extension) or the result is uninformative by construction.

## 4. Repo data: what we can and can't see

Nothing in this dashboard covers the PBS path — the cache tables are GAM,
Magnite DV+, Pubmatic and OpenSincera; server-side wrapper demand arrives as
Prebid line items in GAM with no bidder attribution we ingest. So the
drop-off can't be quantified or confirmed from here.

The one adjacent asset is **`scripts/pull_index_ob_requests.py`** (+
`.github/workflows/pull_index_ob_requests.yml`), which pulls Index **Open
Bidding** callouts / bids / wins / impressions by month from GAM's
`YIELD_GROUP_*` metrics. That's Google's server-side path, not Prebid Server —
but it's a useful **control**: if Index's OB volume is flat across the same
window while PBS is at zero, IX's demand is healthy and the fault is isolated
to the Prebid Server integration, which is a worthwhile line to put in front of
both vendors.

## 5. Draft replies

### 5a. To Daisy Cheng / IX Square (Renee's voice)

> Hi Daisy,
>
> Thanks — that documentation helps, but I want to flag a routing issue so we
> don't lose more days on it: **we don't host Prebid Server.** We're on
> Magnite's RDM managed server-side wrapper, so the PBS instance — and the
> `ix.yaml` bidder config, endpoint and user-sync block — sits with Magnite,
> not with us. There's no file on our side for our dev team to open.
>
> I'm taking the bidder-info request to Magnite in parallel. In the meantime we
> can answer your question from the page itself: we'll capture the PBS
> `/cookie_sync` response on a live article and send you the `ix` entry, which
> shows the sync type (redirect vs iframe) and the `s=` value actually in use.
> If there's no `ix` entry in that response at all, that's our answer as to why
> the cookie stopped flowing.
>
> Two things that would speed this up from your side:
>
> 1. Can you confirm the **regional endpoint and the publisher/site `s=` id**
>    you have provisioned for our server-side integration? We'll reconcile them
>    against what Magnite has configured.
> 2. On your side, when did the Index user id stop appearing on our PBS
>    requests — a hard cutover on a specific date, or a decline? A clean date
>    would let Magnite line it up against their release history.
>
> For context on impact: this is now 20+ days of near-zero server-side volume
> while TAM and client-side are unaffected, so we'd like to keep this moving
> daily until it's resolved.
>
> Renee

### 5b. To Magnite / RDM support

> Hi <RDM contact>,
>
> We've had a near-total drop-off in Index Exchange activity on the
> **server-side** path since the start of August. Index's TAM and client-side
> paths are unaffected. Index's diagnosis is that **the Index user id is no
> longer being passed on our Prebid Server bid requests** — i.e. the cookie
> sync isn't landing.
>
> Since RDM operates our PBS instance, the config in question is on your side.
> Could you check and come back on:
>
> 1. **Did other s2s bidders' match rates drop at the same time, or only
>    Index?** This is the key discriminator — all bidders points at
>    `/cookie_sync` itself, Index-only points at the ix bidder config.
> 2. The current **`ix` bidder-info config** on our PBS instance: is the
>    adapter enabled, what endpoint is set, and what does the `userSync` block
>    contain (`redirect` and/or `iframe`, and the `s=` publisher id in each)?
>    Index has asked us for exactly this.
> 3. Any **PBS release, RDM wrapper version bump, or Prebid.js upgrade around
>    2026-08-01** that could have reset or dropped the ix `userSync` entry.
> 4. Whether the wrapper's **`userSync.filterSettings`** on our pages permits
>    the sync type ix.yaml is serving (an iframe sync behind an image-only
>    filter, or the reverse, is returned but never fires).
> 5. Whether **`ix` is still present in the `s2sConfig.bidders`** list, and
>    whether consent strings (GDPR / GPP / USP) are being forwarded on the
>    sync call.
>
> Happy to get Index on a joint call if that's faster — they're engaged on the
> thread. This is 20+ days of lost server-side revenue at this point.
>
> Renee

## 6. Open items

- [ ] Confirm with Magnite that PBS is RDM-hosted (no self-hosted instance).
- [ ] Capture the `/cookie_sync` response on a live page → send the `ix` entry
      (sync type + `s=`) to Daisy. **Unblocks IX's question without Magnite.**
- [ ] Get the all-bidders-vs-Index-only answer from RDM reporting.
- [ ] Ask IX for the exact date the user id stopped appearing.
- [ ] Reconcile IX's provisioned regional endpoint + `s=` id against Magnite's
      config.
- [ ] Optional control: run `pull_index_ob_requests.py` and check whether Index
      Open Bidding volume held flat over the same window.

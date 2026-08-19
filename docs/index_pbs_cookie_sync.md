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

**Confirmed 2026-08-19 (Roger):** **only Index dropped.** The other
server-side bidders' match rates held. This is the single most useful fact in
the thread — see §3.

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
  browser**, without the file. See §4; it's the fastest path and it doesn't
  block on either vendor.

Worth confirming with Magnite as step zero, because everything below assumes
it: is our PBS instance Magnite-hosted RDM, or did anyone stand up a
self-hosted PBS? The former is what Renee described; the latter would make the
ix.yaml ours after all.

## 2. It's the sync leg that broke, not the bid leg

The chain that puts an Index user id on a server-side bid request:

1. Prebid.js on the page calls PBS **`/cookie_sync`** (configured via
   `s2sConfig.syncEndpoint`), passing the s2s bidder list **and the page's
   allowed sync types** (from `userSync.filterSettings`).
2. PBS answers with a per-bidder sync URL. For `ix` it serves either the
   **`redirect`** (image pixel) or the **`iframe`** variant — **this is exactly
   what ix.yaml's `userSync` block decides**, and exactly what Daisy is asking
   for.
3. The browser calls IX's sync endpoint; IX 302s back to
   `<pbs-host>/setuid?bidder=ix&uid=<IX_UID>`.
4. PBS stores that uid in its own `uids` cookie, on the **PBS host domain**.
5. On the next auction PBS sets `user.buyeruid` on the `ix` bid request.

**IX's own wording localizes the break to steps 2–4.** They can only observe
that "the Index cookie is no longer being passed" if they are still *receiving*
our PBS bid requests — just without a `user.buyeruid`. So the `ix` adapter is
enabled, the endpoint resolves, and ix is still in the auction. The bid leg is
fine; the **sync** leg is what stopped. (Worth confirming with IX in one line —
see §6a — because it removes "adapter disabled" from the list entirely.)

That also explains the revenue shape: unsynced users → IX bids on far less
inventory at far lower rates → server-side activity collapses to near zero
without ever going to literal zero requests.

The `userSync` block in ix.yaml looks like this (shape only — the real hosts,
macros and ids must come from Magnite / IX's onboarding, and IX now issues
**regional** endpoints):

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

## 3. "Only Index dropped" — what that rules in and out

Because **the other s2s bidders kept syncing normally**, everything shared
across bidders is provably working and can be struck off:

- `/cookie_sync` is being called and is returning usable syncs.
- `s2sConfig.syncEndpoint` is configured correctly.
- `/setuid` works and the PBS host's `uids` cookie is being written and read.
- **Third-party cookies are not the story.** The PBS host is a third-party
  domain; if 3PC loss were the cause, every s2s bidder's match rate would have
  fallen together. Say this plainly to both vendors — it's the first thing each
  will reach for, and it's already disproven.
- A global `syncEnabled: false` or a Prebid.js upgrade breaking sync wholesale.

What's left is **ix-specific**, ranked:

1. **ix's sync is iframe-type and the page doesn't allow iframe syncs.**
   Prebid.js `userSync.filterSettings` **defaults to image/redirect only** —
   iframe syncs are off unless explicitly enabled with a `filterSettings.iframe`
   entry. Prebid.js passes those allowed types to PBS on `/cookie_sync`, and PBS
   returns nothing for a bidder whose only configured sync type is filtered out.
   So a bidder that is iframe-only silently gets **zero** syncs while every
   redirect/image bidder is unaffected — *exactly* the "only Index" signature.
   This is also precisely what Daisy's redirect-vs-iframe question is probing,
   which suggests IX already suspects it. **Top candidate.**
2. **The `ix` entry is missing or errored in the `/cookie_sync` response** — the
   `userSync` block emptied, or ix dropped from the sync set, on a Magnite PBS
   release around 2026-08-01. Note the bid leg and the sync leg are configured
   separately, so this is fully consistent with IX still receiving bid requests
   (§2).
3. **A stale or wrong `s=` publisher id in the ix sync URL** — the sync fires
   and 302s, but the match lands in the wrong bucket, so IX never associates it
   with our traffic.
4. **Sync-count starvation** — `syncsPerBidder` / PBS `cookie-sync` priority
   groups truncating the per-request set so ix is consistently cut. Less likely
   to produce a clean cliff, but cheap to check once we can see the config.
5. **IX-side change** — a regional-endpoint migration or a deprovisioned sync
   id on their end around the same date. Their answer to "when exactly did the
   uid stop appearing?" tests this.

Hypotheses 1 and 2 are both settled by the *same* one-minute check, below.

## 4. What we can check ourselves, right now (and it answers Daisy directly)

**Fastest path — paste `docs/snippets/ix_cookie_sync_probe.js` into the DevTools
console on a live newsweek.com article page.** It reads the wrapper config,
checks whether the page allows iframe syncs, looks for cookie-sync / IX
`usermatch` / `/setuid` calls in this pageview's resource timings, and then
**re-issues `/cookie_sync` twice — once with the page's real `filterSettings`,
once with iframe additionally allowed.** If `ix` is absent from the first
response and present in the second, hypothesis 1 is confirmed outright and the
fix is a wrapper config change, not an ix.yaml change. It prints a verdict line
and copies a JSON block to the clipboard to paste back to RevOps / Index. It
only reads config and repeats a call the page already makes — nothing is
written.

**This can't be run from the automation environment** — headless browser egress
is blocked in the Claude Code container (every real site resets the connection;
only `curl` reaches out), so the capture has to come from a real browser. That's
the better artifact anyway: a datacenter-IP headless load has different consent,
geo, and bot-detection behaviour than a real session, and the cookie-sync path
is exactly what those change.

The manual equivalent, if you'd rather click through it — DevTools open on a
live article page:

1. Network, filter **`cookie_sync`** → open the response JSON. Look for the
   `ix` entry: `{"bidder":"ix","usersync":{"url":"…","type":"redirect"|"iframe"}}`.
   - **`type` is literally the answer to Daisy's question**, and the `url`
     carries the `s=` id IX wants to verify. Screenshot it and send it.
   - **No `ix` entry, or `type: iframe`** → that's the bug, and which of the two
     it is separates hypothesis 2 from hypothesis 1.
   - Compare against a bidder that *is* still syncing — the difference between
     its entry and ix's is the fault.
2. Console — `pbjs.getConfig('userSync')` → does `filterSettings` include an
   **`iframe`** entry? If it doesn't, and ix's sync type is iframe, hypothesis 1
   is confirmed outright and the fix is a wrapper config change, not an ix.yaml
   change.
3. Console — `pbjs.getConfig('s2sConfig')` (confirm `bidders` includes `ix`;
   note `syncEndpoint`, `accountId`, `defaultVendor`) and `pbjs.version`.
4. Network, filter **`casalemedia`** → does the `usermatch` call fire? Does it
   302 to `<pbs-host>/setuid?bidder=ix&uid=…`? Does `/setuid` return 200?
5. Application → Cookies → the **PBS host domain** → does the `uids` cookie
   contain an `ix` entry alongside the bidders that are still working?

Test in a browser that still allows third-party cookies (Chrome, non-Incognito,
no blocking extension) or the result is uninformative by construction.

## 5. Repo data: what we can and can't see

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
to the Prebid Server integration.

## 6. Draft replies

### 6a. To Daisy Cheng / IX Square (Renee's voice)

> Hi Daisy,
>
> Thanks — that documentation helps, but I want to flag a routing issue so we
> don't lose more days on it: **we don't host Prebid Server.** We're on
> Magnite's RDM managed server-side wrapper, so the PBS instance — and the
> `ix.yaml` bidder config, endpoint and user-sync block — sits with Magnite,
> not with us. There's no file on our side for our dev team to open. I'm taking
> the bidder-info request to Magnite in parallel.
>
> One important data point from our side: **we've confirmed this is Index-only.
> The other server-side bidders' match rates are unaffected.** That rules out
> the shared parts of the path — our PBS cookie sync is firing, `/setuid` is
> writing, and third-party cookie loss can't be the explanation, or every
> bidder would have dropped together. Whatever changed is specific to the Index
> integration.
>
> To answer your question directly, we're capturing the PBS `/cookie_sync`
> response on a live article page and will send you the `ix` entry, which shows
> the sync type actually being served (redirect vs iframe) and the `s=` value
> in use. If there's no `ix` entry in that response at all, that's our answer as
> to why the cookie stopped flowing.
>
> Three things that would speed this up from your side:
>
> 1. Can you confirm you are **still receiving our PBS bid requests**, just
>    without `user.buyeruid`? That's how we're reading "the cookie is no longer
>    being passed," and it tells us the adapter and endpoint are fine and the
>    break is in the sync specifically.
> 2. Can you confirm the **regional endpoint and the publisher/site `s=` id**
>    provisioned for our server-side integration? We'll reconcile them against
>    what Magnite has configured.
> 3. **When exactly** did the Index user id stop appearing on our PBS requests —
>    a hard cutover on a specific date, or a decline? A clean date lets Magnite
>    line it up against their release history.
>
> For context on impact: this is now 20+ days of near-zero server-side volume
> while TAM and client-side are unaffected, so we'd like to keep this moving
> daily until it's resolved.
>
> Renee

### 6b. To Magnite / RDM support

> Hi <RDM contact>,
>
> We've had a near-total drop-off in Index Exchange activity on the
> **server-side** path since the start of August. Index's TAM and client-side
> paths are unaffected. Index's diagnosis is that **the Index user id is no
> longer being passed on our Prebid Server bid requests** — the cookie sync
> isn't landing.
>
> We've confirmed on our side that **this is Index-only — the other s2s
> bidders' match rates are normal.** So `/cookie_sync` and `/setuid` are working
> in general, and this isn't third-party-cookie attrition. Something specific to
> the `ix` configuration on our PBS instance changed. Since RDM operates that
> instance, could you check and come back on:
>
> 1. **What sync type is configured for `ix`** in the bidder-info config —
>    `redirect`, `iframe`, or both — and the `s=` publisher id in each. Index has
>    asked us for exactly this.
> 2. **Whether our pages' `userSync.filterSettings` permits that type.** If ix is
>    iframe-only and our wrapper is image/redirect-only (the Prebid.js default —
>    iframe syncs are off unless explicitly enabled), ix would get zero syncs
>    while every other bidder is unaffected. That matches our symptom exactly and
>    is our leading theory.
> 3. Whether the `ix` entry is still present and non-erroring in the
>    `/cookie_sync` response for our account.
> 4. Any **PBS release or RDM wrapper version bump around 2026-08-01** that could
>    have reset or dropped the ix `userSync` entry, or changed sync
>    priority/limits (`syncsPerBidder`, cookie-sync priority groups) in a way
>    that starves ix.
>
> To be clear on what we're not asking: the ix bid adapter itself appears fine —
> Index is still receiving our requests, just without the user id — so this is
> the sync configuration, not the endpoint.
>
> Happy to get Index on a joint call if that's faster; they're engaged on the
> thread. This is 20+ days of lost server-side revenue at this point.
>
> Renee

## 7. Open items

- [ ] Confirm with Magnite that PBS is RDM-hosted (no self-hosted instance).
- [ ] Capture the `/cookie_sync` response on a live page → send the `ix` entry
      (sync type + `s=`) to Daisy. **Settles hypotheses 1 and 2 at once and
      unblocks IX's question without Magnite.**
- [ ] Check `pbjs.getConfig('userSync').filterSettings` for an `iframe` entry.
- [x] ~~Do other s2s bidders' match rates show the same drop?~~ **No — Index
      only** (Roger, 2026-08-19).
- [ ] Ask IX to confirm they still receive our PBS requests (without
      `buyeruid`), and the exact date the uid stopped.
- [ ] Reconcile IX's provisioned regional endpoint + `s=` id against Magnite's
      config.
- [ ] Optional control: run `pull_index_ob_requests.py` and check whether Index
      Open Bidding volume held flat over the same window.

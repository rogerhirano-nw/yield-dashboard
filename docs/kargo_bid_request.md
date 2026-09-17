# Sharing a real bid request with Kargo

Raised 2026-09-10: Kargo asked for a real bid request. The short answer is
that **Newsweek cannot produce one**, and the reason is worth understanding
before anyone promises otherwise — the thing Kargo receives is minted by
Magnite's Prebid Server, not by the page.

## How Kargo is actually wired (measured, not assumed)

Captured from four live `www.newsweek.com` article pages, mobile + desktop,
with `scripts/capture_kargo_bid_request.py` (Prebid `v10.29.0`):

| Fact | Value |
|---|---|
| Direct browser→Kargo bid requests | **0** |
| Kargo cookie-sync pixels (`crb.kargo.com/api/v1/dsync/PrebidServer`) | yes |
| Client-side bidder codes on the page | `ttd, aps, rubicon, nativo, pubmatic, triplelift, ix, ozone, teads, criteo, smilewanted, invibes` — **no `kargo`** |
| s2s aliases the page calls | `mgnipbs` (→ `prebid-server.rubiconproject.com`), `aypbs` (→ `pbs-us-east.ay.delivery`) |
| Bidders those PBS hosts called server-side | 30+, **including `kargo`** (also mobkoi, ogury, oms, onetag) |

So the path is:

```
newsweek.com page
  └─ pbjs sends ONE aliased imp  ──POST──>  prebid-server.rubiconproject.com/openrtb2/auction
       ext.prebid.aliases = {"mgnipbs": "rubicon"}          (Magnite PBS, account 9619,
       ext.prebid.bidders.mgnipbs.wrappername =              wrapper "9619_Newsweek_Mobile_Server")
         "9619_Newsweek_Mobile_Server"
            └─ PBS fans out server-side to ~30 bidders ──> Kargo   ← the request Kargo wants
```

**The browser never contacts Kargo.** Its only Kargo-bound traffic is the
user-sync redirect, and that proves nothing about the auction: Prebid
Server's `/cookie_sync` fires a pixel for every bidder on the *account*,
whether or not that bidder was called on the page. Mistaking that sync for a
bid request is the easy wrong turn here — the first capture pass did exactly
that and concluded "client-side" for a bidder that is nothing of the sort.

The tell that Kargo really is in the auction is the PBS **response**:
`ext.responsetimemillis` carries one key per bidder PBS actually called, and
`kargo` is in it on ~50% of Magnite PBS auctions.

## What we can and cannot send

| Artefact | Who has it |
|---|---|
| The OpenRTB request Kargo's endpoint received | **Magnite only.** Ask Magnite (PBS account 9619) to export an outbound sample, or ask Kargo to pull one from their own logs by request id. |
| The client→PBS auction request | **Us.** This is the publisher-side input Kargo's copy is derived from — GPID, floors, sizes, site, device, `user.ext.eids`, `regs`, wrappername. It answers nearly every "what signals is the publisher passing?" question. |
| The PBS response showing Kargo's bid / no-bid | **Us.** |

If Kargo's real question is "why is our bid rate / viewability low", the
client→PBS request plus `docs/prebid_viewability.md` (Kargo reads **64.2%**
viewable on 1.19M impressions vs a 78.7% peer baseline) is the more useful
package anyway. Send the PBS request, say plainly that it is the PBS request,
and loop Magnite in for the byte-exact outbound copy.

## Capturing one

```bash
pip install playwright && playwright install chromium
python scripts/capture_kargo_bid_request.py                    # 6 article loads
ARTICLE_URLS="https://www.newsweek.com/…-12428101" LOADS=4 python scripts/…
REDACT=1 python scripts/capture_kargo_bid_request.py           # mask identifiers
```

Outputs to `$OUT_DIR` (default `/tmp/kargo-bid-request`): `captures.json`
(everything), `sample.json` (the single best request, pretty), `summary.txt`
(the digest, including the wiring verdict and the full server-side bidder
list). Exit code is non-zero when nothing was captured.

The script prefers a *direct* bidder request and only falls back to the PBS
one, so it keeps working unchanged if Kargo is ever moved client-side — the
verdict line in `summary.txt` says which artefact you got.

**On sharing identifiers.** `REDACT=1` masks identifier *values*
(`user.ext.eids[].uids[].id`, `device.ip`, `source.tid`) while keeping the
provider names (`newsweek.com` ppuid, `pubcid.org`, `adserver.org` TDID) and
every non-identity field. It is **off by default**: Kargo already receives
all of it in production, and the eids are usually the point of the
conversation. Note the capture browser is a throwaway headless profile, so a
default capture carries synthetic ids, not a real reader's.

**Running it here vs. from a laptop.** A datacenter IP changes which demand
shows up (SmileWanted, for instance, is requested every auction and never
bids from one — see `docs/prebid_viewability.md`). For a sample that matches
production, run it from a residential connection with
`BROWSER_CHANNEL=chrome INCOGNITO=1`.

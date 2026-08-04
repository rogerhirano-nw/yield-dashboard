# FITO Fluid — GAM setup, and why it works

Companion to `fito_video_custparams_spec.md` (that one is the page-code ask;
this one is the ad-server side). Audience: ad ops, sales planning.

---

## 1. What was broken

The original FITO test ran on order **4144745465** (Apple "Apple at Work") as
**two independent line items**:

| | FITO-Video `7381354074` | FITO-Display `7381370379` |
|---|---|---|
| Type / priority | STANDARD, 8 | STANDARD, 8 |
| Environment | VIDEO_PLAYER | BROWSER |
| Goal | 200,000 lifetime | 480,000 lifetime |
| Creatives | 3 uploaded video, **no companions attached** | 30 Innovid tags across 5 sizes |
| Status when audited | DELIVERING | **COMPLETED** |

Three independent failure modes, all structural:

1. **The display units could not hold together.** One line item carrying 30
   creatives across 5 sizes relies on GAM choosing to deliver several of them to
   the same pageview. It does not guarantee that. `roadblockingType` was
   `ONE_OR_MORE` — best effort, not all-or-none.
2. **The two line items paced independently.** Evenly-paced STANDARD line items
   deliberately skip eligible impressions to spread delivery. Two of them skip
   *independently*, so co-occurrence is a coin flip, not a guarantee.
3. **One finished and the other did not.** Display hit 480k and completed while
   video kept delivering — so at audit time every FITO video impression was
   serving with **no display takeover around it at all**.

It was also sold **PG via DV360**, which is why it could not be fixed in place:
the order is managed/programmatic and GAM rejects new line items on it
(`LineItemError.CANNOT_ADD_TO_MANAGED_ORDER`). PG also forces ASAP delivery and
blocks Sponsorship priority — the root of the over-delivery complaint.

---

## 2. What we built instead

### 2.1 One auction decides the pageview

A single **Sponsorship anchor line item** wins the first display slot on the
page. Everything else on that pageview is a *consequence* of that win rather
than an independent auction that might disagree.

### 2.2 The anchor creative broadcasts a page-level signal

The anchor is a **CustomCreative served in a friendly iframe** (not SafeFrame).
On render it calls:

```js
googletag.pubads().setTargeting('fito', 'live');
```

GPT applies page-level targeting to **every ad request made after that point**.
Because the site defines in-article slots lazily (on scroll), essentially every
remaining display request on the pageview inherits the signal.

### 2.3 Follower line items win the rest natively

A **Sponsorship follower line item** targets `fito = live` and carries ordinary
creatives in each size. It wins the remaining display slots through their normal
auctions.

The anchor carries `fito IS_NOT live` so it cannot compete for its own follower
slots.

### 2.4 The video leg

The pre-roll is a **VIDEO_PLAYER line item** on `/22541732127/vid.newsweek`
(ad-rules inventory: a master playlist request returns pod URLs, and
`cust_params` propagates from master to pod automatically).

Today it is targeted on **shared contextual key-values** (`cat`, `sitecat`,
`topics`, `content`) — the only contextual keys both display and video requests
independently carry. Once the page change in `fito_video_custparams_spec.md`
ships, it targets `fito = live` instead and inherits the same render-condition
guarantee as the display followers.

---

## 3. Why this works

**Display units cannot desync from each other.** They are no longer 30 creatives
hoping to co-occur. The anchor is one impression, and the followers are gated on
a signal that only exists because the anchor rendered. There is no pacing race
between them.

**Nothing can serve without the anchor.** The signal is set *by the creative*,
so if the anchor loses or does not render, the value never exists, the followers
have zero eligible inventory, and the video leg (post-dev-change) has none
either. The failure mode is "no takeover," never "half a takeover."

**Delivery stops in lockstep.** When the anchor completes, it stops rendering →
the signal stops appearing → every downstream line item's eligible inventory
drops to zero automatically. This is what the original setup could not do, and
it is why display ran on for weeks after video had ended.

**Sponsorship makes eligibility a guarantee, not a probability.** At Sponsorship
priority the anchor beats standard, price-priority, AdX and header-bidding
demand, so "targeted" and "will render" are the same statement. This is also why
Kelly's *Direct-only* rule and the sync requirement are really one rule: PG
cannot express Sponsorship priority.

**Every unit is a real, measurable impression.** Followers render in their own
GPT slot iframes, so they report per-unit and measure viewability organically.
An earlier design had the anchor creative *paint* the other units by writing
into the page; that works, but parent-DOM renders read ~0% viewable to Active
View (the Mobkoi problem), so the cascade approach is strictly better.

**Impression control lives outside GAM's pacing engine.** Sponsorship paces to a
percentage, not an impression count — which is exactly Kelly's over-delivery
complaint. The sold number is enforced on the **anchor** (one anchor impression
= one takeover pageview) with a monitor that pauses the set at goal, rather than
by asking two line items to pace themselves into agreement.

---

## 4. Current POC objects

Order **Newsweek_Test-2** `4082002976`, advertiser `[nw] Oracle America`
`5128703908`. Flight ends 2026-08-17.

| Object | ID | Notes |
|---|---|---|
| Anchor line item | `7389497908` | Sponsorship; sets the cascade via its creative |
| Anchor creative | `138569653071` | CustomCreative, 970x250, **non-SafeFrame** (required) |
| Follower line item | `7386791898` | Sponsorship; 970x250 / 300x250 / 728x90 |
| Pre-roll line item | `7386773208` | PRICE_PRIORITY, VIDEO_PLAYER, 640x360 |
| Pre-roll creative | VastRedirect | → wrapper VAST in `docs/snippets/fito_preroll_vast.xml` |
| Audience segment | `9443596281` | **Built but deliberately unused** — see §6 |

Custom targeting keys in use: `fito` (production signal — **must be created in
the GAM UI**), `nwdemocr` (demo/QA only), `categories` `17720447`, `article_id`
`14518902`, `topics`, `cat` / `sitecat`.

---

## 5. Targeting reference

```
Anchor      <section scope>  AND  video_type IS verified  AND  fito IS_NOT live
Followers   fito IS live
Pre-roll    fito IS live                    (after the page change)
            <same section scope>            (until then)
```

`video_type IS verified` on the anchor is what guarantees the two formats
co-occur: it restricts the takeover to pages that actually have a player, so
display never runs where a pre-roll is impossible. Do **not** put `video_type`
on the video line item — the video module derives that field from the video
object rather than the page, so its value does not reliably match.

For section scope, `categories` is the correct key (it lists *every* section an
article is filed under, e.g. `life,personal/finance`), but it is display-only
until the page change ships. `cat`/`sitecat` is the shared fallback — note it is
the article's **primary** category, which is not the same as the section listing.

---

## 6. Decisions and why we rejected the alternatives

**Master/companion roadblock** — the GAM-native way to force video + display
atomically. Rejected: it requires every display slot to be registered with
`googletag.companionAds()`, and **zero slots on the site are**. It also needs the
slots requested eagerly, which conflicts with the lazy-definition ad stack.

**Audience segment gating** (anchor fires a pixel; pre-roll targets the segment)
— rejected because segment membership rides the Google cookie, so Safari/ITP,
Firefox and cookieless traffic never join. The video leg would have
systematically under-delivered against display on a large share of the audience.
The segment exists in GAM but gates nothing.

**Reusing `nwdemocr` as the production signal** — rejected. A non-empty
`window.nwdemocr` changes real ad behaviour: three branches gate on it together
with DoubleVerify's `IDS` signal, and a non-empty value skips `NoPassFQ`/`keyEx`
and forces `googletag.display()` and APS bid requests on `IDS=1` traffic. Using
it in production would bypass the invalid-traffic gate on every takeover
pageview. Hence the dedicated `fito` key.

**Creative-side overrides of page globals** — rejected. Making the creative
defeat the page's own code is the pattern the site's Confiant wrapper exists to
detect, and it hides business logic where engineering cannot see it.

---

## 7. Known constraints (learned the hard way)

- The API service account **cannot** create custom targeting *keys*, approve
  orders, or activate/reserve line items. Values under an existing key are fine.
  Key creation and activation are UI tasks.
- Line items cannot be added to managed/programmatic orders.
- Video line items require `requestPlatformTargeting`.
- `vastXmlUrl` has a length limit far below a full campaign VAST tag — hence the
  short wrapper VAST that redirects to the real tag.
- GAM edge propagation for creative and targeting changes runs ~10–20 minutes.
  Verify with a direct `gampad` request before concluding something is broken.
- GAM matches comma-joined multi-value key-values, so `topics=a,b,c` matches a
  line item targeting `b`.

---

## 8. What this does not solve

**Video inventory.** Roughly **8%** of articles have a real player
(`video_type=verified`); the rest have no player at all, so a pre-roll cannot
render there regardless of targeting. In an audit of 249 articles across
/personal-finance and /business, ~19 had a player and ~13 could run both formats
together.

A takeover sold as *section-wide with video on every impression* therefore
cannot be delivered with instream pre-roll. The options are to sell a smaller
curated always-synced package, to sell display section-wide and video
separately, or to use an **outstream** video unit inside the fluid creative —
which carries its own player and so is not limited by player availability.

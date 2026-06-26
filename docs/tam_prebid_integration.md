# TAM + Prebid Server integration (Elly's team)

Tracking doc for the demand-partner integration that connects Newsweek
inventory to **Amazon TAM (Transparent Ad Marketplace)** first, then
**Prebid Server**. This is a partner/RevOps coordination project, not a
yield-dashboard code change — nothing in this repo serves ads or manages
consent. The doc captures the integration state, the open items on each
side, and the **consent-signal answer** the partner asked for, plus a
**draft reply** (not sent).

> **Partner contact:** Elly (`<add email>`), `<exchange / SSP name>`.
> Newsweek already blocked the IAB categories the partner supplied.

## Status (as of 2026-06-26 email)

Partner ("our side", per Elly):

1. **TAM first** — partner expects to complete TAM setup on their side by
   **Wed 2026-07-01** (the "next Wednesday" in the email).
2. **Prebid Server** — partner will send the **endpoint + integration docs
   within the following week** (~target 2026-07-03).
3. **Consent signals** — partner can support **TCF v2.0 and TCF v2.2** and
   asked **what signals Newsweek currently passes or plans to pass.** ← the
   one open ask on us.

Newsweek ("our side"):

- ✅ Blocked the partner's supplied IAB category list.
- ⬜ Answer the consent-signals question (see below; needs ad-ops/CMP
  confirmation before sending).
- ⬜ On TAM go-live: register the partner as a TAM bidder and add it to the
  `apstag` `slotBids` config on-page (engineering/header-bidding setup,
  outside this repo). Cross-reference the bidder short code against
  `Newsweek_TAM_Bidder_Reference.xlsx` (see README "Magnite Prebid" notes)
  so partner delivery is attributable in reporting.
- ⬜ On Prebid Server docs arrival: review endpoint, bidder params, and
  `s2sConfig` (account/endpoint) before wiring the partner adapter.

## Consent signals — what Newsweek passes (the answer to Elly)

**Headline:** target **TCF v2.2**, not v2.0. IAB Europe **deprecated TCF
v2.0 and sunset live v2.0 strings on 2023-11-20**; the current production
string version is **v2.2**, which is backward-compatible on the wire (same
`__tcfapi` surface). Supporting v2.0 on the partner side is fine as a
fallback, but Newsweek should be passing **v2.2**.

How the signal actually reaches each path (publisher mechanics):

- **TAM (`apstag`).** Amazon's on-page `apstag` library reads the IAB
  **TCF API (`__tcfapi`)** itself and forwards the **TC string** in its
  server-side request to Amazon — the publisher does **not** hand-build a
  consent payload for TAM. Requirement on us is just a **registered,
  TCF-v2.2-compliant CMP present on the page** before `apstag.fetchBids`
  fires. So for TAM the answer is: "TCF v2.2 via our CMP / `__tcfapi`; TAM
  picks it up automatically."

- **Prebid Server.** Consent rides standard OpenRTB, populated by
  Prebid.js consent-management modules from the CMP:
  - **GDPR / TCF** → `regs.ext.gdpr` (0/1) + `user.ext.consent` (the TCF
    v2.2 string). Prebid module: `consentManagementTcf` (a.k.a.
    `consentManagement`).
  - **US privacy** → moving from the legacy CCPA **US Privacy string**
    (`regs.ext.us_privacy`, module `consentManagementUsp`) to **GPP**
    (`regs.gpp` + `regs.gpp_sid`, module `consentManagementGpp`). For a
    primarily **US** news publisher this path is at least as relevant as
    TCF — confirm which of USP/GPP Newsweek emits today.
  - **Supply chain** → `source.ext.schain` (`schain` module) — not a
    consent signal, but the partner will likely also want our **schain**
    node and our **sellers.json / ads.txt** entry; flag it in the same
    thread.

**To confirm with Newsweek ad-ops / engineering before sending** (this
repo can't verify the live ad stack):

- Which **CMP** is deployed (e.g. Sourcepoint / OneTrust / Quantcast) and
  that it advertises **TCF v2.2**.
- Whether Newsweek currently emits **US Privacy (CCPA)** strings, **GPP**,
  or both, and the **GPP section IDs** (`gpp_sid`) in scope.
- Whether a **schain** object is configured and our sellers.json entry.

## Draft reply to Elly (NOT sent — review + fill brackets first)

> Hi Elly,
>
> Thanks for the update — glad the IAB category list is in place, and the
> TAM-first sequencing works well on our end.
>
> On signals: we'll pass **IAB TCF v2.2** (v2.0 was sunset by IAB Europe in
> Nov 2023, so we've standardized on v2.2 — it's backward compatible, so no
> issue if your stack also accepts v2.0). Mechanically:
>
> - **TAM:** our CMP exposes the standard `__tcfapi`, so `apstag` will pick
>   up and forward the TC string automatically once we add your bidder to
>   our TAM config.
> - **Prebid Server:** we pass GDPR consent via `regs.ext.gdpr` +
>   `user.ext.consent`, plus US-privacy signaling [**confirm: US Privacy
>   string `regs.ext.us_privacy` and/or GPP `regs.gpp`/`gpp_sid`**] since
>   most of our audience is US. We can also pass a `schain` node — let me
>   know if you need our sellers.json entry for your seat.
>
> A couple of things from our side:
>
> - For **TAM**, could you send your **bidder short code / alias** so we
>   can register it and attribute delivery in reporting?
> - For **Prebid Server**, we'll be ready to review the endpoint + bidder
>   params as soon as you send them next week.
>
> Thanks,
> Roger

## References

- `docs/seller_comms.md` — seller/AM email-template patterns (tone, structure).
- `docs/confiant_blocklist.md` §"Reply pattern when an SSP follows up" —
  Amazon TAM contact (`ldurica@amazon.com`) and SSP-reply conventions.
- README "Magnite: switching from General to Prebid Analytics" +
  `Newsweek_TAM_Bidder_Reference.xlsx` — bidder short-code reference used to
  break Prebid/TAM metrics down by partner.

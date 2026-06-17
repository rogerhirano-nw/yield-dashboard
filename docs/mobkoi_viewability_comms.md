# Mobkoi viewability — outreach & internal comms

Communications around the Mobkoi Active View fix (root cause + the ad-unit
creative-wrapper fix). The **technical record** is the sibling debrief
`docs/mobkoi_viewability.md`; this file holds the **outbound / stakeholder
artifacts** and the decisions behind them. Created 2026-06-17 (PR #276).

**Status (2026-06-17).** Fix live across both Mobkoi supply paths (direct +
S2S Prebid) via the ad-unit creative wrapper. Internal stakeholder report
produced and delivered. Outreach email to Mobkoi — asking for the native
in-iframe render mode ("path 1" in the debrief) — drafted; the wrapper is the
standing fix until Mobkoi ships that, after which it can be retired.

---

## Internal stakeholder report (.docx)

A ~2-page **non-technical** report for RevOps / ad-ops / sales / leadership,
`Newsweek_Mobkoi_Viewability_Fix.docx`. Sections: Executive summary →
Background (the artifact) → Root cause → What we did (ad-unit creative
wrapper) → **Implementation detail** (what was changed in GAM + what the
wrapper code does, with a technical-specifics note) → Results (by supply path
+ S2S daily table) → Why it matters → Status & next steps (incl. the no-vCPM
caution) → Appendix (how we verified). It is the debrief content at a
non-technical altitude. Generated with `python-docx` (the binary `.docx` is
**not committed** — regenerate from this outline / the debrief). Delivered to
Roger 2026-06-17.

---

## Internal update email (S2S confirmation)

Short internal note announcing the S2S finding to the team.

> **Subject:** Mobkoi viewability — now measuring correctly across all supply paths (direct + S2S/Prebid)
>
> Team,
>
> Good-news update on the Mobkoi Active View viewability issue.
>
> **Background.** Mobkoi's high-impact units (interscroller/uniscroller) were
> reading near-0% viewable in GAM Active View — ~0.5% on the direct lines —
> despite healthy clicks and engagement. It was a measurement artifact, not a
> delivery problem: Mobkoi's renderer draws the ad in the page DOM outside the
> GAM-measured ad iframe, so Active View (and DoubleVerify) scored a hidden
> frame instead of the unit users actually see.
>
> **What we did.** We built a publisher-side "iframe mirror" that makes GAM
> measure the real on-screen unit, and deployed it as a creative wrapper at the
> **ad-unit level** on the affected in-article units. Because it's applied at
> the ad-unit level, it wraps **every** creative that serves on those units —
> not just our directly-trafficked Mobkoi lines, but Mobkoi's programmatic/S2S
> demand too.
>
> **Results.** Viewability is now measuring correctly across both Mobkoi supply
> paths:
> - Direct lines: from ~0.5% to the 30–70% range.
> - S2S Prebid (open auction): Jun 1–15 ~5–8% (artifact) → Jun 16 **45.5%** →
>   Jun 17 **68.6%** (partial day, toward our ~75% display baseline);
>   ~944k imps / $6.7k / $7.13 eCPM over the period, stable throughout.
>
> **Why it matters.** A single publisher-side fix now delivers accurate Active
> View on all Mobkoi inventory on those units — no vendor dependency. (We're
> still flagging the root cause to Mobkoi for a renderer-side fix; until that
> lands, the wrapper stays in place.)

---

## Mobkoi outreach email (the "fix it at source" ask)

To the Mobkoi AM / solutions team. Leads AM-facing (symptom → cause → our
workaround as proof → the ask → easy-win notes), then a **forwardable
"Technical detail — for your solutions/engineering team"** section. Drafted
2026-06-17; pending send.

> **Subject:** Mobkoi interscroller/uniscroller on Newsweek — Active View measurement (request for an in-iframe render mode)
>
> Hi [Mobkoi AM],
>
> We've been running your interscroller/uniscroller high-impact units on
> Newsweek and want to flag a viewability MEASUREMENT issue — and ask for a
> render-mode change on your side that would fix it at the source.
>
> **The symptom.** In Google Ad Manager, your units report ~0.4–0.6% Active
> View viewable at 100% measurable, while everything else on the same
> in-article slots measures ~75%. DoubleVerify shows the same near-zero. The
> engagement says the opposite: CTR is healthy and on some lines there are more
> clicks than "viewable" impressions — impossible if the ads were unseen. This
> is a measurement artifact, not a delivery problem; users clearly see and
> interact with the units.
>
> **The cause.** Your GAM tag boots the renderer inside GAM's friendly iframe,
> then builds the full-screen scroll-reveal by injecting a layer into the
> parent page DOM and hiding the original iframe. GAM Active View measures that
> hidden iframe — not the layer the user sees — so every impression scores
> measurable-but-not-viewable. DoubleVerify instruments the same GAM-served
> element, so it agrees.
>
> **What we did, and what it proves.** As an interim, publisher-side measure we
> deployed a wrapper that restyles the GAM iframe so it tracks the real
> on-screen unit, letting Active View measure what users actually see. The lift
> confirms the units are highly viewable:
> - Directly-trafficked lines moved from ~0.5% to the 30–70% range.
> - Our S2S Prebid (open-auction) Mobkoi demand stepped from ~5–8% to 45.5%
>   (Jun 16) and 68.6% (Jun 17), toward our ~75% display baseline.
>
> The only thing wrong was where the experience renders relative to the
> measured element.
>
> **The ask.** We'd like a build where the creative experience stays inside the
> GAM iframe and your loader restyles/resizes the iframe element itself
> (fixed/sticky + a parent clip container) for the scroll-reveal — rather than
> moving the content out of the iframe. Reaching into the parent DOM to restyle
> the iframe is fine; moving the content out is what breaks measurement. With
> that, GAM and DoubleVerify both measure the real geometry natively. We'd
> prefer not to maintain a publisher-side patch indefinitely, and a native
> in-iframe mode is the cleaner, auditable fix that benefits every publisher
> running these formats.
>
> Notes that should make this straightforward:
> - Your GAM tag is a thin bootstrap (a `boot/<uuid>` config), so we expect
>   this is a render-mode flag, not a re-trafficking job.
> - Active View's threshold for large creatives is 30% of pixels in view for
>   1s, so a scroll-through reveal passes comfortably once the iframe is the
>   measured element.
> - In-frame high-impact formats on our site (e.g. ClipCentric Center Stage)
>   already measure 58–67% organically — that's the bar.
>
> If helpful, point a test `boot/<uuid>` at the new mode and we'll traffic it
> on a test line item and send you the before/after Active View read.
>
> ———
> **Technical detail — for your solutions / engineering team**
>
> *How we diagnosed it.* We rendered on-site previews of the live creatives in
> headless Chromium (mobile emulation) and inspected the DOM:
> - Your loader sets the GAM creative iframe to `display:none` — it stays in the
>   DOM (it is not detached) — and renders the experience in a sibling container
>   (`div#mobkoi-…`) injected into the parent document.
> - That container's box is pixel-identical to the ad slot's box (the in-article
>   slot div, grown to a full viewport-height well, e.g. 390×844). So the slot
>   the page already exposes IS the unit's window.
> - GAM Active View — and DV, instrumenting the same element — measure the
>   hidden iframe, which never meets the in-view threshold → ~0% viewable at
>   100% measurable.
>
> *What our interim wrapper does (a reference for the native version).* A small
> parent-document script waits for the breakout signal (the creative iframe
> going `display:none`), then restyles that same iframe to `position:absolute`,
> 100%×100% of the slot, transparent and `pointer-events:none`, re-asserting
> briefly in case the tag restyles it. AV then measures the real well the user
> scrolls through. We restyle only the iframe (not its GPT container, which
> props the well height) and leave any in-iframe fallback banner untouched.
> Your Celtra render, your advertiser-side DV (`sid=mobkoi`), xpln analytics,
> consent macros, and click tracking are all unchanged.
>
> *What the native fix looks like on your side.* Keep the scroll-reveal inside
> the GAM creative iframe and apply the fixed/sticky positioning + clip
> behavior to the iframe element (and/or a parent clip container), instead of
> relocating the content into a parent-DOM layer.
>
> *Two things we ruled out, so you don't have to:*
> - GAM's view macro (`%%VIEW_URL_UNESC%%`) is delayed impression counting for
>   out-of-page creatives, not a viewability signal, and there's no API to
>   declare Active View viewable impressions — so rendering in the measured
>   element is the only lever. (We tested a parent-document watcher pinging the
>   macro; null result, as expected.)
> - Turning SafeFrame on doesn't help — it would break the unit, and it isn't
>   what governs which element AV measures here.
>
> Happy to share the exact wrapper snippet so your team can see precisely what
> we did, or to get on a call with engineering.

---

## Key decisions

- **Lead with our workaround as proof** the units are viewable (direct
  ~0.5%→30–70%; S2S ~5–8%→45.5%/68.6%), framing the issue as "where it
  renders," not "whether it's viewable."
- **AM-facing top + a forwardable "for your engineering team" section** — don't
  bury the ask under technical depth.
- **Pre-empt "why fix it if you've patched it":** we won't maintain a publisher
  patch indefinitely; native in-iframe is the cleaner, auditable fix for all
  their publishers.
- **Offer the exact wrapper snippet on request**, not pasted inline (keeps the
  email readable; hand it over when their engineer engages). Snippet lives at
  `docs/snippets/mobkoi_iframe_mirror_creative.html`.
- The S2S numbers are attributed to **our ad-unit wrapper**, not to anything
  Mobkoi has shipped — that's the point of the ask.

/**
 * Blank-slot detect + refresh for dfp-ad-sticky.
 *
 * Why this exists: Ogury wins the sticky slot and, on ~8 of 10 impressions,
 * its creative never paints. GPT creates the ad iframe hidden and reveals it
 * when the creative renders, so a slot that never rendered is left holding a
 * 0x0 display:none `google_ads_iframe_*` and nothing else. The impression is
 * counted, the user sees nothing, and Active View scores it non-viewable —
 * correctly. See docs/prebid_viewability.md for the full diagnosis.
 *
 * What this does: after a slot renders, wait, and if the AV-measured iframe
 * is still hidden or zero-sized, run a fresh auction for that slot so a
 * bidder who actually renders fills it.
 *
 * What this does NOT do: it cannot un-count the blank impression GAM already
 * booked. It converts a dead impression into a second, live one — the slot
 * blends toward (0 + viewable)/2 rather than becoming healthy. The real fix
 * is the creative rendering.
 *
 * Two rules that matter, both learned the hard way:
 *
 *  1. EXCLUDE THE BIDDER THAT JUST BLANKED from the retry. The failure is
 *     page-consistent — the sticky slot refreshes twice per pageview and
 *     both renders share the outcome — so a refresh that re-serves the same
 *     bidder just blanks again. Without the exclusion this is a no-op.
 *  2. Only ever fire on a DETECTED BLANK. This must not drift into a general
 *     refresh: that changes the ad experience, inflates impressions, and
 *     invites a policy problem. Blank-detection is the whole justification.
 */

(function () {
  var SLOT_ID = 'dfp-ad-sticky';
  var CHECK_DELAY_MS = 2000;   // creatives that do paint are up well before this
  var MAX_RETRIES = 1;         // one recovery attempt per pageview

  var retries = 0;
  var blockedBidders = [];     // bidders observed blanking on THIS pageview

  function measuredIframe(slotEl) {
    // Only the GPT-served frame matters: it is what Active View measures.
    // Vendor frames (e.g. ogy-iframe-*) can be present and visible while the
    // measured one is still hidden.
    var frames = slotEl.querySelectorAll('iframe[id^="google_ads_iframe_"]');
    return frames.length ? frames[frames.length - 1] : null;
  }

  function isBlank(slotEl) {
    var f = measuredIframe(slotEl);
    if (!f) return true;                       // nothing served at all
    var cs = window.getComputedStyle(f);
    if (cs.display === 'none' || cs.visibility === 'hidden') return true;
    var r = f.getBoundingClientRect();
    return r.width < 2 || r.height < 2;
  }

  function winningBidder(slotId) {
    try {
      var wins = (window.pbjs && pbjs.getAllWinningBids && pbjs.getAllWinningBids()) || [];
      for (var i = wins.length - 1; i >= 0; i--) {
        if (wins[i].adUnitCode === slotId) return wins[i].bidder;
      }
    } catch (e) {}
    return null;
  }

  window.googletag = window.googletag || { cmd: [] };
  googletag.cmd.push(function () {
    googletag.pubads().addEventListener('slotRenderEnded', function (ev) {
      var slot = ev.slot;
      if (slot.getSlotElementId() !== SLOT_ID) return;
      if (ev.isEmpty) return;                  // unfilled is a different case
      if (retries >= MAX_RETRIES) return;

      window.setTimeout(function () {
        var el = document.getElementById(SLOT_ID);
        if (!el || !isBlank(el)) return;       // it painted — nothing to do

        var bidder = winningBidder(SLOT_ID);
        if (bidder && blockedBidders.indexOf(bidder) === -1) {
          blockedBidders.push(bidder);
        }
        retries += 1;

        // Keep the blanking bidder out of the retry auction. Prebid's own
        // bidder filter is the least invasive way to express this; if the
        // wrapper build lacks it, drop the bidder from the ad unit's bids
        // array for this refresh instead.
        try {
          if (window.pbjs && pbjs.requestBids) {
            pbjs.requestBids({
              adUnitCodes: [SLOT_ID],
              bidders: (pbjs.adUnits || [])
                .filter(function (u) { return u.code === SLOT_ID; })
                .reduce(function (acc, u) {
                  (u.bids || []).forEach(function (b) {
                    if (blockedBidders.indexOf(b.bidder) === -1 &&
                        acc.indexOf(b.bidder) === -1) acc.push(b.bidder);
                  });
                  return acc;
                }, []),
              bidsBackHandler: function () {
                try { pbjs.setTargetingForGPTAsync([SLOT_ID]); } catch (e) {}
                googletag.pubads().refresh([slot]);
              }
            });
            return;
          }
        } catch (e) {}

        googletag.pubads().refresh([slot]);    // last resort: plain refresh
      }, CHECK_DELAY_MS);
    });
  });
})();

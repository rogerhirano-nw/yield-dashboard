/*
 * Newsweek — Publisher Provided Signals (PPS): contextual content signals
 * ---------------------------------------------------------------------------
 * WHAT: Maps the page's editorial section/topic to IAB Content Taxonomy 2.2
 *       category IDs and passes them to programmatic buyers via
 *       googletag.setConfig({ pps: { taxonomies: { IAB_CONTENT_2_2: ... } } }).
 *
 *       This is the "Path B" (pass-at-ad-request-time, client-side) PPS route.
 *       It is an alternative to the GAM-UI mapping route (Signals → Publisher
 *       provided signals); use ONE, not both, for a given signal type.
 *
 * WHERE: On every Newsweek page, inside googletag.cmd, BEFORE the first ad
 *        request — i.e. before pubads().refresh() or the initial
 *        googletag.display(). setConfig is read at request time, so a late call
 *        misses the slots already requested.
 *
 * WIRE-UP (the one thing engineering must point at our CMS):
 *   `resolveSections()` below tries several common sources for the article's
 *   primary section/topic. Confirm which one Newsweek actually exposes on the
 *   page and keep (or tighten to) that branch. Everything else is ready.
 *
 * NOTE ON IDS: the SECTION_TO_IAB values are IAB Content Taxonomy 2.2 "Unique
 *   ID" integers (passed to GPT as strings). Tier-1 ids are the safe baseline;
 *   tier-2 ids (commented) sharpen the signal where our sections are specific.
 *   Verify against the canonical sheet before launch:
 *   https://github.com/InteractiveAdvertisingBureau/Taxonomies
 *   ("Content Taxonomies/Content Taxonomy 2.2.tsv").
 */
(function () {
  'use strict';

  /* Newsweek editorial section/topic (normalized slug) → IAB Content
   * Taxonomy 2.2 category ID(s). Best-fit mapping — REVIEW before launch.
   * Keys are lowercased, non-alphanumerics collapsed to '-' (see normalize()). */
  var SECTION_TO_IAB = {
    // News & opinion → "News and Politics" (379)
    'news':            ['379'],
    'world':           ['379'],
    'us':              ['379'],
    'u-s':             ['379'],
    'opinion':         ['379'],
    'my-turn':         ['379'],
    'fact-check':      ['379'],
    'politics':        ['379', '386'],          // 386 = Politics
    'elections':       ['379', '387'],          // 387 = Elections
    'crime':           ['379', '380'],          // 380 = Crime

    // Business / money → "Business and Finance" (52) / "Personal Finance" (391)
    'business':        ['52', '53'],            // 53 = Business
    'economy':         ['52', '80'],            // 80 = Economy
    'money':           ['391'],                 // Personal Finance
    'personal-finance':['391'],

    // Tech & science → "Technology & Computing" (596) / "Science" (464)
    'tech-and-science':['596', '464'],
    'tech':            ['596'],
    'technology':      ['596'],
    'ai':              ['596'],
    'science':         ['464'],
    'space':           ['464', '472'],          // 472 = Space and Astronomy

    // Health → "Medical Health" (286) + "Healthy Living" (223)
    'health':          ['286', '223'],
    'wellness':        ['223', '232'],          // 232 = Wellness

    // Sports → "Sports" (483)
    'sports':          ['483'],
    'nfl':             ['483', '484'],          // 484 = American Football
    'soccer':          ['483', '533'],          // 533 = Soccer
    'nba':             ['483', '547'],          // 547 = Basketball

    // Culture / entertainment → "Pop Culture" (432) / "Movies" (324)
    'culture':         ['432'],
    'entertainment':   ['432', '324'],
    'movies':          ['324'],
    'tv':              ['432'],
    'music':           ['432'],
    'gaming':          ['680'],                 // Video Gaming

    // Lifestyle verticals
    'autos':           ['1'],                   // Automotive
    'cars':            ['1'],
    'auto':            ['1'],
    'travel':          ['653'],                 // Travel
    'food':            ['210'],                 // Food & Drink
    'fashion':         ['552'],                 // Style & Fashion
    'style':           ['552'],
    'education':       ['132'],                 // Education
    'real-estate':     ['441'],                 // Real Estate
    'religion':        ['453']                  // Religion & Spirituality
  };

  function normalize(s) {
    return String(s || '')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');
  }

  /* Resolve the page's section/topic string(s). Returns an array (a page can
   * carry more than one tag). Tries, in order:
   *   1. window.NW_PAGE.section / .topics   — if our CMS exposes a page object
   *   2. dataLayer  { nw_section / section / contentSection }
   *   3. <meta property="article:section">  + <meta name="keywords"> tags
   *   4. <body data-section> / <html data-section>
   *   5. first URL path segment (e.g. /sports/... ) as a last-resort fallback
   * KEEP whichever branch matches Newsweek's real markup; drop the rest. */
  function resolveSections() {
    var out = [];
    try {
      // 1. CMS-provided page object (preferred — set this server-side)
      var P = window.NW_PAGE || window.newsweek || null;
      if (P) {
        if (P.section) out.push(P.section);
        if (P.primarySection) out.push(P.primarySection);
        if (Array.isArray(P.topics)) out = out.concat(P.topics);
      }

      // 2. GTM dataLayer
      var dl = window.dataLayer;
      if (Array.isArray(dl)) {
        for (var i = 0; i < dl.length; i++) {
          var e = dl[i] || {};
          if (e.nw_section) out.push(e.nw_section);
          if (e.section) out.push(e.section);
          if (e.contentSection) out.push(e.contentSection);
        }
      }

      // 3. <meta> tags
      var d = document;
      var ms = d.querySelector('meta[property="article:section"]');
      if (ms && ms.content) out.push(ms.content);
      var mk = d.querySelector('meta[name="keywords"]');
      if (mk && mk.content) out = out.concat(mk.content.split(','));

      // 4. data attribute on <body>/<html>
      var ds = (d.body && d.body.getAttribute('data-section')) ||
               (d.documentElement && d.documentElement.getAttribute('data-section'));
      if (ds) out.push(ds);

      // 5. URL path fallback
      if (!out.length) {
        var seg = (location.pathname || '').split('/').filter(Boolean)[0];
        if (seg) out.push(seg);
      }
    } catch (e) { /* never let signal-gathering break the page */ }
    return out;
  }

  function contentIds() {
    var ids = {};
    var sections = resolveSections();
    for (var i = 0; i < sections.length; i++) {
      var key = normalize(sections[i]);
      var mapped = SECTION_TO_IAB[key];
      if (mapped) {
        for (var j = 0; j < mapped.length; j++) ids[mapped[j]] = 1;
      }
    }
    return Object.keys(ids);
  }

  window.googletag = window.googletag || { cmd: [] };
  googletag.cmd.push(function () {
    try {
      var content = contentIds();
      if (!content.length) return;              // nothing mapped → send nothing
      var taxonomies = { 'IAB_CONTENT_2_2': { values: content } };

      /* OPTIONAL — audience signals (IAB Audience Taxonomy 1.1).
       * Only send on personalization-allowed requests, and only once we have a
       * real first-party audience→IAB_AUDIENCE_1_1 lookup. Disabled by default.
       *
       * var audience = resolveAudienceIds();   // your segment → IAB 1.1 ids
       * if (audience.length && adsPersonalizationAllowed()) {
       *   taxonomies['IAB_AUDIENCE_1_1'] = { values: audience };
       * }
       */

      googletag.setConfig({ pps: { taxonomies: taxonomies } });

      // Optional: surface what we sent for QA via the GPT console / dataLayer.
      try {
        (window.dataLayer = window.dataLayer || []).push(
          { event: 'nw_pps_set', pps_content_2_2: content });
      } catch (x) {}
    } catch (e) { /* swallow — PPS is best-effort, never block ad serving */ }
  });
})();

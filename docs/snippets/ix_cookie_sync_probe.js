/*
 * IX / Prebid Server cookie-sync probe — paste into the DevTools console on a
 * live newsweek.com ARTICLE page (not a section front), after the page has
 * finished loading.
 *
 * Answers, in one paste:
 *   1. Is `ix` in the s2s bidder list at all?
 *   2. Does the page allow IFRAME user-syncs, or only image/redirect?
 *      (Prebid.js defaults to image-only — iframe is off unless explicitly enabled.)
 *   3. Did a cookie_sync / IX usermatch / setuid call actually fire this pageview?
 *   4. THE DECISIVE TEST — re-issues /cookie_sync twice: once with the page's
 *      real filterSettings, once with iframe additionally allowed. If `ix` is
 *      absent from the first and present in the second, the root cause is
 *      confirmed: IX's sync is iframe-type and our wrapper filters it out.
 *
 * Nothing is written or changed — it reads config and repeats a call the page
 * already makes. Copy the whole output back to RevOps.
 *
 * Context: docs/index_pbs_cookie_sync.md
 */
(async () => {
  const log = (...a) => console.log(...a);
  const J = (o) => { try { return JSON.parse(JSON.stringify(o)); } catch (e) { return String(o); } };

  // ---- locate the Prebid global (RDM may not use the default `pbjs`) --------
  let G = null, GNAME = null;
  for (const k of ['pbjs', 'dmpbjs', 'pbjsMagnite'].concat(Object.getOwnPropertyNames(window))) {
    try {
      const v = window[k];
      if (v && typeof v.getConfig === 'function' && (v.version || v.adUnits)) { G = v; GNAME = k; break; }
    } catch (e) {}
  }
  if (!G) {
    log('%cNo Prebid global found on this page.', 'color:#c41608;font-weight:700');
    log('Either the wrapper has not loaded yet (wait and re-run), an ad blocker is active, or this page has no header bidding.');
    return;
  }

  const report = { page: location.href, pbjsGlobal: GNAME, version: G.version };

  // ---- s2s config ----------------------------------------------------------
  let s2s = null;
  try { s2s = G.getConfig('s2sConfig'); } catch (e) {}
  const s2sList = Array.isArray(s2s) ? s2s : (s2s ? [s2s] : []);
  report.s2sConfigs = s2sList.map((c) => ({
    accountId: c.accountId, endpoint: c.endpoint, syncEndpoint: c.syncEndpoint,
    defaultVendor: c.defaultVendor, enabled: c.enabled, timeout: c.timeout,
    bidders: c.bidders, ixInBidders: (c.bidders || []).some((b) => /^ix$/i.test(b)),
  }));

  // ---- user-sync config — the iframe question ------------------------------
  let us = null;
  try { us = G.getConfig('userSync'); } catch (e) {}
  const fs = (us && us.filterSettings) || null;
  report.userSync = {
    syncEnabled: us && us.syncEnabled, syncsPerBidder: us && us.syncsPerBidder,
    aliasSyncEnabled: us && us.aliasSyncEnabled, filterSettings: J(fs),
    iframeAllowed: !!(fs && (fs.iframe || fs.all)),
    imageAllowed: !!(fs && (fs.image || fs.all)),
  };

  // ---- did anything actually fire this pageview? ---------------------------
  const res = performance.getEntriesByType('resource').map((e) => e.name);
  const grab = (re) => res.filter((n) => re.test(n));
  report.observedThisPageview = {
    cookie_sync: grab(/cookie[_-]?sync/i),
    ix_usermatch: grab(/casalemedia|ssum\./i),
    setuid: grab(/\/setuid/i),
  };

  // ---- the decisive test ---------------------------------------------------
  const cfg = s2sList[0];
  const syncUrl = cfg && (typeof cfg.syncEndpoint === 'string'
    ? cfg.syncEndpoint
    : (cfg.syncEndpoint && (cfg.syncEndpoint.p1Consent || cfg.syncEndpoint.noP1Consent)));

  async function askPbs(label, filterSettings) {
    if (!syncUrl) return { label, error: 'no syncEndpoint in s2sConfig' };
    const body = {
      bidders: (cfg.bidders || []), account: cfg.accountId, limit: 20,
      coopSync: true, gdpr: 0, gdpr_consent: '', us_privacy: '', filterSettings,
    };
    try {
      const r = await fetch(syncUrl, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'text/plain' }, body: JSON.stringify(body),
      });
      const txt = await r.text();
      let js = null; try { js = JSON.parse(txt); } catch (e) {}
      const list = (js && (js.bidder_status || js.bidders)) || [];
      const ix = list.find((b) => /^ix$/i.test(b.bidder || b.no_cookie || ''));
      return {
        label, httpStatus: r.status, pbsStatus: js && js.status,
        biddersReturned: list.map((b) => b.bidder),
        ixEntry: ix ? J(ix) : null,
        ixSyncType: ix && ix.usersync ? ix.usersync.type : null,
        ixSyncUrl: ix && ix.usersync ? ix.usersync.url : null,
        raw: js ? undefined : txt.slice(0, 500),
      };
    } catch (e) { return { label, error: String(e) }; }
  }

  const asIs = await askPbs('A · page\'s own filterSettings', fs || { image: { bidders: '*', filter: 'include' } });
  const withIframe = await askPbs('B · iframe additionally allowed', {
    image: { bidders: '*', filter: 'include' },
    iframe: { bidders: '*', filter: 'include' },
  });
  report.cookieSyncProbe = { A_asConfigured: asIs, B_iframeAllowed: withIframe };

  // ---- verdict -------------------------------------------------------------
  let verdict;
  if (!report.s2sConfigs.some((c) => c.ixInBidders)) {
    verdict = 'ix is NOT in the s2s bidder list — Index is not being called server-side at all.';
  } else if (!asIs.ixEntry && withIframe.ixEntry) {
    verdict = 'CONFIRMED: PBS returns an ix sync ONLY when iframe syncs are allowed. '
            + 'IX is iframe-only and our userSync.filterSettings filters it out. '
            + 'Fix is a wrapper config change (allow iframe for ix), not an ix.yaml change.';
  } else if (!asIs.ixEntry && !withIframe.ixEntry) {
    verdict = 'PBS returns NO ix sync under either filter — the ix userSync block is missing/empty '
            + 'on the PBS host, or ix is excluded from cookie-sync. That is Magnite\'s to fix.';
  } else if (asIs.ixEntry) {
    verdict = 'PBS DOES return an ix sync (type: ' + asIs.ixSyncType + '). The sync is being offered, '
            + 'so check whether it actually fires and 302s back to /setuid (see observedThisPageview), '
            + 'and whether the s= id in ixSyncUrl matches what Index provisioned.';
  }
  report.verdict = verdict;

  log('%c=== IX / PBS cookie-sync probe ===', 'font-weight:700;font-size:13px');
  log(report);
  log('%cVERDICT: ' + verdict, 'font-weight:700;color:#c41608');
  log('Copy-paste JSON below:\n' + JSON.stringify(report, null, 2));
  try { copy(JSON.stringify(report, null, 2)); log('(copied to clipboard)'); } catch (e) {}
  return report;
})();

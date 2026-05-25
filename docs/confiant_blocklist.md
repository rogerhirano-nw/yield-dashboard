# Confiant -> GAM blocklist

Weekly job: parse the Confiant "Alert Log CSV By Provider" export, pick the
Google-served bad creatives, append their landing-page domains to a named GAM
Protection's "Advertiser URLs" field.

## Why this is a local-only Playwright job, not a GitHub Actions cron

GAM does not expose Protections (the resource that holds advertiser-URL block
rules) through any API surface — not the modern `admanager_v1` REST client, not
the legacy `googleads` SOAP client, not the Authorized Buyers RTB API. This is
a long-standing gap that Google has not closed; see the [community thread][1].
The only programmatic path is to drive the web UI.

Driving the web UI requires a logged-in Google session with 2FA. That can't be
done from a headless GitHub Actions runner without violating Google's ToS
(stored credentials, automated 2FA), so the job runs locally via launchd on a
Mac that's already authenticated to GAM through a normal browser login.

Tradeoffs you're accepting:
- The Mac must be on (or wake up via Power Nap) for the scheduled run to fire.
- Google occasionally invalidates the saved session; you'll get a failure
  email and need to re-run with `--inspect` once to log in again.
- When Google ships a Protections UI change, the selectors in
  `gam_blocklist_ui.py` break; the script aborts loudly (won't paste into the
  wrong field) and you update `_SELECTORS` and re-run.

[1]: https://support.google.com/admanager/thread/7512693?hl=en

## First-time setup

```bash
cd ~/code/yield-dashboard
pip install -r requirements.txt
python -m playwright install chromium

# 1. Open a browser, log into GAM manually, complete 2FA.
#    This populates ~/.confiant-blocklist/playwright-profile/ with cookies.
export GAM_NETWORK_ID=<your network id>
python confiant_blocklist.py --inspect

# 2. Identify the target Protection ID. In GAM > Delivery > Protections,
#    click into the Protection — the URL ends with .../protection_id=<id>.
#    For Newsweek today: 28044902 ("Everything", prod catch-all).

# 3. Sanity-check what's already in that Protection BEFORE we modify it.
#    --print-existing opens the browser, reads the Advertiser URLs textarea,
#    prints it to stdout, and exits. No CSV processed, no writes.
python confiant_blocklist.py --protection-id 28044902 --print-existing \
    > existing_urls_before.txt
wc -l existing_urls_before.txt

# 4. Test in dry-run with a known-good CSV.
python confiant_blocklist.py \
    --csv ~/Downloads/Alert\ Log\ CSV\ By\ Provider_*.csv \
    --protection-id 28044902 --protection-label Everything \
    --dry-run

# 5. Real run on the same CSV (browser will open). Add --debug for screenshots.
python confiant_blocklist.py \
    --csv ~/Downloads/Alert\ Log\ CSV\ By\ Provider_*.csv \
    --protection-id 28044902 --protection-label Everything \
    --debug
```

## Protection target

The script navigates *directly* to the Protection detail page via its ID, not
by clicking on a name link. This avoids the most fragile selector in the flow.
If GAM ever ships a routing change that breaks the URL format, override it
without code changes:

```bash
export GAM_PROTECTION_DETAIL_URL_FMT='https://admanager.google.com/{network_id}#delivery/protections/<new format>/{protection_id}'
```

`--protection-label` is for emails and the state table only — useful to make
weekly notifications read "Confiant -> GAM blocklist (Everything)" instead of
"(Protection #28044902)". The script never searches by label.

## Wiring up the weekly launchd cron

1. Forward Confiant's weekly CSV email to your agentmail inbox so the script
   can pull it unattended.
2. Copy the plist template and fill in the four `REPLACE_ME` values:
   `GAM_NETWORK_ID`, `AGENTMAIL_API_KEY`, `AGENTMAIL_INBOX_ID`,
   `CONFIANT_REPORT_TO_EMAIL`, and `--protection-name`.
   ```bash
   cp ~/code/yield-dashboard/.launchd/com.newsweek.confiant-blocklist.plist \
      ~/Library/LaunchAgents/
   # edit the copy in ~/Library/LaunchAgents
   ```
3. Load:
   ```bash
   launchctl load -w ~/Library/LaunchAgents/com.newsweek.confiant-blocklist.plist
   ```
4. Trigger a one-off run to verify:
   ```bash
   launchctl start com.newsweek.confiant-blocklist
   tail -f ~/.confiant-blocklist/launchd.err.log
   ```

## What gets emailed weekly

A run summary that includes:
- Counts: total Google rows, blockable domains, already-in-state, new
  domains pushed to GAM, cloaked rows skipped.
- Full list of new domains added to the Protection, with issue type.
- Cloaked rows (Confiant-internal IDs like `ID 17830`) grouped by issue type
  with their adtrace URLs, for manual review.

Failed runs are flagged in the subject line and include the exception in the
body. Dry-run summaries say "(DRY RUN)" in the subject and show what *would*
have been pushed.

## State

- `~/.confiant-blocklist/state.sqlite` — `blocked_domains` (one row per domain
  ever pushed) + `runs` (one row per invocation).
- `~/.confiant-blocklist/playwright-profile/` — Chromium profile with the
  logged-in Google session.
- `~/.confiant-blocklist/csv-cache/` — CSVs pulled from agentmail.
- `~/.confiant-blocklist/launchd.{out,err}.log` — launchd stdio.

None of these are committed; the script auto-creates them.

## When Google changes the Protections UI

The script aborts with a `RuntimeError` and a hint about which selector
failed. To fix:

```bash
python confiant_blocklist.py --inspect
# Open dev tools, find the new selector for the failing element
# Edit gam_blocklist_ui.py:_SELECTORS, then re-run
```

The `--debug` flag saves `debug-pre-save.png` and `debug-final.png` in the
profile dir, which is the fastest way to see what state the page was in when
something broke.

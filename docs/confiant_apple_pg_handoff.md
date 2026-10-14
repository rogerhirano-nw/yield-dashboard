# Handoff: Confiant cron blocked Apple PG creatives

Paste the prompt below into Claude Code on the laptop that runs the
Confiant blocklist cron, from the `~/code/yield-dashboard` checkout.

---

Investigating an incident with the Confiant → GAM blocklist cron that runs
on this laptop (launchd `com.newsweek.confiant-blocklist`, 04:00 ET). In
September it blocked Apple PG creatives in GAM Ad Review Center.

Already established (from a cloud session, see PR
rogerhirano-nw/yield-dashboard#408 and `docs/confiant_blocklist.md`):
- Confiant never flagged any Apple ad. Confiant API, Aug 15 – Oct 14: all 182
  Google cloaked-by-ID flags (87 creatives) came from the open-exchange order
  `Newsweek_OpenExchange_GoogleAdExchange` (3670823949). They were MediaGo
  native ads plus one TTD/citymd.com ad. None came from any Apple PG order or
  line item.
- Nobody blocked them by hand at 4am. The cause is
  `gam_arc.block_in_arc`: it clicked the first "Block ad" button after a
  fixed 8s, which on a slow filter was the top of the unfiltered feed
  (high-volume Apple PG creatives).

Please do this, read-only except step 1:
1. Stop the job until the fix is in:
   `launchctl unload ~/Library/LaunchAgents/com.newsweek.confiant-blocklist.plist`
2. Confirm from this machine's records. Show me:
   - `sqlite3 -header -column ~/.confiant-blocklist/state.sqlite "SELECT first_pushed_to_gam, issue_type FROM blocked_domains WHERE domain LIKE 'gam-arc:%' ORDER BY 1;"`
   - every `ARC Phase 2`, `Confiant <id> → GPT … → BLOCKED` and `ARC phase done`
     line in `~/.confiant-blocklist/launchd.err.log` (and `launchd.out.log`)
   - any `--debug` screenshots in `~/.confiant-blocklist/playwright-profile/`
     from those mornings
   For each recorded block, list the date, the Confiant ID and the GPT
   response ID. I'll match the dates against when the Apple creatives were
   blocked in ARC → Blocked ads.
3. `git fetch origin && git checkout claude/elegant-darwin-4klxrn`
   (or `git pull` on main once PR #408 merges), then run
   `python -m pytest tests/test_gam_arc.py`.
4. Reload the job: `launchctl load -w ~/Library/LaunchAgents/com.newsweek.confiant-blocklist.plist`.
   The ARC phase is now off unless `--arc` is passed, so the nightly run only
   pushes domain blocks to the "Everything" Protection.
5. Don't re-enable `--arc` until one watched `--arc --debug` run (browser
   visible) shows a block landing on the correct card.

Separately: unblock the Apple creatives in GAM → Ad Review Center →
Blocked ads.

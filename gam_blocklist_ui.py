"""
GAMBlocklistBrowser — Playwright automation against the GAM Protections UI.

Why this exists: GAM does not expose Protections (the resource that holds
advertiser-URL block rules) through the SOAP or REST API. The only way to
manage them programmatically is to drive the web UI.

Caveats this code accepts up-front:
  * Selectors target the current admanager.google.com Protections UI. Google
    ships UI changes; expect to update _SELECTORS after roughly every quarterly
    UI revision. When selectors break the script aborts loudly rather than
    pasting into the wrong field.
  * The protection-detail URL format is also UI-dependent. It's overridable via
    GAM_PROTECTION_DETAIL_URL_FMT in case Google ships a routing change.
  * Google login + 2FA cannot be automated unattended. The script uses a
    persistent browser profile (cookies + storage saved to disk). First run
    requires a human to complete login; subsequent runs reuse the session
    until Google invalidates it (typically every few weeks).
  * Cannot run in headless GitHub Actions for the same reason. This is a
    local-only tool meant to be triggered by launchd on your Mac.

Flow (append_to_protection):
  Page:    admanager.google.com/<network>/#delivery/protections/detail/protection_id=<id>
  Expand "Ad content" section if collapsed
  Find "Advertiser URLs" field -> append newline-separated domains
  Save -> wait for toast

Adjust _SELECTORS or GAM_PROTECTION_DETAIL_URL_FMT if Google changes the UI.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path


# ── selectors ─────────────────────────────────────────────────────────────────
# Update these when Google reshuffles the Protections UI. Each is documented
# with what it should match. Prefer role/text-based locators over CSS classes —
# Google's class names are auto-generated and rotate often.

_SELECTORS = {
    # "Ad content" section header on the protection detail page; we click it
    # to expand the section if it's collapsed.
    "ad_content_section":
        "text=/^Ad content$/",

    # The "Advertiser URLs" field. GAM renders this as a chip input or a
    # textarea depending on entry count. The textarea selector should work
    # in both cases — its visible label is "Advertiser URLs".
    "advertiser_urls_textarea":
        "textarea[aria-label='Advertiser URLs'], "
        "div[aria-label='Advertiser URLs'] textarea, "
        "label:has-text('Advertiser URLs') ~ * textarea",

    # The Save button at the bottom of the protection detail page.
    "save_button":
        "button:has-text('Save'):not([disabled])",

    # Save confirmation toast.
    "save_toast":
        "text=/saved|updated successfully/i",

    # Detects the Google login redirect — if we land on accounts.google.com
    # the session expired and the user needs to log in manually.
    "login_redirect_marker":
        "input[type='email'], [aria-label='Email or phone']",
}


# URL format for the Protection detail page. Override via env var if Google
# ships a routing change (e.g. drops the `detail/` segment, switches to a
# query-string style, etc.). The braces are str.format() placeholders.
_PROTECTION_DETAIL_URL_FMT = os.environ.get(
    "GAM_PROTECTION_DETAIL_URL_FMT",
    "https://admanager.google.com/{network_id}#delivery/protections/detail/protection_id={protection_id}",
)


@dataclass
class GAMBlocklistBrowser:
    profile_dir: Path
    network_id: str
    headless: bool = False
    debug: bool = False
    nav_timeout_ms: int = 30_000

    def __post_init__(self) -> None:
        self.profile_dir = Path(self.profile_dir).expanduser()
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    # ── public methods ────────────────────────────────────────────────────────

    def append_to_protection(self, protection_id: int, new_domains: list[str]) -> int:
        """Open the Protection in GAM and append `new_domains` to its
        Advertiser URLs list. Returns the count of domains actually written
        (post-dedupe within this batch).

        Raises RuntimeError on any selector miss, login redirect, or save
        failure — the caller is expected to surface this in the failure email.
        """
        if not new_domains:
            return 0

        from playwright.sync_api import TimeoutError as PWTimeout

        payload = "\n".join(sorted(set(new_domains)))

        with self._open_protection_page(protection_id) as (page, ctx):
            textarea = self._find_advertiser_urls(page)

            existing = (textarea.input_value() or "").strip()
            suffix = ("\n" if existing else "") + payload
            textarea.focus()
            textarea.press("End")
            textarea.type(suffix, delay=5)
            self._sleep("post-type settle", 1.0)

            if self.debug:
                page.screenshot(path=str(self.profile_dir / "debug-pre-save.png"), full_page=True)

            save = page.locator(_SELECTORS["save_button"]).first
            if not save.count():
                raise RuntimeError(
                    "Save button not found / disabled after entering new "
                    "domains. The field may have rejected one of the values; "
                    "inspect debug-pre-save.png in the profile dir."
                )
            save.click()

            try:
                page.locator(_SELECTORS["save_toast"]).first.wait_for(timeout=15_000)
            except PWTimeout:
                raise RuntimeError(
                    "Clicked Save but no confirmation toast appeared within 15s. "
                    "Treat as failure and verify the protection in GAM before "
                    "the next run."
                )

            return len(set(new_domains))

    def read_existing_urls(self, protection_id: int) -> list[str]:
        """Open the Protection and return the current Advertiser URLs as a
        list (one entry per line, blanks stripped). No modification."""
        with self._open_protection_page(protection_id) as (page, _):
            textarea = self._find_advertiser_urls(page)
            raw = textarea.input_value() or ""
            return [line.strip() for line in raw.splitlines() if line.strip()]

    def inspect(self, protection_id: int | None = None) -> None:
        """Open the GAM Protections page in a visible browser and wait. Use
        this for first-time login and for verifying/updating selectors after
        a Google UI change. If protection_id is given, lands on that detail
        page directly; otherwise lands on the protections list."""
        from playwright.sync_api import sync_playwright

        if protection_id is not None:
            url = _PROTECTION_DETAIL_URL_FMT.format(
                network_id=self.network_id, protection_id=protection_id
            )
        else:
            url = f"https://admanager.google.com/{self.network_id}#delivery/protections"

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=False,
            )
            page = ctx.new_page()
            page.goto(url)
            print(
                f"Inspect mode: browser open at {url}\n"
                f"Profile dir: {self.profile_dir}\n"
                f"Log in (and complete 2FA) if needed; verify selectors against "
                f"the Protection detail page; then close the browser window."
            )
            try:
                page.wait_for_event("close", timeout=0)
            except Exception:
                pass
            ctx.close()

    # ── internals ─────────────────────────────────────────────────────────────

    def _open_protection_page(self, protection_id: int):
        """Context manager that launches the persistent Chromium context,
        navigates to the Protection detail page, checks for login redirect,
        expands the Ad content section, and yields (page, ctx). Closes the
        context on exit, capturing a final screenshot in debug mode.
        """
        from contextlib import contextmanager
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        url = _PROTECTION_DETAIL_URL_FMT.format(
            network_id=self.network_id, protection_id=protection_id
        )

        @contextmanager
        def _cm():
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = ctx.new_page()
                page.set_default_timeout(self.nav_timeout_ms)
                try:
                    page.goto(url, wait_until="domcontentloaded")
                    self._sleep("post-nav settle", 3.0)

                    if page.locator(_SELECTORS["login_redirect_marker"]).count():
                        raise RuntimeError(
                            "GAM session expired — Google login screen detected. "
                            "Re-run with --inspect to log in manually and re-"
                            f"establish the profile at {self.profile_dir}."
                        )

                    # Expand the Ad content section if it's collapsed.
                    section = page.locator(_SELECTORS["ad_content_section"]).first
                    if section.count():
                        try:
                            section.click()
                        except PWTimeout:
                            pass  # already expanded
                    self._sleep("section expand", 1.0)

                    yield page, ctx
                finally:
                    if self.debug:
                        page.screenshot(
                            path=str(self.profile_dir / "debug-final.png"),
                            full_page=True,
                        )
                    ctx.close()

        return _cm()

    def _find_advertiser_urls(self, page):
        """Locate the Advertiser URLs field on a Protection detail page.
        Raises if not found — the caller turns this into a failure email."""
        textarea = page.locator(_SELECTORS["advertiser_urls_textarea"]).first
        if not textarea.count():
            raise RuntimeError(
                "Could not locate the 'Advertiser URLs' field on the "
                "Protection detail page. Google likely changed the UI; "
                "update _SELECTORS['advertiser_urls_textarea'] in "
                "gam_blocklist_ui.py. Run with --inspect to find the new "
                "selector."
            )
        return textarea

    def _sleep(self, label: str, seconds: float) -> None:
        if self.debug:
            print(f"  [browser] sleep {seconds:.1f}s ({label})", file=sys.stderr)
        time.sleep(seconds)


def default_profile_dir() -> Path:
    return Path(os.environ.get(
        "CONFIANT_BLOCKLIST_PROFILE_DIR",
        "~/.confiant-blocklist/playwright-profile",
    )).expanduser()

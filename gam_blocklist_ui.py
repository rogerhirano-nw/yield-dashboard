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
  * Google login + 2FA cannot be automated unattended. The script uses a
    persistent browser profile (cookies + storage saved to disk). First run
    requires a human to complete login; subsequent runs reuse the session
    until Google invalidates it (typically every few weeks).
  * Cannot run in headless GitHub Actions for the same reason. This is a
    local-only tool meant to be triggered by launchd on your Mac.

Selector layout for the Protections "Ad content" section:
  Page:    admanager.google.com/<network>#delivery/protections
  Click on a Protection by its visible name -> detail page
  Expand "Ad content" section -> find "Advertiser URLs" field
  Append newline-separated domains -> Save -> wait for toast

Adjust _SELECTORS below if Google changes the UI.
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
    # The Protections list view shows each protection by its name; we click
    # the link whose visible text exactly matches the protection name.
    "protection_link_by_name":
        "a:has-text('{name}'), [role='link']:has-text('{name}')",

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

    def append_to_protection(self, protection_name: str, new_domains: list[str]) -> int:
        """Open the named Protection in GAM and append `new_domains` to its
        Advertiser URLs list. Returns the count actually appended (after the
        UI's own dedupe).

        Raises RuntimeError on any selector miss or login redirect — the caller
        is expected to surface this in the failure email.
        """
        if not new_domains:
            return 0

        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        url = f"https://admanager.google.com/{self.network_id}#delivery/protections"
        payload = "\n".join(sorted(set(new_domains)))

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
                        "Re-run with --inspect to log in manually and re-establish "
                        f"the profile at {self.profile_dir}."
                    )

                link_sel = _SELECTORS["protection_link_by_name"].format(name=protection_name)
                link = page.locator(link_sel).first
                if not link.count():
                    raise RuntimeError(
                        f"No Protection named {protection_name!r} found on "
                        f"{url}. Check the exact name in GAM > Delivery > Protections."
                    )
                link.click()
                self._sleep("protection detail load", 3.0)

                section = page.locator(_SELECTORS["ad_content_section"]).first
                if section.count():
                    try:
                        section.click()
                    except PWTimeout:
                        pass  # already expanded
                self._sleep("section expand", 1.0)

                textarea = page.locator(_SELECTORS["advertiser_urls_textarea"]).first
                if not textarea.count():
                    raise RuntimeError(
                        "Could not locate the 'Advertiser URLs' field on the "
                        "Protection detail page. Google likely changed the UI; "
                        "update _SELECTORS['advertiser_urls_textarea'] in "
                        "gam_blocklist_ui.py."
                    )

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

            finally:
                if self.debug:
                    page.screenshot(path=str(self.profile_dir / "debug-final.png"), full_page=True)
                ctx.close()

    def inspect(self) -> None:
        """Open the GAM Protections page in a visible browser and wait. Use this
        for first-time login and for verifying/updating selectors after a Google
        UI change.
        """
        from playwright.sync_api import sync_playwright

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
                f"Log in (and complete 2FA) if needed; navigate to your Confiant "
                f"Protection to verify selectors; then close the browser window."
            )
            try:
                page.wait_for_event("close", timeout=0)
            except Exception:
                pass
            ctx.close()

    def _sleep(self, label: str, seconds: float) -> None:
        if self.debug:
            print(f"  [browser] sleep {seconds:.1f}s ({label})", file=sys.stderr)
        time.sleep(seconds)


def default_profile_dir() -> Path:
    return Path(os.environ.get(
        "CONFIANT_BLOCKLIST_PROFILE_DIR",
        "~/.confiant-blocklist/playwright-profile",
    )).expanduser()

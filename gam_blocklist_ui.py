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
    # The "Advertiser URLs" section heading on the Protection detail page.
    # We use it as an anchor — the section's Edit button is the next Edit
    # button visually after this text.
    "advertiser_urls_section_label":
        "text=/^Advertiser URLs$/",

    # The "Edit" button for the Advertiser URLs section. Located by finding
    # the Advertiser URLs heading then the next visible Edit button. (The
    # Protection page has Edit buttons for Sensitive categories, Buyer, etc.
    # — this XPath targets the one for our section specifically.)
    "advertiser_urls_edit_button":
        "xpath=//*[normalize-space(text())='Advertiser URLs']"
        "/following::*[normalize-space(text())='Edit'][1]",

    # Modal-panel title that appears once we click Edit. Used to confirm
    # the modal opened before we interact with it.
    "modal_title":
        "text=/^Advertiser URLs$/ >> visible=true",

    # The textarea inside the modal where you type URLs to add. GAM uses a
    # placeholder of "Add advertiser URLs" or similar. The helper text
    # "Separate URLs by a new line" sits right under it, which gives us a
    # reliable anchor.
    "modal_textarea":
        "xpath=//*[contains(normalize-space(text()), 'Separate URLs by a new line')]"
        "/preceding::textarea[1]",

    # "Add" button inside the modal — applies what's in the textarea to the
    # right-side blocked-URLs list. Distinct from any Add buttons on the
    # parent page (which are out of view when the modal is open).
    "modal_add_button":
        "xpath=//*[contains(normalize-space(text()), 'Separate URLs by a new line')]"
        "/following::button[normalize-space(.)='Add'][1]",

    # "Update" button in the modal header (top-right). Closes the modal
    # and stages the URL list change on the parent Protection page.
    "modal_update_button":
        "xpath=//button[normalize-space(.)='Update' and not(@disabled)]",

    # "X" close button on the modal panel — used by read_existing_urls
    # to dismiss the modal without changes.
    "modal_close_button":
        "xpath=(//button[@aria-label='Close' or contains(@class, 'close')])[last()]",

    # Each blocked URL in the modal's right-side list. We read these as the
    # current Advertiser URLs in read_existing_urls. The modal shows URLs as
    # rows of text — extract the visible text per row.
    "modal_blocked_url_rows":
        "xpath=//*[contains(normalize-space(text()), 'blocked advertiser URLs')]"
        "/following::div[normalize-space(text()) "
        "and not(contains(., 'Note:')) "
        "and not(contains(., 'indirect transactions'))]",

    # The Save button at the bottom of the Protection detail page. After we
    # Update the modal, this becomes enabled — clicking it commits the
    # change to GAM.
    "parent_save_button":
        "xpath=//button[normalize-space(.)='Save' and not(@disabled)]",

    # Save confirmation toast (or banner) on the Protection list page after
    # the parent Save fires.
    "save_toast":
        "text=/saved|updated successfully|changes have been saved/i",

    # Detects the Google login redirect — if we land on accounts.google.com
    # the session expired and the user needs to log in manually.
    "login_redirect_marker":
        "input[type='email'], [aria-label='Email or phone']",
}


# URL format for the Protection detail page. Verified against the current GAM
# UI (May 2026): Protections is a top-level nav section, not under Delivery;
# `type=AD_CONTENT` lands us directly on the Ad content tab where Advertiser
# URLs live. Override via env var if Google ships a routing change.
_PROTECTION_DETAIL_URL_FMT = os.environ.get(
    "GAM_PROTECTION_DETAIL_URL_FMT",
    "https://admanager.google.com/{network_id}#protections/detail/protection_id={protection_id}&type=AD_CONTENT",
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

        UI flow (verified May 2026):
          1. Land on Protection detail page (already done by _open_protection_page)
          2. Click "Edit" next to "Advertiser URLs" section -> right-side modal opens
          3. Type newline-separated domains into the modal's textarea
          4. Click modal "Add" button -> domains move to the right-side blocked list
          5. Click modal "Update" button -> modal closes, change staged on parent
          6. Click parent "Save" button -> commits to GAM, success toast fires

        Raises RuntimeError on any selector miss, login redirect, or save
        failure — the caller is expected to surface this in the failure email.
        """
        if not new_domains:
            return 0

        from playwright.sync_api import TimeoutError as PWTimeout

        payload = "\n".join(sorted(set(new_domains)))

        with self._open_protection_page(protection_id) as (page, ctx):
            # 1+2: Open the Advertiser URLs edit modal.
            self._open_advertiser_urls_modal(page)

            # 3: Type new domains into the modal textarea.
            textarea = page.locator(_SELECTORS["modal_textarea"]).first
            if not textarea.count():
                raise RuntimeError(
                    "Edit modal opened but couldn't find the Add-URLs textarea. "
                    "Update _SELECTORS['modal_textarea']."
                )
            textarea.focus()
            textarea.fill(payload)
            self._sleep("post-type settle", 1.0)

            if self.debug:
                page.screenshot(path=str(self.profile_dir / "debug-pre-add.png"), full_page=True)

            # 4: Click "Add" in the modal -> domains move to the right-side list.
            add_btn = page.locator(_SELECTORS["modal_add_button"]).first
            if not add_btn.count():
                raise RuntimeError(
                    "Modal Add button not found. Update _SELECTORS['modal_add_button']."
                )
            add_btn.click()
            self._sleep("post-add settle (modal list updates)", 2.0)

            if self.debug:
                page.screenshot(path=str(self.profile_dir / "debug-pre-update.png"), full_page=True)

            # 5: Click "Update" -> modal closes, change is staged.
            update_btn = page.locator(_SELECTORS["modal_update_button"]).first
            if not update_btn.count():
                raise RuntimeError(
                    "Modal Update button not found / disabled. The Add may have "
                    "rejected one of the values; inspect debug-pre-update.png."
                )
            update_btn.click()
            self._sleep("post-update settle (modal closes)", 2.0)

            # 6: Click parent Save -> commit to GAM.
            if self.debug:
                page.screenshot(path=str(self.profile_dir / "debug-pre-save.png"), full_page=True)

            save = page.locator(_SELECTORS["parent_save_button"]).first
            if not save.count():
                raise RuntimeError(
                    "Parent Save button not found / disabled after modal Update. "
                    "Change may not have been staged. Inspect debug-pre-save.png."
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
        """Open the Protection and return the current Advertiser URLs as a list.

        Same modal-open flow as append_to_protection, but reads the right-side
        blocked-list and closes the modal without changes.
        """
        with self._open_protection_page(protection_id) as (page, _):
            self._open_advertiser_urls_modal(page)
            self._sleep("modal list render", 1.5)

            if self.debug:
                page.screenshot(
                    path=str(self.profile_dir / "debug-modal-open.png"),
                    full_page=True,
                )

            rows = page.locator(_SELECTORS["modal_blocked_url_rows"])
            count = rows.count()
            urls: list[str] = []
            for i in range(count):
                text = (rows.nth(i).inner_text() or "").strip()
                if text and "." in text and " " not in text:  # crude domain shape filter
                    urls.append(text)
            # Dismiss the modal (don't save).
            close = page.locator(_SELECTORS["modal_close_button"]).first
            if close.count():
                try:
                    close.click()
                except Exception:
                    pass
            return urls

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
                    # GAM is a heavy React SPA; the hash route + detail content
                    # often take 8-12 seconds to render after DOMContentLoaded.
                    self._sleep("post-nav settle (SPA hydration)", 12.0)
                    if self.debug:
                        page.screenshot(
                            path=str(self.profile_dir / "debug-post-nav.png"),
                            full_page=True,
                        )

                    if page.locator(_SELECTORS["login_redirect_marker"]).count():
                        raise RuntimeError(
                            "GAM session expired — Google login screen detected. "
                            "Re-run with --inspect to log in manually and re-"
                            f"establish the profile at {self.profile_dir}."
                        )

                    # No section-expand step needed: the &type=AD_CONTENT URL
                    # param lands us directly on the Ad content tab, and
                    # Advertiser URLs is a top-level section on that page.

                    yield page, ctx
                finally:
                    if self.debug:
                        page.screenshot(
                            path=str(self.profile_dir / "debug-final.png"),
                            full_page=True,
                        )
                    ctx.close()

        return _cm()

    def _open_advertiser_urls_modal(self, page) -> None:
        """Scroll to and click the Edit button next to the Advertiser URLs
        section, then wait for the modal panel to appear. Raises a clear
        RuntimeError if either step fails."""
        edit = page.locator(_SELECTORS["advertiser_urls_edit_button"]).first
        if not edit.count():
            raise RuntimeError(
                "Could not find the 'Edit' button for the Advertiser URLs "
                "section on the Protection detail page. Google likely changed "
                "the section ordering or the Edit label. Update "
                "_SELECTORS['advertiser_urls_edit_button']. Run with --inspect "
                "to verify."
            )
        edit.scroll_into_view_if_needed()
        edit.click()
        self._sleep("modal open animation", 2.0)

        # Confirm the modal actually opened by waiting for its textarea.
        from playwright.sync_api import TimeoutError as PWTimeout
        try:
            page.locator(_SELECTORS["modal_textarea"]).first.wait_for(
                state="visible", timeout=8000
            )
        except PWTimeout:
            raise RuntimeError(
                "Clicked Edit but the modal textarea didn't appear within 8s. "
                "Either the modal didn't open, or its textarea is under a "
                "different selector. Run with --inspect."
            )

    def _sleep(self, label: str, seconds: float) -> None:
        if self.debug:
            print(f"  [browser] sleep {seconds:.1f}s ({label})", file=sys.stderr)
        time.sleep(seconds)


def default_profile_dir() -> Path:
    return Path(os.environ.get(
        "CONFIANT_BLOCKLIST_PROFILE_DIR",
        "~/.confiant-blocklist/playwright-profile",
    )).expanduser()

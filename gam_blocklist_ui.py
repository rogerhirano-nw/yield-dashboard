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

    # The textarea inside the modal where you type URLs to add. Verified
    # from clickable-element dump: it has aria-label="Add advertiser URLs".
    "modal_textarea":
        "textarea[aria-label='Add advertiser URLs']",

    # "Add" button inside the modal. The modal contains exactly one button
    # with exact text "Add" (other Add-like elements are links/headers, not
    # <button>). The button can be scrolled off-screen when the textarea is
    # huge, so the caller must scroll_into_view before clicking.
    "modal_add_button":
        "xpath=//button[normalize-space(.)='Add']",

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

            # 2a: Snapshot the existing-URL count BEFORE we touch anything.
            # We use this for post-save count-delta verification instead of
            # the previous URL-nav heuristic (which produced false negatives —
            # GAM sometimes saves successfully without navigating away from
            # /detail/, especially when the page state is otherwise stable).
            initial_count = self._count_blocked_urls(page)
            if self.debug:
                print(f"  [browser] initial blocked-URL count: {initial_count}",
                      file=sys.stderr)

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
            # GAM uses <material-button role="button"> custom elements rather
            # than <button>; the helper tries multiple selector strategies and
            # .first picks the modal's button (appears earlier in DOM than any
            # parent-page Add). Note: the modal isn't tagged aria-modal="true"
            # so we can't scope to it; we trust DOM ordering instead.
            add_btn = self._click_or_dump(page, "Add", "modal Add button")
            add_btn.scroll_into_view_if_needed()
            add_btn.click()
            self._sleep("post-add settle (modal list updates)", 2.0)

            if self.debug:
                page.screenshot(path=str(self.profile_dir / "debug-pre-update.png"), full_page=True)

            # 5: Click "Update" -> modal closes, change is staged on parent.
            # Update is disabled until the Add above succeeds; wait for it to
            # become clickable, then click. Same locator-strategy approach.
            update_btn = self._click_or_dump(page, "Update", "modal Update button")
            # 30s rather than 10s: with batches >40 entries, GAM's input
            # validation can take 15-25s before the Update button enables.
            # Smaller batches finish in <2s so no real cost for the common
            # case.
            self._wait_until_enabled(update_btn, "modal Update", timeout_s=30)
            update_btn.click()
            self._sleep("post-update settle (modal closes)", 3.0)

            # 6: Click parent Save -> commit to GAM. Same disabled-until-staged
            # behavior; wait for enabled.
            if self.debug:
                page.screenshot(path=str(self.profile_dir / "debug-pre-save.png"), full_page=True)

            save = self._click_or_dump(page, "Save", "parent Save button")
            self._wait_until_enabled(save, "parent Save", timeout_s=10)
            save.click()

            # Success verification by COUNT DELTA, not URL navigation. After
            # Save click, GAM either (a) navigates to /protections/list, or
            # (b) stays on /detail with the modal closed. Both are valid save
            # outcomes — what matters is that the persisted blocked-URLs list
            # grew. Re-open the modal and verify.
            #
            # We sleep briefly first to give the save time to propagate, then
            # re-navigate (no-op if we're already on the detail page) and
            # re-open the modal. Count >= initial + 1 confirms a real write.
            self._sleep("post-save propagation", 5.0)
            page.goto(
                _PROTECTION_DETAIL_URL_FMT.format(
                    network_id=self.network_id, protection_id=protection_id
                ),
                wait_until="domcontentloaded",
            )
            self._sleep("re-nav settle for verification", 8.0)
            self._open_advertiser_urls_modal(page)
            final_count = self._count_blocked_urls(page)
            if self.debug:
                print(f"  [browser] final blocked-URL count: {final_count} "
                      f"(initial was {initial_count})", file=sys.stderr)

            if final_count <= initial_count:
                raise RuntimeError(
                    f"Save click fired but blocked-URL count didn't change "
                    f"({initial_count} -> {final_count}). GAM may have rejected "
                    f"the URLs (e.g. invalid format) or the save didn't persist. "
                    f"Verify the protection in the UI; inspect debug-pre-save.png."
                )

            actually_added = final_count - initial_count
            if self.debug:
                print(f"  [browser] verified {actually_added} URLs added "
                      f"(submitted {len(set(new_domains))}, some may have been "
                      f"duplicates GAM merged silently)", file=sys.stderr)
            return actually_added

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
            # Dismiss the modal (don't save). The X close button has aria-label
            # "Close" in GAM; if not found by that label, the persistent context
            # will close the page anyway on exit.
            close = page.locator("[aria-label='Close']").first
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
                    # GAM is a heavy React SPA. Detail-page content can take
                    # 12-20s to render. Some runs land on a "New protection"
                    # creation modal first; the hash route needs settle time
                    # to redirect to the actual detail view.
                    self._sleep("post-nav settle (SPA hydration)", 20.0)
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

    def _click_or_dump(self, page, text: str, label: str):
        """Find a clickable by text, or dump diagnostics + raise if not found."""
        loc = self._find_clickable_by_text(page, text)
        if loc is None:
            self._dump_clickable_diagnostics(page)
            raise RuntimeError(
                f"{label} not found via any selector strategy. See stderr for "
                f"a dump of clickable elements; update the strategies list in "
                f"_find_clickable_by_text if needed."
            )
        return loc

    def _wait_until_enabled(self, loc, label: str, timeout_s: float = 10.0) -> None:
        """Poll until a locator's element is no longer disabled (handles both
        the `disabled` property and `aria-disabled='true'` attribute, which
        material-button uses)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                is_disabled = loc.evaluate(
                    "el => el.disabled || el.getAttribute('aria-disabled') === 'true'"
                )
                if not is_disabled:
                    if self.debug:
                        print(f"  [browser] {label} enabled", file=sys.stderr)
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise RuntimeError(
            f"{label} did not become enabled within {timeout_s}s — the previous "
            f"step may not have staged its change correctly."
        )

    def _find_clickable_by_text(self, page, text: str):
        """Find a clickable element whose visible text is exactly `text`.

        GAM uses <material-button role="button"> custom elements rather than
        <button>. We try several strategies in order; first match wins.
        Returns a Locator (.first) or None.

        DOM ordering note: modal elements appear earlier in the DOM than the
        underlying parent-page elements, so .first reliably picks the modal's
        button when one is open. We can't scope to the modal via aria-modal
        because GAM doesn't set that attribute.
        """
        q = _xq(text)
        strategies = [
            f"xpath=//button[normalize-space(.)={q}]",
            f"xpath=//*[@role='button' and normalize-space(.)={q}]",
            f"xpath=//a[normalize-space(.)={q}]",
            f"xpath=//*[normalize-space(.)={q} "
            f"and (self::button or self::a or @role='button' "
            f"or @role='link' or @tabindex='0')]",
            f"xpath=//*[normalize-space(text())={q} and not(*)]",
        ]
        for sel in strategies:
            loc = page.locator(sel).first
            if loc.count():
                if self.debug:
                    print(f"  [browser] {text!r} matched: {sel}", file=sys.stderr)
                return loc
        return None

    def _dump_clickable_diagnostics(self, page) -> None:
        """Print every clickable-looking element in the open modal to stderr.
        Used when Add can't be found — gives us enough info to pick the right
        selector without needing browser dev tools."""
        try:
            modal_html_dump_path = self.profile_dir / "debug-modal-clickables.txt"
            elements = page.evaluate("""
                () => {
                    const root = document.querySelector('[aria-modal=\"true\"]')
                                 || document.body;
                    const sel = 'button, a, [role=\"button\"], [role=\"link\"], '
                                + '[tabindex=\"0\"], input[type=\"submit\"]';
                    return Array.from(root.querySelectorAll(sel))
                        .map(el => ({
                            tag: el.tagName.toLowerCase(),
                            role: el.getAttribute('role') || '',
                            ariaLabel: el.getAttribute('aria-label') || '',
                            text: (el.innerText || '').trim().slice(0, 60),
                            classes: (el.className || '').toString().slice(0, 80),
                            disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
                        }));
                }
            """)
            lines = ["=== modal clickable elements ==="]
            for i, el in enumerate(elements):
                lines.append(
                    f"  [{i}] <{el['tag']}> "
                    f"role={el['role']!r} "
                    f"text={el['text']!r} "
                    f"aria-label={el['ariaLabel']!r} "
                    f"disabled={el['disabled']}"
                )
            dump = "\n".join(lines)
            print(dump, file=sys.stderr)
            modal_html_dump_path.write_text(dump)
        except Exception as e:
            print(f"  [browser] diagnostic dump failed: {e}", file=sys.stderr)

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

    def _count_blocked_urls(self, page) -> int:
        """Count the rows in the modal's right-side 'blocked advertiser URLs'
        list. Used for save-success verification (post-save count > pre-save
        count proves the write persisted).

        Falls back to parsing the modal header text ("N blocked advertiser
        URLs") if the row selector misses; the header is more stable than the
        per-row markup.
        """
        # Try parsing the header text first — fastest and most robust.
        # The modal shows "N blocked advertiser URLs" at the top of the
        # right column. Match the visible text and extract N.
        import re
        header_loc = page.locator("text=/\\d+ blocked advertiser URLs/i").first
        if header_loc.count():
            try:
                header_text = header_loc.inner_text(timeout=2000) or ""
                m = re.search(r"(\d+)\s+blocked advertiser URLs", header_text, re.I)
                if m:
                    return int(m.group(1))
            except Exception:
                pass
        # Fallback: count rows directly.
        try:
            return page.locator(_SELECTORS["modal_blocked_url_rows"]).count()
        except Exception:
            return 0

    def _sleep(self, label: str, seconds: float) -> None:
        if self.debug:
            print(f"  [browser] sleep {seconds:.1f}s ({label})", file=sys.stderr)
        time.sleep(seconds)


def _xq(text: str) -> str:
    """XPath-quote a string. XPath has no escape for quotes, so we use
    concat() when the string contains both ' and "."""
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    parts = text.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"


def default_profile_dir() -> Path:
    return Path(os.environ.get(
        "CONFIANT_BLOCKLIST_PROFILE_DIR",
        "~/.confiant-blocklist/playwright-profile",
    )).expanduser()

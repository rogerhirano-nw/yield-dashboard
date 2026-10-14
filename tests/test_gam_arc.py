"""Guards in gam_arc.block_in_arc — the ARC auto-block must never click an ad
it can't prove is the one Confiant flagged.

2026-09 incident: the old flow waited a fixed 8s after filtering and clicked
the first "Block ad" button on the page. When GAM's filter was slower than
that, the page was still the unfiltered ARC feed and the cron blocked the
top ad — Apple PG creatives Confiant never flagged. These tests replay that
page state with a fake Playwright page.
"""

import gam_arc

GPT = "CITw3evhgZcDFUPDAgcdzSAFhw"


class _Btn:
    def __init__(self, page):
        self._page = page

    def is_visible(self):
        return True

    def hover(self):
        pass

    def click(self):
        self._page.clicks += 1


class _Locator:
    def __init__(self, page, selector):
        self._page = page
        self._sel = selector

    def _n(self):
        if self._sel == gam_arc._BLOCK_BTN:
            return self._page.frame["buttons"]
        return 0  # no confirm dialog

    def count(self):
        return self._n()

    def nth(self, i):
        return _Btn(self._page)

    @property
    def first(self):
        return _Btn(self._page)

    @property
    def last(self):
        return self

    def is_visible(self):
        return False


class FakePage:
    """Each frame is what ARC shows at a moment: {"buttons": n, "text": str}.
    wait_for_timeout advances time; `frames` maps elapsed-ms thresholds to the
    frame shown from then on. `after_reload` replaces the timeline on reload."""

    def __init__(self, frames, after_reload=None):
        self.t = 0
        self.frames = frames
        self.after_reload = after_reload
        self.clicks = 0
        self.reloads = 0

    @property
    def frame(self):
        cur = self.frames[0][1]
        for at, f in self.frames:
            if self.t >= at:
                cur = f
        return cur

    def goto(self, *a, **k):
        pass

    def wait_for_timeout(self, ms):
        self.t += ms

    def reload(self, *a, **k):
        self.reloads += 1
        self.t = 0
        if self.after_reload is not None:
            self.frames = self.after_reload

    def inner_text(self, _sel):
        return self.frame["text"]

    def locator(self, selector):
        return _Locator(self, selector)


def _run(page, monkeypatch):
    monkeypatch.setattr(gam_arc, "apply_ad_response_id_filter", lambda p, g: None)
    return gam_arc.block_in_arc(page, GPT, "22541732127")


FEED = {"buttons": 12, "text": f"Ad response ID: {GPT} Apple TV Slow Horses ..."}
MATCH = {"buttons": 1, "text": f"Ad response ID: {GPT} Ad match MediaGo"}
NOT_FOUND = {"buttons": 0, "text": "Couldn't find matching ad"}
EMPTY = {"buttons": 0, "text": "loading"}


def test_unfiltered_feed_is_refused(monkeypatch):
    # The incident: filter never narrows the list — many cards stay on screen.
    page = FakePage([(0, FEED)])
    assert _run(page, monkeypatch) == "ambiguous"
    assert page.clicks == 0


def test_slow_filter_waits_for_the_single_match(monkeypatch):
    # Feed still showing for 12s, then the filter lands on one card.
    page = FakePage([(0, FEED), (19000, MATCH)])
    assert _run(page, monkeypatch) == "blocked"
    assert page.clicks == 1


def test_slow_filter_landing_on_not_found(monkeypatch):
    # Old flow checked "Couldn't find" once at 8s, saw the feed, clicked.
    page = FakePage([(0, FEED), (19000, NOT_FOUND)])
    assert _run(page, monkeypatch) == "not-in-arc"
    assert page.clicks == 0


def test_reload_that_drops_the_filter_is_refused(monkeypatch):
    # Skeleton-stuck card → reload → GAM forgets the filter → full feed.
    page = FakePage([(0, EMPTY)], after_reload=[(0, FEED)])
    assert _run(page, monkeypatch) == "ambiguous"
    assert page.reloads == 1
    assert page.clicks == 0


def test_reload_that_keeps_the_filter_blocks(monkeypatch):
    page = FakePage([(0, EMPTY)], after_reload=[(0, MATCH)])
    assert _run(page, monkeypatch) == "blocked"
    assert page.clicks == 1


def test_reload_then_not_found(monkeypatch):
    page = FakePage([(0, EMPTY)], after_reload=[(0, NOT_FOUND)])
    assert _run(page, monkeypatch) == "not-in-arc"
    assert page.clicks == 0


def test_transient_single_card_mid_refresh_is_not_trusted(monkeypatch):
    # A one-card blip between feed frames must not pass the two-poll check.
    page = FakePage([(0, FEED), (19000, MATCH), (20000, FEED)])
    assert _run(page, monkeypatch) == "ambiguous"
    assert page.clicks == 0


def test_card_without_our_response_id_is_refused(monkeypatch):
    other = {"buttons": 1, "text": "Ad response ID: SOMETHINGELSE Ad match"}
    page = FakePage([(0, other)])
    assert _run(page, monkeypatch) == "unconfirmed"
    assert page.clicks == 0


def test_programmatic_guaranteed_card_is_refused(monkeypatch):
    pg = {"buttons": 1,
          "text": f"Ad response ID: {GPT} Ad match Programmatic Guaranteed"}
    page = FakePage([(0, pg)])
    assert _run(page, monkeypatch) == "deal-ad"
    assert page.clicks == 0


def test_preferred_deal_card_is_refused(monkeypatch):
    pd_ = {"buttons": 1, "text": f"Ad response ID: {GPT} Preferred Deal"}
    page = FakePage([(0, pd_)])
    assert _run(page, monkeypatch) == "deal-ad"


def test_nothing_renders_at_all(monkeypatch):
    page = FakePage([(0, EMPTY)], after_reload=[(0, EMPTY)])
    assert _run(page, monkeypatch) == "no-block-btn"
    assert page.clicks == 0


def test_refused_statuses_cover_every_non_block_outcome():
    assert set(gam_arc.REFUSED_STATUSES) == {
        "ambiguous", "deal-ad", "unconfirmed", "no-block-btn"}

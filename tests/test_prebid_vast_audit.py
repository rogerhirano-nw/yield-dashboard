"""VAST unwrapping for scripts/prebid_vast_audit.py — offline, fake fetch."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import prebid_vast_audit as va  # noqa: E402

WRAPPER = """<VAST version="3.0"><Ad id="1"><Wrapper>
  <AdSystem>onetag</AdSystem>
  <VASTAdTagURI><![CDATA[{next}]]></VASTAdTagURI>
  <Impression><![CDATA[https://imp.example/never-fetched]]></Impression>
</Wrapper></Ad></VAST>"""

INLINE = """<VAST version="4.1" xmlns="http://www.iab.com/VAST"><Ad id="2"><InLine>
  <AdSystem>dsp</AdSystem>
  <AdVerifications><Verification vendor="doubleverify.com-omid">
    <JavaScriptResource apiFramework="omid"><![CDATA[https://v.example/omid.js]]></JavaScriptResource>
  </Verification></AdVerifications>
  <Creatives><Creative><Linear><Duration>00:00:15</Duration><MediaFiles>
    <MediaFile type="video/mp4" delivery="progressive" width="1920" height="1080" bitrate="8000">
      <![CDATA[https://cdn.example/a.mp4]]></MediaFile>
    <MediaFile type="application/javascript" apiFramework="VPAID" width="640" height="360">
      <![CDATA[https://cdn.example/vpaid.js]]></MediaFile>
  </MediaFiles></Linear></Creative></Creatives>
</InLine></Ad></VAST>"""

VPAID_ONLY = """<VAST version="2.0"><Ad><InLine><AdSystem>x</AdSystem><Creatives><Creative>
  <Linear><Duration>00:00:30</Duration><MediaFiles>
    <MediaFile type="application/javascript" apiFramework="VPAID" width="640" height="360">
      https://cdn.example/v.js</MediaFile>
  </MediaFiles></Linear></Creative></Creatives></InLine></Ad></VAST>"""


def _fetcher(pages):
    seen = []

    def fetch(url):
        seen.append(url)
        return (200, pages[url], 40) if url in pages else (404, "", 5)
    return fetch, seen


def test_parse_inline_namespaced_with_omid_and_vpaid():
    p = va.parse_vast(INLINE)
    assert p["kind"] == "inline"
    assert p["verifications"] == ["doubleverify.com-omid"]
    assert p["duration"] == "00:00:15"
    s = va.summarize_media(p["media"])
    assert s["has_vpaid"] and not s["vpaid_only"]
    assert s["n_mp4"] == 1 and s["max_bitrate"] == 8000 and s["max_height"] == 1080


def test_vpaid_only_flagged():
    s = va.summarize_media(va.parse_vast(VPAID_ONLY)["media"])
    assert s["vpaid_only"] and s["n_mp4"] == 0


def test_empty_and_garbage():
    assert va.parse_vast('<VAST version="3.0"/>')["kind"] == "empty"
    assert va.parse_vast("<html>nope</html>")["kind"] == "error"
    assert va.parse_vast("not xml")["kind"] == "error"


def test_chain_two_wrappers_then_inline_and_never_fetches_impressions():
    pages = {"https://b.example/vast": WRAPPER.format(next="https://c.example/vast"),
             "https://c.example/vast": INLINE}
    fetch, seen = _fetcher(pages)
    c = va.resolve_chain(WRAPPER.format(next="https://b.example/vast"), fetch)
    assert c["result"] == "inline"
    assert c["depth"] == 2
    assert [h["host"] for h in c["hops"]] == ["(in bid)", "b.example", "c.example"]
    assert seen == ["https://b.example/vast", "https://c.example/vast"]
    assert not any("imp.example" in u for u in seen)


def test_chain_from_url_markup_and_fetch_failure():
    fetch, _ = _fetcher({"https://cache.example/x": INLINE})
    c = va.resolve_chain("https://cache.example/x", fetch)
    assert c["result"] == "inline" and c["depth"] == 0

    fetch, _ = _fetcher({})
    c = va.resolve_chain(WRAPPER.format(next="https://gone.example/v"), fetch)
    assert c["result"] == "fetch_failed" and c["depth"] == 1


def test_chain_depth_cap():
    loop = WRAPPER.format(next="https://loop.example/v")
    fetch, _ = _fetcher({"https://loop.example/v": loop})
    c = va.resolve_chain(loop, fetch)
    assert c["result"] == "too_deep"


def test_video_bids_from_auction_ignores_banner_imps():
    req = {"imp": [{"id": "video", "video": {}}, {"id": "dfp-ad-sticky", "banner": {}}]}
    resp = {"seatbid": [
        {"seat": "onetag", "bid": [{"impid": "video", "price": 6.1, "adm": INLINE, "crid": "c1",
                                    "adomain": ["brand.com"]}]},
        {"seat": "rubicon", "bid": [{"impid": "dfp-ad-sticky", "price": 1, "adm": "<div/>"}]},
        {"seat": "pubmatic", "bid": [{"impid": "video", "price": 7,
                                      "ext": {"prebid": {"cache": {"vastXml": {"url": "https://c/x"}}}}}]},
    ], "ext": {"responsetimemillis": {"onetag": 73, "pubmatic": 4}}}
    bids = va.video_bids_from_auction(req, resp)
    assert [(b["bidder"], b["pbs_ms"]) for b in bids] == [("onetag", 73), ("pubmatic", 4)]
    assert bids[1]["markup"] == "https://c/x"
    assert va.video_bids_from_auction({"imp": [{"id": "a", "banner": {}}]}, resp) == []


def test_ima_targeting_reads_prebid_winner_from_cust_params():
    from urllib.parse import quote
    cust = "cat=nwus-politics&hb_bidder=onetag&hb_pb=6.00&hb_uuid=abc-123&pbs_ab=ay"
    url = ("https://securepubads.g.doubleclick.net/gampad/ads?iu=%2F22541732127%2Fvid.newsweek"
           f"&output=xml_vast4&cust_params={quote(cust)}")
    tg = va.ima_targeting(url)
    assert tg == {"hb_bidder": "onetag", "hb_pb": "6.00", "hb_uuid": "abc-123"}
    assert va.ima_targeting("https://x/gampad/ads?iu=vid") == {}


def test_gam_served_prebid_only_when_cache_key_in_response():
    tg = {"hb_bidder": "onetag", "hb_uuid": "abc-123"}
    assert va.gam_served_prebid(tg, "<VAST><VASTAdTagURI>https://c/cache?uuid=abc-123</VASTAdTagURI></VAST>")
    assert not va.gam_served_prebid(tg, "<VAST><Ad><InLine/></Ad></VAST>")
    assert not va.gam_served_prebid({}, "abc-123")

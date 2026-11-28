import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import screenshots_auto_spec as sas  # noqa: E402


def test_build_spec(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "order_id": "4204198540", "line_item_id": "7440274079",
        "order_name": "Newsweek_Direct_Health_NA_NA_NA_NA_Elevance-Health_AI-Health-Summit_US_"
                      "Display_SO01150_Team-USA_AShah",
        "article_url": "https://www.newsweek.com/some-article-123?x=1", "captured": "23 Sep 2026"}))
    for n in ("7440274079_1_300x250_mobile_context_framed.png",
              "7440274079_1_970x250_desktop_context_framed.png",
              "7440274079_1_970x250_desktop_context.png",
              "7440274079_1_970x250_desktop_crop.png"):
        (tmp_path / n).write_bytes(b"")
    s = sas.build_spec(tmp_path, {"AShah": "Amit Shah"})
    assert s["advertiser"] == "Elevance Health" and s["campaign"] == "AI Health Summit"
    assert s["seller"] == "Amit Shah" and s["page_url"] == "newsweek.com/some-article-123"
    assert [x["file"] for x in s["shots"]] == ["7440274079_1_970x250_desktop_context_framed.png",
                                              "7440274079_1_300x250_mobile_context_framed.png"]
    assert s["shots"][0]["title"] == "Billboard on desktop"

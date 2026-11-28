import json
import urllib.request

import adops_mail


class _Resp:
    status = 202
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_not_configured_without_url(monkeypatch):
    monkeypatch.delenv("MAIL_WEBHOOK_URL", raising=False)
    assert not adops_mail.configured()


def test_send_posts_flow_payload(monkeypatch):
    monkeypatch.setenv("MAIL_WEBHOOK_URL", "https://flow.example/hook")
    seen = {}
    def fake(req, timeout=0):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data)
        return _Resp()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    adops_mail.send(["adops@newsweek.com"], "Yield health — ✅", "line1\n<b>x</b>")
    b = seen["body"]
    assert seen["url"] == "https://flow.example/hook"
    assert b["to"] == ["adops@newsweek.com"] and b["cc"] == []
    assert b["subject"] == "Yield health — ✅"
    assert "&lt;b&gt;x&lt;/b&gt;" in b["html"] and "line1\n" in b["html"]
    assert "attachments" not in b


def test_send_raises_on_http_error(monkeypatch):
    import urllib.error
    monkeypatch.setenv("MAIL_WEBHOOK_URL", "https://flow.example/hook")
    def boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    try:
        adops_mail.send(["a@x.com"], "s", "t")
    except RuntimeError as e:
        assert "HTTP 400" in str(e)
    else:
        raise AssertionError("expected RuntimeError")

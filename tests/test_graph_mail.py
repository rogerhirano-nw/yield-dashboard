"""graph_mail: the reporting@newsweek.com (Microsoft 365) mail path.

No network: graph_mail._request is stubbed. Pins (1) the opt-in switch —
nothing changes until GRAPH_MAILBOX + creds are set; (2) subject matching is
re-checked client-side; (3) the DV / TTD clients route through Graph when
enabled and parse with their existing parsers."""
import pandas as pd
import pytest

import graph_mail


@pytest.fixture
def graph_env(monkeypatch):
    for k in ("GRAPH_MAILBOX", "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
              "ENTRA_TENANT_ID", "ENTRA_CLIENT_ID", "ENTRA_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GRAPH_MAILBOX", "reporting@newsweek.com")
    monkeypatch.setenv("GRAPH_TENANT_ID", "t")
    monkeypatch.setenv("GRAPH_CLIENT_ID", "c")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "s")
    return monkeypatch


def test_disabled_without_mailbox(monkeypatch):
    for k in ("GRAPH_MAILBOX", "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ENTRA_TENANT_ID", "t")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "c")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "s")
    # Existing ENTRA_* secrets alone must NOT flip prod over to Graph.
    assert not graph_mail.enabled()
    monkeypatch.setenv("GRAPH_MAILBOX", "reporting@newsweek.com")
    assert graph_mail.enabled()  # creds fall back to ENTRA_*


def _msg(i, subject, when):
    return {"id": f"id{i}", "subject": subject, "receivedDateTime": when,
            "sentDateTime": when, "internetMessageId": f"<m{i}@x>",
            "from": {"emailAddress": {"address": "noreply@doubleverify.com"}},
            "hasAttachments": True}


def test_list_messages_filters_subject_and_sorts(graph_env):
    calls = []

    def fake(method, path, **kw):
        calls.append(path)
        return {"value": [
            _msg(1, "Unified Analytics Report: IVT", "2026-11-10T09:00:00Z"),
            _msg(2, "Re: something mentioning IVT in the body", "2026-11-12T09:00:00Z"),
            _msg(3, "Unified Analytics Report: IVT", "2026-11-12T09:00:00Z"),
        ]}
    graph_env.setattr(graph_mail, "_request", fake)
    out = graph_mail.list_messages("Unified Analytics Report: IVT", limit=5)
    assert [m["id"] for m in out] == ["id3", "id1"]
    assert "/users/reporting%40newsweek.com/messages" in calls[0]
    assert "$search" in calls[0]


def test_list_messages_falls_back_when_search_fails(graph_env):
    def fake(method, path, **kw):
        if "$search" in path:
            raise RuntimeError("search not supported")
        return {"value": [_msg(1, "Report Available: Newsweek Chumba Casino", "2026-11-12T09:00:00Z")]}
    graph_env.setattr(graph_mail, "_request", fake)
    assert [m["id"] for m in graph_mail.list_messages("Chumba")] == ["id1"]


def test_send_mail_payload(graph_env):
    sent = {}
    graph_env.setattr(graph_mail, "_request",
                      lambda method, path, body=None, **kw: sent.update(method=method, path=path, body=body))
    graph_mail.send_mail('Jane Doe <jane@x.com>, bob@y.com', "Subj", html="<b>hi</b>",
                         cc=["c@z.com"], attachments=[("a.csv", b"1,2", "text/csv")])
    assert sent["method"] == "POST" and sent["path"].endswith("/sendMail")
    m = sent["body"]["message"]
    assert m["toRecipients"] == [{"emailAddress": {"address": "jane@x.com", "name": "Jane Doe"}},
                                 {"emailAddress": {"address": "bob@y.com"}}]
    assert m["ccRecipients"] == [{"emailAddress": {"address": "c@z.com"}}]
    assert m["body"]["contentType"] == "HTML"
    assert m["attachments"][0]["contentBytes"] == "MSwy"
    assert sent["body"]["saveToSentItems"] is True


def test_dv_ivt_routes_through_graph(graph_env):
    import dv_ivt_client
    graph_env.setattr(graph_mail, "list_messages",
                      lambda needle, limit=30: [{"id": "g1", "message_id": "<m1@x>", "subject": needle}])
    graph_env.setattr(graph_mail, "list_attachments",
                      lambda mid: [{"id": "a1", "name": "ivt.csv"}, {"id": "a2", "name": "logo.png"}])
    graph_env.setattr(graph_mail, "attachment_bytes", lambda mid, aid: b"csv-bytes")
    seen = []
    graph_env.setattr(dv_ivt_client, "parse_dv_ivt_csv",
                      lambda content: seen.append(content) or pd.DataFrame({"date": ["2026-11-12"]}))
    df = dv_ivt_client.pull_dv_ivt("unused", "unused", limit=2)
    assert seen == [b"csv-bytes"]
    assert df["_email_message_id"].tolist() == ["m1@x"]


def test_dv_attention_routes_through_graph(graph_env):
    import dv_attention_client
    graph_env.setattr(graph_mail, "list_messages",
                      lambda needle, limit=30: [{"id": "g1", "message_id": "<m1@x>", "subject": needle}])
    graph_env.setattr(graph_mail, "list_attachments", lambda mid: [{"id": "a1", "name": "att.xlsx"}])
    graph_env.setattr(graph_mail, "attachment_bytes", lambda mid, aid: b"xlsx")
    graph_env.setattr(dv_attention_client, "parse_dv_xlsx",
                      lambda content: pd.DataFrame({"date": ["2026-11-12"]}))
    df = dv_attention_client.pull_dv_attention("unused", "unused", limit=2)
    assert len(df) == 1 and df["_email_message_id"].iloc[0] == "m1@x"


def test_ttd_routes_through_graph(graph_env):
    import ttd_client
    graph_env.setattr(graph_mail, "list_messages",
                      lambda needle, limit=100: [{"id": "g1", "subject": "Report Available: Newsweek Chumba Casino",
                                                  "sent_at": "2026-11-12T09:00:00Z",
                                                  "received_at": "2026-11-12T09:00:00Z"}])
    graph_env.setattr(graph_mail, "message_text", lambda mid: "body")
    graph_env.setattr(ttd_client, "extract_ttd_download_url", lambda body: "https://desk.thetradedesk.com/reports/view/1")
    graph_env.setattr(ttd_client, "download_ttd_csv", lambda url: b"x")
    graph_env.setattr(ttd_client, "parse_ttd_csv",
                      lambda content, **kw: pd.DataFrame({"date": ["2026-11-12"]}))
    df, meta = ttd_client.pull_ttd("unused", "unused", subject_needle="Chumba")
    assert len(df) == 1 and meta["message_id"] == "g1"

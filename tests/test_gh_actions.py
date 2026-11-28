import io
import zipfile

import gh_actions as gha


class _R:
    def __init__(self, code=200, js=None, content=b"", text=""):
        self.status_code, self._js, self.content, self.text = code, js or {}, content, text

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 300:
            raise RuntimeError(self.status_code)


def test_dispatch_cleans_inputs(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "t")
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(url=url, json=json, auth=headers["Authorization"])
        return _R(204)
    monkeypatch.setattr(gha.requests, "post", post)
    gha.dispatch("final_campaign_report.yml",
                 {"order": " 123 ", "end_date": "", "revenue": True, "x": None})
    assert seen["url"].endswith("/repos/rogerhirano-nw/yield-dashboard/actions/workflows/"
                                "final_campaign_report.yml/dispatches")
    assert seen["json"] == {"ref": "main", "inputs": {"order": "123", "revenue": "true"}}
    assert seen["auth"] == "Bearer t"


def test_dispatch_raises_on_error(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "t")
    monkeypatch.setattr(gha.requests, "post", lambda *a, **k: _R(422, text="bad input"))
    try:
        gha.dispatch("w.yml", {})
    except RuntimeError as e:
        assert "422" in str(e)
    else:
        raise AssertionError("should raise")


def test_runs_artifacts_and_labels(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "t")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("report.xlsx", b"xl")
        z.writestr("sub/", b"")
    pages = {
        "/runs": _R(js={"workflow_runs": [
            {"id": 1, "display_title": "Final report · order 9", "status": "completed",
             "conclusion": "success", "created_at": "2026-11-28T10:00:00Z", "html_url": "u"}]}),
        "/artifacts": _R(js={"artifacts": [{"id": 7, "name": "final-report",
                                            "size_in_bytes": 10}]}),
        "/zip": _R(content=buf.getvalue()),
    }
    monkeypatch.setattr(gha.requests, "get",
                        lambda url, **k: next(v for s, v in pages.items() if url.endswith(s)))
    runs = gha.recent_runs("w.yml")
    assert runs[0]["title"] == "Final report · order 9" and gha.run_label(runs[0]) == "Done"
    assert gha.run_artifacts(1)[0]["name"] == "final-report"
    assert gha.artifact_files(7) == {"report.xlsx": b"xl"}
    assert gha.run_label({"status": "in_progress"}) == "Running"
    assert gha.run_label({"status": "completed", "conclusion": "failure"}) == "Failed"

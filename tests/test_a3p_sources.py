"""a3p decoding for the secure-signals check (scripts/prebid_render_forensics.py)."""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from prebid_render_forensics import a3p_sources  # noqa: E402


def _ld(field: int, payload: bytes) -> bytes:
    return bytes([(field << 3) | 2, len(payload)]) + payload


def _a3p(*signals: tuple[str, str]) -> str:
    """Build an a3p the way GPT does: repeated field 2, source in sub-field 1,
    id in sub-field 2, plus a varint field; URL-safe base64 with '.' padding."""
    raw = b"".join(_ld(2, _ld(1, s.encode()) + _ld(2, i.encode()) + b"\x58\x01")
                   for s, i in signals)
    return base64.urlsafe_b64encode(raw).decode().replace("=", ".")


def test_decodes_sources_in_order_and_never_ids():
    a = _a3p(("adserver.org", "secret-tdid"), ("pubcid.org", "secret-pubcid"))
    out = a3p_sources(a)
    assert out == ["adserver.org", "pubcid.org"]
    assert not any("secret" in s for s in out)


def test_padding_variants():
    for n in range(1, 4):  # exercise every base64 padding length
        src = "x" * n + ".org"
        assert a3p_sources(_a3p((src, "id"))) == [src]

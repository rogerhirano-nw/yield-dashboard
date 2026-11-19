"""Fetch the newest TTD Chumba export from AgentMail and write it ENCRYPTED.

One-off preview helper: the repo is public, so the export never touches the
log or an artifact in the clear. It is encrypted to a public key whose private
half stays on the requester's machine (AES-256 key wrapped with RSA-OAEP).
Logs carry only counts and the received timestamp -- no subjects, no URLs.
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ttd_client as t  # noqa: E402

logging.basicConfig(level=logging.ERROR)
key, inbox = os.environ["AGENTMAIL_API_KEY"], os.environ["AGENTMAIL_INBOX_ID"]
pub, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)

msgs = [m for m in t.list_ttd_messages(key, inbox, subject_needle=t.CHUMBA_SUBJECT_NEEDLE)
        if "report available" in (m.get("subject") or "").lower()]
msgs.sort(key=t._msg_ts, reverse=True)
print(f"{len(msgs)} candidate message(s)")
for m in msgs:
    d = t.get_message_detail(key, inbox, m.get("id") or m.get("message_id"))
    body = d.get("text") or d.get("body") or d.get("html") or ""
    url = t.extract_ttd_download_url(body)
    if not url:
        continue
    blob = t.download_ttd_csv(url)
    if blob[:2] != b"PK":
        print("download was not an .xlsx; trying next")
        continue
    (out / "export.xlsx").write_bytes(blob)
    (out / "received.txt").write_text(t._msg_ts(m))
    subprocess.run(["openssl", "rand", "-out", str(out / "k.bin"), "32"], check=True)
    subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-in", str(out / "export.xlsx"),
                    "-out", str(out / "export.xlsx.enc"), "-pass", f"file:{out / 'k.bin'}"], check=True)
    subprocess.run(["openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", str(pub),
                    "-pkeyopt", "rsa_padding_mode:oaep", "-in", str(out / "k.bin"),
                    "-out", str(out / "k.bin.enc")], check=True)
    for f in ("export.xlsx", "k.bin"):
        (out / f).unlink()
    print(f"encrypted export written ({len(blob)} bytes, received {t._msg_ts(m)})")
    sys.exit(0)
sys.exit("no downloadable Chumba export found")

#!/usr/bin/env python3
"""Copy the last known good grail snapshot to kodyw.com: one zip in the media library and the page kodyw.com/grail.

    WP_USER=... WP_APP_PASSWORD=... python3 publish_kodyw.py [--out docs]

Idempotent: a snapshot already uploaded is reused, and the page is updated in place (found by slug).
The zip holds grail.bundle, grail.tar.gz, manifest.json and install.sh; its SHA-256 is on the page
and in the beacon, so a copy fetched from kodyw.com is verified the same way as any other. Standard library only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

SITE = os.getenv("WP_URL", "https://kodyw.com").rstrip("/")
SLUG = "grail"


def api(method: str, path: str, body: bytes | None = None, headers: dict | None = None, params: str = ""):
    auth = base64.b64encode(f"{os.environ['WP_USER']}:{os.environ['WP_APP_PASSWORD']}".encode()).decode()
    request = urllib.request.Request(f"{SITE}/wp-json/wp/v2/{path}{params}", data=body, method=method,
                                     headers={"Authorization": f"Basic {auth}", "User-Agent": "Mozilla/5.0 grail-vault",
                                              **(headers or {})})
    for attempt in range(4):  # the host sometimes stalls on the TLS handshake
        try:
            with urllib.request.urlopen(request, timeout=90) as reply:
                return json.loads(reply.read())
        except urllib.error.HTTPError as error:
            raise SystemExit(f"{method} {path}: HTTP {error.code} {error.read()[:300]!r}")
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def fetch_sha(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 grail-vault", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=120) as reply:
        return hashlib.sha256(reply.read()).hexdigest()


def build_zip(out: Path, name: str) -> bytes:
    folder = out / "snapshots" / name
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        # Only files fixed for this snapshot, so the zip (and its hash) never changes once published.
        for path in (folder / "grail.bundle", folder / "grail.tar.gz", folder / "manifest.json", out / "install.sh"):
            info = zipfile.ZipInfo(f"grail-{name}/{path.name}", date_time=(2026, 1, 1, 0, 0, 0))
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def page_html(beacon: dict, zip_url: str, zip_sha: str) -> str:
    kernel = beacon["kernel"]
    machine = json.dumps({**beacon, "kodyw_copy": {"zip": zip_url, "sha256": zip_sha}}, indent=2)
    rows = "".join(f"<tr><td><code>{html.escape(f)}</code></td><td><code>{h}</code></td></tr>"
                   for f, h in kernel["sha256"].items())
    return f"""
<p>This page keeps a verified copy of the <strong>RAPP Brainstem kernel</strong> (the grail), independent of GitHub,
so a bad change to the grail or an outage never takes the working kernel with it.</p>
<h2>Last known good</h2>
<p>Version <strong>{kernel['version']}</strong>, commit <code>{kernel['commit']}</code>, copied {kernel['taken_at']}.
It passed every check: {html.escape('; '.join(beacon['gates']))}.</p>
<p><a href="{zip_url}"><strong>Download grail-{kernel['snapshot']}.zip</strong></a><br>
SHA-256 of the zip: <code>{zip_sha}</code></p>
<table><thead><tr><th>File inside</th><th>SHA-256</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Install it</h2>
<pre><code>{html.escape(beacon['install']['command'])}</code></pre>
<p>Other copies: the <a href="https://kody-w.github.io/grail-vault">grail vault</a> on GitHub Pages and
<a href="{beacon['get']['archives']['software_heritage_browse']}">Software Heritage</a>
(<code>{beacon['get']['archives']['software_heritage']}</code>). Every copy is checked by its hash, not by who served it.</p>
<h2>For agents</h2>
<p>The same facts, machine-readable (schema <code>{beacon['schema']}</code>):</p>
<pre id="rapp-grail-beacon"><code>{html.escape(machine)}</code></pre>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs")
    out = Path(parser.parse_args().out)
    beacon = json.loads((out / "beacon.json").read_text())
    name = beacon["kernel"]["snapshot"]
    filename = f"grail-{name}.zip"
    data = build_zip(out, name)
    digest = hashlib.sha256(data).hexdigest()

    found = api("GET", "media", params=f"?search=grail-{name}&per_page=20")
    media = next((m for m in found if m.get("source_url", "").endswith(filename)), None)
    if media is not None and fetch_sha(media["source_url"]) != digest:
        api("DELETE", f"media/{media['id']}", params="?force=true")  # an earlier upload of different bytes
        media = None
    if media is None:
        # A multipart form upload, as a browser sends it: the host's firewall refuses raw-body uploads.
        boundary = hashlib.sha256(data).hexdigest()[:32]
        form = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: application/zip\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        media = api("POST", "media", body=form, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        api("POST", f"media/{media['id']}", body=json.dumps({"title": f"Grail {name}",
            "description": f"Verified copy of the RAPP Brainstem kernel {name}. SHA-256 {digest}"}).encode(),
            headers={"Content-Type": "application/json"})
    zip_url = media["source_url"]
    if fetch_sha(zip_url) != digest:
        raise SystemExit(f"{zip_url} does not serve the bytes uploaded; refusing to publish the page")

    # WordPress turns a newline inside a paragraph into a visible line break; keep each paragraph on one line.
    content = re.sub(r"<p>.*?</p>", lambda m: " ".join(m.group(0).split()), page_html(beacon, zip_url, digest), flags=re.S)
    pages = api("GET", "pages", params=f"?slug={SLUG}&status=publish,draft,private")
    body = json.dumps({"title": "Grail vault", "slug": SLUG, "status": "publish", "content": content}).encode()
    if pages:
        page = api("POST", f"pages/{pages[0]['id']}", body=body, headers={"Content-Type": "application/json"})
    else:
        page = api("POST", "pages", body=body, headers={"Content-Type": "application/json"})
    (out / "kodyw.json").write_text(json.dumps({"snapshot": name, "zip": zip_url, "sha256": digest,
                                                "page": page["link"]}, indent=2) + "\n")
    print(json.dumps({"page": page["link"], "zip": zip_url, "sha256": digest}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

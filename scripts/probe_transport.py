#!/usr/bin/env python3
"""Fixed-target connectivity probe. It never prints response bodies."""

import hashlib
import socket
import time
from urllib.parse import quote
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError

TARGET = "https://torgi.gov.ru/new/opendata/7710568760-notice/meta.json"
URLS = [
    TARGET,
    TARGET.replace("https://", "http://", 1),
    TARGET.replace("torgi.gov.ru", "www.torgi.gov.ru"),
    TARGET.replace("https://torgi.gov.ru", "http://www.torgi.gov.ru"),
    "https://r.jina.ai/http://torgi.gov.ru/new/opendata/7710568760-notice/meta.json",
    "https://r.jina.ai/https://torgi.gov.ru/new/opendata/7710568760-notice/meta.json",
    "https://api.allorigins.win/raw?url=" + quote(TARGET, safe=""),
]


def main():
    for host in ("torgi.gov.ru", "www.torgi.gov.ru", "r.jina.ai", "api.allorigins.win"):
        try:
            addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
            print(f"dns host={host} addresses={','.join(addresses)}")
        except OSError as exc:
            print(f"dns host={host} error={type(exc).__name__}")
    opener = build_opener()
    for url in URLS:
        started = time.monotonic()
        try:
            request = Request(url, headers={"User-Agent": "ReestrScan-Transport-Probe/1", "Range": "bytes=0-4095"})
            with opener.open(request, timeout=15) as response:
                raw = response.read(4096)
                print(
                    "probe "
                    f"url={url} status={response.status} bytes={len(raw)} "
                    f"type={response.headers.get('content-type', '')!r} "
                    f"sha256={hashlib.sha256(raw).hexdigest()[:16]} "
                    f"seconds={time.monotonic() - started:.2f}"
                )
        except HTTPError as exc:
            print(f"probe url={url} http={exc.code} seconds={time.monotonic() - started:.2f}")
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            print(f"probe url={url} error={type(reason).__name__} seconds={time.monotonic() - started:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

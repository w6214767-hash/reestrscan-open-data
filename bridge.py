#!/usr/bin/env python3
"""Build a small normalized auction feed from the free official GIS Torgi OpenData."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

META_URL = "https://torgi.gov.ru/new/opendata/7710568760-notice/meta.json"
ALLOWED_HOST = "torgi.gov.ru"
RU_ROOT_CA_URL = "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt"
RU_ROOT_CA_SHA256 = "936a43fea6e8e525bcc0f81acd9c3d21b4fc4b9b68acea7906d698005afc6504"
RU_SUB_CA_URL = "http://nuc-cdp.voskhod.ru/cdp/subca_ssl_rsa2024.crt"
RU_SUB_CA_SHA256 = "6f9d829c8e6712444fce3624658d8788672849c5d5b7b53fd9cf7e83eac4193e"
_CA_BUNDLE_LOCK = threading.Lock()
_CA_TEMP_DIRECTORY: tempfile.TemporaryDirectory[str] | None = None
_CA_BUNDLE_PATH: Path | None = None
_PINNED_TLS_CONTEXT_LOCK = threading.Lock()
_PINNED_TLS_CONTEXT: ssl.SSLContext | None = None
SOURCE_ID = "gis-torgi"
REGIONS = {"50": ("moskovskaya-oblast", "Московская область"), "77": ("moskva", "Москва")}
CAD = re.compile(r"\b\d{2}\s*:\s*\d{2}\s*:\s*\d{6,7}\s*:\s*\d+\b")
RU = str.maketrans({
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y",
    "к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f",
    "х":"h","ц":"ts","ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
})
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


class BridgeError(RuntimeError):
    pass


class SameOriginRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute = urljoin(req.full_url, newurl)
        _approved_url(absolute)
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def _approved_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != ALLOWED_HOST
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise BridgeError("unapproved_upstream_url")
    return value


def _decode_json(raw: bytes, url: str) -> Any:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    elif raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".json")]
            if not names:
                raise BridgeError("archive_without_json")
            raw = archive.read(sorted(names)[0])
    if len(raw) > 128 * 1024 * 1024:
        raise BridgeError("decompressed_response_too_large")
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("invalid_upstream_json") from exc



def _network_error_code(exc: BaseException) -> str:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "upstream_tls_certificate_error"
    if isinstance(reason, ssl.SSLError):
        return "upstream_tls_error"
    if isinstance(reason, socket.gaierror):
        return "upstream_dns_error"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "upstream_timeout"
    if isinstance(reason, ConnectionRefusedError):
        return "upstream_connection_refused"
    if isinstance(reason, ConnectionResetError):
        return "upstream_connection_reset"
    return f"upstream_network_error_{type(reason).__name__.casefold()}"



def _run_curl(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout + 5,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeError("upstream_timeout") from exc


def _download_pinned_certificate(
    curl: Path,
    destination: Path,
    *,
    url: str,
    sha256: str,
    timeout: int,
) -> None:
    approved = {
        RU_ROOT_CA_URL: ("https", "ru_root_ca"),
        RU_SUB_CA_URL: ("http", "ru_sub_ca"),
    }
    if url not in approved:
        raise BridgeError("unapproved_ca_url")
    scheme, label = approved[url]
    command = [
        str(curl),
        "--fail",
        "--silent",
        "--show-error",
        "--proto",
        f"={scheme}",
        "--connect-timeout",
        str(min(timeout, 15)),
        "--max-time",
        str(timeout),
        "--max-filesize",
        str(64 * 1024),
        "--output",
        str(destination),
        url,
    ]
    result = _run_curl(command, timeout)
    if result.returncode:
        raise BridgeError(f"{label}_download_failed")
    raw = destination.read_bytes()
    if (
        len(raw) > 64 * 1024
        or hashlib.sha256(raw).hexdigest() != sha256
        or b"-----BEGIN CERTIFICATE-----" not in raw
        or b"-----END CERTIFICATE-----" not in raw
    ):
        raise BridgeError(f"{label}_integrity_error")


def _build_pinned_ru_ca_bundle(curl: Path, directory: Path, timeout: int) -> Path:
    root = directory / "russian-trusted-root.pem"
    intermediate = directory / "russian-trusted-sub-2024.pem"
    bundle = directory / "russian-trusted-bundle.pem"
    _download_pinned_certificate(
        curl,
        root,
        url=RU_ROOT_CA_URL,
        sha256=RU_ROOT_CA_SHA256,
        timeout=timeout,
    )
    _download_pinned_certificate(
        curl,
        intermediate,
        url=RU_SUB_CA_URL,
        sha256=RU_SUB_CA_SHA256,
        timeout=timeout,
    )
    bundle.write_bytes(
        root.read_bytes().rstrip() + b"\n" + intermediate.read_bytes().rstrip() + b"\n"
    )
    return bundle


def _cached_pinned_ru_ca_bundle(curl: Path, timeout: int) -> Path:
    global _CA_BUNDLE_PATH, _CA_TEMP_DIRECTORY
    with _CA_BUNDLE_LOCK:
        if _CA_BUNDLE_PATH is not None and _CA_BUNDLE_PATH.is_file():
            return _CA_BUNDLE_PATH
        temp_directory = tempfile.TemporaryDirectory(prefix="reestrscan-ca-")
        try:
            bundle = _build_pinned_ru_ca_bundle(curl, Path(temp_directory.name), timeout)
        except BaseException:
            temp_directory.cleanup()
            raise
        _CA_TEMP_DIRECTORY = temp_directory
        _CA_BUNDLE_PATH = bundle
        return bundle


def _pinned_tls_context(curl: Path, timeout: int) -> ssl.SSLContext:
    global _PINNED_TLS_CONTEXT
    with _PINNED_TLS_CONTEXT_LOCK:
        if _PINNED_TLS_CONTEXT is None:
            ca_file = _cached_pinned_ru_ca_bundle(curl, timeout)
            _PINNED_TLS_CONTEXT = ssl.create_default_context(cafile=str(ca_file))
        return _PINNED_TLS_CONTEXT

def _curl_fetch_command(
    curl: Path,
    url: str,
    output: Path,
    *,
    timeout: int,
    max_bytes: int,
    ca_file: Path | None = None,
) -> list[str]:
    command = [
        str(curl),
        "--fail",
        "--silent",
        "--show-error",
        "--compressed",
        "--proto",
        "=https",
        "--connect-timeout",
        str(min(timeout, 15)),
        "--max-time",
        str(timeout),
        "--max-filesize",
        str(max_bytes),
        "--header",
        "Accept: application/json, application/zip",
        "--user-agent",
        USER_AGENT,
    ]
    if ca_file is not None:
        command.extend(["--cacert", str(ca_file)])
    command.extend(["--output", str(output), url])
    return command


def _fetch_with_system_curl(url: str, *, timeout: int, max_bytes: int) -> Any:
    curl = Path("/usr/bin/curl")
    if not curl.is_file():
        raise BridgeError("upstream_tls_certificate_error")
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "response.bin"
        command = _curl_fetch_command(curl, url, output, timeout=timeout, max_bytes=max_bytes)
        result = _run_curl(command, timeout)
        if result.returncode == 60:
            _pinned_tls_context(curl, timeout)
            ca_file = _cached_pinned_ru_ca_bundle(curl, timeout)
            command = _curl_fetch_command(
                curl,
                url,
                output,
                timeout=timeout,
                max_bytes=max_bytes,
                ca_file=ca_file,
            )
            result = _run_curl(command, timeout)
        error_codes = {
            6: "upstream_dns_error",
            7: "upstream_connection_refused",
            28: "upstream_timeout",
            35: "upstream_tls_error",
            56: "upstream_connection_reset",
            60: "upstream_tls_certificate_error",
            63: "upstream_response_too_large",
        }
        if result.returncode:
            raise BridgeError(error_codes.get(result.returncode, f"upstream_curl_error_{result.returncode}"))
        raw = output.read_bytes()
    if len(raw) > max_bytes:
        raise BridgeError("upstream_response_too_large")
    return _decode_json(raw, url)

def fetch_json(url: str, *, timeout: int = 30, max_bytes: int = 64 * 1024 * 1024) -> Any:
    _approved_url(url)
    # curl's --max-time bounds the entire transfer, including slow response bodies.
    # urllib's socket timeout only bounds individual reads and could stall a job.
    if Path('/usr/bin/curl').is_file():
        return _fetch_with_system_curl(url, timeout=timeout, max_bytes=max_bytes)
    handlers: list[Any] = [SameOriginRedirects()]
    if _PINNED_TLS_CONTEXT is not None:
        handlers.append(HTTPSHandler(context=_PINNED_TLS_CONTEXT))
    opener = build_opener(*handlers)
    request = Request(url, headers={"Accept": "application/json, application/zip", "User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
    except HTTPError as exc:
        raise BridgeError(f"upstream_http_{exc.code}") from None
    except (URLError, TimeoutError, OSError) as exc:
        code = _network_error_code(exc)
        if code == "upstream_tls_certificate_error":
            return _fetch_with_system_curl(url, timeout=timeout, max_bytes=max_bytes)
        raise BridgeError(code) from exc
    if len(raw) > max_bytes:
        raise BridgeError("upstream_response_too_large")
    return _decode_json(raw, url)


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        for key in ("name", "value", "fullName", "displayName", "fullAddress"):
            result = _text(value.get(key))
            if result:
                return result
    if isinstance(value, list):
        parts = [result for item in value if (result := _text(item))]
        return ", ".join(parts) or None
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        for key in ("value", "amount", "sum"):
            result = _decimal(value.get(key))
            if result is not None:
                return result
        return None
    try:
        return Decimal(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _minor(value: Any) -> int:
    number = _decimal(value)
    return int((number * 100).quantize(Decimal("1"))) if number is not None and number >= 0 else 0


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value.strip(), pattern)
                break
            except ValueError:
                continue
        else:
            return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold().translate(RU)).strip("-")[:80] or "region-wide"


def _notice(payload: Any) -> dict[str, Any] | None:
    node = payload
    for key in ("exportObject", "structuredObject", "notice"):
        node = node.get(key) if isinstance(node, dict) else None
    if isinstance(node, dict):
        return node
    return payload if isinstance(payload, dict) and isinstance(payload.get("lots"), list) else None


def _characteristic(info: dict[str, Any], *codes: str) -> Any:
    wanted = {code.casefold() for code in codes}
    for node in _walk(info):
        if not isinstance(node, dict) or not isinstance(node.get("characteristics"), list):
            continue
        for item in node["characteristics"]:
            if isinstance(item, dict) and str(item.get("code", "")).casefold() in wanted:
                return item.get("characteristicValue")
    return None


def _cadastres(info: dict[str, Any]) -> list[str]:
    chunks = [
        _characteristic(info, "CadastralNumber", "kadastrNumber"),
        info.get("cadastralNumbers"),
        info.get("estateAddress"),
    ]
    flattened = " ".join(str(node) for chunk in chunks if chunk for node in _walk(chunk))
    return list(dict.fromkeys(re.sub(r"\s+", "", match.group()) for match in CAD.finditer(flattened)))


def _area(info: dict[str, Any]) -> float | None:
    candidates = (
        info.get("estateArea"),
        info.get("area"),
        _characteristic(info, "SquareZU", "SquareZU_project", "totalAreaRealty", "EstateArea", "LotSquare"),
    )
    for raw in candidates:
        value = _decimal(raw)
        if value is not None and 0 < value <= Decimal("1000000000000"):
            return float(value)
    return None


def _municipality(info: dict[str, Any], region_name: str) -> tuple[str, str]:
    for node in _walk(info.get("estateAddressFIAS", {})):
        if not isinstance(node, dict) or not isinstance(node.get("hierarchyObjects"), list):
            continue
        for item in node["hierarchyObjects"]:
            level = item.get("level", {}) if isinstance(item, dict) else {}
            name = _text(item.get("name")) if isinstance(item, dict) else None
            if str(level.get("code")) == "3" and name:
                return _slug(name), name[:120]
    return "region-wide", region_name


def _category(lot: dict[str, Any], info: dict[str, Any]) -> str:
    haystack = " ".join(filter(None, [
        _text(info.get("category")), _text(lot.get("lotName")), _text(lot.get("lotDescription"))
    ])).casefold()
    groups = (
        ("zemlya", ("земел", "участ")),
        ("kvartiry", ("квартир", "комнат")),
        ("doma", ("жилой дом", "домовлад", "коттедж")),
        ("transport", ("автомоб", "транспорт", "машин", "прицеп")),
        ("equipment", ("оборудован", "станок", "линия")),
        ("commercial", ("помещен", "здани", "сооружен", "нежил")),
    )
    return next((kind for kind, words in groups if any(word in haystack for word in words)), "other")


def _transaction(notice: dict[str, Any], lot: dict[str, Any]) -> tuple[str, str]:
    common = notice.get("commonInfo", {})
    haystack = " ".join(filter(None, [
        _text(common.get("procedureName")), _text(common.get("biddType")),
        _text(lot.get("lotName")), _text(lot.get("lotDescription")),
    ])).casefold()
    if re.search(r"ежегодн\w*\s+аренд", haystack):
        return "annual_rent", "Ежегодная арендная плата"
    if "аренд" in haystack:
        return "lease_right", "Право заключения договора аренды"
    return "sale", "Продажа имущества"


def _procedure(notice: dict[str, Any], lot: dict[str, Any]) -> str:
    text = (str(notice) + str(lot)).casefold()
    if "банкрот" in text:
        return "bankruptcy"
    if "арестован" in text or "исполнительн" in text:
        return "seized"
    return "municipal"


def _status(lot: dict[str, Any], deadline: datetime | None, current: datetime) -> str:
    raw = str(lot.get("lotStatus", "")).upper()
    if any(word in raw for word in ("CANCEL", "ANNUL", "WITHDRAW")):
        return "cancelled"
    if any(word in raw for word in ("COMPLETE", "RESULT", "CONTRACT", "FINISH")) or (
        deadline is not None and deadline <= current
    ):
        return "closed"
    if deadline and any(word in raw for word in ("PUBLISH", "BIDD", "APPLICATION", "ACTIVE")):
        return "accepting"
    return "announced"


def _region_code(row: dict[str, Any]) -> str:
    for key in ("subjectEstateCode", "subjectRFCode", "regionCode"):
        value = row.get(key)
        if value is not None:
            return str(value).strip().zfill(2)
    return ""


def _row_date(row: dict[str, Any]) -> datetime:
    for key in ("publishDate", "lastUpdateDate", "createDate"):
        if parsed := _date(row.get(key)):
            return parsed
    return datetime.min.replace(tzinfo=timezone.utc)


def _source_entries(meta: Any) -> list[dict[str, Any]]:
    if isinstance(meta, dict) and isinstance(meta.get("data"), list):
        entries = [item for item in meta["data"] if isinstance(item, dict) and item.get("source")]
    else:
        entries = [
            node for node in _walk(meta)
            if isinstance(node, dict) and node.get("source") and isinstance(node.get("source"), str)
        ]
    output = []
    for item in entries:
        absolute = urljoin(META_URL, str(item["source"]))
        filename = Path(urlsplit(absolute).path).name.casefold()
        if filename in {"meta.json", "list.json"} or filename.startswith("structure-"):
            continue
        try:
            _approved_url(absolute)
        except BridgeError:
            continue
        output.append({**item, "source": absolute})
    if not output:
        raise BridgeError("metadata_without_data_sources")
    return output


def discover_sources(meta: Any, days: int) -> list[str]:
    entries = _source_entries(meta)

    def key(item: dict[str, Any]) -> tuple[str, str]:
        stamp = next(
            (str(item.get(name)) for name in ("created", "modified", "date", "version") if item.get(name)),
            "",
        )
        return stamp, item["source"]

    ordered = sorted(entries, key=key, reverse=True)
    return [item["source"] for item in ordered[:days]]


def extract_index_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("listObjects", "objects", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list) and (not value or isinstance(value[0], dict)):
                return [item for item in value if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    raise BridgeError("data_file_without_index_rows")


def select_rows(payloads: list[Any], max_details: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = [row for payload in payloads for row in extract_index_rows(payload)]
    target = [row for row in rows if _region_code(row) in REGIONS]
    newest: dict[str, dict[str, Any]] = {}
    for row in sorted(target, key=_row_date):
        reg_num = str(row.get("regNum", "")).strip()
        href = str(row.get("href", "")).strip()
        if reg_num and href:
            newest[reg_num] = row
    selected = sorted(newest.values(), key=_row_date, reverse=True)[:max_details]
    return selected, {
        "index_rows": len(rows),
        "target_rows": len(target),
        "unique_notices": len(newest),
        "selected_notices": len(selected),
    }


def fetch_details(rows: list[dict[str, Any]], workers: int) -> tuple[dict[str, Any], int]:
    details: dict[str, Any] = {}
    failures = 0

    def load(row: dict[str, Any]) -> tuple[str, str, Any]:
        href = urljoin(META_URL, str(row["href"]))
        _approved_url(href)
        return str(row["regNum"]), href, fetch_json(href, max_bytes=16 * 1024 * 1024)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(load, row): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            try:
                reg_num, href, payload = future.result()
                details[reg_num] = payload
                details[href] = payload
                details[str(row["href"])] = payload
            except BridgeError as exc:
                failures += 1
                print(f'detail_failed={exc} failures={failures}', flush=True)
            completed = len(details) // 3 + failures
            print(f'details_progress={completed}/{len(rows)}', flush=True)
    return details, failures


def build_feed(
    index_rows: list[dict[str, Any]],
    details: dict[str, Any],
    *,
    current: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    current = (current or datetime.now(timezone.utc)).astimezone(timezone.utc)
    output: list[dict[str, Any]] = []
    counts = {
        "details_missing": 0,
        "details_invalid": 0,
        "regions_skipped": 0,
        "lots": 0,
    }
    for row in index_rows:
        reg_num = str(row.get("regNum", "")).strip()
        href = str(row.get("href", "")).strip()
        payload = details.get(href) or details.get(reg_num)
        if not payload:
            counts["details_missing"] += 1
            continue
        notice = _notice(payload)
        lots = notice.get("lots") if notice else None
        common = notice.get("commonInfo", {}) if notice else {}
        published = _date(common.get("publishDate")) or _date(row.get("publishDate"))
        if not isinstance(lots, list) or published is None or not reg_num:
            counts["details_invalid"] += 1
            continue
        org = notice.get("bidderOrg", {})
        org = org.get("orgInfo", {}) if isinstance(org, dict) else {}
        organizer = (_text(org.get("name")) or "")[:300]
        for ordinal, lot in enumerate(lots, 1):
            if not isinstance(lot, dict):
                continue
            info = lot.get("biddingObjectInfo") if isinstance(lot.get("biddingObjectInfo"), dict) else lot
            subject = info.get("subjectRF", {}) if isinstance(info.get("subjectRF"), dict) else {}
            code = str(subject.get("code") or _region_code(row)).strip().zfill(2)
            if code not in REGIONS:
                counts["regions_skipped"] += 1
                continue
            region, region_name = REGIONS[code]
            municipality, municipality_name = _municipality(info, region_name)
            conditions = (
                lot.get("biddConditions")
                if isinstance(lot.get("biddConditions"), dict)
                else notice.get("biddConditions", {})
            )
            deadline = (
                _date(conditions.get("biddEndTime") or conditions.get("endDate") or lot.get("biddEndTime"))
                if isinstance(conditions, dict)
                else None
            )
            transaction, right = _transaction(notice, lot)
            number = str(lot.get("lotNumber") or ordinal)
            external_id = f"{reg_num}:{number}"
            category = _category(lot, info)
            cadastral = _cadastres(info)
            ids = cadastral or [external_id]
            href_ui = _text(common.get("href"))
            source_url = (
                href_ui
                if href_ui and href_ui.startswith("https://torgi.gov.ru/")
                else f"https://torgi.gov.ru/new/public/notices/view/{reg_num}"
            )
            address = (_text(info.get("estateAddress")) or municipality_name)[:500]
            output.append({
                "external_id": external_id,
                "title": (_text(lot.get("lotName")) or f"Лот {number} · {reg_num}")[:250],
                "description": (_text(lot.get("lotDescription")) or "")[:15000],
                "region": region,
                "region_name": region_name,
                "municipality": municipality,
                "municipality_name": municipality_name,
                "category": category,
                "transaction": transaction,
                "status": _status(lot, deadline, current),
                "price_minor": _minor(
                    lot.get("priceMin")
                    or lot.get("priceMinVAT")
                    or _characteristic(info, "StartPrice", "InitialPrice", "MinPrice")
                ),
                "deposit_minor": _minor(lot.get("deposit")) if lot.get("deposit") is not None else None,
                "right_description": right,
                "procedure": _procedure(notice, lot),
                "procedure_id": reg_num,
                "source_url": source_url,
                "platform_url": None,
                "organizer": organizer,
                "deadline": _iso(deadline),
                "source_updated_at": _iso(published),
                "assets": [{
                    "external_id": f"{external_id}:{ident}"[:200],
                    "kind": category,
                    "cadastral_number": ident if ident in cadastral else None,
                    "address": address,
                    "area_m2": _area(info),
                    "latitude": None,
                    "longitude": None,
                    "location_accuracy": "unknown",
                } for ident in ids[:100]],
                "documents": [],
            })
            counts["lots"] += 1

    deduplicated = {lot["external_id"]: lot for lot in output}
    feed = {
        "version": 1,
        "source_id": SOURCE_ID,
        "lots": sorted(deduplicated.values(), key=lambda lot: lot["external_id"]),
    }
    validate_feed(feed)
    return feed, counts


def validate_feed(feed: dict[str, Any]) -> None:
    if feed.get("version") != 1 or feed.get("source_id") != SOURCE_ID:
        raise BridgeError("invalid_feed_header")
    lots = feed.get("lots")
    if not isinstance(lots, list) or len(lots) > 10000:
        raise BridgeError("invalid_feed_size")
    required = {
        "external_id", "title", "region", "region_name", "municipality", "municipality_name",
        "category", "transaction", "status", "price_minor", "right_description", "procedure",
        "procedure_id", "source_url", "source_updated_at", "assets",
    }
    seen: set[str] = set()
    for lot in lots:
        if not isinstance(lot, dict) or not required.issubset(lot):
            raise BridgeError("invalid_lot")
        if lot["external_id"] in seen:
            raise BridgeError("duplicate_lot")
        seen.add(lot["external_id"])
        if not isinstance(lot["assets"], list) or not lot["assets"]:
            raise BridgeError("lot_without_asset")
        if not str(lot["source_url"]).startswith("https://torgi.gov.ru/"):
            raise BridgeError("invalid_source_url")


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    temp.replace(path)


def update(days: int, max_details: int, workers: int, output: Path, status_path: Path) -> dict[str, Any]:
    started = time.monotonic()
    print('stage=metadata', flush=True)
    meta = fetch_json(META_URL, max_bytes=4 * 1024 * 1024)
    sources = discover_sources(meta, days)
    print(f'stage=index files={len(sources)}', flush=True)
    payloads = []
    for number, source in enumerate(sources, 1):
        print(f'index_start={number}/{len(sources)}', flush=True)
        payloads.append(fetch_json(source))
        print(f'index_done={number}/{len(sources)} seconds={time.monotonic()-started:.1f}', flush=True)
    rows, index_counts = select_rows(payloads, max_details)
    print(f'stage=details selected={len(rows)} workers={workers}', flush=True)
    if not rows:
        raise BridgeError("no_target_notices")
    details, detail_failures = fetch_details(rows, workers)
    feed, mapper_counts = build_feed(rows, details)
    if not feed["lots"]:
        raise BridgeError("empty_feed_refused")
    generated_at = _iso(datetime.now(timezone.utc))
    status = {
        "ok": True,
        "generated_at": generated_at,
        "source_id": SOURCE_ID,
        "regions": sorted(REGIONS),
        "source_files": len(sources),
        "detail_failures": detail_failures,
        "partial": detail_failures > 0 or index_counts['unique_notices'] > len(rows),
        "duration_seconds": round(time.monotonic() - started, 1),
        **index_counts,
        **mapper_counts,
    }
    atomic_json(output, feed)
    atomic_json(status_path, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max-details", type=int, default=2500)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, default=Path("public/feed-v1.json"))
    parser.add_argument("--status", type=Path, default=Path("public/status.json"))
    args = parser.parse_args()
    if not 1 <= args.days <= 90 or not 1 <= args.max_details <= 10000 or not 1 <= args.workers <= 16:
        parser.error("arguments outside safe limits")
    try:
        status = update(args.days, args.max_details, args.workers, args.output, args.status)
    except BridgeError as exc:
        print(f"bridge_error={exc}", file=sys.stderr)
        return 2
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

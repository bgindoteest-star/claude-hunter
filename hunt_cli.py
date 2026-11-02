#!/usr/bin/env python3
"""claude-hunt CLI — hunter link referral/invite Claude Pro multi-source.

Usage:
    ./hunt scan                        # semua source, simpan link baru ke DB
    ./hunt scan --sources urlscan,github --validate --notify
    ./hunt urlscan                     # satu source saja (tampilkan semua temuan)
    ./hunt github --deep               # GitHub issues, 3 halaman per query
    ./hunt archive                     # Internet Archive CDX + Common Crawl
    ./hunt reddit                      # Reddit JSON (butuh IP non-datacenter)
    ./hunt ddg                         # DuckDuckGo Lite
    ./hunt telegram                    # t.me/s channels dari .env
    ./hunt validate <url> [url...]     # cek hidup/mati via HEAD
    ./hunt list                        # isi database temuan
    ./hunt export --format csv         # ekspor temuan

Semua source publik & tanpa auth. Rate limit dijaga: jitter + backoff.
"""

import argparse
import csv
import json
import random
import sys
import time
from pathlib import Path

import httpx

from hunter import (
    BROWSER_UA,
    DDG_QUERY,
    LINK_RE,
    REDDIT_UA,
    TELEGRAM_CHANNELS,
    extract_links,
    jitter_pause,
    load_seen,
    log,
    now_wib,
    send_telegram,
    validate_link,
)

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "found_links.jsonl"
SEEN_FILE = BASE_DIR / "seen_links.txt"

URLSCAN_PATTERNS = [
    "domain:claude.ai AND page.url:invite",
    "domain:claude.ai AND page.url:claim",
    "domain:claude.ai AND page.url:referral",
    "domain:claude.ai AND page.url:promo",
    "domain:claude.ai AND page.url:credit",
    "domain:claude.ai AND page.url:claim-credit",
    "domain:claude.ai AND page.url:join",
    "domain:claude.ai AND page.url:gift",
    "domain:claude.ai AND page.url:friend",
    "domain:claude.ai AND page.url:reward",
    "domain:claude.ai AND page.url:trial",
    "domain:claude.ai AND page.url:free",
    "domain:claude.ai AND page.url:bulk",
]

GITHUB_QUERIES = [
    '"claude.ai/referral"',
    '"claude.ai/invite"',
    '"claude.ai/claim"',
    '"claude.ai/join"',
    '"claude.ai/promo"',
    '"claim-credit"',
    '"claude.ai/code/claim-credit"',
    '"claude pro referral"',
    '"claude invite link"',
]

ARCHIVE_PATTERNS = [
    "claude.ai/invite*",
    "claude.ai/referral*",
    "claude.ai/claim*",
    "claude.ai/join*",
    "claude.ai/promo*",
    "claude.ai/code*",
    "claude.ai/credit*",
    "claude.ai/gift*",
    "claude.ai/invites*",
]

ALL_SOURCES = ["urlscan", "github", "archive", "reddit", "ddg", "telegram"]


# ---------------------------------------------------------------- db

def load_db() -> dict[str, dict]:
    """url -> record dari found_links.jsonl + seen_links.txt."""
    db: dict[str, dict] = {}
    if DB_FILE.exists():
        for line in DB_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                db[rec["url"]] = rec
            except (ValueError, KeyError):
                continue
    for url in load_seen():
        db.setdefault(url, {"url": url, "source": "manual-hunt",
                            "first_seen": None, "status": None})
    return db


def save_db(db: dict[str, dict]) -> None:
    with DB_FILE.open("w", encoding="utf-8") as fh:
        for rec in db.values():
            fh.write(json.dumps(rec) + "\n")


def normalize(url: str) -> str:
    url = url.strip().rstrip(".,);\"'")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def record(url: str, source: str) -> tuple[str, bool]:
    """Kembalikan (url, is_new) dan catat ke DB kalau baru."""
    url = normalize(url)
    db = load_db()
    if url in db:
        return url, False
    db[url] = {
        "url": url,
        "source": source,
        "first_seen": now_wib().strftime("%Y-%m-%d %H:%M:%S"),
        "status": None,
    }
    save_db(db)
    with SEEN_FILE.open("a", encoding="utf-8") as fh:
        fh.write(url + "\n")
    return url, True


# ---------------------------------------------------------------- scanners

def scan_urlscan(client: httpx.Client,
                 patterns: list[str] | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for q in patterns or URLSCAN_PATTERNS:
        tag = q.split("page.url:")[-1]
        try:
            resp = client.get(
                "https://urlscan.io/api/v1/search/",
                params={"q": q, "size": 100},
                headers={"User-Agent": BROWSER_UA},
                timeout=25.0,
            )
            if resp.status_code != 200:
                log(f"urlscan[{tag}] HTTP {resp.status_code}")
                jitter_pause()
                continue
            results = resp.json().get("results", [])
            for res in results:
                url = res.get("page", {}).get("url", "")
                if url:
                    out.append((url, f"urlscan:{tag}"))
            log(f"urlscan[{tag}] {len(results)} hasil")
        except (httpx.HTTPError, ValueError) as exc:
            log(f"urlscan[{tag}] error: {exc!r}")
        jitter_pause()
    return out


def scan_github(client: httpx.Client, queries: list[str] | None = None,
                pages: int = 1) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    headers = {"User-Agent": BROWSER_UA,
               "Accept": "application/vnd.github+json"}
    for q in queries or GITHUB_QUERIES:
        for page in range(1, pages + 1):
            try:
                resp = client.get(
                    "https://api.github.com/search/issues",
                    params={"q": q, "per_page": 100, "page": page,
                            "sort": "created", "order": "desc"},
                    headers=headers,
                    timeout=25.0,
                )
                if resp.status_code in (403, 429):
                    log(f"github[{q}] HTTP {resp.status_code} — rate limit, "
                        f"tunggu 60s")
                    time.sleep(60)
                    continue
                if resp.status_code != 200:
                    log(f"github[{q}] HTTP {resp.status_code}")
                    break
                items = resp.json().get("items", [])
                for it in items:
                    blob = f"{it.get('title') or ''}\n{it.get('body') or ''}"
                    for link in extract_links(blob):
                        out.append((link, "github"))
                log(f"github[{q}] halaman {page}: {len(items)} item")
                if len(items) < 100:
                    break
            except (httpx.HTTPError, ValueError) as exc:
                log(f"github[{q}] error: {exc!r}")
                break
            jitter_pause()
    return out


def scan_archive(client: httpx.Client,
                 patterns: list[str] | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    # Internet Archive CDX
    for pat in patterns or ARCHIVE_PATTERNS:
        try:
            resp = client.get(
                "http://web.archive.org/cdx/search/cdx",
                params={"url": pat, "output": "text", "fl": "original",
                        "collapse": "urlkey", "limit": 200},
                headers={"User-Agent": BROWSER_UA},
                timeout=40.0,
            )
            if "Temporarily Offline" in resp.text:
                log("archive: Internet Archive sedang offline, skip")
                break
            if resp.status_code != 200:
                log(f"archive[{pat}] HTTP {resp.status_code}")
                jitter_pause()
                continue
            urls = [ln.strip() for ln in resp.text.splitlines() if ln.strip()]
            for url in urls:
                out.append((url, "archive"))
            log(f"archive[{pat}] {len(urls)} url")
        except httpx.HTTPError as exc:
            log(f"archive[{pat}] error: {exc!r}")
        jitter_pause()
    # Common Crawl (2 index terbaru)
    try:
        colls = client.get("https://index.commoncrawl.org/collinfo.json",
                           headers={"User-Agent": BROWSER_UA},
                           timeout=25.0).json()
        apis = [c["cdx-api"] for c in colls[:2]]
        for pat in patterns or ARCHIVE_PATTERNS:
            for api in apis:
                try:
                    resp = client.get(api, params={"url": pat,
                                                   "output": "json",
                                                   "limit": 100},
                                      headers={"User-Agent": BROWSER_UA},
                                      timeout=40.0)
                    if resp.status_code != 200:
                        continue
                    n = 0
                    for line in resp.text.splitlines():
                        try:
                            rec = json.loads(line)
                            out.append((rec["url"], "commoncrawl"))
                            n += 1
                        except ValueError:
                            continue
                    log(f"commoncrawl[{pat}@{api.rsplit('/', 1)[-1]}] {n} url")
                except httpx.HTTPError:
                    continue
                jitter_pause()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log(f"commoncrawl error: {exc!r}")
    return out


def scan_reddit(client: httpx.Client) -> list[tuple[str, str]]:
    from hunter import scan_reddit as _sr
    return _sr(client)


def scan_ddg(client: httpx.Client) -> list[tuple[str, str]]:
    from hunter import scan_ddg as _sd
    return _sd(client)


def scan_telegram(client: httpx.Client) -> list[tuple[str, str]]:
    from hunter import scan_telegram as _st
    return _st(client)


SCANNERS = {
    "urlscan": scan_urlscan,
    "github": scan_github,
    "archive": scan_archive,
    "reddit": scan_reddit,
    "ddg": scan_ddg,
    "telegram": scan_telegram,
}


# ---------------------------------------------------------------- commands

def emit(found: list[tuple[str, str]], json_out: bool) -> int:
    """Tampilkan temuan dengan penanda [NEW]/[known]. Kembalikan jumlah baru."""
    new_count = 0
    for url, source in found:
        url = normalize(url)
        if not LINK_RE.fullmatch(url) and not LINK_RE.match(url):
            continue
        _, is_new = record(url, source)
        if json_out:
            print(json.dumps({"url": url, "source": source, "new": is_new}))
        else:
            mark = "NEW " if is_new else "known"
            print(f"[{mark}] {source:<22} {url}")
        if is_new:
            new_count += 1
    return new_count


def cmd_scan(args) -> int:
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in sources if s not in SCANNERS]
    if unknown:
        print(f"source tidak dikenal: {unknown}. Pilihan: {ALL_SOURCES}",
              file=sys.stderr)
        return 2
    log(f"scan mulai: sources={sources}")
    all_found: list[tuple[str, str]] = []
    with httpx.Client(http2=False) as client:
        for name in sources:
            try:
                found = SCANNERS[name](client)
            except Exception as exc:
                log(f"source {name} crash: {exc!r}")
                continue
            all_found.extend(found)
    new_count = emit(all_found, args.json)
    if args.validate:
        db = load_db()
        with httpx.Client(http2=False) as client:
            for url in list(db):
                if db[url].get("status") is None:
                    status = validate_link(client, url)
                    db[url]["status"] = status
                    log(f"validate {url} -> {status}")
            save_db(db)
    if args.notify and new_count:
        db = load_db()
        with httpx.Client(http2=False) as client:
            for url, rec in db.items():
                if rec.get("status") != "dead" and rec.get("first_seen"):
                    if send_telegram(client, url, rec["source"]):
                        log(f"notified {url}")
    log(f"scan selesai: {new_count} link baru dari {len(all_found)} kandidat")
    if not args.json:
        print(f"\n=== {new_count} link baru, total DB: {len(load_db())} ===")
    return 0


def cmd_source(args) -> int:
    name = args.command
    if name not in SCANNERS:
        print(f"source tidak dikenal: {name}", file=sys.stderr)
        return 2
    kwargs = {}
    if name == "github" and args.deep:
        kwargs["pages"] = 3
    with httpx.Client(http2=False) as client:
        found = SCANNERS[name](client, **kwargs)
    new_count = emit(found, args.json)
    if not args.json:
        print(f"\n=== {new_count} link baru, total DB: {len(load_db())} ===")
    return 0


def cmd_validate(args) -> int:
    urls = [normalize(u) for u in args.urls]
    if args.all:
        urls = list(load_db())
    if not urls:
        print("tidak ada url untuk divalidasi", file=sys.stderr)
        return 2
    with httpx.Client(http2=False) as client:
        for url in urls:
            status = validate_link(client, url)
            print(f"{status:<8} {url}")
            db = load_db()
            if url in db:
                db[url]["status"] = status
                save_db(db)
    return 0


def cmd_list(args) -> int:
    db = load_db()
    if not db:
        print("DB kosong.")
        return 0
    if args.json:
        for rec in db.values():
            print(json.dumps(rec))
        return 0
    print(f"{'URL':<70} {'SOURCE':<22} {'STATUS':<8} FIRST SEEN")
    for rec in sorted(db.values(), key=lambda r: r.get("first_seen") or ""):
        print(f"{rec['url']:<70} {rec.get('source', '?'):<22} "
              f"{rec.get('status') or '-':<8} {rec.get('first_seen') or '-'}")
    print(f"\ntotal: {len(db)}")
    return 0


def cmd_export(args) -> int:
    db = load_db()
    if not db:
        print("DB kosong.", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else BASE_DIR / f"found_links_export.{args.format}"
    if args.format == "txt":
        out.write_text("\n".join(sorted(db)) + "\n", encoding="utf-8")
    elif args.format == "csv":
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["url", "source", "status", "first_seen"])
            for rec in db.values():
                w.writerow([rec["url"], rec.get("source"),
                            rec.get("status"), rec.get("first_seen")])
    else:
        out.write_text(
            "\n".join(json.dumps(r) for r in db.values()) + "\n",
            encoding="utf-8")
    print(f"ekspor {len(db)} link -> {out} ({args.format})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="hunt",
        description="claude-hunt CLI — hunter link referral/invite Claude Pro")
    parser.add_argument("--json", action="store_true",
                        help="output JSONLines")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="jalankan multi-source dan simpan temuan")
    p.add_argument("--sources", default=",".join(ALL_SOURCES),
                   help=f"pisah koma. default: {','.join(ALL_SOURCES)}")
    p.add_argument("--validate", action="store_true",
                   help="HEAD-validate link baru")
    p.add_argument("--notify", action="store_true",
                   help="kirim alert Telegram (butuh token valid di .env)")
    p.set_defaults(func=cmd_scan)

    for name in ALL_SOURCES:
        p = sub.add_parser(name, help=f"scan source {name} saja")
        if name == "github":
            p.add_argument("--deep", action="store_true",
                           help="3 halaman per query")
        p.set_defaults(func=cmd_source)

    p = sub.add_parser("validate", help="cek hidup/mati link via HEAD")
    p.add_argument("urls", nargs="*")
    p.add_argument("--all", action="store_true", help="validasi seluruh DB")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("list", help="tampilkan isi DB temuan")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("export", help="ekspor DB ke file")
    p.add_argument("--format", choices=["txt", "csv", "json"], default="txt")
    p.add_argument("--out", default=None,
                   help="default: found_links_export.<format>")
    p.set_defaults(func=cmd_export)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

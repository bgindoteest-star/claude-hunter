#!/usr/bin/env python3
"""Claude Pro invite-link hunter.

Single-pass scanner for public sources (Reddit JSON, Telegram public
channel previews, DuckDuckGo Lite). Extracts claude.ai invite/claim/promo
links, optionally validates them, dedupes via seen_links.txt, and pushes
alerts to Telegram. Designed for cron: runs one pass, exits cleanly.

Usage:
    python3 hunter.py                # live run (needs .env token)
    python3 hunter.py --dry-run      # scan + print, no Telegram, no state write
    python3 hunter.py --no-validate  # skip HEAD validation
"""

import argparse
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from dotenv import load_dotenv
from selectolax.lexbor import LexborHTMLParser as HTMLParser

BASE_DIR = Path(__file__).resolve().parent
SEEN_FILE = BASE_DIR / "seen_links.txt"
LOG_FILE = BASE_DIR / "hunter.log"
ENV_FILE = BASE_DIR / ".env"

WIB = timezone(timedelta(hours=7))

load_dotenv(ENV_FILE)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_CHANNELS = [
    c.strip().lstrip("@")
    for c in os.getenv("TELEGRAM_CHANNELS", "").split(",")
    if c.strip()
]

REDDIT_UA = "linux:claude-hunter:v1.0 (by /u/dev)"
DDG_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

REDDIT_URLS = [
    "https://www.reddit.com/r/ClaudeAI/new.json?limit=25",
    "https://www.reddit.com/search.json?q=claude+invite+OR+claude+pro+trial&sort=new&limit=25",
]
DDG_QUERY = os.getenv(
    "DDG_QUERY",
    '"claude.ai/invite/" OR "claude.ai/claim/" OR "claude.ai/referral/"'
).strip() or '"claude.ai/invite/" OR "claude.ai/claim/" OR "claude.ai/referral/"'

LINK_RE = re.compile(
    r"https?://(?:www\.)?claude\.ai/(?:[a-z0-9-]+/)*"
    r"(?:invite|claim|promo|referral|credit|join|gift|reward|trial)"
    r"(?:(?:-[a-zA-Z0-9_\-]+)|(?:/[a-zA-Z0-9_\-]+))+",
    re.IGNORECASE,
)

REQUEST_TIMEOUT = 15.0
MAX_RETRIES = 3
MIN_PAUSE = 1.0
MAX_PAUSE = 2.5


def now_wib() -> datetime:
    return datetime.now(WIB)


def log(msg: str) -> None:
    """Append one timestamped line to hunter.log and echo to stdout."""
    line = f"[{now_wib().strftime('%Y-%m-%d %H:%M:%S')} WIB] {msg}"
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        print(f"[warn] cannot write log: {exc}", file=sys.stderr)


def jitter_pause() -> None:
    time.sleep(random.uniform(MIN_PAUSE, MAX_PAUSE))


def fetch(client: httpx.Client, url: str, ua: str, method: str = "GET",
          data: dict | None = None,
          params: dict | None = None) -> httpx.Response | None:
    """GET/POST with retry + backoff on 429/5xx/network errors."""
    headers = {"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if method == "POST":
                resp = client.post(url, headers=headers, data=data,
                                   timeout=REQUEST_TIMEOUT,
                                   follow_redirects=True)
            else:
                resp = client.get(url, headers=headers, params=params,
                                  timeout=REQUEST_TIMEOUT,
                                  follow_redirects=True)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 ** attempt + random.random()
                log(f"HTTP {resp.status_code} from {urlparse(url).netloc}, "
                    f"retry {attempt}/{MAX_RETRIES} in {wait:.1f}s")
                time.sleep(wait)
                continue
            return resp
        except httpx.HTTPError as exc:
            wait = 2 ** attempt
            log(f"network error on {urlparse(url).netloc}: {exc!r}, "
                f"retry {attempt}/{MAX_RETRIES} in {wait}s")
            time.sleep(wait)
    return None


def extract_links(text: str) -> set[str]:
    return {m.group(0).rstrip(".,);\"'") for m in LINK_RE.finditer(text)}


def unwrap_ddg(href: str) -> str:
    """DuckDuckGo wraps results as //duckduckgo.com/l/?uddg=<urlencoded>."""
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    return href


# ---------------------------------------------------------------- sources

def scan_reddit(client: httpx.Client) -> tuple[list[tuple[str, str]], int]:
    """Return (links, scanned_count) from Reddit JSON endpoints."""
    found: list[tuple[str, str]] = []
    scanned = 0
    for url in REDDIT_URLS:
        resp = fetch(client, url, REDDIT_UA)
        if resp is None:
            log(f"reddit source unreachable: {url}")
            continue
        if resp.status_code != 200:
            log(f"reddit source HTTP {resp.status_code}: {url} "
                f"(IP mungkin diblokir Reddit untuk datacenter)")
            continue
        try:
            payload = resp.json()
        except ValueError:
            log(f"reddit source returned non-JSON: {url}")
            continue
        children = (
            payload.get("data", {}).get("children", [])
            if isinstance(payload, dict) else []
        )
        for child in children:
            data = child.get("data", {})
            scanned += 1
            blob = " ".join(
                str(data.get(k) or "") for k in ("selftext", "url", "title")
            )
            for link in extract_links(blob):
                found.append((link, "Reddit"))
        jitter_pause()
    return found, scanned


def scan_telegram(client: httpx.Client) -> tuple[list[tuple[str, str]], int]:
    """Parse t.me/s/<channel> public previews."""
    found: list[tuple[str, str]] = []
    scanned = 0
    if not TELEGRAM_CHANNELS:
        log("telegram: TELEGRAM_CHANNELS kosong, skip")
        return found, scanned
    for channel in TELEGRAM_CHANNELS:
        url = f"https://t.me/s/{channel}"
        resp = fetch(client, url, BROWSER_UA)
        if resp is None or resp.status_code != 200:
            code = resp.status_code if resp is not None else "unreachable"
            log(f"telegram channel @{channel}: HTTP {code}, skip")
            jitter_pause()
            continue
        tree = HTMLParser(resp.text)
        nodes = tree.css(".tgme_widget_message_text")
        for node in nodes:
            scanned += 1
            text = node.text()
            for link in extract_links(text):
                found.append((link, "Telegram"))
            for a in node.css("a"):
                href = a.attributes.get("href") or ""
                for link in extract_links(href):
                    found.append((link, "Telegram"))
        jitter_pause()
    return found, scanned


def scan_ddg(client: httpx.Client) -> tuple[list[tuple[str, str]], int]:
    """DuckDuckGo Lite search (GET — POST dapat 202 challenge)."""
    found: list[tuple[str, str]] = []
    scanned = 0
    resp = fetch(client, "https://lite.duckduckgo.com/lite/", DDG_UA,
                 method="GET", params={"q": DDG_QUERY})
    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "unreachable"
        log(f"ddg source: HTTP {code}, skip")
        return found, scanned
    tree = HTMLParser(resp.text)
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        real = unwrap_ddg(href)
        scanned += 1
        for link in extract_links(real):
            found.append((link, "Search"))
    for link in extract_links(resp.text):
        found.append((link, "Search"))
    return found, scanned


# ---------------------------------------------------------------- pipeline

def validate_link(client: httpx.Client, url: str) -> str:
    """HEAD check. Returns 'alive', 'dead', or 'unknown'."""
    try:
        resp = client.head(url, headers={"User-Agent": BROWSER_UA},
                           timeout=10.0, follow_redirects=True)
        if resp.status_code in (404, 410):
            return "dead"
        if resp.status_code < 500:
            return "alive"
        return "unknown"
    except httpx.HTTPError:
        return "unknown"


def load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    return {
        line.strip()
        for line in SEEN_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def append_seen(url: str) -> None:
    with SEEN_FILE.open("a", encoding="utf-8") as fh:
        fh.write(url + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def send_telegram(client: httpx.Client, url: str, source: str) -> bool:
    text = (
        "🚨 <b>CLAUDE PRO INVITE DETECTED</b> 🚨\n\n"
        f"🔗 Link: {url}\n"
        f"📍 Source: {source}\n"
        f"⏰ Time: {now_wib().strftime('%Y-%m-%d %H:%M:%S')} WIB"
    )
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = client.post(
            api,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            return True
        log(f"telegram sendMessage HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    except httpx.HTTPError as exc:
        log(f"telegram send error: {exc!r}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude invite link hunter")
    parser.add_argument("--dry-run", action="store_true",
                        help="scan and print only; no Telegram, no state write")
    parser.add_argument("--no-validate", action="store_true",
                        help="skip HEAD validation of links")
    args = parser.parse_args()

    if not args.dry_run and not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        log("FATAL: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diisi di .env "
            "(atau pakai --dry-run)")
        return 1

    log(f"scan start (dry_run={args.dry_run}, validate={not args.no_validate})")

    seen = load_seen()
    all_found: list[tuple[str, str]] = []
    total_scanned = 0

    with httpx.Client(http2=False) as client:
        for name, scanner in (("reddit", scan_reddit),
                              ("telegram", scan_telegram),
                              ("search", scan_ddg)):
            try:
                links, scanned = scanner(client)
            except Exception as exc:  # keep the pass alive on source bugs
                log(f"source '{name}' crashed: {exc!r}")
                continue
            total_scanned += scanned
            all_found.extend(links)
            log(f"source {name}: {scanned} item dipindai, "
                f"{len(links)} kandidat link")

    # dedupe within this pass, preserving order
    uniq: dict[str, str] = {}
    for url, source in all_found:
        uniq.setdefault(url, source)

    new_links = {u: s for u, s in uniq.items() if u not in seen}
    log(f"total item dipindai: {total_scanned}, kandidat unik: {len(uniq)}, "
        f"link baru: {len(new_links)}")

    sent = 0
    for url, source in new_links.items():
        if not args.no_validate:
            status = validate_link(client, url)
            log(f"validate {url} -> {status}")
            if status == "dead":
                if not args.dry_run:
                    append_seen(url)  # never re-alert a dead link
                continue
        if args.dry_run:
            log(f"[DRY-RUN] would alert [{source}] {url}")
            continue
        append_seen(url)
        if send_telegram(client, url, source):
            sent += 1
            log(f"alert terkirim [{source}] {url}")
        jitter_pause()

    log(f"scan selesai: {sent} alert terkirim, "
        f"{len(new_links)} link baru diproses")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Test suite untuk hunter.py — verifikasi extraction, parsing, dedup,
validasi, dan format payload Telegram tanpa bergantung live source yang
lagi diblokir (Reddit 403 / DDG 202 dari IP box ini).

Jalankan: .venv/bin/python3 test_hunter.py
"""

import sys
import tempfile
from pathlib import Path

import httpx

import hunter

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


# 1. regex extractor
def test_regex():
    blob = (
        "dapat claude pro gratis di https://claude.ai/invite/abc_DEF-123 ya "
        "atau https://www.claude.ai/claim/xyz789 juga http://www.claude.ai/promo/q-1 "
        "bukan https://claude.ai/invite/ (terlalu pendek) "
        "dan https://claude.ai/invite/ok_banget-99. "
        "referral: https://claude.ai/referral/m5Ak2Sa7aQ "
        "credit: https://claude.ai/code/claim-credit/10 "
        "artifacts: https://claude.ai/artifacts/invite/4724fe71-1749-4e5d-bdaa-9c96b644ef9e "
        "ios: https://claude.ai/referral/Y9YNbekrsg?s=ios"
    )
    links = hunter.extract_links(blob)
    check("regex: ekstrak 8 link valid",
          links == {"https://claude.ai/invite/abc_DEF-123",
                    "https://www.claude.ai/claim/xyz789",
                    "http://www.claude.ai/promo/q-1",
                    "https://claude.ai/invite/ok_banget-99",
                    "https://claude.ai/referral/m5Ak2Sa7aQ",
                    "https://claude.ai/code/claim-credit/10",
                    "https://claude.ai/artifacts/invite/4724fe71-1749-4e5d-bdaa-9c96b644ef9e",
                    "https://claude.ai/referral/Y9YNbekrsg"},
          f"dapat {sorted(links)}")
    noise = "session https://claude.ai/code/session_01ABC dan artifact https://claude.ai/code/artifact/xyz dan project https://claude.ai/code/project/chan_01"
    check("regex: tolak session/artifact/project link",
          hunter.extract_links(noise) == set(),
          f"dapat {sorted(hunter.extract_links(noise))}")


# 2. ddg unwrap
def test_ddg_unwrap():
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fclaude.ai%2Finvite%2Fzz9&rut=abc"
    check("ddg: unwrap uddg", hunter.unwrap_ddg(wrapped)
          == "https://claude.ai/invite/zz9")
    check("ddg: href biasa lewat", hunter.unwrap_ddg("https://claude.ai/claim/a1")
          == "https://claude.ai/claim/a1")


# 3. telegram HTML parse (fixture bentuk t.me/s)
def test_telegram_parse():
    html = """
    <div class="tgme_widget_message" data-post="ch/1">
      <div class="tgme_widget_message_text">
        gratis trial: <b>https://claude.ai/invite/tg-FIXTURE1</b>
        <a href="https://claude.ai/claim/tgfix2">klik</a>
      </div>
    </div>
    <div class="tgme_widget_message" data-post="ch/2">
      <div class="tgme_widget_message_text">tidak ada link di sini</div>
    </div>
    """
    tree = hunter.HTMLParser(html)
    found = []
    for node in tree.css(".tgme_widget_message_text"):
        found.extend(hunter.extract_links(node.text()))
        for a in node.css("a"):
            found.extend(hunter.extract_links(a.attributes.get("href") or ""))
    check("telegram: parse fixture 2 link",
          set(found) == {"https://claude.ai/invite/tg-FIXTURE1",
                         "https://claude.ai/claim/tgfix2"},
          f"dapat {sorted(set(found))}")


# 4. validator (live, ringan)
def test_validator():
    with httpx.Client() as client:
        dead = hunter.validate_link(client, "https://example.com/halaman-tidak-ada-404")
        alive = hunter.validate_link(client, "https://example.com")
    check("validator: 404 -> dead", dead == "dead", f"status={dead}")
    check("validator: 200 -> alive", alive == "alive", f"status={alive}")


# 5. dedup seen_links
def test_seen_dedup():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "seen.txt"
        orig = hunter.SEEN_FILE
        hunter.SEEN_FILE = tmp
        try:
            check("dedup: awal kosong", hunter.load_seen() == set())
            hunter.append_seen("https://claude.ai/invite/a")
            hunter.append_seen("https://claude.ai/invite/b")
            hunter.append_seen("https://claude.ai/invite/a")  # dupeat
            seen = hunter.load_seen()
            check("dedup: 2 entri unik setelah dupeat",
                  seen == {"https://claude.ai/invite/a",
                           "https://claude.ai/invite/b"},
                  f"isi={sorted(seen)}")
        finally:
            hunter.SEEN_FILE = orig


# 6. telegram payload shape (mock client, tanpa kirim)
def test_telegram_payload():
    captured = {}

    class FakeResp:
        status_code = 200
        text = '{"ok":true}'

    class FakeClient:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return FakeResp()

    orig_tok, orig_chat = hunter.TELEGRAM_BOT_TOKEN, hunter.TELEGRAM_CHAT_ID
    hunter.TELEGRAM_BOT_TOKEN, hunter.TELEGRAM_CHAT_ID = "123:ABC", "999"
    try:
        ok = hunter.send_telegram(FakeClient(), "https://claude.ai/invite/p1",
                                  "Reddit")
    finally:
        hunter.TELEGRAM_BOT_TOKEN, hunter.TELEGRAM_CHAT_ID = orig_tok, orig_chat

    payload = captured.get("json", {})
    check("telegram: kirim sukses", ok is True)
    check("telegram: url API benar",
          captured.get("url") == "https://api.telegram.org/bot123:ABC/sendMessage")
    check("telegram: chat_id & parse_mode",
          payload.get("chat_id") == "999" and payload.get("parse_mode") == "HTML")
    text = payload.get("text", "")
    check("telegram: text berisi link/source/WIB",
          "https://claude.ai/invite/p1" in text and "Reddit" in text
          and "WIB" in text and "CLAUDE PRO INVITE DETECTED" in text)


def main() -> int:
    for fn in (test_regex, test_ddg_unwrap, test_telegram_parse,
               test_validator, test_seen_dedup, test_telegram_payload):
        try:
            fn()
        except Exception as exc:
            check(f"{fn.__name__} (exception)", False, repr(exc))
    print(f"\n{'SEMUA LOLOS' if not FAILURES else 'GAGAL: ' + ', '.join(FAILURES)}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

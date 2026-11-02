# Claude Reward Link Hunt — Hasil 2026-11-02 02:38 WIB

## Link NYATA (terverifikasi ada di index publik)

| Link | Sumber | Status validasi |
|---|---|---|
| https://claude.ai/referral/m5Ak2Sa7aQ | GitHub PRs (OneLiteFeatherNET/Voyager #377-381, x5) | HTTP 403 dari IP datacenter ini = TIDAK BISA DIVERIFIKASI dari sini (bukan dead) |
| https://claude.ai/referral/gw4RiBkYS | urlscan.io public index | HTTP 403 — idem |
| https://claude.ai/referral/Y9YNbekrsg | urlscan.io (source ios) | HTTP 403 — idem |
| https://claude.ai/artifacts/invite/4724fe71-1749-4e5d-bdaa-9c96b644ef9e | urlscan.io (x3 scan) | HTTP 403 — idem |
| https://claude.ai/artifacts/invite/d0b4ea10-cbaf-43da-a54c-905d06296241 | urlscan.io | HTTP 403 — idem |

Catatan: claude.ai (Cloudflare) memblokir IP datacenter/AWS dengan 403 untuk SEMUA request,
termasuk HEAD. Validasi hidup/mati harus dari IP residensial/VPS WELY, atau klik manual di browser HP.

## Pola URL hadiah (dari hasil hunt)

- `claude.ai/referral/<code>` — referral $10 (contoh: m5Ak2Sa7aQ, gw4RiBkYS, Y9YNbekrsg)
- `claude.ai/code/claim-credit/10` — endpoint klaim kredit $10 (terlihat di redirect login urlscan)
- `claude.ai/artifacts/invite/<uuid>` — invite artifact (format UUID)
- `claude.ai/join`, `claude.ai/invites/bulk` — halaman dasar (bukan kode)

Regex hunter.py sudah di-upgrade untuk menangkap semua pola di atas.

## Sumber yang DIBLOKIR dari IP box ini (AWS datacenter)

| Sumber | Gejala |
|---|---|
| Reddit (direct, browser, old.reddit, jina, codetabs, redlib x5) | 403 block page / Anubis / 522 |
| Google, Brave, Ecosia, DDG, Yahoo, Mojeek, MetaGer, Swisscows, Bing | captcha / 202 / hasil rusak |
| X/Twitter, TikTok, Instagram, Facebook | login wall / captcha |
| YouTube | consent wall (825 char interstitial) |
| Bluesky API | 403 bunny.net firewall |
| GitHub web search | jalan, tapi nol hasil relevan (API jalan) |
| Internet Archive CDX | hidup 1x lalu "Temporarily Offline" |
| Common Crawl index | 504 terus |
| grep.app | jalan via r.jina.ai — nol hasil claude.ai/invite |
| urlscan.io API | JALAN — sumber semua temuan di atas |
| GitHub API (issues search) | JALAN — sumber referral m5Ak2Sa7aQ |
| t.me/s/<channel> | JALAN (claudeai & anthropicai ada, nol link) |

## Rekomendasi operasional

1. Jalankan hunter.py dari VPS WELY (IP non-datacenter) — Reddit/DDG/YouTube akan hidup.
2. urlscan.io + GitHub API bisa ditambahkan sebagai source scanner di hunter.py (API terbuka, nol blokir).
3. Token bot perlu diperbaiki (401) sebelum alert otomatis bisa jalan.

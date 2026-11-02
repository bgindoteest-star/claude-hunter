# claude-hunter

CLI hunter link referral/invite Claude Pro multi-source. Memindai sumber publik
(urlscan.io, GitHub, Reddit, DuckDuckGo, Telegram, Internet Archive/Common
Crawl), mengekstrak link `claude.ai/referral|invite|claim|promo|credit`,
menyimpan ke database lokal dengan dedup, dan opsional mengirim alert ke
Telegram.

Dibangun untuk riset keamanan/OSINT dan monitoring promo publik. Pure Python,
pure HTTP — tanpa browser automation.

## Fitur

- **6 sumber scanner**: urlscan.io (13 pola query), GitHub Issues API (9 query),
  Internet Archive CDX + Common Crawl, Reddit JSON, DuckDuckGo Lite, preview
  channel Telegram (`t.me/s/`)
- **CLI lengkap**: `scan`, per-source subcommand, `validate`, `list`, `export`
  (txt/csv/json)
- **Dedup database**: `found_links.jsonl` + `seen_links.txt` — link yang sudah
  pernah ditemukan tidak pernah di-alert dua kali
- **Regex ketat**: hanya menangkap link hadiah (`/referral/<code>`,
  `/invite/<code>`, `/claim`, `/code/claim-credit/10`, `/artifacts/invite/<uuid>`);
  link `code/session_*` / `code/artifact_*` (Claude Code share) otomatis dibuang
- **Validasi HEAD** opsional + alert Telegram Bot API
- **Rate limit sopan**: custom User-Agent, jitter antar request, retry dengan
  backoff pada 429/5xx, isolate error per sumber (satu sumber mati tidak
  membunuh pass lainnya)
- **Test suite** berbasis fixture (regex, parser, dedup, payload Telegram)

## Install

```bash
git clone https://github.com/<user>/claude-hunter.git
cd claude-hunter
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # lalu isi token bila mau alert Telegram
```

Butuh Python 3.10+. Dependensi: `httpx`, `python-dotenv`, `selectolax` (>=1.0,
backend lexbor).

## Pakai

```bash
./hunt --help                          # daftar subcommand
./hunt scan                            # semua sumber, simpan temuan baru
./hunt scan --sources urlscan,github   # sumber tertentu saja
./hunt scan --validate --notify        # HEAD-validate + alert Telegram
./hunt urlscan                         # satu sumber
./hunt github --deep                   # 3 halaman per query
./hunt archive                         # IA CDX + Common Crawl
./hunt reddit                          # Reddit JSON (butuh IP non-datacenter)
./hunt ddg                             # DuckDuckGo Lite
./hunt telegram                        # channel dari TELEGRAM_CHANNELS di .env
./hunt validate --all                  # cek hidup/mati seluruh isi DB
./hunt list                            # tampilkan DB
./hunt export --format csv             # ekspor DB
```

Atau pakai langsung: `.venv/bin/python3 hunt_cli.py <subcommand>`.

## Konfigurasi (`.env`)

```env
TELEGRAM_BOT_TOKEN=      # dari @BotFather — opsional, hanya untuk --notify
TELEGRAM_CHAT_ID=        # chat/grup tujuan alert
CHECK_INTERVAL_MINUTES=30
TELEGRAM_CHANNELS=       # channel publik tanpa @, pisah koma — untuk ./hunt telegram
DDG_QUERY=               # override query DuckDuckGo (opsional)
```

## Otomasi (cron)

```bash
./setup_cron.sh           # pasang entri cron (interval dari CHECK_INTERVAL_MINUTES)
./setup_cron.sh --print   # lihat entri tanpa memasang
```

## Catatan sumber & IP

Beberapa sumber memblokir IP datacenter/VPS (Reddit 403, Google/Brave/DDG
captcha, YouTube consent wall, Bluesky 403). urlscan.io, GitHub API, dan
`t.me/s/` umumnya tetap bisa dari IP mana pun. Untuk hasil maksimal, jalankan
dari IP residensial atau VPS bersih. Semua sumber hanya diakses lewat HTTP
GET dengan header wajar dan jeda antar request.

## Test

```bash
.venv/bin/python3 test_hunter.py    # 7 cek: regex, ddg unwrap, parser, validator, dedup, payload
```

## Struktur

```
hunter.py         # engine inti: fetch, scanner, regex, dedup, dispatcher
hunt_cli.py       # CLI multi-source + database temuan
hunt              # wrapper shell (pakai .venv otomatis)
test_hunter.py    # test suite berbasis fixture
setup_cron.sh     # pemasang cron
FOUND_LINKS.md    # dokumentasi hasil hunt & catatan blokir per sumber
```

## Disclaimer

Tool ini memindai halaman dan API publik dengan rate limit sopan, untuk riset
dan monitoring promo. Hormati ToS masing-masing platform dan aturan hukum
lokal. Link yang ditemukan adalah URL publik yang terindeks — verifikasi sendiri
sebelum dipakai.

## License

MIT — lihat [LICENSE](LICENSE).

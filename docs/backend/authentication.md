# Autentikasi pelanggan AUTH-01

AUTH-01 menambahkan sesi anonim, challenge verifikasi email dan sesi pelanggan. Kode yang benar membuktikan penguasaan email; ini tidak memberikan role staf atau grants booking/payment. Login staf tetap AUTH-03, dan grants data sumber diperiksa pada integrasi source.

## Alur yang tersedia

Browser memulai POST /api/v1/auth/sessions melalui HTTPS dan Origin yang sama. Backend memilih tenant dari host yang terdaftar. Sesi baru memakai token acak256bit di cookie __Host-airport_session yang Secure/HttpOnly/SameSite=Lax/Path=/ tanpa Domain. Cookie __Host-airport_csrf bersifat Secure dan dapat dibaca frontend untuk header X-CSRF-Token; hash terikat sesi tetap diperiksa server. Cookie sesi tidak disalin ke JSON/localStorage. Respons sesi, me dan error autentikasi memakai Cache-Control:no-store.

Bootstrap dengan sesi yang masih sah mempertahankan sesi dan memeriksa cookie CSRF miliknya. Token tidak dikenal tidak dipakai sebagai ID sesi baru. CSRF cookie yang hilang/rusak pada sesi aktif ditolak; pemulihan dengan memulai ulang login memerlukan membersihkan cookie browser terkait, bukan mengambil token server dari log.

POST /api/v1/auth/challenges menerima contact dan purpose=customer_login. Setelah pemeriksaan sesi anonim/CSRF, layanan menormalisasi email, membuat kode8digit dan menyimpan verifier HMAC dengan TTL dan batas percobaan. Respons202 hanya challenge_id dan verification_requested, tanpa membedakan kontak baru/lama. Alamat dengan local part SMTPUTF8 belum didukung; local part ASCII dipertahankan dan domain dinormalisasi. Provider adapter menerima hanya pesan yang ditentukan layanan bisnis.

POST /api/v1/auth/challenges/{id}/verify menerima code sebagai string8digit. Backend memeriksa sesi asal, tenant, CSRF, purpose, expiry, attempts dan kode di dalam transaksi terkunci. Keberhasilan mengonsumsi challenge sekali, mengikat kontak hanya jika belum terikat, memakai principal aktif yang sudah ada atau membuat principal pelanggan baru, lalu membuat token/CSRF baru dan mencabut sesi anonim lama. Cookie lama tidak lagi sah. Verifikasi contact milik staf tetap menghasilkan session_kind=customer dan tidak memberikan hak staf.

GET /api/v1/me hanya mengembalikan session_kind serta principal_id miliknya. POST /api/v1/auth/logout memerlukan sesi/CSRF dan same-origin, mencabut sesi server-side serta menghapus cookie. Token revoked/expired tidak membuka data walaupun cookie disalin kembali. Staf dan endpoint kasus belum ditambahkan pada tahap ini.

## Batas pengiriman saat ini

Adapter pengiriman terkontrol hanya berada dalam kode tes; pesan/kode dibaca harness tes dalam memori, bukan endpoint debug atau respons API. Instans API normal tidak memasang sender palsu. Challenge request pada instans tersebut menghasilkan503 VERIFICATION_UNAVAILABLE; tidak mengaku sudah mengirim email.

MAIL-05 akan memasang/membuktikan provider nyata dan pengiriman challenge; sampai itu lulus, jangan menyebut customer email login teruji live. Saat ini yang dibuktikan adalah database nyata dan integrasi API melalui HTTPS ASGI, dengan delivery terkontrol. Uvicorn bootstrap lokal masih HTTP; endpoint auth memerlukan HTTPS dari proxy/runner yang benar. Ini tidak mengklaim deployment TLS publik telah dibuat.

## Setup lokal

Setelah DB-03 dan database khusus proyek siap:

```powershell
uv sync --locked --project backend
uv run --locked --project backend airport-assistant-auth-setup --demo-host localhost
uv run --locked --project backend airport-assistant-api --check-config
```

Setup menyiapkan owner auth NOLOGIN, menerapkan revision0003_customer_auth, menghasilkan SESSION_SECRET kuat secara internal bila belum ada, lalu menyalakan APP_DATABASE_ENABLED dan APP_AUTH_ENABLED pada file lokal. Key tidak dicetak dan key valid yang sudah ada dipertahankan. --demo-host opsional hanya menambahkan metadata tenant fiktif untuk host canonical; host yang sudah terdaftar tidak dipindahkan ke tenant lain. Tanpa flag itu, gunakan host tenant yang sudah disiapkan melalui administrasi.

Restart API yang sudah berjalan agar memuat key/flag baru. Jangan merotasi key hanya untuk restart; perubahan key mengubah HMAC dan memerlukan rencana pencabutan sesi/challenge. Pada deployment, SESSION_SECRET diambil dari secret store, runtime hanya mendapat DATABASE_URL dan key; credential direct/provisioner tidak dipasang pada proses API. Tidak ada password akun pelanggan pada fitur ini.

## Nilai awal yang dapat dikonfigurasi

| Variabel | Default |
| --- | --- |
| APP_AUTH_ANONYMOUS_TTL | 86400detik. |
| APP_AUTH_CUSTOMER_TTL | 28800detik. |
| APP_AUTH_CHALLENGE_TTL | 300detik, batas30-600. |
| APP_AUTH_MAX_ATTEMPTS | 5, batas1-10. |
| APP_AUTH_RATE_WINDOW | 900detik, batas60-3600. |
| APP_AUTH_BOOT_LIMIT | 100sesi baru/IP/window. Bootstrap sesi valid tidak membuat sesi tambahan. |
| APP_AUTH_CONTACT_LIMIT | 3challenge/contact/window. |
| APP_AUTH_IP_LIMIT / SESSION_LIMIT | 100/5challenge perIP/sesi/window. |
| APP_AUTH_VERIFY_IP_LIMIT / VERIFY_SESSION_LIMIT | 200/15verifikasi perIP/sesi/window. |
| APP_AUTH_DELIVERY_TIMEOUT | 10detik, batas0.1-30. |

Rate memakai counter PostgreSQL fixed-window, bukan memory perproses. Gagal kode tetap mengonsumsi attempts dan counter; rate-limited response tetap menyimpan hit. Konfigurasi awal dapat dikalibrasi untuk NAT bandara, kapasitas dan biaya provider sebelum rilis. Cookie/OTP/token/IP lookup memakai digest HMAC dengan pemisahan tag/tenant; raw key/token/kode tidak disimpan di database. Email canonical tetap disimpan sebagai data kontak yang terlindungi.

Counter/session/challenge mempunyai index expiry/window untuk maintenance. Worker cleanup dan target retensi operasional belum diimplementasikan; tetapkan dan buktikan bersama pekerjaan job/release terkait. AUTH-01 tidak menjadi klaim production readiness seluruh sistem.

## Pengamanan HTTP dan database

Mutasi auth memerlukan HTTPS dan Origin yang sama termasuk port. Origin/Host ganda, cookie auth ganda dan header CSRF ganda ditolak. Identitas tenant tidak diambil dari body/model; field tambahan ditolak. Service menggunakan peer request.client dari ASGI dan tidak mem-parsing X-Forwarded-For sendiri. Di deployment, edge proxy harus mengganti header forwarding dari client, dan Uvicorn/proxy hanya mempercayai peer yang dikonfigurasi; jangan menggunakan trust-all forwarded IP. Tes ASGI membuktikan header tambahan tidak mengubah peer pada lapisan aplikasi, bukan bukti konfigurasi proxy publik.

Function database dimiliki airport_assistant_auth_owner, NOLOGIN/NOBYPASSRLS/NOINHERIT tanpa parent role. Haknya hanya SELECT/INSERT dan UPDATE kolom yang dibutuhkan. Session/challenge/rate tables memakai ENABLE+FORCE RLS; runtime tidak dapat SELECT/DML tabel tersebut secara bebas. PUBLIC EXECUTE dicabut, search_path pg_catalog dan nama tabel terqualifikasi. Runtime hanya memanggil fungsi terbatas. Hash NULL tidak dianggap cocok; contact principal tidak direbind dan principal nonaktif tidak diaktifkan ulang.

Row session/challenge/contact dikunci untuk konsumsi/rotasi atomik. Dua verify pada challenge sama hanya menghasilkan satu upgrade; dua browser sah memverifikasi kontak baru bersamaan tetap memakai satu principal. Browser lain yang mengetahui challenge+code tidak menghabiskan attempt korban atau mengambil sesinya. Sender gagal/timeout membatalkan challenge yang dibuat oleh origin tersebut, tanpa menghapus rate hit. Secret dan detail exception provider tidak masuk respons/log.

## Bagian yang menunggu fitur berikutnya

AUTH-02 pada schema0007 memindahkan ownership percakapan mandiri asal, mempertahankan provenance pesan dan membatalkan pending draft dalam transaksi upgrade yang sama dengan rotasi/revoke. [Panduan adopsi anonim](anonymous_adoption.md) menjelaskan proof browser, retry, RLS dan rollback. Challenge tanpa browser untuk email follow-up/case-contact memakai flow tersendiri saat MAIL-04; purpose yang tersedia sekarang hanya customer_login.

## Pengujian

```powershell
$env:RUN_NEON_DB_TESTS = '1'
$env:RUN_NEON_MIGRATION_TESTS = '1'
$env:RUN_NEON_RLS_TESTS = '1'
$env:RUN_NEON_UPGRADE_TESTS = '1'
$env:RUN_NEON_AUTH_TESTS = '1'
try { uv run --locked --project backend pytest backend/tests } finally {
  Remove-Item Env:RUN_NEON_DB_TESTS, Env:RUN_NEON_MIGRATION_TESTS, Env:RUN_NEON_RLS_TESTS, Env:RUN_NEON_UPGRADE_TESTS, Env:RUN_NEON_AUTH_TESTS
}
```

Tes memakai schema/role acak dan delivery dalam memori, lalu membersihkannya. Kasus mencakup origin/CSRF/cookie, lifecycle sesi, wrongbrowser/tenant, replay/expiry/resend/exhaustion, persistent/concurrent limits, serialized verification, inactive identities, no-staff promotion, sender failure dan pengecekan ACL/HMAC. Default offline melewati tes Neon; bukan pengganti bukti integrasi.

Rujukan: [OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html), [Session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html), [email-validator](https://github.com/JoshData/python-email-validator).

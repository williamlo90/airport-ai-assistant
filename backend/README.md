# Menjalankan backend

BE-01 menyediakan bootstrap FastAPI yang executable. BE-02 menambahkan liveness, error terstruktur, request ID dan batas input. DB-01 menyediakan koneksi database opt-in; koneksi Neon nyata masih menunggu pengujian. Schema bisnis, autentikasi, worker executable dan route bisnis belum tersedia.

## Prasyarat

- Python 3.13.
- uv; instalasi mengikuti [panduan resmi uv](https://docs.astral.sh/uv/getting-started/installation/). Contoh untuk lingkungan Python yang dikelola sendiri: `python -m pip install uv`.
- Jalankan perintah berikut dari root repository.

## Instalasi yang terkunci

```powershell
uv sync --locked --project backend
Copy-Item .env.example .env
uv run --locked --project backend airport-assistant-api --check-config
uv run --locked --project backend airport-assistant-api
```

Copy `.env.example` hanya jika `.env` belum ada; jangan menimpa credential lokal. File `.env` dibaca dari working directory. Untuk lokasi berbeda gunakan `--env-file PATH`. Environment proses mengalahkan nilai dotenv.

Dalam development, buka http://127.0.0.1:8000/docs atau http://127.0.0.1:8000/openapi.json. Endpoint operasional `/health/live` mengembalikan `{"status":"ok","scope":"process"}`. Root `/` mengembalikan error 404 terstruktur. Hentikan dengan Ctrl+C. Dokumentasi development tidak diaktifkan ketika APP_ENV=production.

Jangan mengganti `uv sync --locked` dengan pembaruan dependency tanpa review. `backend/uv.lock` mengunci dependency transitif; `pyproject.toml` menyatakan paket langsung dan batas versi. `.venv` lokal tidak di-commit.

## Konfigurasi yang dipakai saat ini

| Variabel | Aturan |
| --- | --- |
| APP_ENV | Wajib: development/test/production. |
| APP_HOST | Default 127.0.0.1; pilihan loopback/bind: 127.0.0.1, 0.0.0.0, ::1, ::. Gunakan loopback untuk development. |
| APP_PORT | Default 8000; integer 1–65535. |
| APP_MAX_REQUEST_BODY_BYTES | Default 1048576 (1 MiB); integer 1–16777216. Batas seluruh body request saat ini, termasuk encoding/overhead. |

Field integrasi email/model/source di `.env.example` masih placeholder. Konfigurasi database DB-01 dimuat terpisah ketika dipakai. API tanpa APP_DATABASE_ENABLED=true tidak memanggil provider atau Neon. Mode bootstrap tanpa database tetap tidak memerlukan credential.

Environment-only untuk deployment atau pengujian:

```powershell
$env:APP_ENV = 'development'
uv run --locked --project backend airport-assistant-api --no-env-file
```

Config wajib hilang atau salah membuat proses keluar dengan kode 2 dan daftar nama variabel yang perlu diperbaiki, tanpa mencetak nilai input. File dotenv hilang/tidak terbaca juga menghasilkan error yang jelas. `--check-config` tidak membuka port dan tidak menguji koneksi layanan lain.

## Pengujian

```powershell
uv run --locked --project backend pytest backend/tests
```

Tes bootstrap memeriksa dotenv/environment override, config wajib, redaksi output CLI dan batas dokumentasi development. Tes boundary memeriksa error 404/405/422/500, normalisasi dan isolasi request ID, body berlebih termasuk stream tanpa Content-Length, serta redaksi log. Route pengujian hanya dipasang oleh tes, tidak oleh aplikasi.

## Perilaku API BE-02

- `GET /health/live` tersedia juga pada production dan hanya menyatakan proses API hidup. Belum ada readiness dependency; `/health/ready` tetap 404. Endpoint bisnis kelak memakai `/api/v1`.
- Setiap respons aplikasi memiliki `X-Request-ID`. Satu header UUID valid dinormalisasi ke huruf kecil; header hilang, ganda atau invalid diganti UUID baru. ID ini hanya korelasi, bukan identitas atau bukti otorisasi.
- Error berbentuk `{"error":{"code":"RESOURCE_NOT_FOUND","message":"Data atau halaman tidak ditemukan.","request_id":"<uuid>","retryable":false}}`. Detail exception, input Pydantic dan field buatan pengguna tidak dikembalikan. Header protokol Allow, Retry-After dan WWW-Authenticate dapat dipertahankan.
- Body dihitung dari byte aktual, bukan hanya Content-Length; kelebihan menghasilkan 413. Content-Length invalid, ganda atau tidak cocok menghasilkan 400 pada batas ASGI. Server HTTP dapat menolak framing invalid lebih awal dengan respons protokolnya sendiri.
- `RequestModel` adalah dasar schema mutasi: field tambahan dan coercion tipe ditolak, string default maksimal 4096 karakter. Schema fitur harus mewarisinya dan menentukan batas field/domain lebih rinci saat fitur dibuat. Tidak ada endpoint mutasi dummy di aplikasi.
- Kegagalan sebelum respons dimulai menghasilkan 500 generik; kegagalan setelah headers terkirim menghentikan respons dengan exception yang sudah diredaksi. Log error memakai request ID tanpa isi exception/body/query. CLI mematikan access log Uvicorn yang biasanya memuat URL mentah. Jika memakai runner/proxy lain, atur redaksi log pada runner tersebut.
- Batas body ini fondasi awal, bukan implementasi upload atau perlindungan lengkap terhadap slow clients. Timeout, batas koneksi/proxy dan aturan attachment 5 MiB/file ditambahkan bersama deployment/CASE-04; konfigurasi body request harus menampung multipart overhead ketika upload dibuat.

Implementasi mengikuti [exception handlers FastAPI](https://fastapi.tiangolo.com/tutorial/handling-errors/) dan [pure ASGI middleware Starlette](https://starlette.dev/middleware/).

## Dependency wiring BE-03

`create_app()` memasang `ApplicationServices` dari `composition.py` pada instance aplikasi. `api/dependencies.py` mengambil layanan, lalu route health memanggil `HealthService.check()`. Hasil domain `ProcessStatus` diterjemahkan ke schema respons oleh route. Service tidak mengimpor FastAPI atau SDK.

- Factory default hanya memasang `ProcessHealthService`, tanpa database/provider maupun state tenant/aktor. Tidak ada singleton global antaraplikasi.
- Tes bisa memasok `create_app(settings, services=...)` atau override `get_health_service` melalui `app.dependency_overrides`. Override harus dibersihkan setelah pengujian bila memakai ulang app.
- `modules/identity/ports.py` mendefinisikan `TenantRepository` (lookup host canonical ke tenant) dan `SourceGrantProvider` (pemeriksaan grants per tenant/principal/tipe resource/reference/scopes). Ini kontrak consumer, bukan adapter executable atau bukti akses aman. Keduanya belum dipasang pada runtime karena use case identitas/sumber belum tersedia; tidak ada fallback palsu atau endpoint privat yang memakai keduanya.
- Implementasi Neon/repository ditambahkan pada tugas database/identitas; adapter grants ditambahkan pada SRC-03A. Layanan bisnis nantinya memeriksa izin serta menangani kegagalan secara fail closed. Port tidak mengambil keputusan email recipient atau mengganti pemeriksaan bisnis.
- `composition.py` memilih implementasi dan menyuntikkannya melalui constructor service ketika fitur tersedia. Koneksi, transaksi, tenant dan aktor harus memiliki scope yang sesuai; tidak disimpan sebagai state mutable pada service aplikasi. Worker kelak memakai composition yang sama, tanpa mengimpor FastAPI; saat ini worker tetap placeholder.

Jalankan pemeriksaan arsitektur tersendiri:

```powershell
uv run --locked --project backend pytest backend/tests/contracts/test_dependencies.py
```

Guard AST memeriksa semua import statis (termasuk relatif dan import di dalam fungsi) pada source. API boleh mengimpor API/modul bisnis, modul bisnis hanya modul bisnis, adapter/infrastructure boleh mengimpor kontrak modul bisnis. Import konkret SDK pada API, framework pada modul bisnis, serta bypass umum melalui importlib/builtins/__import__ ditolak. Daftar dependency eksternal yang diizinkan diperluas bersama adapter nyata dan reviewnya. Composition root menjadi tempat pemilihan implementasi; entry point API/worker tidak mengakses adapter secara langsung.

Guard ini membuktikan batas import yang diperiksa, bukan seluruh aturan bisnis atau kontrol akses. Tes izin/transaksi tetap dibuat bersama fitur. Health tetap memiliki wire response BE-02 yang sama.

Pola override mengikuti [dependency overrides FastAPI](https://fastapi.tiangolo.com/advanced/testing-dependencies/).

## Koneksi database DB-01

Driver yang dipakai adalah Psycopg 3 dengan binary wheel dan async pool. Instalasi tetap `uv sync --locked --project backend`; hentikan server lokal dengan Ctrl+C terlebih dahulu jika Windows mengunci executable virtualenv. Runtime memakai URL pooled dan pool lokal kecil untuk membatasi koneksi per proses; migration memakai koneksi direct terpisah.

Isi lokal `.env` menggunakan database/branch pengujian khusus proyek ini:

```dotenv
DATABASE_URL=postgresql://runtime_user:PASSWORD@ep-example-pooler.REGION.aws.neon.tech/testdb?sslmode=require
DATABASE_DIRECT_URL=postgresql://migration_user:PASSWORD@ep-example.REGION.aws.neon.tech/testdb?sslmode=require
APP_DATABASE_ENABLED=false
```

URL di atas hanya format, bukan credential yang dapat dipakai. URL pooled/direct harus menuju endpoint dan nama database sama; username dapat berbeda. Ambil kedua URL dari Neon. Password harus URL-encoded bila mengandung karakter khusus. Opsi query yang didukung adalah sslmode, channel_binding dan connect_timeout. Driver selalu memperketat TLS menjadi verify-full, CA bundle certifi dan channel binding require; mode TLS yang dilemahkan ditolak. Credential disimpan sebagai SecretStr dan diagnosis konfigurasi hanya menyebut nama variabel.

Validasi konfigurasi tanpa membuka jaringan:

```powershell
uv run --locked --project backend airport-assistant-db-check
```

Uji nyata berikut hanya untuk database pengujian. Perintah membuat satu tabel probe dengan nama acak `_airport_db01_probe_*` di public, memberi SELECT/INSERT pada role runtime untuk tabel itu saja, menguji commit/rollback dan penggunaan ulang pool setelah SQL error, kemudian menghapus tabel dalam finally. Role migration perlu CREATE pada schema public serta hak grant/drop atas tabel yang dibuat. Jika proses dimatikan paksa atau koneksi putus saat cleanup, periksa dan hapus tabel probe tersisa melalui role migration sebelum mengulang. Tidak ada perubahan schema bisnis.

```powershell
uv run --locked --project backend airport-assistant-db-check --live
```

Perintah tidak mencetak URL, nama role, query parameter atau detail exception. Error pool/driver diredaksi pada logger psycopg/psycopg.pool. Tidak melakukan retry otomatis atas transaksi bisnis. Hasil ini tidak membuktikan least privilege runtime atau RLS; role dan isolasi diperketat pada DB-03. Credential migration tidak dimuat oleh startup API.

Untuk mengaktifkan database pada API, ubah APP_DATABASE_ENABLED=true di `.env`, lalu jalankan API seperti biasa. Startup membuka pool dan SELECT 1; kegagalan koneksi menggagalkan startup, shutdown menutup pool. Liveness tetap process-only dan belum ada readiness endpoint. Default false mempertahankan mode bootstrap tanpa Neon.

| Konfigurasi | Default dan batas |
| --- | --- |
| APP_DB_POOL_MAX | 2; 1-20 per proses. |
| APP_DB_MAX_WAITING | 8; 1-100 request menunggu pool. |
| APP_DB_CONNECT_TIMEOUT | 10 detik; 1-60. |
| APP_DB_ACQUIRE_TIMEOUT | 10 detik; 1-60, mencakup antrean dan pemeriksaan koneksi client-side. |
| APP_DB_STATEMENT_TIMEOUT_MS | 10000 ms; 100-120000 per transaksi runtime. |

Runtime memakai autocommit pada koneksi dan BEGIN/COMMIT/ROLLBACK eksplisit pada `RuntimeDatabase.transaction()`. Statement/lock/idle timeout bersifat transaction-local; tidak ada tenant context sesi. Prepared statements otomatis dimatikan untuk kompatibilitas pooler. Probe direct memakai statement timeout 10 detik dan lock timeout 3 detik. Startup probe juga memiliki deadline client-side sebesar acquire timeout ditambah statement timeout. Pembatalan saat check/transaction menutup koneksi yang tidak pasti sebelum dikembalikan ke pool; tidak mengulang transaksi bisnis. Koneksi, error dan query nyata tetap perlu diuji dengan credential lokal; unit fake tidak dihitung sebagai integrasi Neon.

Tes integrasi opt-in yang memanggil probe yang sama:

```powershell
$env:RUN_NEON_DB_TESTS = '1'
try { uv run --locked --project backend pytest backend/tests/integration/test_neon_connections.py } finally { Remove-Item Env:RUN_NEON_DB_TESTS }
```

Secara default tes ini skipped. `DB_TEST_ENV_FILE` dapat menunjuk file konfigurasi pengujian lain; CLI juga mendukung --env-file atau --no-env-file. CLI API dan DB-check memilih selector event loop pada Windows untuk Psycopg async; runner ASGI lain perlu konfigurasi loop yang setara.

Rujukan: [Psycopg pools](https://www.psycopg.org/psycopg3/docs/advanced/pool.html), [async connections](https://www.psycopg.org/psycopg3/docs/advanced/async.html), [Neon pooling](https://neon.com/docs/connect/connection-pooling).

# Menjalankan backend

BE-01 menyediakan bootstrap FastAPI yang executable. BE-02 menambahkan liveness, error terstruktur, request ID dan batas input. DB-01 menyediakan koneksi database opt-in yang sudah diuji pada Neon untuk pooled/direct, TLS, commit/rollback dan penggunaan ulang pool. DB-02 menambahkan tabel fondasi tenant/identitas; DB-03 menambahkan role runtime terbatas, RLS dan konteks transaksi. Autentikasi pelanggan dan staf tersedia dalam scope AUTH-01/03 di bagian akhir panduan. Schema alur layanan, worker executable dan route bisnis menyusul.

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

Runtime memakai autocommit pada koneksi dan BEGIN/COMMIT/ROLLBACK eksplisit pada `RuntimeDatabase.transaction()`. Statement/lock/idle timeout bersifat transaction-local; tidak ada tenant context sesi. Prepared statements otomatis dimatikan untuk kompatibilitas pooler. Probe direct memakai statement timeout 10 detik dan lock timeout 3 detik. Startup probe juga memiliki deadline client-side sebesar acquire timeout ditambah statement timeout. Pembatalan saat check/transaction menutup koneksi yang tidak pasti sebelum dikembalikan ke pool; tidak mengulang transaksi bisnis. Integrasi Neon DB-01 telah lulus dengan credential pengujian lokal; suite lengkap dengan integrasi aktif menghasilkan 108 passed. Unit fake tetap tidak dihitung sebagai integrasi provider.

Tes integrasi opt-in yang memanggil probe yang sama:

```powershell
$env:RUN_NEON_DB_TESTS = '1'
try { uv run --locked --project backend pytest backend/tests/integration/test_neon_connections.py } finally { Remove-Item Env:RUN_NEON_DB_TESTS }
```

Secara default tes ini skipped. `DB_TEST_ENV_FILE` dapat menunjuk file konfigurasi pengujian lain; CLI juga mendukung --env-file atau --no-env-file. CLI API dan DB-check memilih selector event loop pada Windows untuk Psycopg async; runner ASGI lain perlu konfigurasi loop yang setara.

Rujukan: [Psycopg pools](https://www.psycopg.org/psycopg3/docs/advanced/pool.html), [async connections](https://www.psycopg.org/psycopg3/docs/advanced/async.html), [Neon pooling](https://neon.com/docs/connect/connection-pooling).

## Migration fondasi DB-02

Riwayat schema memakai Alembic dengan SQLAlchemy Core dan Psycopg direct connection. Pada checkout baru, jalankan provisioner DB-03 terlebih dahulu agar role yang diperlukan revision0002 tersedia; perintah upgrade berikut dipakai setelah persiapan tersebut. Source migration ada di `backend/migrations/`, ikut dibundel pada wheel sebagai resource paket. Tidak ada URL/credential pada file konfigurasi Alembic.

Dari root repository, dengan server lama dihentikan bila mengunci virtualenv:

```powershell
uv sync --locked --project backend
uv run --locked --project backend airport-assistant-migrate current
uv run --locked --project backend airport-assistant-migrate upgrade
```

Script memuat hanya DATABASE_DIRECT_URL melalui konfigurasi migration; --env-file dan --no-env-file tersedia. Output hanya status/revision. Upgrade menambahkan schema app dan revision `0001_foundation`: sembilan tabel fondasi tenant/identitas beserta constraints, indexes dan trigger timestamp/timezone. Tidak menambahkan akun, data pelanggan, sesi, kasus atau role runtime.

Seluruh upgrade, version tracking dan pembuatan schema dilakukan dalam satu transaksi. Advisory transaction lock mengurutkan runner untuk schema yang sama, statement timeout 30 detik dan lock timeout 3 detik. Menjalankan ulang upgrade pada head tidak membuat ulang tabel atau menghapus data. Schema yang berkonflik tidak di-stamp atau dihapus otomatis; review drift sebelum melanjutkan. Tidak ada perintah downgrade destruktif.

Uji migration dari kosong memakai schema sementara acak `db02_test_<uuid>` pada database pengujian yang sama. Setelah tes, schema tepat tersebut dihapus dalam finally; app tidak dihapus. Constraint diuji memakai role migration untuk memisahkan bukti integritas relasi dari bukti RLS/runtime DB-03.

```powershell
$env:RUN_NEON_MIGRATION_TESTS = '1'
try { uv run --locked --project backend pytest backend/tests/integration/test_foundation_migration.py } finally { Remove-Item Env:RUN_NEON_MIGRATION_TESTS }
```

Aktifkan RUN_NEON_DB_TESTS=1 juga untuk regresi koneksi DB-01 pada full suite. Tanpa flag, tes Neon dilewati. DB_TEST_ENV_FILE dapat menunjuk konfigurasi pengujian lain. Bila proses dimatikan paksa saat cleanup, tinjau hanya schema dengan prefix db02_test_ milik run pengujian tersebut; jangan menghapus app.

Rujukan: [Alembic connection sharing](https://alembic.sqlalchemy.org/en/latest/cookbook.html#sharing-a-connection-across-one-or-more-programmatic-migration-commands), [PostgreSQL constraints](https://www.postgresql.org/docs/current/ddl-constraints.html).

Verifikasi DB-02 pada 2 November 2026: **144 tes live lulus**, audit QA independen lulus. Schema app sudah ber-revision `0001_foundation`; upgrade ulang/current dan metadata sembilan tabel fondasi diverifikasi. RLS serta role runtime belum menjadi klaim DB-02.

## Role runtime dan RLS DB-03

Persiapan awal (migration credential harus dapat membuat role):

```powershell
uv sync --locked --project backend
uv run --locked --project backend airport-assistant-db-provision
```

Provisioner membuat role runtime LOGIN terbatas dan policy-owner NOLOGIN, menerapkan revision `0002_runtime_rls`, menguji login runtime, lalu mengganti DATABASE_URL di .env secara internal. Password dihasilkan script dan tidak dicetak. Credential migration tidak diganti. Jangan set DATABASE_URL di environment proses saat memakai provisioner file lokal; override tersebut akan ditolak. --env-file tersedia untuk lokasi konfigurasi lain. API yang sudah berjalan perlu direstart untuk memakai konfigurasi baru.

Role yang sudah ada tidak diberi privilege tambahan secara diam-diam bila atribut/membership-nya tidak aman. Pengulangan dengan URL role runtime yang sama mempertahankan password. Bila terjadi kegagalan sebelum update file, role/migration mungkin sudah dibuat; perbaiki tahap yang dilaporkan dan ulangi provisioner. Tidak perlu menyalin password ke chat. PUBLIC CREATE/TEMP pada database dan CREATE pada schema public dicabut; gunakan database khusus proyek ini seperti pada setup DB-01.

Layanan menggunakan `db.transaction(DatabaseContext(tenant_id, principal_id, actor_kind))`. Context immutable berasal dari server yang telah memverifikasi sesi; bukan input tenant/aktor dari user atau model. Tanpa context, query data fondasi default deny. Jalur bootstrap terbatas adalah `app.lookup_tenant_host(host)` dan hanya mengembalikan UUID dari host/tenant aktif. Tidak ada endpoint autentikasi atau bypass RLS baru pada DB-03.

Startup RuntimeDatabase menolak credential owner/BYPASSRLS/CREATE ROLE/CREATE DB, role induk yang bisa di-SET, database/schema ownership dan hak CREATE/TEMP. Semua transaksi memasang context transaction-local; cancellation membuang koneksi yang tidak pasti. RLS fondasi membatasi kontak/principal ke diri sendiri, membership dan mutasi role ke admin aktif dalam organisasi yang sesuai. Kasus dan aturan akses CS ke kasus tetap tugas berikutnya.

Tes RLS opt-in memakai login khusus acak dan schema db03_test_UUID terisolasi; credential hanya berada dalam proses tes. Role dan schema tes dibersihkan setelah selesai. Tambahkan flag berikut untuk full suite:

```powershell
$env:RUN_NEON_DB_TESTS = '1'
$env:RUN_NEON_MIGRATION_TESTS = '1'
$env:RUN_NEON_RLS_TESTS = '1'
try { uv run --locked --project backend pytest backend/tests } finally {
  Remove-Item Env:RUN_NEON_DB_TESTS, Env:RUN_NEON_MIGRATION_TESTS, Env:RUN_NEON_RLS_TESTS
}
```

Migrasi fondasi DB-02 tetap diuji secara khusus pada revision0001. Tes RLS meng-upgrade dari kosong ke revision0002 serta memeriksa login nyata; menjalankan SET ROLE dari owner bukan pengganti bukti tersebut. DB-01 probe tetap memberi SELECT/INSERT hanya pada tabel probe sementara yang akan dihapus, bukan memberi CREATE pada runtime.

Rujukan: [PostgreSQL row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html), [Neon roles](https://neon.com/docs/manage/roles).

Mutasi membership/role/queue untuk principal aktor sendiri ditolak, termasuk ketika ia admin. Positive admin management hanya menarget principal lain dalam organisasi yang diizinkan. Runtime hanya boleh mengubah active/version pada membership, role pada membership_roles, dan queue_id pada membership_queues; tenant/principal/organization/membership target tidak dapat diretarget lewat UPDATE. Perubahan scope membutuhkan operasi eksplisit terhadap record yang sesuai, dengan pemeriksaan bisnis/audit pada fitur AUTH/admin berikutnya.

Status pemasangan DB-03: app ber-revision `0002_runtime_rls`. Provisioner memverifikasi login runtime terbatas dan memperbarui hanya DATABASE_URL secara internal; DATABASE_DIRECT_URL tetap credential migration. API yang sudah berjalan perlu direstart untuk memuat URL baru. Saat deployment, berikan DATABASE_URL saja pada API/worker; credential direct hanya untuk job migration/provisioning.

Hasil final DB-03: full suite live **192 passed** pada 2 November 2026. QA independen mengulang suite191tes dan tes tambahan credential runtime aktual, memverifikasi rollout, dan menyatakan lulus dalam scope keamanan fondasi.

## Pola upgrade DB-04

[Migration workflow](../docs/backend/migration_workflow.md) menjelaskan pembuatan revision offline, PK/FK/index/RLS, siklus FK, expand/contract dan bukti upgrade. `airport-assistant-migrate new --message "..."` membuat file scaffold dari checkout tanpa membaca credential; implementasikan file sebelum upgrade. Setelah upgrade, guard menolak tabel tanpa ENABLE/FORCE RLS atau dengan runtime sebagai owner dan me-rollback transaksi.

Tambahkan RUN_NEON_UPGRADE_TESTS=1 pada full suite untuk populated-upgrade dan siklus FK di schema sementara. Revision contoh hanya fixture test; tidak menambah tabel demonstrasi ke app atau mengubah head production0002. Tes ini bukan bukti performa backfill besar, rolling deployment, atau fitur bisnis yang belum diimplementasikan.

Verifikasi DB-04: **201 tes live lulus**, dikonfirmasi ulang QA. Build bersih offline129passed72skipped serta HTTP bootstrap lulus. Smoke schema app tetap revision0002 dan sembilan tabel fondasi; fixture contoh tidak terpasang di app.

## Customer authentication AUTH-01

[Authentication guide](../docs/backend/authentication.md) memuat setup internal key, cookie/CSRF/origin, kontrak endpoint, rate/TTL dan batas delivery. Feature default nonaktif pada .env.example; setup script menyalakan auth/database dan dapat mendaftarkan host demo. Origin harus HTTPS; server HTTP bootstrap lokal tetap untuk health/development non-auth sampai proxy HTTPS disiapkan.

```powershell
uv run --locked --project backend airport-assistant-auth-setup --demo-host localhost
```

Sender nyata belum dipasang, sehingga API normal memberi503 untuk permintaan kirim kode. Controlled sender hanya ada di tes; tidak ada debug endpoint untuk mengambil OTP. Login staf dan pemindahan riwayat/draf bukan bagian AUTH-01.


## Staff authentication AUTH-03

[Staff authentication guide](../docs/backend/staff_authentication.md) menjelaskan login password lokal untuk staf terprovisi, scope membership/queue, pencabutan, CLI credential interaktif dan batas rilis. POST /api/v1/staff/auth/login memerlukan sesi anonim/CSRF; GET /api/v1/staff/me membaca membership terbaru. Logout memakai endpoint umum. Credential tidak membuat membership baru dan tidak dikirim melalui email pelanggan.

Revision0004_staff_auth dipasang melalui migration runner; histori0001–0003 tidak berubah. Runtime tidak mempunyai akses tabel credential langsung. Uji terfokus: unit/test_staff_passwords.py dan integration/test_staff_auth.py dengan RUN_NEON_AUTH_TESTS=1.


## Shared authorization AUTH-04

[Authorization guide](../docs/backend/authorization.md) memuat identitas bersama, helper proof read/mutation, exact tenant/kind/id, ownership dan scope staf, serta concealed404/visible403. /me dan /staff/me memakai AccessService; tidak ada migration atau endpoint resource produksi baru. Uji terfokus: unit/test_access_policy.py dan integration/test_access_http.py dengan RUN_NEON_AUTH_TESTS=1.

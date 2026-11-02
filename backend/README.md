# Menjalankan backend

BE-01 menyediakan bootstrap FastAPI yang executable. BE-02 menambahkan liveness, error terstruktur, request ID dan batas input. Database, autentikasi, worker executable dan route bisnis belum tersedia.

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

Field provider lain di `.env.example` adalah placeholder tahap berikutnya dan tidak dimuat oleh Settings BE-01. API tidak memanggil provider atau Neon saat bootstrap. Tidak perlu mengisi credential untuk menjalankan tugas ini.

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

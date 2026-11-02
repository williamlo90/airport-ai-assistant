# Menjalankan backend

BE-01 menyediakan bootstrap FastAPI yang executable. Belum ada database, autentikasi, worker executable, health endpoint, atau route bisnis. Health dan format error API berada pada BE-02.

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

Dalam development, buka http://127.0.0.1:8000/docs atau http://127.0.0.1:8000/openapi.json. Daftar business paths masih kosong. Root `/` mengembalikan 404; tidak dipakai sebagai bukti health integrasi. Hentikan dengan Ctrl+C. Dokumentasi development tidak diaktifkan ketika APP_ENV=production.

Jangan mengganti `uv sync --locked` dengan pembaruan dependency tanpa review. `backend/uv.lock` mengunci dependency transitif; `pyproject.toml` menyatakan paket langsung dan batas versi. `.venv` lokal tidak di-commit.

## Konfigurasi yang dipakai saat ini

| Variabel | Aturan |
| --- | --- |
| APP_ENV | Wajib: development/test/production. |
| APP_HOST | Default 127.0.0.1; pilihan loopback/bind: 127.0.0.1, 0.0.0.0, ::1, ::. Gunakan loopback untuk development. |
| APP_PORT | Default 8000; integer 1–65535. |

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

Tes BE-01 memeriksa dotenv/environment override, config wajib, nilai invalid dan redaksi output CLI, pembuatan app tanpa layanan eksternal, serta batas dokumentasi development. Tes API security dan business workflow menyusul bersama fiturnya.

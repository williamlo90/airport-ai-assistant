# Airport Reference Source — SRC-01

Service sumber mandiri untuk data fiktif bandara. Ada booking parkir/lounge, catatan transaksi, kebijakan versi lama/baru dan relasi pengguna. API hanya membaca; tidak membuat booking, menerima pembayaran, mengeksekusi refund atau memberikan kuasa.

Service ini mempunyai package Python, dependency lock, SQLite dan tes sendiri. Tidak mengimpor backend asisten, tidak memakai credential Neon, dan tidak mengubah database aplikasi. SQLite dipilih untuk sumber referensi yang ringan. Integrasi backend ke API ini adalah SRC-02; pemeriksaan grants dan security labels di sisi aplikasi adalah SRC-03A/B.

## Jalankan dari root repository

Python3.13 dan uv diperlukan. Setup menyiapkan database terpisah dan dua key acak di reference_service/.env yang diabaikan Git. Key hanya dipakai script secara internal dan tidak dicetak. Tidak perlu menyalin credential dari database aplikasi.

```powershell
uv sync --locked --project reference_service
uv run --locked --project reference_service airport-reference-setup
uv run --locked --project reference_service airport-reference-api --check-config
uv run --locked --project reference_service airport-reference-api
```

Service berjalan di http://127.0.0.1:8010. Health publik: http://127.0.0.1:8010/health/live. Dokumentasi development: http://127.0.0.1:8010/docs. Endpoint data memerlukan credential service dan konteks bertanda tangan, sehingga membuka URL booking langsung tanpa proof memberi401.

Buka PowerShell kedua dari root repository untuk mencoba data melalui client lokal yang menandatangani request:

```powershell
uv run --locked --project reference_service airport-reference-demo booking PK-A-001
uv run --locked --project reference_service airport-reference-demo transaction TX-A-001 --subject beneficiary
uv run --locked --project reference_service airport-reference-demo transaction TX-A-001 --subject payer
uv run --locked --project reference_service airport-reference-demo booking PK-A-001 --subject outsider
```

Tiga perintah pertama memberi HTTP200; outsider memberi404 dan exit1. Demo bukan endpoint bypass: ia memuat key lokal secara internal dan memakai protokol autentikasi normal. Pilihan aktor hanya untuk dataset fiktif lokal. URL tujuan tetap127.0.0.1 dengan port lokal; proxy environment dan redirect dimatikan agar credential tidak diteruskan ke tujuan lain. Jangan menaruh signer/key pada browser atau model.

Hentikan API dengan Ctrl+C. Jika uv belum tersedia pada PATH di workspace ini, interpreter yang sudah disiapkan juga dapat dipakai:

```powershell
.\reference_service\.venv\Scripts\python.exe -m airport_reference.main
.\reference_service\.venv\Scripts\python.exe -m airport_reference.demo booking PK-A-001
```

## Dataset yang tersedia

| Metadata | Isi |
| --- | --- |
| airport-a | Tenant11111111-1111-4111-8111-111111111111; zonaAsia/Jakarta dan IDR exponent0. |
| airport-b | Tenant22222222-2222-4222-8222-222222222222; zonaAsia/Singapore dan SGD exponent2. Referensi booking/transaksi sengaja sama untuk menguji isolasi. |
| Booking per tenant | PK-A-001 parkir, LG-A-001 lounge. |
| Transaksi per tenant | TX-A-001 pending, TX-A-002 status provider_review yang ditampilkan unknown, TX-L-001 paid. |
| Kebijakan | parking-v2/v3 dan lounge-v2/v3; booking merujuk v2, tidak otomatis memilih v3. |
| Subject per tenant | owner, representative, beneficiary, payer, outsider. Reference ini belum dihubungkan ke principal aplikasi; mapping dilakukan saat SRC-02. |

Total fixture:2tenant,10subject,4booking,6transaksi,8versi kebijakan dan30relasi. Semua fiktif; tidak ada API payment gateway atau data pelanggan nyata.

| Subject | Booking | Transaksi |
| --- | --- | --- |
| owner | booking:read pada parkir/lounge. | payment_status:read + payment_details:read. |
| representative | booking:read sebagai recorded_representative. | Tidak ada grant pembayaran. |
| beneficiary | booking:read. | payment_status:read, tanpa nominal/mata uang/refund detail. |
| payer | Tidak ada grant booking. | Status dan detail transaksi langsung; booking_reference tidak ditampilkan. |
| outsider | Tidak ada. | Tidak ada; own relations kosong. |

Hak berasal dari scopes yang eksplisit pada relasi resource, bukan nama owner/payer atau klaim keluarga. Relasi sumber dicatat satu record efektif per tenant/subject/resource dengan scope terpisah untuk booking dan transaksi. API tidak membuat hubungan baru.

## API dan projection

| Endpoint GET | Syarat dan hasil |
| --- | --- |
| /source/v1/bookings/{reference} | booking:read; field booking tanpa field pembayaran. |
| /source/v1/bookings/{reference}/transactions | Booking harus boleh dibaca; setiap transaksi juga diperiksa grant pembayaran sebelum pagination. Tidak ada grant transaksi:404. |
| /source/v1/transactions/{reference} | payment_status:read; payment_details:read menambah currency/amount_minor/currency_exponent/refund_status. booking_reference hanya jika booking juga berhak dibaca. |
| /source/v1/policies/{reference} | Versi persis yang dirujuk setidaknya satu booking yang berhak dibaca, dalam tenant yang sama. |
| /source/v1/subjects/me/relations | Hanya relasi milik subject yang ditandatangani. |

Transaksi status-only tidak mengembalikan field detail sebagai null; field tersebut tidak ada. source_updated_at berasal dari sumber, observed_at adalah saat dibaca. Waktu UTC RFC3339 disertai zona IANA untuk layanan. Uang integer minor units dengan exponent yang divalidasi; tidak memakai floating point. Status tidak dikenal tetap unknown dan source_status_label terbatas, tidak dianggap paid/failed. Tidak ada payment instruction URL pada dataset ini.

Endpoint list menerima limit default20/maksimum100 dan cursor. Respons items,next_cursor; tanpa total global. Filtering hak akses dilakukan sebelum pagination. Cursor HMAC memuat binding tenant/subject/endpoint/filter/limit/order/generation dataset dan expiry15menit. Cursor bukan bukti hak: grants dibaca lagi setiap halaman. Untuk dataset referensi yang dibatasi ukuran fixture, implementasi mengumpulkan data berhak dan projection sebelum slicing halaman; limit bukan batas total pekerjaan query. Benchmark skala besar dan optimasi keyset SQL belum dilakukan. Cursor rusak/tidak sesuai memberi400; penggantian dataset membuat generation baru dan menolak cursor lama.

Missing/hidden resource, tenant/subject nonaktif dan subject tak dikenal memberi404 yang sama. Credential/signature tidak sah401. Storage tidak tersedia/tidak valid503 SOURCE_UNAVAILABLE, bukan booking tidak ditemukan atau pembayaran gagal. Error memakai envelope dan request-ID terkontrol; tidak mengembalikan SQL, secret atau detail exception. Semua respons no-store; access log Uvicorn dimatikan.

## Protokol caller untuk SRC-02

Dua secret berbeda: REF_SERVICE_TOKEN untuk Authorization:Bearer, REF_CONTEXT_SECRET untuk HMAC konteks dan cursor dengan domain tag berbeda. Keduanya minimum32karakter; setup menghasilkan token acak48byte. Runtime API hanya memerlukan file SQLite baca dan key service ini.

Header wajib tunggal: Authorization, X-Source-Context, X-Source-Signature. Context adalah JSON UTF-8 dalam base64url tanpa padding, berisi v=1, aud=airport-reference-v1, tenant_id UUID, subject reference, iat, exp dan nonce UUID. JSON duplicate/extra fields ditolak. iat/exp integer, TTL1–60detik, toleransi clock maju5detik dan exp wajib belum lewat.

Signature berupa hex HMAC-SHA256 atas concatenation byte berikut:

```text
source-v1\n + METHOD_ASCII + \n + RAW_PATH_BYTES + \n + RAW_QUERY_BYTES + \n + CONTEXT_BASE64URL_ASCII
```

Raw path termasuk percent encoding; raw query termasuk urutan/encoding parameter, tanpa tanda?. Tidak dilakukan decode lalu encode ulang sebelum verifikasi. Signature dibandingkan secara konstan. Semua endpoint data GET tanpa body; method/path/query yang berubah memerlukan signature baru. signed_headers menyediakan implementasi referensi untuk client internal.

Nonce adalah identifier request, bukan penyimpanan anti-replay. GET identik boleh diulang selama TTL; sumber memeriksa grants lagi setiap kali. Token service saja atau header identitas publik tidak cukup. Query identitas/field tidak dikenal dan parameter ganda ditolak; X-Tenant-ID/X-Subject-ID/X-Principal-ID/X-Source-Subject ditolak. Backend SRC-02 wajib membentuk subject dari identitas terverifikasi dan mapping sumber, bukan input/model.

HTTP tanpa TLS hanya diterima untuk development/test dengan peer loopback dan host localhost/127.0.0.1/::1. CLI default bind127.0.0.1 dan tidak mempercayai forwarding headers. Production membutuhkan runner/TLS yang dikonfigurasi dan menonaktifkan docs; deployment TLS, key rotation, observability/load limits dan ketersediaan belum menjadi bukti SRC-01.

## Storage dan reset fixture

Fixture JSON divalidasi ketat sebelum publikasi: tipe/statuslabel/scopes, uang, waktu, timezone serta relasi tenant/resource. Seeder membuat file sementara, memakai transaksi dan composite foreign keys, memeriksa integrity/foreign_key_check, menutup writer, lalu memublikasikan file tanpa menimpa target baru yang sudah muncul. Seeder memakai lock kooperatif; reset eksplisit memeriksa identitas/stat file sebelumnya. Kegagalan tidak mengganti database lama. Runtime membuka mode=ro serta query_only dengan transaksi baca per request; tidak memakai immutable=1, WAL atau cache grants antar request.

Hentikan API sebelum reset, lalu:

```powershell
uv run --locked --project reference_service airport-reference-setup --reset-fixtures
uv run --locked --project reference_service airport-reference-api
```

Setup biasa idempotent dan mempertahankan database/key yang sudah ada. Reset eksplisit hanya boleh mengganti file SQLite yang application_id-nya dikenali. Ini alur offline stop/reset/restart; tidak menjanjikan hot reseed, rolling upgrade atau writer produksi. Jika proses seed dihentikan paksa, lock .seed.lock dapat tertinggal. Pastikan tidak ada proses seed/API berjalan, lalu hapus hanya lock milik database referensi tersebut sebelum mencoba ulang. Tidak ada klaim melindungi file dari administrator lokal yang sengaja mengganti file/lock di luar protokol. Mode readonly tidak menggantikan izin filesystem: file dan key harus tetap dilindungi dari pihak yang tidak dipercaya. SQLite ini tidak memakai PostgreSQL RLS; isolasi source diuji pada query dan protokol service.

## Pengujian

```powershell
uv run --locked --project reference_service pytest reference_service/tests -q
```

Tes memakai file sementara dan key fiktif, tanpa provider/Neon. Mencakup ACL/field projection, tenant collision, status unknown, versi policy, grant revoke, cursor, signature/path/query/time, seeding atomic/readonly, setup tanpa secret output dan TCP normal+demo. Server tes selalu dihentikan. Scope SRC-02 belum diuji karena konektor backend belum dibuat.

Rujukan storage: [SQLite URI readonly](https://www.sqlite.org/uri.html), [SQLite isolation](https://www.sqlite.org/isolation.html).

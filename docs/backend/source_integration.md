# Konektor sumber SRC-02

SRC-02 menghubungkan backend asisten ke API source reference melalui HTTP. Backend tidak mengimpor package/source database, membaca SQLite langsung, atau menggunakan data fixture sebagai fallback. Konektor menyediakan booking, transaksi langsung, transaksi melalui booking, policy versi tertentu dan own relations.

Tiga endpoint portal tersedia saat APP_SOURCE_ENABLED=true:

| Endpoint GET | Akses |
| --- | --- |
| /api/v1/bookings/{reference} | Customer terverifikasi dengan source mapping dan grant sumber. |
| /api/v1/transactions/{reference} | Customer terverifikasi; tidak mewajibkan booking reference. |
| /api/v1/bookings/{reference}/transactions | Customer terverifikasi, limit1–100 dan cursor opaque opsional. |

Policy/relations merupakan metode internal SourceReadService, belum endpoint publik atau tool model. Anonymous/staff tidak memakai endpoint customer walaupun principal staf juga mempunyai source reference. Endpoint tidak membuat booking, pembayaran, refund atau relasi baru.

## Identitas dan mapping

get_customer_identity memakai pemeriksaan sesi bersama AUTH-04. Tenant berasal dari host terdaftar, principal dari cookie sesi aktif. SourceReadService memakai SOURCE_TENANT_MAP yang dikelola backend, lalu PostgresSourceSubjects mengambil principals.source_subject_ref melalui DatabaseContext milik customer tersebut dan runtime RLS. Principal serta tenant wajib aktif.

Map tenant berbentuk JSON canonicalUUID ke canonicalUUID, satu-ke-satu. Duplicate key, UUID alias dan dua tenant aplikasi menuju tenant sumber sama ditolak. Mapping disalin ke struktur readonly service. Missing tenant/subject mapping memberi404 generik sebelum request ke provider. Subject tidak valid memberi503 konfigurasi sumber, bukan tebakan identitas baru. Tidak ada lookup berbasis nama/email customer dari query, dan tidak ada pemberian kuasa.

Header identitas publik dan query ekstra ditolak. Client tidak dapat mengganti tenant, subject, source URL, credential atau pemilik resource melalui chat/body/header/query. Mapping principal hanya boleh ditetapkan operator/admin tepercaya; runtime tidak diberi hak UPDATE source_subject_ref. Cookie pelanggan tidak diteruskan ke source.

## Konektor HTTP

SourceHttpClient memakai satu AsyncClient per aplikasi, pool maksimum4 dan keepalive2. Waktu tunggu pool1detik, connect/read/write2detik dan deadline jaringan keseluruhan SOURCE_TIMEOUT default5detik. Response dibaca sebagai raw stream dengan SOURCE_MAX_RESPONSE_BYTES default262144byte, maksimum1MiB. Content-Length saja tidak dipercaya; actual bytes dihitung. Response terkompresi ditolak dan request meminta identity encoding untuk menghindari decompression bomb. Validasi CPU dibatasi ukuran payload/model, bukan benchmark hard real-time.

Origin hanya dari konfigurasi server: HTTPS tanpa userinfo/query/fragment/basepath tambahan. HTTP hanya untuk numeric loopback127.0.0.1/::1 dengan flag eksplisit dan env development/test; production menolak HTTP termasuk bentuk huruf besar/kecil. Proxy environment, redirect, retry otomatis dan cookies provider dimatikan/ditolak. Tidak mengikuti URL dari payload atau cursor. Key harus ASCII terlihat tanpa whitespace.

Setiap request membuat konteks baru dengan aud/version/tenant sumber/subject/nonce/iat/exp dan HMAC atas raw path/query yang benar-benar dikirim HTTPX. Format mengikuti [protokol SRC-01](../../reference_service/README.md). Assertion tidak disimpan sebagai cookie. Raw request identik boleh diulang dalam TTL oleh source, tetapi adapter ini tidak menambahkan retry otomatis.

Client ditutup melalui lifespan bersama resource database, termasuk bila startup DB gagal. Response stream ditutup pada sukses/error, timeout dan cancellation. Logger HTTPX/HTTPCore yang dipakai dibatasi ke event teredaksi agar URL/ref/cursor/header/exception tidak menjadi log sensitif.

## Validasi respons dan status

Adapter menolak duplicate JSON keys, format non-UTF8/JSON, field ekstra, tipe salah, UTC/timezone/interval tidak valid, nominal bukan integer/exponent salah, scopes yang tidak sesuai, reference berbeda dari permintaan, item halaman duplikat atau parent booking yang salah. Jumlah item harus tidak melebihi limit; next_cursor hanya opaque string terbatas atau null.

Status provider baru yang berupa label string valid menjadi unknown dengan source_status_label terbatas. Field status yang hilang/null/tipe salah tetap invalid502; tidak difabrikasi menjadi unknown. Status/refund yang hilang tidak dianggap paid/failed. Status-only transaction menolak seluruh field money/refund detail bahkan jika nilainya null. DTO output menghilangkan field yang tidak berhak, bukan menampilkannya sebagai null.

Source tetap otoritas grants saat read ini. Khusus booking_reference pada transaksi, hak terhadap booking tidak dapat dibuktikan dari payment-only scopes pada DTO; adapter mempercayai redaksi source yang telah diotorisasi, sambil menguji perilaku payer melalui source normal. Ini tidak menggantikan pemeriksaan SourceGrantProvider, security labels atau data turunan SRC-03A/B.

| Kondisi | Respons portal |
| --- | --- |
| Sesi anonymous/staff/tidak sah | 401 sebelum pembacaan sumber. |
| Mapping tidak ada atau source404 | 404 RESOURCE_NOT_FOUND generik. |
| Cursor/query tidak valid, source400 | 400 BAD_REQUEST. |
| Timeout/network/pool/source401/403/408/429/5xx | 503 SOURCE_UNAVAILABLE, tanpa logout customer atau retry otomatis. |
| Redirect, cookie provider, protocol rusak, respons terlalu besar/terkompresi/schema salah | 502 SOURCE_INVALID_RESPONSE. |

Source error tidak diteruskan sebagai raw body. Respons dan error private no-store; request-ID memakai envelope aplikasi. Tidak ada fallback cache/fixture/empty list ketika source gagal. Pagination relay tetap terikat tenant/subject/filter/generation oleh source dan memeriksa grants pada halaman baru.

## Setup lokal tanpa menampilkan key

Jalankan dari root repository setelah SRC-01 dan AUTH-01 siap:

```powershell
uv sync --locked --project backend
uv run --locked --project backend airport-assistant-source-setup
uv run --locked --project backend airport-assistant-api --check-config
```

Setup membaca reference_service/.env dan .env hanya di dalam script. Key source disalin ke field SOURCE_* lokal; host localhost yang sudah terdaftar dipetakan ke airport-a fixture. Setup tidak mengubah credential database/session lain, membuat customer, memverifikasi kontak, menetapkan source_subject_ref atau membuat relasi sumber. Source origin/key yang sudah berbeda tidak ditimpa sebagai rotasi terselubung. Restart backend yang sudah berjalan untuk memuat konfigurasi baru.

APP_SOURCE_ENABLED defaultfalse pada contoh. Saat true, source settings dan authentication wajib tersedia; mismatch gagal startup/config check. SOURCE_TENANT_MAP wajib canonical satu-ke-satu. Key produksi harus datang dari secret store; helper ini hanya local reference setup, bukan mekanisme onboarding/rotation enterprise.

## Cara mencoba konektor yang sudah terpasang

Terminal pertama:

```powershell
.\reference_service\.venv\Scripts\python.exe -m airport_reference.main
```

Terminal kedua:

```powershell
.\local\db01-venv\Scripts\python.exe -m airport_assistant.source_check booking PK-A-001
.\local\db01-venv\Scripts\python.exe -m airport_assistant.source_check transaction TX-A-001 --subject beneficiary
```

Keduanya harus menghasilkan HTTP200. source_check menggunakan adapter backend yang sama, tetapi merupakan diagnostic operator untuk fixture localhost saja. Ia memuat key secara internal dan memilih aktor fiktif; bukan endpoint login/bypass dan bukan bukti autentikasi pelanggan live. CLI menolak tujuan nonloopback/production, redirect dan proxy environment. Portal customer tetap memerlukan sesi customer terverifikasi serta principal source mapping. Pengiriman verifikasi email nyata masih MAIL-05.

## Pengujian dan batas tahap

```powershell
uv run --locked --project backend pytest backend/tests/unit/test_source_client.py backend/tests/unit/test_source_service.py -q

$env:RUN_NEON_AUTH_TESTS='1'
$env:RUN_SOURCE_NETWORK_TESTS='1'
try {
  uv run --locked --project backend pytest backend/tests/integration/test_source_bridge.py -v
} finally { Remove-Item Env:RUN_NEON_AUTH_TESTS,Env:RUN_SOURCE_NETWORK_TESTS }
```

Tes bridge memerlukan environment reference_service yang sudah diinstal. Server source memakai interpreter tersendiri, port/SQLite/key sementara dan normal HTTP/HMAC; backend memakai restricted login/schema Neon sementara dengan OTP delivery terkontrol di tes. Tidak memprovisi akun/relasi pada app aktual. Tes membuktikan tenant aplikasi berbeda dari tenant sumber, role resource owner/rep/beneficiary/payer/outsider, policy/relations internal, pagination, logout, mapping/claim rejection, source outage dan grant revoke. Server/schema/role tes dibersihkan.

Tidak ada migration baru; schema tetap0004_staff_auth. SRC-03A selanjutnya mengimplementasikan pemeriksaan grants serta security labels untuk commit kasus privat. SRC-03B/QUALITY-01 memeriksa case/blob/cursor/model/worker/email sebenarnya. Snapshot identity request atau hasil read SRC-02 tidak boleh menjadi izin permanen untuk pekerjaan berikutnya.

Rujukan: [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/), [HTTPX async streaming](https://www.python-httpx.org/async/).

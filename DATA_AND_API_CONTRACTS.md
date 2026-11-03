# Airport AI Assistant Data and API Contracts

Spesifikasi desain yang menurunkan [PRODUCT_CONTRACT.md](PRODUCT_CONTRACT.md) dan [ARCHITECTURE.md](ARCHITECTURE.md). Nama field berikut menjadi kontrak awal untuk implementasi; perubahan memerlukan pembaruan pengujian kontrak. Tidak ada endpoint untuk booking baru, perubahan booking, pembayaran, refund execution atau pemberian kuasa.

## 1 Konvensi dan konteks akses

- Base path `/api/v1`; JSON UTF-8; enum wire memakai English; label UI Indonesia/Inggris.
- ID internal UUID; waktu UTC RFC3339. Waktu layanan juga memuat zona IANA dari sumber. Nominal integer dalam minor units bersama currency dan aturan exponent mata uang, bukan floating point.
- Tenant efektif berasal dari host terdaftar dan identitas server-side. tenant_id body/query tidak bisa mengganti konteks; field ekstra pada mutasi ditolak.
- Browser memakai cookie sesi aman dan CSRF protection untuk mutasi. Sesi anonim tidak dapat membaca data pribadi selain percakapan anonimnya sendiri. Sesi staf tidak bisa dibuat melalui endpoint verifikasi pelanggan.
- Semua object read, list, pagination, event dan blob download memeriksa tenant serta ACL. Objek di luar scope mengembalikan 404 generik; 403 untuk tindakan yang dilarang pada objek yang sudah terlihat. Error tidak mengungkap keberadaan objek tenant lain.
- Pagination cursor terikat tenant, aktor dan filter; limit default 20, maksimum 100. Query tidak melakukan lookup UUID global lalu mengembalikan isi sebelum pemeriksaan.
- Header `X-Request-ID` dihasilkan atau dinormalisasi server. Error memuat `code`, `message`, `request_id`, `retryable`; tidak membawa stack trace, SQL, secret atau snapshot pelanggan lain.

```json
{"error":{"code":"SOURCE_UNAVAILABLE","message":"Informasi belum dapat diperiksa.","request_id":"req-123","retryable":true}}
```

Error utama: 401 AUTH_REQUIRED; 403 ACTION_FORBIDDEN; 404 RESOURCE_NOT_FOUND; 409 VERSION_CONFLICT/IDEMPOTENCY_CONFLICT/DRAFT_CHANGED; 410 DRAFT_EXPIRED; 422 VALIDATION_ERROR; 429 RATE_LIMITED; 502 SOURCE_INVALID_RESPONSE; 503 SOURCE_UNAVAILABLE. Kegagalan sumber bukan 404 booking dan bukan status pembayaran gagal.

### Fondasi HTTP (BE-02)

- Endpoint operasional `GET /health/live` berada di luar base path bisnis dan mengembalikan `{"status":"ok","scope":"process"}`. Tidak memeriksa readiness database/provider. Belum ada endpoint readiness.
- `X-Request-ID`: UUID valid tunggal dinormalisasi; header lain diganti UUID server. Error body dan header memakai ID sama. ID bukan dasar otorisasi.
- Batas body awal 1 MiB melalui `APP_MAX_REQUEST_BODY_BYTES` (1 byte sampai 16 MiB). Byte aktual diperiksa juga tanpa Content-Length; kelebihan memberi 413 PAYLOAD_TOO_LARGE. Framing Content-Length invalid/ganda/tidak cocok yang mencapai ASGI memberi 400 BAD_REQUEST. Penolakan framing oleh server HTTP sebelum ASGI berada di luar envelope aplikasi.
- Schema mutasi mewarisi `RequestModel`: extra fields ditolak, tipe strict, string default maksimum 4096 karakter. Batas field/domain lebih rinci ditentukan bersama endpoint fitur. Batas transport ini harus disesuaikan saat attachment multipart dibuat; tidak mengganti batas 5 MiB/file pada kontrak attachment.
- Routing 405 memberi METHOD_NOT_ALLOWED; exception tak terduga memberi 500 INTERNAL_ERROR. Keduanya mengikuti envelope error. 500 tidak menginstruksikan retry otomatis, karena hasil side effect belum tentu diketahui. Header Allow tetap tersedia pada 405.
- HTTPException biasa memakai pesan server terkontrol berdasarkan status; exception detail tidak dipublikasikan. Kode domain yang berbagi status (misalnya konflik draft/idempotency) ditambahkan melalui error domain terkontrol bersama fitur terkait.

## 2 Model data aplikasi

Semua tabel tenant-scoped memiliki tenant_id NOT NULL, created_at, updated_at, unique `(tenant_id,id)` dan composite FK untuk relasi tenant. RLS dan repository checks sama-sama diterapkan. Audit/change events tidak diedit melalui endpoint biasa.

| Entitas | Field inti dan invariant |
| --- | --- |
| tenants | id, slug, display_name, allowed_hosts, timezone, supported_languages; host unik dan onboarding teknis. |
| organizations | tenant_id, id, name; unit di dalam tenant, bukan tenant tambahan. |
| service_queues | tenant_id, id, organization_id, service_type, active; parkir/lounge atau informasi umum. |
| principals/contacts | tenant_id, id, verified_email, verified_at, source_subject_ref nullable; email kontak dan scope source dipisahkan. |
| staff_memberships | tenant_id, principal_id, organization_id, roles, allowed_queue_ids, active; pelanggan tidak bisa menulis membership. |
| verification_challenges | tenant_id, id, purpose, contact_hash, hashed_code, expires_at, attempts, consumed_at; tidak mengandung kode plaintext/log. |
| sessions | tenant_id, id, principal_id nullable, anonymous_conversation_scope, expires_at, revoked_at; token browser disimpan hash server-side. |
| conversations | tenant_id, id, customer_principal_id nullable, anonymous_session_id nullable, case_id nullable, handler_staff_id nullable, language, mode, generation_epoch, version; tepat satu kepemilikan anonim/terverifikasi pada satu waktu; handler wajib pada mode human. |
| conversation_events | tenant_id, id, conversation_id, sequence, event_type, visibility, actor_id, payload; unique `(tenant_id,conversation_id,sequence)`. |
| messages | tenant_id, id, conversation_id/case_id, author_type, author_id, channel, body, visibility, language, client_message_id, source_kind; internal note tidak tampil ke pelanggan/model customer. |
| action_drafts | tenant_id, id, principal/session_scope, conversation_id, action_type, payload, payload_hash, version, expires_at, state; pending/committed/cancelled/expired. |
| cases | tenant_id, id, public_reference, owner_contact_id, organization_id, queue_id, category, service_type, booking_ref nullable, transaction_ref nullable, summary, customer_report, source_observations, status, assigned_to nullable, next_actor, next_action, version, close_reason nullable, resolution nullable. |
| case_participants | tenant_id, case_id, principal_id, scopes, relation_origin, verified_at; hanya hubungan yang diizinkan backend; tidak mengubah perwakilan sumber. |
| case_history | tenant_id, id, case_id, actor_id, event_type, previous_version, resulting_version, reason, occurred_at. |
| email_messages | tenant_id, id, case_id, direction, message_id, in_reply_to, thread_token_hash, sender, recipient, verification_state, delivery_status, provider_message_id nullable, content_ref; provider accepted tidak berarti dibaca. |
| email_inbox | provider_account_id, provider_event_id, provider_message_id, tenant_id, signature_checked, received_at, processing_state; unique `(provider_account_id,provider_event_id)`. Message ID tidak unique di inbox karena satu email memiliki beberapa event lifecycle. |
| jobs/outbox | tenant_id, id, kind, entity_id, dedupe_key, state, attempts, next_attempt_at, lease_token, lease_until, last_error_code; satu logical job per dedupe_key. |
| idempotency_records | tenant_id, actor_scope, operation, key, request_hash, result_ref, state; unique scope+operation+key. |
| knowledge_versions | tenant_id, id, document_id, version, source_uri, service_type, visibility, organization_id nullable, status, valid_from/to, applicability, content_hash, published_at. |
| knowledge_chunks | tenant_id, id, knowledge_version_id, text, embedding, embedding_model_version, ACL; tidak boleh mewarisi tenant dari teks. |
| attachments | tenant_id, id, case_id/draft_id, uploader_id, blob_key, mime_type, byte_size, scan_status, content_hash; download hanya setelah otorisasi. |
| ratings | tenant_id, id, target_type, target_id, customer_scope, score, comment nullable; satu rating aktif per target/pelanggan. |

Messages yang hanya terkait case dapat memiliki conversation_id null; minimal salah satu relasi wajib ada, dan jika keduanya ada case harus sama dengan conversation.case_id. Client message uniqueness discope ke tenant, pengirim dan percakapan/kasus. Source observations menyimpan field minimum, source_updated_at dan observed_at; tidak menggantikan sistem sumber.

email_messages memiliki unique `(provider_account_id,direction,provider_message_id)` untuk message_id yang sudah tersedia; field provider_account_id juga disimpan pada entitas itu. Banyak email_inbox events boleh mengacu pada satu email_messages row. Event yang berbeda tetap diproses; redelivery event yang sama tidak membuat efek berulang.

Kasus dan semua konten turunannya memuat resource_security_labels: source resource references dan required_source_scopes. Labels diwariskan dari tool observations dan scope kasus; summary/pesan bebas tidak otomatis dideklasifikasi. Untuk MVP pelanggan membaca agregat privat hanya jika ia peserta kasus dan seluruh grants sumber yang diperlukan masih aktif. Hak case_read tidak memberikan payment read. Grant hilang/tidak dapat diperiksa mengakibatkan penolakan seluruh isi privat, termasuk events, blob dan riwayat email. Kasus umum tanpa resource source mengikuti kontak kasus terverifikasi. Pemeriksaan sama wajib sebelum memasukkan riwayat ke model dan sebelum worker mengirim outbound; izin saat job dibuat tidak cukup. Staf mengakses sesuai membership layanan/organisasi, bukan grants pelanggan.

Rating awal memakai integer 1–5 dengan label bahasa UI, opsional tanpa nilai default. Skala ini adalah keputusan implementasi untuk memenuhi rating dalam product contract, bukan klaim benchmark. Rating tidak mengubah case status.

## 3 Kontrak source reference API

Service referensi memiliki dataset fiktif dan storage terpisah dari schema bisnis aplikasi. API hanya menerima credential service baca dan konteks caller yang ditandatangani/ditetapkan backend; header identitas publik tidak diterima langsung. Sumber memeriksa tenant dan grants aktor di setiap endpoint. Otorisasi aplikasi tetap dilakukan sebelum data ditampilkan.

| Endpoint internal sumber | Hasil |
| --- | --- |
| GET /source/v1/bookings/{reference} | Booking terotorisasi atau 404 generik; berbeda dari 503 sumber gagal. |
| GET /source/v1/bookings/{reference}/transactions | Transaksi sesuai scope; kepemilikan booking tidak otomatis membuka semua field pembayaran. |
| GET /source/v1/transactions/{reference} | Pencarian langsung transaksi dengan grants aktor, termasuk ketika referensi booking belum diketahui; tidak memberi akses booking otomatis. |
| GET /source/v1/policies/{policy_reference} | Versi kebijakan yang berlaku untuk pesanan. |
| GET /source/v1/subjects/me/relations | Relasi owner/recorded_representative/beneficiary dan scope; tidak tersedia untuk model secara mentah. |

Contoh booking terotorisasi:

```json
{"reference":"PK-A-001","service_type":"parking","status":"confirmed","start_at":"2026-11-10T02:00:00Z","end_at":"2026-11-12T09:00:00Z","service_timezone":"Asia/Jakarta","location":"Terminal 2 Parking","policy_reference":"parking-v3","source_updated_at":"2026-11-02T01:00:00Z","observed_at":"2026-11-02T01:01:00Z","allowed_scopes":["booking:read","payment_status:read"]}
```

Transaksi mengembalikan reference, booking_reference, currency, amount_minor, currency_exponent, status, refund_status jika tersedia, source_updated_at dan observed_at. Status tidak dikenal dinormalisasi sebagai unknown dengan label sumber terbatas; tidak dikonversi menjadi paid/failed. payment instructions URL hanya dari sumber terverifikasi dengan allowlist dan expiry jika tersedia. Tidak ada endpoint source POST/PATCH untuk pembayaran atau perwakilan dalam aplikasi.

### Perincian SRC-01 yang executable

Service mandiri reference_service menyediakan lima endpoint GET di atas, dengan SQLite read-only terpisah dan fixture fiktif. Credential Bearer dan konteks signed HMAC singkat diperlukan bersamaan; konteks tenant/subject ditetapkan backend. Protokol byte, TTL/replay, pagination dan demo client ada pada [reference source guide](reference_service/README.md). Header identitas publik tidak menjadi caller.

Grant booking:read tidak memberi payment read. Transaksi minimal membutuhkan payment_status:read; currency/amount_minor/currency_exponent/refund_status hanya ada dengan payment_details:read. booking_reference pada transaksi hanya ditampilkan jika booking juga berhak dibaca. List melalui booking membutuhkan booking grant dan menyaring setiap transaksi sebelum pagination. Policy hanya versi persis dari booking berhak; relasi hanya milik subject caller. List memakai items,next_cursor dengan default20/maksimum100, cursor signed dan terikat tenant/subject/filter/limit/generation dataset. Unknown status tetap unknown; tidak ada URL instruksi pembayaran pada fixture ini.

SRC-01 membuktikan sumber mandiri. Konektor aplikasi dan pemetaan principal/source_subject_ref tetap SRC-02; grants/security labels data turunan tetap SRC-03A/B.

### Perincian SRC-02 yang executable

Backend menggunakan adapter HTTP async independen untuk kelima read SRC-01, tanpa mengimpor source package/SQLite. Tiga GET portal booking/direct transaction/booking transactions aktif hanya dengan APP_SOURCE_ENABLED dan customer session sah. Tenant aplikasi dipetakan satu-ke-satu ke tenant sumber melalui SOURCE_TENANT_MAP; subject diambil dari source_subject_ref principal sendiri dengan runtime RLS. Client tidak memasok caller atau tujuan sumber.

Respons divalidasi terhadap DTO ketat, exact reference/page/parent, scopes dan proyeksi status/details. Missing/null required status tetap invalid502; provider enum string baru menjadi unknown. Source404/mapping tidak ada tetap404, transport/timeout/sourceauth/5xx503 dan respons rusak502, tanpa mengubah status transaksi. Policy/relations belum menjadi endpoint publik. [Source integration guide](docs/backend/source_integration.md) menjelaskan protokol, batas jaringan, error, setup dan diagnostic fixture yang terpisah dari login pelanggan.

### Perincian SRC-03A yang executable

`CurrentSourceGrants` memeriksa subject aktif melalui RLS dan GET resource sumber setiap kali dipanggil; tidak memakai relasi atau hasil grant yang disimpan. Kebutuhan kosong/unknown ditolak, pembayaran tidak diwarisi dari booking,404 berarti tidak berhak dan kegagalan lain diteruskan fail closed. Pemanggil tetap wajib memeriksa sesi/ACL/keanggotaan objek secara terpisah.

Backend membentuk `SecurityLabels` dari DTO utuh, termasuk scope detail pembayaran dan kebutuhan booking terpisah bila booking_reference ikut ditampilkan. Label policy terikat booking serta referensi versi tepat. Union mempertahankan semua kebutuhan, menolak lintas tenant dan dibatasi64resource. Restore repository memvalidasi JSON dengan tenant dari baris terotorisasi; empty/malformed labels tidak mengizinkan konten privat sumber. `PrivateSourceAccess` memerlukan seluruh grants pelanggan saat ini. [Source authorization](docs/backend/source_authorization.md) mencatat batas snapshot, fixture repository dan penerapan case/blob/model/outbound yang tetap mengikuti SRC-03B. Tidak ada endpoint label/grant publik atau perubahan schema.

## 4 Endpoint portal dan identitas

| Endpoint | Akses dan kontrak |
| --- | --- |
| GET /public/config | Tenant situs, bahasa, kategori dan informasi publik; tidak mengandung credential. |
| POST /auth/sessions | HTTPS/same-origin bootstrap sesi anonim; tidak menerima identitas/tenant client. Respons200 session_kind,principal_id(null untuk anonim); token dan CSRF hanya cookie. |
| POST /auth/challenges | Body contact,purpose; respons 202 generik, challenge_id opaque; rate limit per kontak dan IP/session. |
| POST /auth/challenges/{id}/verify | Kode sekali pakai; sukses membuat sesi pelanggan. Tidak memberi role staf. Gagal dibatasi dan kode expired tidak bisa dipakai. |
| POST /auth/logout | Revoke sesi server-side dan cookie. |
| GET /me | Identitas, tenant dan hak UI minimum; tidak mengembalikan seluruh permission tenant lain. |
| POST /conversations | language id/en; sesi anonim diperbolehkan; kepemilikan server-side. |
| GET /conversations/{id} | Metadata percakapan mandiri untuk pemilik: language,version,generation_epoch,last_sequence. Tidak mengembalikan riwayat. |
| GET /conversations/{id}/events?after={cursor} | Event terotorisasi berurutan dan next_cursor; polling, respons kosong sah. |
| POST /conversations/{id}/messages | body, client_message_id; idempotent; respons berisi message_id, sequence, processing_state. Bukan otomatis pengajuan kasus. |
| PATCH /conversations/{id}/language | language dan expected_version; tidak mengubah referensi/tanggal/nominal. |
| GET /bookings/{reference} | Verifikasi dan source grants wajib. |
| GET /bookings/{reference}/transactions | Verifikasi dan payment scopes wajib. |
| GET /transactions/{reference} | Pengecekan transaksi langsung dengan identitas dan grants sumber, tanpa mewajibkan pengguna mengetahui booking reference. |
| GET /cases | Hanya kasus yang boleh dilihat aktor, pagination terotorisasi. |
| GET /cases/{id} | Status, last_updated_at, next_actor/action, pesan publik dan detail yang diizinkan. |

Autentikasi staf memakai endpoint terpisah yang memvalidasi identitas staf terprovisi dan menghasilkan sesi sesuai membership aktif. Detail provider identitas tidak diekspos dalam kontrak bisnis. Pencabutan membership berlaku pada request berikutnya, bukan hanya setelah login ulang.

Challenge pelanggan mencatat hash sesi anonim asal dan nonce anti-CSRF jika ada. Verify pada browser yang sama memeriksa token asal, tenant, challenge dan kode sekali pakai; dalam satu transaksi rotasi sesi, pindahkan ownership percakapan yang terbukti, revoke sesi lama dan cancel pending action drafts. Susun ulang draf setelah upgrade dengan akses baru dan konfirmasi baru. Browser/sesi lain yang hanya mengetahui challenge atau conversation ID tidak dapat mengklaim riwayat. Verifikasi lewat email tanpa sesi anonim tidak mengubah ownership percakapan browser.

### Perincian AUTH-03 yang executable

POST /api/v1/staff/auth/login menerima contact/password dari sesi anonim dengan HTTPS, Origin dan CSRF yang sah. Identitas staf harus sudah diprovisi dengan kontak terverifikasi, credential aktif serta membership/role pada organisasi aktif. Password salah/identitas tidak dikenal/nonaktif memberi401 STAFF_LOGIN_FAILED yang sama. Rate limit429; kapasitas KDF penuh503. Keberhasilan200 memberi session_kind=staff,principal_id dan cookie sesi/CSRF baru, sekaligus mencabut sesi asal. Customer login tidak memberi staff; pelanggan logout dan bootstrap anonim dahulu sebelum staff login.

GET /api/v1/staff/me memberi session_kind,principal_id,memberships aktif dengan id,organization_id,version,roles,queue_ids. /me dan bootstrap sesi aktif juga mendukung kind staff tanpa rincian membership. POST /api/v1/auth/logout berlaku untuk staf. Credential_version dan password/hash tidak dikirim ke browser. Membership/roles/queues dibaca ulang dan credential_version diperiksa pada setiap request. Admin hanya tidak memperoleh akses kasus; kombinasi role tetap terikat organisasi/queue membership yang sama. Ini fondasi policy; enforcement endpoint kasus/admin menyusul AUTH-04 dan fitur masing-masing.

Adapter password lokal, provisioning/rotation tersembunyi serta batas rilis dijelaskan pada [staff authentication](docs/backend/staff_authentication.md). Tidak ada self-registration, staff password di env/commandline, endpoint debug, SSO atau MFA pada tahap ini.

### Perincian AUTH-04 yang executable

/me dan /staff/me memakai AccessService dan dependency identitas bersama; mutasi memakai proof Origin/CSRF. Metadata objek hanya dari backend dan harus cocok persis dengan ResourceKey tenant/kind/id. Anonymous/customer hanya memiliki akses ownership pada conversation/draft mandiri yang backend klasifikasikan ownership_only=true; default false menolak konteks case/source yang belum diperiksa. Staff memakai scope role/organisasi/queue per membership. Target membership diri sendiri tidak boleh dimutasi melalui izin admin.

Sesi invalid401 sebelum lookup. Missing/cross-tenant/out-of-scope/mismatched projection menghasilkan404 sama; visible object dengan aksi terlarang403. Snapshot per-call tidak menggantikan pemeriksaan transaksi/state/consent/source-grants saat fitur dijalankan. Aplikasi belum menambah endpoint case/cursor/blob/tool; bukti AUTH-04 memakai fixture projection dengan identitas Neon nyata. [Authorization guide](docs/backend/authorization.md) memuat kontrak helper, mapping aksi dan kewajiban integrasi berikutnya.

### Perincian AUTH-01 yang executable

Semua path di atas berada di /api/v1. Purpose challenge saat ini customer_login; response202 berisi challenge_id,status=verification_requested. Verify menerima code string8digit, memberi respons200 session_kind=customer dan principal_id melalui cookie baru. /me hanya memuat kedua field tersebut; tidak memuat role staf atau hash. Bootstrap/logout body kosong atau null, field tambahan ditolak. Mutasi memakai HTTPS, Origin sama (termasuk port), cookie CSRF terikat sesi dan header X-CSRF-Token; bootstrap tidak memerlukan header X-CSRF-Token: sesi baru belum memiliki proof, sedangkan bootstrap sesi aktif memeriksa cookie CSRF yang terikat sesi serta Origin. Cookie __Host-airport_session HttpOnly/Secure/SameSite=Lax/Path=/, CSRF cookie __Host-airport_csrf Secure dan readable oleh frontend, tanpa Domain. Respons sesi/error memakai no-store.

Kode di-HMAC, TTL default300detik, maksimal5percobaan, sekali pakai dan terikat tenant/sesi anonim/CSRF asal. Counter fixed-window tersimpan percontact/IP/session; failure/rate-limit tidak me-rollback counter. Session TTL default anonim24jam dan customer8jam, revocation serta rotasi server-side. Kebijakan configurable dan detail proof ada di [authentication guide](docs/backend/authentication.md).

Sender default belum terpasang: request verifikasi mengembalikan503 VERIFICATION_UNAVAILABLE, bukan fake202. Pengiriman terkontrol hanya untuk tes, MAIL-05 tetap syarat pengiriman email nyata. Failed/expired/replayed/unknown challenge memberi422 VERIFICATION_FAILED generik; session tidak sah401, CSRF/origin403 CSRF_REQUIRED, batas429 RATE_LIMITED dengan Retry-After. Email dinormalisasi dengan domain ASCII/lowercase dan local part ASCII dipertahankan (SMTPUTF8 belum didukung); tidak menghapus plus-tag/titik secara universal. Verifikasi kontak tidak me-rebind principal lama atau mengaktifkan principal nonaktif.

Rotasi/revoke dan adopsi browser-bound AUTH-02 sudah diimplementasikan. Percakapan mandiri dipindahkan ke principal; pesan mempertahankan ID/sequence/body dan origin_session_id, sehingga retry tidak menggandakan pesan. Pending draft asal dibatalkan, bukan diadopsi sebagai consent; deadline yang lewat menjadi expired. Verifikasi email follow-up tanpa browser masih MAIL-04. Ini tidak membuka login staf atau memberi grants sumber melalui verifikasi pelanggan.

### Perincian AUTH-02 yang executable

Schema0007 memperluas auth_verify tanpa mengubah endpoint/signature login. Origin session/token/CSRF, challenge/nonce, kode, tenant, rate/attempt dan expiry dibuktikan sebelum perubahan. Session lock UPDATE menunggu mutasi anonim yang memegang SHARE; ownership, metadata/provenance pesan, draf, kontak, sesi pengganti dan challenge disimpan atomik. Version/epoch percakapan naik sekali. Error atau late expiry membatalkan seluruh mutasi adopsi; rate/attempt accounting yang sah tetap dipertahankan untuk late expiry. Runtime tidak dapat menulis provenance atau memakai penanda adoption GUC sebagai role/izin.

Sesi customer baru dapat mengakses riwayat asal, tetapi draf lama404 dan perlu dibuat ulang. Existing principal menerima riwayat asal saja; mapping source dan role tetap utuh. Case-bound/source-labeled history tidak diperluas melalui fallback ownership. [Panduan adopsi](docs/backend/anonymous_adoption.md) menjelaskan metadata baru, lock order, batas bukti dan pengujian.

### Perincian BEHAV-01 yang executable

POST create, GET metadata, POST message dan PATCH language tersedia untuk percakapan mandiri. Ownership memakai sesi anonim atau principal pelanggan terverifikasi dari backend. Bahasa mematuhi daftar tenant. Message response berisi processing_state received setelah transaksi commit; tidak mengantrekan agent atau membuat kasus. client_message_id UUID dideduplikasi menurut tenant/pengirim/percakapan: body sama menghasilkan message_id/sequence semula, body berbeda409 MESSAGE_CONFLICT. Body tepat1–4000karakter, bukan whitespace saja dan tanpa NUL. Body tidak diubah dan bahasa pesan mengikuti bahasa percakapan saat commit.

Session SHARE lock mendahului conversation UPDATE lock. Revoke/expiry/tenant/principal aktif diperiksa di dalam transaksi; counter/version/pesan atomik. Pergantian bahasa dengan versi yang cocok menaikkan version/epoch tanpa menerjemahkan histori. Metadata tidak memuat owner/session/tenant IDs. Tabel ini hanya menerima laporan pengguna tanpa case/source binding; dukungan kasus/stream event menunggu implementasinya. [Panduan percakapan](docs/backend/conversations.md) memuat batas schema0005, RLS, retry, AUTH-02 dan pengujian.

## 5 Konfirmasi dan tindakan pelanggan

Semua mutasi pengajuan/tambahan informasi atas nama pelanggan memakai action draft. Action types: create_case, add_case_information, request_handoff. Draft terikat sesi/aktor, tenant, tujuan dan versi payload. Attachment harus selesai diperiksa sebelum ikut payload terkonfirmasi.

| Endpoint | Kontrak |
| --- | --- |
| POST /action-drafts | Body action_type,conversation_id,case_id jika relevan,payload; backend menyaring dan mengotorisasi. Menghasilkan draft_id,version,summary,destination,expires_at,payload_hash. |
| GET /action-drafts/{id} | Pratinjau penuh milik aktor dan sesi pembuat; expiry dinormalisasi saat dibaca. |
| PATCH /action-drafts/{id} | Koreksi payload dengan expected_version; versi bertambah, persetujuan versi lama tidak berlaku. |
| DELETE /action-drafts/{id} | Membatalkan pending draft; tidak menghapus kasus yang sudah committed. |
| POST /action-drafts/{id}/confirm | expected_version dan payload_hash yang telah ditampilkan; Idempotency-Key wajib; hanya sesi pemilik/UI terautentikasi atau sesi anonim yang diizinkan untuk kasus umum. |
| GET /actions/{draft_id} | Hasil commit milik aktor: pending/committed/cancelled/expired dan result_ref. Dipakai setelah timeout, bukan mencoba key baru. |
| POST /cases/{id}/continue | Balasan eksplisit untuk masalah sama pada kasus closed, terverifikasi; idempotent dan versioned. |

create_case payload: category,service_type,summary,customer_report,booking_reference?,transaction_reference?,contact_email?,attachment_ids[]. Backend menghitung destination_queue_id yang sah dan mengembalikannya sebagai output destination; client/model tidak memasok tujuan, tenant, owner atau status. Referensi laporan divalidasi formatnya dan tidak membuktikan kepemilikan. Pengambilan fakta sumber tetap membutuhkan otorisasi sumber tersendiri. Kasus umum tanpa booking tidak memerlukan source subject. Kontak diperlukan untuk tindak lanjut email; email belum diverifikasi tidak berhak menerima detail pribadi.

Confirm menyimpan consent actor/time/hash, kasus atau case message, audit, idempotency result dan outbox dalam satu transaksi. Untuk request_handoff dengan case yang sudah ada tidak membuat case baru. Jika tidak ada petugas, pengajuan offline memerlukan email serta konfirmasi sebelum hasil case reference ditampilkan.

Replay key dan request hash sama mengembalikan hasil sebelumnya; key sama payload berbeda memberi 409. Race dua confirm pada draft sama menghasilkan satu hasil karena draft dikunci dan result_ref unik. Pembatalan setelah commit memberi 409 dan referensi hasil, tidak diam-diam menghapus kasus. Bukti idempotency dipertahankan selama action/case masih ada; key tidak direcycle di tengah retry.

### Perincian CASE-01 yang executable

Create/get/edit/cancel dan GET actions tersedia untuk create_case/request_handoff pada percakapan mandiri; konfirmasi dan hasil committed dijelaskan pada CASE-02 di bawah. Draf terikat actor XOR serta created_session_id untuk anonim maupun pelanggan terverifikasi. Principal yang sama pada sesi lain tidak meneruskan draf. TTL15menit menurut waktu database tidak diperpanjang koreksi. PATCH mengganti payload utuh, menaikkan version dan hash canonical atas seluruh isi pratinjau, tujuan, bahasa, identitas dan deadline. Guard internal require_confirmation menolak versi/hash lama, tetapi bukan eksekusi atau otorisasi tindakan.

Tujuan memakai queue aktif dan organisasi aktif tenant yang sama: exactservice lalu generalfallback, urutan UUID deterministik. Tanpa tujuan sah,503 dan tidak ada draf. Snapshot tujuan berubah hanya melalui koreksi. Summary/laporan1–200/1–4000karakter wajib nonwhitespace tanpa NUL; referensi/email opsional; attachments harus kosong. Case_id, add_case_information dan private source observations menunggu implementasi terkait. State pending/cancelled/expired tersimpan; terminal immutable. Edit/cancel draf expired memberi410 setelah transisi expiry disimpan. Sesi yang habis di tengah operasi memberi401 dengan rollback. [Panduan action drafts](docs/backend/action_drafts.md) menjelaskan batas CASE-01, RLS dan pengujian; CASE-02 melakukan revalidasi tujuan/kontak dan commit atomik sesuai scope laporan di bawah.

### Perincian CASE-02 yang executable

POST /action-drafts/{id}/confirm menerima expected_version integer positif dan payload_hash64hex lowercase, cookie sesi pembuat, Origin/CSRF dan tepat satu Idempotency-Key16–128ASCII alfanumerik/._:-. Respons201 berisi state committed, case_id, message_id dan result_ref. Key tenant/aktor/operasi mengikat draft_id/version/hash; key sama request berbeda409. Beberapa key pada draf sama tetap menghasilkan satu kasus. Committed tidak dapat diedit/cancel;409 menyertakan result_ref.

Tujuan organisasi/queue aktif dikunci SHARE dan harus sama persis dengan snapshot; berubah409 CASE_DESTINATION_CHANGED, tanpa reroute. create_case memerlukan contact_email eksplisit milik principal terverifikasi: tidak ada422 CASE_CONTACT_REQUIRED; tidak cocok422 CASE_CONTACT_VERIFICATION_REQUIRED. request_handoff tanpa email tidak mengaktifkan follow-up email. Anonim hanya handoff informasi umum, service general tanpa referensi/email. Referensi laporan bukan fakta sumber atau bukti entitlement.

Consent, case, pesan snapshot, participant pemilik terverifikasi, history, audit, outbox queued, hasil draf dan receipt idempotency disimpan atomik dengan deferred graph constraints. Fault membatalkan seluruh graph; expiry draf di tengah proses410 dengan marker expired, session loss401 rollback. Outbox hanya ID, belum dieksekusi/dikirim.

GET /actions/{draft_id} untuk committed mengikuti pemilik kasus saat ini dan mengembalikan receipt tanpa body/email, expires_at null. Preview/confirm tetap terikat sesi pembuat; sesudah adopsi atau dari browser lain dapat membaca receipt tetapi bukan memakai ulang persetujuan sesi lama. Pending/cancelled/expired tetap terikat sesi draf.

Schema0008 hanya membuat kasus baru berisi laporan, source observations/labels[] dan attachment kosong. Case menyimpan origin_conversation_id, pesan awal case-only; percakapan asal belum ditautkan live. Adopsi anonim mencakup kasus umum/snapshot dengan provenance dan history, tanpa consent/outbox/email baru. Existing case/additional information mengikuti fitur berikutnya; live handoff CHAT-01, worker JOB-01, delivery MAIL, private derived data SRC-03B. [Panduan konfirmasi](docs/backend/case_confirmation.md).

## 6 Endpoint petugas dan admin

| Endpoint | Aktor dan invariant |
| --- | --- |
| GET /staff/queues/cases | CS/supervisor sesuai organisasi dan queue, termasuk item needs_staff_review. |
| POST /staff/conversations/{id}/accept | CS aktif berwenang, expected_version, Idempotency-Key; satu pemenang, mode human dan epoch baru. |
| POST /staff/cases/{id}/messages | Body, channel chat/email, expected_version, Idempotency-Key; petugas menekan kirim, target email ditentukan backend dari kontak yang berhak. |
| POST /staff/cases/{id}/translation-drafts | Menghasilkan draf dan original_message_id; tidak mengirim ke pelanggan. |
| PATCH /staff/cases/{id}/status | expected_version,status,next_action,reason/resolution; CS/supervisor berwenang. |
| PATCH /staff/cases/{id}/assignment | expected_version,assignee_id; supervisor dalam scope atau CS self-claim unassigned yang diizinkan. |
| POST /cases/{id}/ratings | Pelanggan pemilik; score 1–5 dan comment opsional; idempotent/upsert miliknya. |
| POST /conversations/{id}/ratings | Pelanggan sesi pemilik untuk bantuan tanpa kasus; tidak wajib verifikasi booking. |
| POST /admin/knowledge/documents | Admin tenant; membuat draft, bukan langsung jawaban publik. |
| POST /admin/knowledge/versions/{id}/publish | Admin; versi valid, ACL/masa berlaku sah, indeks siap, audit dan invalidasi cache. |
| PATCH /admin/staff/{id}/memberships | Admin tenant; scope tidak boleh di luar kewenangannya, audit, cegah self-escalation. |

Provision/update akun staf tidak membuat peran pelaksana/mitra baru. Endpoint admin informasi dan membership tidak memberi akses isi kasus. Tidak ada UI konfigurasi credential API di MVP.

Accept juga menerima expected_case_version dan secara atomik menetapkan assigned_to penerima, status in_progress jika new, handler percakapan serta generation_epoch. Jika kasus sudah dimiliki petugas lain, kembalikan 409 ASSIGNEE_CONFLICT sampai supervisor melakukan reassignment. Reassignment kasus dengan chat aktif memindahkan handler dan menaikkan epoch secara atomik; pengirim lama ditolak. Staff send memeriksa handler/assignment saat commit, bukan hanya saat membuka layar.

Accept closed case mengembalikan 409 CASE_CLOSED. Reassign/close kasus dengan percakapan aktif memeriksa expected_conversation_version juga; semua mutasi memakai urutan lock case lalu conversation. Close mengosongkan handler, mengakhiri sesi human/waiting dengan mode ended, menambah epoch dan tidak memulai AI. Replay close tidak menggandakan notifikasi penutupan. Send yang kalah race terhadap close/reassign ditolak 409 dan isi draf petugas tetap di UI untuk ditinjau ulang.

Jika transaksi maupun booking tidak ditemukan tetapi pengguna ingin melapor, pengajuan memakai kategori kendala transaksi dengan referensi yang dilaporkan dan bukti sebagai customer_report. Jangan membuat source relation atau mengonfirmasi kepemilikan. Kasus dimiliki kontak kasus yang diverifikasi; pemeriksaan status sumber tetap membutuhkan grants terpisah.

## 7 Transisi kasus dan percakapan

| Dari | Ke | Aturan |
| --- | --- | --- |
| new | in_progress | Petugas menerima; assignee aktif wajib. |
| in_progress | awaiting_customer | Pertanyaan/informasi yang ditunggu dan cara membalas wajib. |
| in_progress | awaiting_external | Penanggung jawab tetap ada, next_action koordinasi dicatat. |
| awaiting_customer / awaiting_external | in_progress | Tindak lanjut diterima atau petugas melanjutkan. |
| new / status aktif lainnya | closed | CS/supervisor, close_reason dan resolution wajib; hasil disampaikan. |
| closed | in_progress / new | Pelanggan berhak meminta kelanjutan masalah sama; in_progress jika assignee aktif dalam scope, selain itu new. |

Permintaan berbeda tidak dipaksakan ke kasus lama. close_reason: resolved, customer_cancelled, duplicate, no_response. Duplicate memuat related_case_ref yang juga diotorisasi; no_response mencatat tindak lanjut petugas. Sistem tidak menutup otomatis.

Perubahan memakai version check. Respons sukses mengembalikan version baru dan case_history. Jika pesan pelanggan tiba bersamaan dengan penutupan, transaksi berurutan memastikan pesan tidak hilang dan kelanjutan eksplisit tidak tertutup oleh write lama. Balasan email ambigu menjadi item review petugas; email terima kasih tidak diklasifikasikan otomatis sebagai permintaan reopen.

Conversation ai → waiting_staff saat handoff terkonfirmasi; waiting_staff → human saat accepted; kasus ditutup mengakhiri sesi terkait menjadi ended. Perubahan meningkatkan generation_epoch. Publication AI memerlukan epoch dan mode ai yang cocok. Restart tidak mengembalikan human/ended menjadi ai. Kelanjutan AI setelah kasus selesai memerlukan percakapan baru, bukan takeover diam-diam.

## 8 Email webhook dan delivery

POST /integrations/email/{account_alias}/events memvalidasi signature pada raw body, timestamp dan account configuration. account_alias tidak cukup sebagai autentikasi. Event replay tidak membuat message/job baru. Payload tidak menimpa tenant dari mapping server. Return 2xx setelah event tersimpan; jika storage gagal, return non-2xx untuk retry.

Normalized event: event_id,message_id,event_type,provider_account_ref,received_at,content_reference,signature_verified. Body yang belum tersedia diambil worker, tidak diakui sudah menjadi pesan kasus sebelum diproses. Failure fetching ditandai retry/review.

Email direction inbound/outbound; verification_state pending/verified/rejected; delivery_status queued/sending/accepted/delivered/bounced/failed/delivery_unknown. Balasan inbound yang valid dicatat pada case tanpa action draft; ini pesan asli pengguna, bukan tindakan yang dikarang agent. Hanya pesan yang terverifikasi dapat memicu tindak lanjut/reopen dan akses privat. Pesan ditolak ditahan dari customer timeline/model sampai pemeriksaan.

Correlation memakai account tenant mapping, reply token, In-Reply-To/References, lalu verifikasi sender. Tidak memakai kesamaan subject sebagai bukti. Email pengirim tak dikenal tidak membuka akses. Confirmation/verifikasi melalui balasan email didukung agar portal tidak wajib dibuka.

Worker mengirim stable delivery key yang sama saat retry provider yang mendukung idempotency. Jika respons hilang, rekonsiliasi dilakukan; tanpa receipt/status/idempotency yang dapat dipercaya, delivery_unknown diperiksa petugas dan tidak dikirim ulang membabi buta. Tidak menjanjikan exactly-once delivery lintas provider. Delivery callback diurutkan berdasarkan aturan status/waktu; callback lama tidak menurunkan delivered menjadi queued.

## 9 Attachment kontrak minimum

POST /attachments menyediakan upload privat untuk sesi berhak; hasil id,byte_size,mime_type,scan_status. GET /attachments/{id}/download memeriksa akses kasus/draf dan memberi stream atau URL bertanda tangan singkat. Attachment belum clean tidak dapat dikirim atau diberikan ke model. Request melebihi batas konfigurasi memberi 413; tipe terlarang 415. Batas awal 5 MiB/file dan 3 file/pengajuan untuk bukti JPG/PNG/PDF; arsip/script ditolak. Batas adalah implementasi yang dapat diubah melalui keputusan terdokumentasi, bukan kemampuan OCR baru.

## 10 Tool agent

Allowlist: search_knowledge, read_booking, read_transaction_status, read_case, prepare_action_draft. Aktor/tenant diberikan backend di luar argumen model. Model tidak mendapat execute_sql, arbitrary_http, send_email, confirm_action, close_case, grant_access atau payment tools. Update status bisnis tidak tersedia melalui tool.

Setiap hasil menyertakan source_kind,observed_at,allowed_fields dan error terstruktur. Budget tool loop mencegah pemanggilan tanpa batas; kegagalan tidak menghasilkan status fiktif. Retrieval dan tool results diperlakukan sebagai data, bukan instruksi.

## 11 Ketertelusuran

| Product contract | Bagian implementasi | Test suite |
| --- | --- | --- |
| Akses tenant/pelanggan/perwakilan | Identitas, RLS, source grants, ACL objek | SEC |
| Informasi dan kebijakan | Knowledge versions dan read tools | RAG, AI |
| Konfirmasi dan pengajuan | Draft, commit, idempotency dan outbox | WF |
| CS/supervisor/admin | Internal endpoints, scope dan audit | OPS |
| Handoff dan konteks | Conversation mode, epoch, durable events | CHAT |
| Email langsung | Inbox/outbox, verification dan correlation | MAIL |
| Status, reopen dan rating | Case state, history dan ratings | CASE |
| Deployment, reliability dan biaya | Runtime/worker/database | REL, PERF, DELIVERY |

# Airport AI Assistant Data and API Contracts

Spesifikasi desain yang menurunkan [PRODUCT_CONTRACT.md](PRODUCT_CONTRACT.md) dan [ARCHITECTURE.md](ARCHITECTURE.md). Nama field berikut menjadi kontrak awal untuk implementasi; perubahan memerlukan pembaruan pengujian kontrak. Tidak ada endpoint untuk booking baru, perubahan booking, pembayaran, refund execution atau pemberian kuasa.

## Kontrak komunikasi petugas — FE-MAIL-01

Semua rute berikut memakai prefix `/api/v1`, sesi staf aktif, pemeriksaan tenant/kasus/organisasi/queue terkini, dan `Cache-Control: no-store`. Mutasi memerlukan CSRF dan satu Idempotency-Key. Kanal dan alamat penerima tidak memberi kewenangan baru kepada klien.

| Rute | Kontrak |
| --- | --- |
| `GET /staff/cases/{id}/communication-capabilities?membership_id={id}` | Metadata kasus, versi, selected membership dan `email_available`. Bersifat advisory; penerima, label dan izin tetap diperiksa saat POST. Tidak mengembalikan alamat penerima. |
| `GET /staff/cases/{id}/messages?membership_id={id}&limit=50` | Pesan aplikasi yang sudah dipublikasikan, maksimal 50/halaman, urutan kronologis. Halaman lebih lama memakai pasangan `before_time` dan `before_id` dari respons. `has_more` bukan klaim seluruh riwayat sudah dimuat. Tidak memproyeksikan karantina atau raw provider body. |
| `POST /staff/cases/{id}/messages` | Balasan pada live chat menggunakan conversation ID, case/conversation version dan epoch terkini; handler harus sesuai penugasan. |
| `POST /staff/cases/{id}/email-messages` | Email langsung pada kasus: body, bahasa, expected case version dan membership ID. Tidak menerima recipient, conversation ID atau epoch palsu. |
| `GET /staff/cases/{id}/message-receipts/{key}?membership_id={id}` | Receipt pesan milik principal, parent kasus tersimpan dan akses operasional terkini diperiksa. `state: saved` berarti pesan tersimpan; email masih dapat queued/unknown/failed. Bukan bukti delivered/read. |
| `POST /staff/cases/{id}/translation-drafts` | Draft terjemahan live chat/email dengan konteks conversation/version/epoch. |
| `POST /staff/cases/{id}/email-translation-drafts` | Draft terjemahan untuk email kasus tanpa live chat, terikat principal/sesi, membership, versi kasus, request key, teks asli dan konteks penerima. |
| `GET /staff/cases/{id}/translation-requests/{key}` dan `GET /staff/cases/{id}/email-translation-requests/{key}` | Penemuan draft melalui request key milik principal/sesi. Hanya membaca; tidak memanggil model atau mengubah expiry. |
| `GET /staff/cases/{id}/translation-drafts/{draft_id}` dan padanan `email-translation-drafts` | Membaca draft `generating/ready/failed/sent` dengan pemeriksaan akses terkini. |
| `POST /staff/cases/{id}/translation-drafts/{draft_id}/send` dan padanan `email-translation-drafts` | Teks hasil review, expected revision dan `approved: true` eksplisit. Pesan, job email dan transisi draft sent atomik. Model tidak menyetujui atau mengirim. |

Receipt dan draft memiliki `conversation_id: null` hanya untuk jalur email langsung pada kasus. Jalur live chat tetap memiliki conversation ID nyata. Key pengiriman terdahulu tidak dapat diambil alih oleh draft baru.

Respons hilang atau 5xx setelah mutasi adalah hasil belum diketahui. Frontend memulihkan melalui GET, tanpa retry POST otomatis atau key pengganti. `404` receipt tidak membuktikan pesan belum dikirim. Outage sumber menghapus teks/cache privat tetapi mempertahankan hanya ID pemulihan yang terikat owner, membership dan kasus dalam memori; reset/logout menginvalidasinya. Hasil ditampilkan kembali setelah akses kasus diperiksa, dengan kanal asli yang benar. Rincian teknis internal ada di `docs/frontend/fe_mail_01.md`.

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
- Schema mutasi mewarisi `RequestModel`: extra fields ditolak, tipe strict, string default maksimum 4096 karakter. Batas field/domain lebih rinci ditentukan bersama endpoint fitur. Upload file memakai raw body dengan batas HTTP khusus 5 MiB pada POST `/api/v1/attachments`; endpoint lain tetap memakai anggaran transport yang dikonfigurasi.
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
| jobs/outbox | tenant_id,id,case_id,action_draft_id,kind,dedupe_key,state,attempts/max_attempts,next_attempt_at,replay_policy,lease_worker_id,lease_token,lease_generation,leased_at/lease_until,completed_at,result_code; satu logical job per dedupe_key. |
| idempotency_records | tenant_id, actor_scope, operation, key, request_hash, result_ref, state; unique scope+operation+key. |
| knowledge_versions | tenant_id, id, document_id, version, source_uri, service_type, visibility, organization_id nullable, status, valid_from/to, applicability, content_hash, published_at. |
| knowledge_chunks | tenant_id, id, knowledge_version_id, text, embedding, embedding_model_version, ACL; tidak boleh mewarisi tenant dari teks. |
| attachments | tenant_id, id, draft_id, case_id?, uploader_principal_id/uploader_session_id, created_session_id, body (private bytea), mime_type, byte_size, scan_status, content_hash, resource_security_labels; download hanya setelah otorisasi. |
| ratings | tenant_id, id, case_id XOR conversation_id, score, comment nullable, version, created_session_id, updated_session_id, created_at, updated_at, resource_security_labels; satu rating per target dengan customer scope dari ownership parent saat ini. |

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

Respons divalidasi terhadap DTO ketat, exact reference/page/parent, scopes dan proyeksi status/details. Missing/null required status tetap invalid502; provider enum string baru menjadi unknown. Source404/mapping tidak ada tetap404, transport/timeout/sourceauth/5xx503 dan respons rusak502, tanpa mengubah status transaksi. Policy/relations belum menjadi endpoint publik. Source integration guide menjelaskan protokol, batas jaringan, error, setup dan diagnostic fixture yang terpisah dari login pelanggan.

### Perincian SRC-03A yang executable

`CurrentSourceGrants` memeriksa subject aktif melalui RLS dan GET resource sumber setiap kali dipanggil; tidak memakai relasi atau hasil grant yang disimpan. Kebutuhan kosong/unknown ditolak, pembayaran tidak diwarisi dari booking,404 berarti tidak berhak dan kegagalan lain diteruskan fail closed. Pemanggil tetap wajib memeriksa sesi/ACL/keanggotaan objek secara terpisah.

Backend membentuk `SecurityLabels` dari DTO utuh, termasuk scope detail pembayaran dan kebutuhan booking terpisah bila booking_reference ikut ditampilkan. Label policy terikat booking serta referensi versi tepat. Union mempertahankan semua kebutuhan, menolak lintas tenant dan dibatasi64resource. Restore repository memvalidasi JSON dengan tenant dari baris terotorisasi; empty/malformed labels tidak mengizinkan konten privat sumber. `PrivateSourceAccess` memerlukan seluruh grants pelanggan saat ini. Source authorization mencatat batas snapshot, fixture repository dan penerapan case/blob/model/outbound yang tetap mengikuti SRC-03B. Tidak ada endpoint label/grant publik atau perubahan schema.

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

Adapter password lokal, provisioning/rotation tersembunyi serta batas rilis dijelaskan pada staff authentication. Tidak ada self-registration, staff password di env/commandline, endpoint debug, SSO atau MFA pada tahap ini.

### Perincian AUTH-04 yang executable

/me dan /staff/me memakai AccessService dan dependency identitas bersama; mutasi memakai proof Origin/CSRF. Metadata objek hanya dari backend dan harus cocok persis dengan ResourceKey tenant/kind/id. Anonymous/customer hanya memiliki akses ownership pada conversation/draft mandiri yang backend klasifikasikan ownership_only=true; default false menolak konteks case/source yang belum diperiksa. Staff memakai scope role/organisasi/queue per membership. Target membership diri sendiri tidak boleh dimutasi melalui izin admin.

Sesi invalid401 sebelum lookup. Missing/cross-tenant/out-of-scope/mismatched projection menghasilkan404 sama; visible object dengan aksi terlarang403. Snapshot per-call tidak menggantikan pemeriksaan transaksi/state/consent/source-grants saat fitur dijalankan. Aplikasi belum menambah endpoint case/cursor/blob/tool; bukti AUTH-04 memakai fixture projection dengan identitas Neon nyata. Authorization guide memuat kontrak helper, mapping aksi dan kewajiban integrasi berikutnya.

### Perincian AUTH-01 yang executable

Semua path di atas berada di /api/v1. Purpose challenge saat ini customer_login; response202 berisi challenge_id,status=verification_requested. Verify menerima code string8digit, memberi respons200 session_kind=customer dan principal_id melalui cookie baru. /me hanya memuat kedua field tersebut; tidak memuat role staf atau hash. Bootstrap/logout body kosong atau null, field tambahan ditolak. Mutasi memakai HTTPS, Origin sama (termasuk port), cookie CSRF terikat sesi dan header X-CSRF-Token; bootstrap tidak memerlukan header X-CSRF-Token: sesi baru belum memiliki proof, sedangkan bootstrap sesi aktif memeriksa cookie CSRF yang terikat sesi serta Origin. Cookie __Host-airport_session HttpOnly/Secure/SameSite=Lax/Path=/, CSRF cookie __Host-airport_csrf Secure dan readable oleh frontend, tanpa Domain. Respons sesi/error memakai no-store.

Kode di-HMAC, TTL default300detik, maksimal5percobaan, sekali pakai dan terikat tenant/sesi anonim/CSRF asal. Counter fixed-window tersimpan percontact/IP/session; failure/rate-limit tidak me-rollback counter. Session TTL default anonim24jam dan customer8jam, revocation serta rotasi server-side. Kebijakan configurable dan detail proof ada di authentication guide.

Sender default nonaktif: request verifikasi mengembalikan503 VERIFICATION_UNAVAILABLE. MAIL-05 menambahkan route server-only tenant/account dan pengiriman login OTP opt-in. Provider accepted atau hasil kirim tidak pasti/timeout memberi202 verification_requested dengan challenge terikat browser yang tetap berlaku sampai TTL, tanpa klaim delivered. Penolakan pasti membatalkan challenge dan memberi503; cancellation mempertahankan TTL-bound challenge dan diteruskan. Tidak ada resend otomatis. Pengiriman terkontrol hanya bukti wiring; MAIL-05 tetap memerlukan round-trip provider nyata. Failed/expired/replayed/unknown challenge memberi422 VERIFICATION_FAILED generik; session tidak sah401, CSRF/origin403 CSRF_REQUIRED, batas429 RATE_LIMITED dengan Retry-After. Email dinormalisasi dengan domain ASCII/lowercase dan local part ASCII dipertahankan (SMTPUTF8 belum didukung); tidak menghapus plus-tag/titik secara universal. Verifikasi kontak tidak me-rebind principal lama atau mengaktifkan principal nonaktif.

Rotasi/revoke dan adopsi browser-bound AUTH-02 sudah diimplementasikan. Percakapan mandiri dipindahkan ke principal; pesan mempertahankan ID/sequence/body dan origin_session_id, sehingga retry tidak menggandakan pesan. Pending draft asal dibatalkan, bukan diadopsi sebagai consent; deadline yang lewat menjadi expired. Verifikasi email follow-up tanpa browser masih MAIL-04. Ini tidak membuka login staf atau memberi grants sumber melalui verifikasi pelanggan.

### Perincian AUTH-02 yang executable

Schema0007 memperluas auth_verify tanpa mengubah endpoint/signature login. Origin session/token/CSRF, challenge/nonce, kode, tenant, rate/attempt dan expiry dibuktikan sebelum perubahan. Session lock UPDATE menunggu mutasi anonim yang memegang SHARE; ownership, metadata/provenance pesan, draf, kontak, sesi pengganti dan challenge disimpan atomik. Version/epoch percakapan naik sekali. Error atau late expiry membatalkan seluruh mutasi adopsi; rate/attempt accounting yang sah tetap dipertahankan untuk late expiry. Runtime tidak dapat menulis provenance atau memakai penanda adoption GUC sebagai role/izin.

Sesi customer baru dapat mengakses riwayat asal, tetapi draf lama404 dan perlu dibuat ulang. Existing principal menerima riwayat asal saja; mapping source dan role tetap utuh. Case-bound/source-labeled history tidak diperluas melalui fallback ownership. Panduan adopsi menjelaskan metadata baru, lock order, batas bukti dan pengujian.

### Perincian BEHAV-01 yang executable

POST create, GET metadata, POST message dan PATCH language tersedia untuk percakapan mandiri. Ownership memakai sesi anonim atau principal pelanggan terverifikasi dari backend. Bahasa mematuhi daftar tenant. Message response berisi processing_state received setelah transaksi commit; tidak mengantrekan agent atau membuat kasus. client_message_id UUID dideduplikasi menurut tenant/pengirim/percakapan: body sama menghasilkan message_id/sequence semula, body berbeda409 MESSAGE_CONFLICT. Body tepat1–4000karakter, bukan whitespace saja dan tanpa NUL. Body tidak diubah dan bahasa pesan mengikuti bahasa percakapan saat commit.

Session SHARE lock mendahului conversation UPDATE lock. Revoke/expiry/tenant/principal aktif diperiksa di dalam transaksi; counter/version/pesan atomik. Pergantian bahasa dengan versi yang cocok menaikkan version/epoch tanpa menerjemahkan histori. Metadata tidak memuat owner/session/tenant IDs. Tabel ini hanya menerima laporan pengguna tanpa case/source binding; dukungan kasus/stream event menunggu implementasinya. Panduan percakapan memuat batas schema0005, RLS, retry, AUTH-02 dan pengujian.

## 5 Konfirmasi dan tindakan pelanggan

Pengajuan kasus dan handoff memakai action draft dengan action types `create_case` dan `request_handoff`. Draft terikat sesi/aktor, tenant, tujuan dan versi payload. Attachment harus selesai diperiksa sebelum ikut payload terkonfirmasi. Tambahan teks pada kasus yang sudah ada memakai preview/konfirmasi khusus di bawah; bukan action type draft yang belum diimplementasikan.

### Tambahan informasi pelanggan — kontrak COMPLETE-01

`POST /cases/{id}/information-previews` menerima expected_version, message asli1–4000karakter Unicode dan language en/id. Pemilik terverifikasi dengan akses sumber terkini mendapat preview_id unik, salinan teks/bahasa/versi yang ditinjau, expires_at dan confirmation_token bertanda tangan. Preview berlaku15menit dan tidak menulis kasus/pesan. Koreksi menghasilkan preview baru. Token **tidak dienkripsi**: hanya POST JSON dan memori UI; tidak boleh masuk URL, webstorage, log atau model. Membuang preview hanya menghapus state lokal; tidak ada klaim durable pending/cancelled untuk token ini.

`POST /cases/{id}/information` menerima confirmation_token dan confirmed:true; Idempotency-Key wajib. Backend memeriksa kembali owner, sesi, participant, versi, kasus belum closed, expiry dan seluruh label sumber. Satu transaksi mencatat ledger preview sekali pakai, satu pesan customer case-owned dan history customer_information, serta menaikkan versi kasus satu. Status, assignment, next_actor/next_action serta mode/version/epoch chat tetap. Versi baru membuat keputusan petugas yang memakai versi lama konflik; pesan tambahan bukan bukti semua permintaan informasi terpenuhi. Tidak ada booking/payment/refund, email job atau pemanggilan model.

Satu preview tidak boleh menghasilkan pesan kedua dengan key baru. Replay key yang sama/intent yang sama mengembalikan hasil tersimpan; intent berbeda409. `GET /cases/{id}/information-receipts/{key}` membaca hasil tersimpan dengan izin **saat ini**, independen expiry preview, versi lama atau kasus yang kemudian closed. Respons saved berisi actual message_id/sequence dan case_version saat pencatatan.404 tidak membuktikan pesan belum tersimpan. Respons commit hilang dipulihkan melalui GET, tanpa retry POST otomatis. Kasus closed membutuhkan alur continue eksplisit yang sudah tersedia.

Kontrak ini diterapkan melalui migration0035. Preview/confirmation dan receipt terpisah dari action-draft kasus baru.

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

Tujuan memakai queue aktif dan organisasi aktif tenant yang sama: exactservice lalu generalfallback, urutan UUID deterministik. Tanpa tujuan sah,503 dan tidak ada draf. Snapshot tujuan berubah hanya melalui koreksi. Summary/laporan1–200/1–4000karakter wajib nonwhitespace tanpa NUL; referensi/email opsional; attachments harus kosong. Case_id, add_case_information dan private source observations menunggu implementasi terkait. State pending/cancelled/expired tersimpan; terminal immutable. Edit/cancel draf expired memberi410 setelah transisi expiry disimpan. Sesi yang habis di tengah operasi memberi401 dengan rollback. Panduan action drafts menjelaskan batas CASE-01, RLS dan pengujian; CASE-02 melakukan revalidasi tujuan/kontak dan commit atomik sesuai scope laporan di bawah.

### Perincian CASE-02 yang executable

POST /action-drafts/{id}/confirm menerima expected_version integer positif dan payload_hash64hex lowercase, cookie sesi pembuat, Origin/CSRF dan tepat satu Idempotency-Key16–128ASCII alfanumerik/._:-. Respons201 berisi state committed, case_id, message_id dan result_ref. Key tenant/aktor/operasi mengikat draft_id/version/hash; key sama request berbeda409. Beberapa key pada draf sama tetap menghasilkan satu kasus. Committed tidak dapat diedit/cancel;409 menyertakan result_ref.

Tujuan organisasi/queue aktif dikunci SHARE dan harus sama persis dengan snapshot; berubah409 CASE_DESTINATION_CHANGED, tanpa reroute. create_case memerlukan contact_email eksplisit milik principal terverifikasi: tidak ada422 CASE_CONTACT_REQUIRED; tidak cocok422 CASE_CONTACT_VERIFICATION_REQUIRED. request_handoff tanpa email tidak mengaktifkan follow-up email. Anonim hanya handoff informasi umum, service general tanpa referensi/email. Referensi laporan bukan fakta sumber atau bukti entitlement.

Consent, case, pesan snapshot, participant pemilik terverifikasi, history, audit, outbox queued, hasil draf dan receipt idempotency disimpan atomik dengan deferred graph constraints. Fault membatalkan seluruh graph; expiry draf di tengah proses410 dengan marker expired, session loss401 rollback. Outbox hanya ID, belum dieksekusi/dikirim.

GET /actions/{draft_id} untuk committed mengikuti pemilik kasus saat ini dan mengembalikan receipt tanpa body/email, expires_at null. Preview/confirm tetap terikat sesi pembuat; sesudah adopsi atau dari browser lain dapat membaca receipt tetapi bukan memakai ulang persetujuan sesi lama. Pending/cancelled/expired tetap terikat sesi draf.

Schema0008 hanya membuat kasus baru berisi laporan, source observations/labels[] dan attachment kosong. Case menyimpan origin_conversation_id, pesan awal case-only; percakapan asal belum ditautkan live. Adopsi anonim mencakup kasus umum/snapshot dengan provenance dan history, tanpa consent/outbox/email baru. Existing case/additional information mengikuti fitur berikutnya; live handoff CHAT-01, worker JOB-01, delivery MAIL, private derived data SRC-03B. Panduan konfirmasi.

## 6 Endpoint petugas dan admin

| Endpoint | Aktor dan invariant |
| --- | --- |
| GET /staff/queues/cases | CS/supervisor sesuai organisasi dan queue, termasuk item needs_staff_review. |
| POST /staff/conversations/{id}/accept | CS aktif berwenang, expected_version, Idempotency-Key; satu pemenang, mode human dan epoch baru. |
| POST /staff/cases/{id}/messages | Body, channel chat/email, expected_version, Idempotency-Key; petugas menekan kirim, target email ditentukan backend dari kontak yang berhak. |
| POST /staff/cases/{id}/translation-drafts | Menghasilkan translation draft ID, original_body dan proposal; original belum dikirim ke pelanggan. |
| PATCH /staff/cases/{id}/status | expected_version,status,next_action,reason/resolution; CS/supervisor berwenang. |
| PATCH /staff/cases/{id}/assignment | expected_version,assignee_id; supervisor dalam scope atau CS self-claim unassigned yang diizinkan. |
| GET/POST /cases/{id}/ratings | Pemilik terverifikasi dan participant aktif; score 1–5, comment opsional, expected_version; perubahan saat kasus closed, current-source gate dan idempotent desired-state upsert. |
| GET/POST /conversations/{id}/ratings | Pemilik pelanggan/sesi anonim untuk bantuan tanpa kasus setelah jawaban tersimpan atau percakapan ended; tidak wajib verifikasi booking. |
| POST /admin/knowledge/documents | Admin tenant; membuat draft, bukan langsung jawaban publik. |
| POST /admin/knowledge/versions/{id}/publish | Admin; versi valid, ACL/masa berlaku sah, indeks siap, audit dan invalidasi cache. |
| GET /admin/workspace; GET /admin/staff; GET /admin/staff/{id}/memberships | Selected membership_id admin aktif; metadata akun dan queue hanya pada organisasi terpilih, keyset directory tanpa isi kasus/credential. |
| PATCH /admin/staff/{id}/memberships | Admin organisasi; actor_membership_id/actor_version, expected_version target, active, roles, queue_ids; audit atomik, self-principal change dilarang. |

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

### Perincian CASE-03 yang executable

GET /cases/{id}, GET /staff/cases/{id} dan GET /staff/queues/cases tersedia untuk kasus laporan berlabel kosong. Antrean adalah100item pertama berizin, filter status opsional, urutan updated_at/id. Scope petugas memerlukan CS/supervisor pada organisasi dan queue aktif; admin saja tidak membaca kasus. Respons status/detail membawa versi, next_actor/action, closure result dan history publik. History dibatasi resulting_version<=versi kasus yang dibaca, sehingga merupakan snapshot konsisten pada pembacaan awal.

PATCH assignment menerima expected_version dan assignee_id berupa UUID membership. CS hanya self-claim kasus unassigned dan sekaligus menerima menjadi in_progress; supervisor dapat preassign new atau reassign aktif. Assignee memerlukan principal/membership/role/queue/organisasi dan credential lokal beserta kontak terverifikasi yang aktif. Mengosongkan assignment hanya pada new oleh supervisor. Closed menolak perubahan staf.

PATCH status menerima expected_version,status,next_action serta close_reason/resolution untuk closed. Duplicate memakai related_case_id berhak dan pemilik sama; API menampilkan related_case_ref hanya jika tetap berhak dibaca. no_response mewajibkan followup_evidence. Closure result tersimpan dan dapat dibaca pelanggan; pengiriman email/close-email job belum diimplementasikan.

POST /cases/{id}/continue menerima expected_version,intent same_issue,message asli pelanggan dan client_request_id UUID. Hanya pemilik customer terverifikasi; kasus harus closed. Assignee sah dipertahankan dan status in_progress, selain itu assignment null/status new. Close fields sekarang dibersihkan, snapshot lama dipertahankan. Request ID sama+digest command sama tidak menambah mutasi/history dan memberi tampilan kasus saat ini; request berbeda409, write lama key baru409. Baik penulisan baru maupun replay memeriksa owner, participant/sesi aktif dan izin sumber terkini terhadap kasus, origin serta referensi kasus terkait yang dikembalikan. Pemeriksaan sumber berlangsung di luar transaksi, dengan deadline operasi sejak sebelum grant pertama sampai sesudah commit dan reload basis terkini. Commit lambat dapat menjadi503 outcome unknown; gunakan GET kasus dengan izin saat ini, bukan request ID baru. Intent ambigu/thanks/new_issue tidak diinterpretasikan sebagai reopen; pemrosesan email tetap MAIL-04.

Schema0009 memastikan case/history atomik dengan lock, CHECK, scoped RLS, column grants, immediate/deferred guards dan history immutable. Runtime tidak dapat memalsukan identity_verified history/audit atau memasang history masa depan. Auth-owner principal SELECT/INSERT dipisahkan dari privilege UPDATE lock-only. Auth adoption/receipt CASE-02 tetap berlaku tanpa memperluas consent atau ownership runtime. Panduan lifecycle. Live conversation handler/ended mode mengikuti CHAT-01/02.

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


## CASE-04 private attachment API

Create an empty-attachment draft first. POST /api/v1/attachments?draft_id=<uuid>&expected_version=<positive integer> accepts one raw JPG/PNG/PDF body (exact Content-Type image/jpeg, image/png or application/pdf) up to5MiB with Origin/CSRF and a stable UUID Idempotency-Key. It returns201 {attachment:{id,byte_size,mime_type,scan_status},draft:<updated DraftView>}. Three stored files per draft; scanner failure/unavailable rejects without persisting. Upload updates the selected IDs, version, language, displayed summary and hash; old preview consent is rejected. Create-draft still requires an empty selection, while edit permits only up to three unique clean IDs belonging to that same draft/session. Removing a selected ID does not physically delete the stored file or release its storage slot.

Retry keys are namespaced per draft. Same key/bytes/MIME returns the current preview without another scan or mutation, including when the original expected_version has become stale; changed content409. New uploads require current version. Replay results never replay consent. Unknown upload can be retried with the original key and bytes.

Selected files bind to the resulting case atomically with confirmation. GET /api/v1/cases/{id}/attachments returns metadata list; GET /api/v1/attachments/{id}/download rechecks current parent access and returns a private no-store attachment stream with nosniff/sandbox headers. Tenant-safe404 for inaccessible objects; no public blob URL. Pending files require original active session and pending unexpired draft; confirmed files follow current case access. Durability uses PostgreSQL bytea, not temporary process files. Source-derived private contents remain unsupported/fail closed until SRC-03B. No attachment bytes enter models or email in this slice.

Normal JSON request limits remain unchanged; only exact POST /api/v1/attachments has the5MiB binary transport allowance. Nonclean files are never persisted or downloadable. Scanner engine must be provisioned and validated separately; controlled scanner tests do not establish real malware detection.


## CHAT-01 executable event polling and handoff

GET /api/v1/conversations/{id}/events accepts only after (signed cursor, <=2048chars) and limit1–100/default20. Returns {items:[{id,sequence,event_type,created_at,payload}],next_cursor,has_more,mode,generation_epoch,version}. Types: message_received {message_id,body,language,author_type:customer}; language_changed {language}; handoff_requested {case_id,mode:waiting_staff}. Event sequence differs from message sequence. Stable ID/order are persisted, empty polls valid, no auto reply. Cursor binds tenant/session/principal/kind/conversation/limit/customer visibility and expires after15minutes; current ACL is checked on every poll before cursor validation. Inaccessible resource404, invalid cursor400. New identity must restart polling and dedupe stable event IDs. No raw body/contact in cursor.

Confirmed request_handoff now atomically links conversation to the resulting case, changes ai to waiting_staff, advances version/epoch and emits one handoff event; same confirm retry produces no second case/event. Create_case without handoff keeps conversation standalone. Existing-case request uses optional CreateDraft.case_id with action_type=request_handoff only, owner-authorized open case and server destination, no contact_email or attachments. Preview/hash includes existing target reference. Confirm stores new consent/receipt pointing to existing initial message; no new case/message/outbox/email is implied. GET actions resolves the specific consent draft. One active (waiting_staff/human) conversation per case; ended conversations remain historical. Pending drafts are cancelled on proven anonymous adoption; linked report-only context stays authorized and cursor rotates with session.

Events are trigger-emitted with NOLOGIN metadata-only owner, forced RLS, tenant composite FKs and immutable rows; runtime cannot INSERT/UPDATE events or UPDATE event counter. Only stored user messages are backfilled, not invented historic transitions. Staff reads require current linked-case scope. Private source-derived context remains fail closed. CHAT-02 below supplies accept/human/ended and paired case/conversation mutations; active-case PATCH requires the reviewed conversation version. Staff send is specified by CHAT-03 below.


## CHAT-02 executable staff lifecycle

GET /api/v1/staff/conversations/{id} returns a scoped, case-then-conversation locked snapshot {conversation_id,case_id,case_version,version,generation_epoch,mode,handler_membership_id}. POST .../{id}/accept requires expected_version (conversation), expected_case_version and stable Idempotency-Key. Handler is the caller's freshly authorized membership, prioritizing an existing assignment to that membership. Another assignee409; closed409; stale version409; wrong scope404. Accepted receipt {state:accepted,...snapshot} is immutable and historical, replays only identical command/key with current ACL. Refresh metadata after replay before acting; old receipt is not current ownership after reassignment/close.

PATCH staff case assignment/status adds expected_conversation_version for active chats: missing422 CHAT_VERSION_REQUIRED, mismatch409. Supervisor-only assignment keeps waiting chat unaccepted or moves the human handler with case assignment. CS accepts through the dedicated endpoint. Close ends chat/clears handler; other active status updates retain mode/handler. Each successful active operation advances case/conversation versions and generation_epoch, writes matching case history, public event and transition ledger atomically. PATCH retries with old versions409/no duplicate history/event. Unlinked cases preserve CASE-03 behavior.

Modes now ai/waiting_staff/human/ended. Public events add chat_accepted/chat_reassigned/chat_ended/chat_updated {case_id,mode}; event IDs/order remain stable. New customer messages to ended conversations409 CONVERSATION_ENDED; identical old message retry remains a stored receipt. Customer continuation may reopen a case while leaving old chat ended; a new conversation can handoff to that case, one active chat per case. No automatic AI takeover.

0012_chat_handoff stores immutable actor-owned accept receipts/transition records, composite origin FKs and deferred case/conversation/history/event checks. Every active case UPDATE requires a paired transition, not just status/assignment-column writes; private authenticated identity adoption has the existing ownership-only exemption. Ledger kind/history/role rules require supervisor for assignment and self for accept. RLS and latest memberships apply on every operation/replay. CHAT-03 below supplies staff chat send and controlled-generator publication guards; no browser, email, live model or production-wide acceptance claim.


## CHAT-03 executable messaging and internal publication

POST /api/v1/staff/cases/{id}/messages requires staff mutation session, stable Idempotency-Key, conversation_id,channel(chat/email),body1–4000,language(en/id),expected_version(case),expected_conversation_version and expected_epoch. New chat replies require the current human handler/assignee and exact basis; stale409 STALE_MESSAGE. Response201 {state:saved,message_id,conversation_id,case_id,sequence,channel:chat|email} is a historical storage receipt, not delivered/read. Exact retry returns the same after current ACL; changed command/key409. Email follows MAIL-01 below: without active tenant account/followup contact it returns503 MAIL_UNAVAILABLE; otherwise message/event/job are saved atomically, not marked delivered. Original body/language persists as staff_message.

Polling adds staff_message/ai_message and explicit author_type staff/ai. Internal AIPublication has no HTTP/model-tool endpoint. Tickets bind original session/principal/conversation/version/epoch/language and5minute deadline to a hidden nonce hash. Generation work is outside transactions. Current permission/context is rechecked at persistence; waiting/human/ended never accept new AI output. Changed input/language/mode or expiry discards without body/event. A NULL-to-linked race releases locks then only reauthorizes/discards, never replays writes. SQL expiry Z0013 rolls counter/body/event back before marking stale; other integrity errors are not masked.

0013 enforces distinct author/source shapes, immutable retry receipts, generation FKs and basis fields. AI authors do not impersonate customers. Unique generation/deferred checks bind one published message to one ticket. SQL validates handler/assignee/mode/version/epoch/language and case-then-conversation order. Runtime DB is backend-trusted; public callers cannot set source/author/generation fields or access tickets.

Acceptance uses controlled delayed generators, not a live model/provider. Repeat with actual orchestration at AI-03. Private-source context remains SRC-03B; MAIL, UI integration, model quality/cost/latency and FILE-01 scanner are separate.


## JOB-01 executable scheduler

Worker uses a separate WORKER_DATABASE_URL credential, never the API DATABASE_URL or migration credential. It can execute the scheduler capability functions job_claim/job_renew/job_finish and the MAIL-01 lease-scoped mail functions; no direct case/message/contact/outbox table or column access. Fixed-path NOLOGIN scheduler owner has only job/tenant-active/account-routing metadata access; mail projections use a separate owner. API cannot execute scheduler functions or insert worker state, attempts, schedule or safe-replay policy.

Claim is bounded SKIP LOCKED over registered kinds/active tenants. Every lease binds tenant/job/worker/token/generation; renew/finish recheck database time after row lock. Network/business handler awaits are outside transactions. Heartbeat failure/cancellation prevents stale completion. Outcomes/codes are finite; unexpected errors/timeouts become unknown, not automatic retry. Explicit retry persists capped exponential backoff/max attempts. Default replay_policy reconcile turns expired leases unknown; safe jobs may be reclaimed with a new token/generation. This fences scheduler state, not external side effects/provider exactly-once.

MAIL-01 registers case_created/email_send handlers only with explicit enabled mail configuration and account credentials. --once with empty registry verifies credential and leaves jobs untouched; daemon without handlers fails. MAIL-01 validates email through controlled transports; real provider round-trip remains MAIL-05. No model execution is claimed.

## MAIL-01 executable outbound boundary

Staff POST messages accepts channel=chat/email and returns a saved historical receipt with the same channel. For email, missing active tenant mail account or followup contact gives503/no write. Otherwise it atomically writes the staff message, event and a unique email_send job. The existing handler/assignee, exact versions/epoch and current staff ACL are required. Exact retry does not enqueue again. No HTTP/model tool accepts arbitrary to/from or sends directly.

Email notification handlers re-read verified contact, principal/participant and account state at send; private labels are fail-closed pending SRC-03B. Conditional reservation binds the latest projection and active job fence before network. Worker exposes only lease-scoped mail RPCs with no raw table access; account-filtered claim/reaper support multiple tenant credentials and subset workers. Stored IDs distinguish source app message UUID, internal email UUID, outbox job UUID, requested RFC header, actual RFC ID nullable, provider receipt and stable account delivery key.

Only sending/accepted/failed/delivery_unknown storage is executable in MAIL-01. MAIL-02 below adds delivery state and receipt reconciliation; callback ingress and inbound are not yet active. A successful POST is accepted, not delivered/read. Sending/unknown is never blindly resent. Default-disabled configuration leaves app queues untouched; provider live round-trip and OTP sender remain MAIL-05.

## MAIL-02 executable delivery boundary

GET /api/v1/staff/cases/{id}/email-deliveries requires current staff session/queue/case access, rejects identity override headers and query parameters, and returns no-store {items,has_more}, at most100 latest sends. Item fields: job_id,app_message_id nullable,email_message_id nullable,delivery_status,delivery_version nullable,needs_review,review_reason nullable,queued_at,provider_status_at nullable,last_checked_at nullable. No body/address/provider receipt/RFC key is returned. Nonstaff uses the existing401 staff authentication boundary; unauthorized staff gets404. No new mutation/evidence/resend HTTP endpoint exists.

0016 stores lifecycle/review metadata and an immutable capability-protected evidence ledger. Each envelope atomically gets a unique safe GET email_reconcile child, including upgrade backfill. Routing preserves account subset isolation and skips live source POST leases. Snapshot/observe RPCs bind tenant/job/worker/token/generation to the exact email and optimistic delivery version, recheck expiry after row-lock waits and after writes, and commit state/evidence together. Raw API/worker writes remain denied.

GET receipt evidence is matched to the saved account/ID/sender/sole recipient and bounded. Missing receipt ->NO_RECEIPT, lookup exhausted ->LOOKUP_UNAVAILABLE, repeatedly accepted at cap ->PENDING_DELIVERY. None triggers another POST. Accepted/delivered/read are distinct; no read claim is stored. Older accepted/unknown cannot regress terminal state; conflicting terminal proof keeps current state with CONFLICTING_EVIDENCE. Deduped accepted evidence does not suppress a later cap review decision. provider_status_at tracks actual supplied provider event time only; poll timestamps are local observations, not invented from email created_at. Verified callback ingress remains MAIL-03.

## MAIL-03 executable inbox boundary

POST /integrations/email/{alias}/events takes raw application/json<=64KiB with one each svix-id/svix-timestamp/svix-signature and no query parameters. Signature verification precedes JSON parse; attempt timestamp±300s and configured rotation secrets bind id.timestamp.rawbytes. Server alias/account and active DB mapping define tenant; payload identity fields do not.202 {accepted:true} and no-store follow successful inbox/job commit only. Invalid auth401, malformed signed event400, oversize413, conflicting reused eventID409, unavailable mapping503 and storage failure non2xx. Unknown supported-shape event types are durably ignored without inventing messages.

Inbox unique(account,eventID) is independent of email unique(account,direction,providerID). Received content is a private inbound/pending stage with NULL case/app/contact/principal and delivery status. Body is not released to timeline/model; headers/HTML/authentication observations are untrusted for authorization. New inbox jobs have explicit NULL-case shapes and account/fence-protected fetch/bind/complete capabilities. A stopped worker leaves durable review. Outbound event jobs use a saved exact account receipt and signed historical type/time after GET context verification, even after the ordinary polling child is terminal. Case identity/correlation remains MAIL-04.


## MAIL-04 staff email reply status

`GET /api/v1/staff/cases/{id}/email-replies` requires a live scoped CS/supervisor session. It returns at most100 ordered items with email_id, state, reason, app_message_id, created_at, published body, verification_state, send_state and attempts. Pending/control body, recipient, tokens, hashes and raw headers are excluded. Responses use Cache-Control:no-store; unknown/out-of-scope cases return404. Customer sessions cannot use the staff endpoint.

Verified replies are case-only messages, not events appended to an ended conversation. Closed thanks and ambiguous replies remain readable in this projection; only explicit same-issue continuation reopens a case.


## MAIL-05 authentication mail route

`APP_MAIL_AUTH_ROUTES` maps the server-resolved tenant UUID to an explicit account UUID/ref and sender/reply identities. The API-only `auth_mail_route_allowed` capability checks current active metadata and returns a boolean, without raw account/body access. Missing or mismatched routes cannot call the provider. Readiness checks configuration only; live acceptance requires actual mailbox/webhook/round-trip evidence.


## API-FE-03 — Selected staff workspace metadata

`GET /api/v1/staff/cases/{id}/capabilities?membership_id=<uuid>` requires live staff case access in that exact membership, organization and queue. Success200 contains case_id/case_version, actor_principal_id, membership_id/membership_version, organization{id,name}, queue{id,name}, assignee{membership_id,display_name nullable} ornull, active_conversation{id,version,mode waiting_staff|human} ornull, self_claim_membership_id ornull, allowed_actions, allowed_statuses and allowed_close_reasons. The selected membership is revalidated in the database; role/queue permissions from other organizations are not combined. Admin-only has no case access. Names come from constrained server metadata; contacts/emails/passwords/session secrets are not directory projections.

Actions are exactly claim,assign,unassign,update_status,close. CS claim is only for unassigned cases without an active linked chat and uses the exact selected membership. Supervisor assignment may preassign new cases without accepting them. Supervisor unassign requires an assigned new case and no human chat. Existing backend policy permits any authorized scoped CS/supervisor to update status, not only the assignee. Nonclosed statuses follow TRANSITIONS including allowed same-state edits and require a current valid assignee. Close reasons are resolved,customer_cancelled,duplicate,no_response with required command-specific resolution/target/evidence. Closed cases offer no mutations. These are advisory snapshots; mutations still revalidate actor, target, case version, active conversation version and all state/evidence. Active linked chat is waiting_staff/human only; historical ended chat does not require expected_conversation_version.

`GET /api/v1/staff/cases/{id}/eligible-assignees?membership_id=<uuid>&limit=25&cursor=<opaque>` is restricted to a supervisor in the selected membership. Limit1..100, default25. Response200 contains case_id/case_version, membership_id/membership_version, items[{membership_id,display_name nullable}], has_more and next_cursor nullable. Candidates satisfy the same live active principal/membership/organization/queue/CS-or-supervisor/credential/verified-bound-contact predicate as assignment. No global directory, contact fallback or fabricated name. Closed case returns no candidates after validating any supplied cursor.

The signed cursor is at most2048characters and binds tenant/session/principal/case/caseversion/selectedmembership/version/organization/queue/limit with900second validity. UUID ascending keyset ordering is a live directory, not a frozen snapshot: revoked targets disappear, names do not reorder identities and newly eligible IDs before the cursor require a fresh first page. Caseversion/scope/query changes reject the cursor rather than silently restarting. No total counts are returned. Scope is checked before cursor validity to conceal inaccessible cases.

`GET /api/v1/staff/cases/{id}` accepts optional membership_id for exact workspace scope; existing unscoped staff access and customer CaseView DTOs remain compatible. Unknown/duplicate query keys400, malformed typed fields422, inaccessible case/member generic404, unauthorized directory403, invalid/tampered/mismatched/expired cursor400. All staff responses including dependency/path/query/errors use no-store. Capability reads lock case, active conversation, then authoritative metadata in lifecycle order; they do not execute business mutations or send email. API-FE-05 queue pagination and duplicate-candidate lookup have separate contracts below.


## API-FE-05 — Server-side selected-membership queue pages

`GET /api/v1/staff/queues/cases` retains the legacy no-membership contract: only optional status, items-only bounded first100, no completeness claim. A supplied membership_id selects the scoped paged response; queue_id is optional within this membership, view=all|mine|unassigned(defaultall), optional status=new|in_progress|awaiting_customer|awaiting_external|closed, limit1..100(default25), sort=created_asc only, cursor optional1..2048characters. Any new query key without membership_id is rejected, not silently processed as legacy. Unknown/duplicate keys400, invalid typed values422, current inaccessible scope404, invalid/tampered/expired/context-mismatched cursor400. All staff responses including errors are no-store.

Scope comes from live current-actor membership/organization/CS-or-supervisor/queue and credential/verified-contact checks through private `case_staff_queue_workspace`. Admin-only, foreign membership or unavailable requested queue is concealed404. Zero active permitted queues is404; an authorized scope with no matching cases is200empty with real scope labels. No additional raw-table grants. Metadata returns organization{id,name} plus the full live authorized queues[{id,name}], even when a specific queue_id filters the query. Rows are SQL-filtered by tenant, selected organization, effective authorized queues, view and status BEFORE pagination. Mine means exact assigned_membership_id==selectedmembership, unassigned means storedNULL (not an inactive assignee), all means current readable cases in this selected scope. No union with other memberships/organizations.

Response fields: membership_id/membership_version,organization,queues,queue_id(nullable selected filter),view,status,sort,limit,items(existing QueueItem fields plus queue_id and created_at),has_more,next_cursor nullable,consistency='live_keyset',watermark and observed_at. No total or tabcounts. has_more describes the current remaining query, not a frozen/globalcomplete list. Client references source facts and case details separately; queue rows do not include customer_report, contacts or source/payment data.

Immutable created_at ASC,id ASC provides deterministic UUID tiebreaking, unaffected by updated_at changes. SQL uses strict(created_at,id)>cursorposition and created_at<=initial database watermark, retaining timestamp microseconds. Dedicated-purpose signed cursor binds tenant/session/principal/membership/version/organization/fullauthorizedqueue-set fingerprint/selectedqueue/view/status/sort/limit, initial watermark, last tuple and fixed900second expiry. Canonical SHA256 queue-set binding is bounded regardless of queue count; live grant/removal invalidates it even without membershipversion change. Renamed labels need not invalidate an otherwise unchanged scope. All pages revalidate live scope before decoding cursor; there is no invalid-cursor firstpage fallback.

This is a LIVE filtered traversal, not snapshot isolation. Initially committed+visible rows remaining eligible and authorized traverse exactly once; status/assignment/revocation changes can remove rows, and newly eligible or newly committed rows behind the cursor require a fresh firstpage. Rows with created_at after the watermark are excluded. An in-flight insertion timestamped before the watermark but committed later may become visible later or be missed behind the cursor. Do not claim all newly committed cases are excluded or that a false has_more means global completeness. Watermark and expiry do not slide on later pages. New queue views do not change API-FE-03 capabilities or authorize staff mutations.

## API-FE-04 — exact duplicate candidate lookup

POST /api/v1/staff/cases/{id}/duplicate-candidates/search is an advisory read using the existing staff lifecycle boundary. It requires same-origin HTTPS/session/CSRF even though it makes no business write. Strict JSON body: membership_id(UUID), expected_version(positive integer, not boolean/string), public_reference(exact canonical CASE- followed by32lowercase hexadecimal characters). No query parameters, extra body fields or caller-supplied identity/tenant headers. Reference is in the POST body rather than a URL; no partial/prefix/wildcard search, directory listing or automatic normalization.

Source case and active conversation are locked in existing lifecycle order; the exact selected membership is checked before disclosing stale/closed state. Missing/inaccessible source or selected membership returns generic404. Stale source expected_version409VERSION_CONFLICT; closed source409CASE_CLOSED. Nonstaff401; missing/incorrect CSRF or wrongOrigin403; forbidden query/identity headers400; malformed typed inputs422; unavailable dependency503. All staff responses, including errors, are no-store.

Success200: {case_id,case_version,membership_id,membership_version,items:[{case_id,public_reference}]}, with at most one item. Envelope IDs/versions describe the source and selected membership. Target projection contains only its ID and public reference; no target owner/contact/report/history/status/version or reason for exclusion. Missing/self/foreign-tenant/different-owner/out-of-scope target all return the same empty items array under an otherwise valid source envelope.

Target must pass forced runtime RLS and the SAME selected membership's active organization/queue authority. Permissions from another membership are not combined. Owner equality compares the complete nullable owner_principal_id/owner_anonymous_session_id pair; two verified contacts on one principal do not imply different owners, while distinct anonymous sessions do. Both open and closed targets can be eligible under existing duplicate-closure policy. Lookup uses exact tenant/public_reference equality and the existing unique index, with a minimal projection and no target case lock, avoiding reciprocal source/target row-lock ordering.

Lookup does not reserve/authorize a future close, alter versions, create history/outbox/email, or issue an executable consent token. PATCH closed/duplicate still receives related_case_id and freshly checks version, active conversation, current resource access and owner equality. A changed/revoked target or changed source must be reviewed again; a previous lookup is not proof of current authority. No new schema/function grant or close policy is introduced by this endpoint.


## KNOW-01 executable knowledge control plane

Admin routes under /api/v1/admin/knowledge create a document+initialdraft, add versions, read metadata/version/audit pages, replace draft content, publish and withdraw. Mutations bind selected membership_id plus expected version/document revision, server session tenant, HTTPS/Origin/CSRF and no-store. Extra identity fields, query keys and index-ready overrides are rejected. No URL fetch, delete or public booking-policy claim endpoint is introduced.

Documents belong to an organization; management requires current admin in that exact organization. Published public knowledge is tenant-local; staff knowledge requires allowed role and organization within the same active membership. Admin draft access never makes a draft eligible for an answer. Applicability is terminal plus optional aware-UTC booking_created_from/to, intervals start-inclusive/end-exclusive; missing required context is no match. Version selection uses exact document_key/service/language/authoritative policy_reference and as_of, status published/current ACL; it never substitutes latest for withdrawn/missing historical policy. Published content/ACL is immutable; supersedes must reference an older same-document version and does not auto-withdraw it.

Publish checks readiness outside the DB transaction, then freshly rechecks session/admin/revision/full SQL basis hash and index generation/deadline before the write. Default UnavailableIndex returns503 without publication until KNOW-02 supplies real validated readiness; positive test injection is not a production index. Concurrent conflicting scopes are serialized by document locks. Withdrawal requires reason and preserves admin history. Trigger-only immutable audit and incremented document revision are committed atomically; future caches and AI publication must revalidate current status/ACL/generation. Detailed request/response fields: docs/backend/knowledge_lifecycle.md. No embeddings, model orchestration or live AI quality claim in KNOW-01.


## Knowledge index and retrieval

`POST /api/v1/admin/knowledge/versions/{id}/index` accepts `{membership_id,expected_revision}` under staff session/CSRF and exact organization admin authorization. Returns `{version_id,generation,basis_hash,valid_until}` with no-store. It builds immutable version-bound chunks atomically after external embedding calls and current revision/access checks. Repeated indexing of an already complete current basis reuses generation. It does not publish. Missing capability yields503KNOWLEDGE_INDEX_UNAVAILABLE; provider overload/unavailability uses503EMBEDDING_BUSY/EMBEDDING_UNAVAILABLE. No caller-supplied vectors, model selection, tenant, index-ready flag or arbitrary fetch URL.

Internal `KnowledgeRetrieval.search(identity,Search)` returns at most10 eligible chunks with source/version/basis/generation/documentrevision. Tenant/currentACL/publication/currentgeneration and trusted policy/time/applicability are SQL-filtered before cosine ranking. No public search endpoint is added here; identity and booking applicability must be supplied by server services. Chunk bodies are source data, never instructions/tool authority. Index readiness and search ranking do not imply grounded-answer acceptance.


## Knowledge evidence consumption

Internal `KnowledgeEvidence.collect/consume` returns one of `sources_available`, `insufficient_sources`, `conflicting_sources`, `stale_sources`, or `source_unavailable`. Failure results contain no chunks or snapshot. Live authorization failures remain authorization errors. `sources_available` supplies candidate source excerpts and citations; it does not approve factual claims or tool actions.

Snapshots contain signed actor/session/tenant/context/policy and version/generation/basis/ordinal/documentrevision references, never cached body or URI. Consumption re-reads authoritative text under current SQL ACL and applicability. TTL is rechecked after database transaction completion. The current eligible-version scope fingerprint invalidates snapshots when a relevant new source is published, even if previously cited documents did not change. Signing and scoring policy are server dependencies; no new public endpoint, body override, cache store or automatically selected universal threshold is introduced. Agent publication must revalidate sources after long-running generation.


## AI-01 planning foundation

No public agent execution endpoint is added in this slice. Internal AgentFoundation scopes planning and read execution to the current owned latest customer message, conversation version/epoch/language and server identity. Tool inputs are strict reference/query schemas; actor/tenant/ACL/policy/time/consent overrides are rejected. Allowed functions are read_booking, read_transaction, read_booking_policy and search_knowledge only when backend capabilities exist. Private observations retain whole-DTO SRC-03A labels and current grant checks; model output is never execution authority.

Draft preparation is a separate stateless request using only persisted unlabeled customer text, not private tool/history context. Its classification/summary produces a pending DraftService preview with server references/contact/routing; no case, commit, email or source transaction. DraftService.create optionally takes internal expected_conversation_version/expected_generation_epoch, checked after lock before INSERT. Existing public draft request/response schemas remain unchanged. AI-02/03 must implement orchestration/replay/context and guarded publication; source-derived persistence remains SRC-03B.


## AI-02 internal workflow contract

ApplicationServices.agent_workflow exposes internal start(identity,RunRequest) and resume(identity,run_id); there is no new public agent HTTP endpoint. RunRequest binds current persisted conversation/message, request UUID, assist/draft mode, bounded server reference allowlists, optional general knowledge descriptor, contact and existing create_case/request_handoff draft action. Same request key with changed payload is a conflict. Identity/tenant/ACL/consent are never accepted from model output.

Durable agent_runs rows are typed domain checkpoints, not serialized LangGraph actor state. Resume rebuilds up to eight current-author customer messages (≤16,000 characters), current session and grants; draft report≤4,000 and model context≤24,000. Latest explicit reference correction narrows tool scope. Search checkpoint excludes query/as_of/private applicability; query is rebuilt from latest user report and time from server. Booking-specific knowledge filters are rejected until the labelled persistence bridge. Private DTOs, knowledge bodies and generated questions remain ephemeral. Restored waiting_user uses a generic localized template rather than replaying unlabelled private-derived wording.

All attempts including refresh reserve durable budget before dispatch: 3 model / 12 tool-read, serial graph deadline180s, node lease120s. Counter means tool attempts, not internal HTTP calls. Model timeout/unknown outcome is not replayed. Safe GET recovery can retry within budget. Draft INSERT + run receipt share one transaction under conversation/run fences; committed receipt returns same draft ID. Current authorization and source refresh are mandatory, never checkpoint-derived. No automatic case submission, consent, source mutation, payment or email. Publication/handoff and private-derived persistence remain AI-03/SRC-03B.


## AI-02 final checkpoint — 5 November 2026

**DONE / bounded CODE+DB orchestration QA PASS**, no confirmed remaining P1/P2. LangGraph serial routing, typed domain-owned PostgreSQL checkpoints, bounded current customer memory, fresh authorization/source checks, durable attempt budgets and atomic pending-draft receipts are implemented. Unknown model outcomes require review; safe GET recovery is bounded; restart does not repeat committed drafts or a completed draft-proposal model request. Private tool bodies and generated clarifications are not checkpointed. The typed draft proposal uses customer reports only.

Lead sole serial validation:43 targeted PASS3.44s including3 independently authored QA regressions; one final backend894PASS474opt-inSKIP17.84s;5 final isolated Neon scenarios PASS81.30s. Initial4NeonPASS78.78s are preserved separately, not counted as additional unique final scenarios. Final repeat followed a real SQL transition/current-context guard change and added database negative scenario. Freeze covers253source/test/migration/manifest/lock files; all unchanged after validation. QA independently checked20 relevant hashes and source/evidence, without executing tests/providers/database operations. Both QA P2 findings were fixed: private booking applicability excluded from checkpoints, and all read refresh/recovery attempts durably counted against12.

Actual schema0025→0026_agent_runs applied: all tables forced RLS, agent_runs empty, runtime DELETE/parameterUPDATE denied, case/draft/history/job/session counts unchanged. Actual-config in-process ASGI health200/protectedroute401 no-store/no cookie; agent_workflow absent while disabled; agent+embedding off and five mailflags false. Zero model/provider requests; existing listening API not restarted. Evidence local/ai-02/{source-hashes,final-validation,rollout,runtime-smoke}.json and sanitized logs. docs/local remain ignored. No Docker, extra server/browser or model downloads.

This validates orchestration code and real DB behavior with controlled model/source doubles, not live LLM quality/account access or production availability. AI-03 still owns actual chat publication/handoff/staff translation, AI-04 model comparison, and SRC-03B private-derived persistence integration. Next task in backend order: AI-03 with its publication/security dependencies. Details docs/backend/agent_orchestration.md.


## AI-03 delivery and translation HTTP contracts

POST /conversations/{id}/agent-runs accepts message_id/request_key/purpose assist|handoff|answer and optional service_type general|parking|lounge; identity and tools remain server-owned. POST /agent-runs/{id}/resume returns published/discarded/draft/review plus run_id and optional message_id/sequence/draft_id/reason, never a second response body outside the event feed. Explicit POST calls require current session, Origin and CSRF. Handoff remains a pending preview followed by existing human confirmation. Per-current-message/session/mode dedup prevents request-key rotation from allocating extra model runs. Same generation ticket replay returns its immutable receipt without another model request.

POST /staff/cases/{id}/translation-drafts accepts StaffMessage original text/channel/language/current version triple plus target_language and request_key. It reserves one attempt, returns generating/ready/failed/sent metadata, original_body, translated_body, reviewed_body and optional sent message_id. Original is an unsent draft body, not a fictitious original_message_id. GET same path/{draft_id} is creator-session and current case scoped. POST same path/{draft_id}/send requires reviewed text, approved=true (actual boolean), expected_revision and one Idempotency-Key; original/proposal/reviewed text are preserved and only reviewed text is sent via shared staff messaging rules. Message/event/email job if applicable and translation receipt commit atomically. Runtime has no draft DELETE/metadata rewrite; current handler, source-label gate and version/epoch checks prevent stale/unauthorized send.

Agent remains opt-in/off. Customer API does not expose private read scope or model approval. AI-ANSWER-01 implements public extractive knowledge answers/citations/reconsumption; its code/DB evidence and live model quality AI-04 remain distinct gates. SRC-03B separately owns labelled private-derived persistence.


AI-03 current validated checkpoint: bounded CODE+DB integration QA PASS (5 November 2026). Lead serial final validation:912 backend PASS/485 opt-in SKIP and11 isolated Neon scenarios PASS;105 targeted tests include4 independent QA regressions. Schema0027 forced RLS and business counts unchanged; ASGI health200/protected401/no-store. Runtime agent/embedding/email disabled; no paid model requests, emails or server restart. Current scope is actual clarification publication, human-confirmed handoff and human-approved staff translation. AI-ANSWER-01 factual-answer implementation and SRC-03B private-derived persistence remain explicit release gates; live quality remains AI-04. Evidence local/ai-03 and guide docs/backend/agent_delivery.md.


## AI-ANSWER-01 public knowledge answers

POST /conversations/{id}/agent-runs now accepts purpose answer and service_type general|parking|lounge. This category only filters public information; it is not object entitlement. Current conversation language, latest owned customer report, server time, evidence policy and tenant/session scope are server-bound. Browser document/policy/terminal/booking-date/ACL/model/citation overrides are forbidden. Knowledge capability requires explicit calibrated server policy; absent capability returns AGENT_KNOWLEDGE_UNAVAILABLE before run/provider work.

AnswerSelection is a tool-free strict model response: outcome answer|insufficient|conflict and at most3 unique source_ids. The backend copies complete selected384character chunks and authoritative URI, then publishes through current version/epoch/session gates. No model-written factual prose or URI is accepted. Source ambiguity/insufficiency/staleness/unavailability and model abstention produce a localized staff offer, never automatic case submission. Actual handoff still requires a pending preview and customer confirmation.

Same snapshot is consumed after model and revalidated inside publication's SQL transaction. A bounded NOWAIT catalog lock, live public applicability/catalog fingerprint, literal chunk/URI/version/revision checks, timestamp types/TTL and exact renderer SQL trigger protect INSERT. Late source failure rolls factual savepoint back and publishes only a deterministic staff offer under the same generation guard/receipt; stale mode/epoch/session/deadline still blocks publication. No model retry or stored answer checkpoint.

GET /conversations/{id}/messages/{message_id}/citations returns an authorized historical_publication receipt: message_id, issued_at and citations(version_id,generation,ordinal,basis_hash,document_id,document_revision,quote,source_uri). It exposes no query/actor/HMAC/scoring config. The endpoint uses current session/ownership and no-store. Historical citations describe publication time; they are not current policy claims after withdrawal. Existing event/history body remains the UI source of truth. Private booking data remains SRC-03B. Relevance/completeness/cross-chunk qualifiers/semantic conflicts require AI-04 evaluation; exact quotation is not semantic entailment proof.


AI-ANSWER-01 current validated checkpoint (5 November 2026): bounded CODE+DB public extractive-answer capability QA PASS.935 backend tests and14 unique isolated Neon scenarios have passing evidence;160 targeted tests include4 independent QA regressions. Source268hashes match, schema0028 forced RLS/unchanged business counts, actual-config ASGI health200/protected401no-store. Answer publication and historical server-owned citations are implemented. Current agent/embedding/email remain disabled;0provider requests/emails/server restart. Live semantic quality remains AI-04 and private-derived persistence SRC-03B. Evidence local/ai-answer-01 and private guide docs/backend/public_answers.md.


## COMPLETE-03 — scoped service information discovery and index recovery

GET `/api/v1/admin/knowledge/documents` accepts one membership_id, optional UUID after, limit1–50/default25 and optional exact lower-hyphen document_key. Response: membership_id, organization_id, items(DocumentView metadata only), limit, has_more, next_after UUID/null and consistency=live_keyset. UUID ascending/lookahead within the current selected organization; no total/snapshot. Current session/admin is rechecked. Extra/duplicate query and actor headers are rejected. Exact-key creation recovery drops prior page position; a missing record does not prove an in-flight creation failed.

GET `/api/v1/admin/knowledge/versions/{id}/index?membership_id=...` checks current admin/document/version and actual stored index proof against current basis. Response: version_id, basis_hash, ready boolean, generation UUID/null, indexing_available boolean. Ready iff current proof exists and matches; availability is configured retrieval, not a publish permission. No provider/embedding call or index-ready override. Publish independently rechecks current proof, version and applicability. Both GETs are no-store. Existing POST/PATCH lifecycle contracts remain unchanged; no new migration/head.

Frontend `/admin/information` uses these scoped GETs and existing create/version/edit/index/publish/withdraw APIs. All writes require explicit reviewed action. Unknown writes recover with GET current records, without automatic resend or causal receipt claims. Read-only recovery preserves only safe IDs/key and current membership binding; private body/review is purged on loss of scope/session. UTC fields retain the original full instant when unedited.

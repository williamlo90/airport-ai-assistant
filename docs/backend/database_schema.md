# Database schema Airport AI Assistant

Database aplikasi ini menyimpan identitas, percakapan, pengajuan, kasus, knowledge, email, dan pekerjaan worker. Booking dan pembayaran tetap dimiliki sistem sumber. Kita menyimpan referensinya untuk membantu penanganan, bukan membuat sistem pembayaran baru.

Dokumen ini memperinci [DATA_AND_API_CONTRACTS.md](../../DATA_AND_API_CONTRACTS.md), mengikuti [PRODUCT_CONTRACT.md](../../PRODUCT_CONTRACT.md) dan [arsitektur backend](backend_architecture.md). Ini rancangan untuk migration, bukan bukti bahwa constraint, RLS, atau concurrency sudah diuji. Pembuktiannya mengikuti [TEST_PLAN.md](../../TEST_PLAN.md).

## 1 Cara membaca schema

| Notasi atau tipe | Artinya |
| --- | --- |
| PK | Primary key: identitas unik baris. |
| FK | Foreign key: referensi ke baris lain. |
| UQ | Unique constraint atau unique index. |
| NN | NOT NULL: wajib terisi. |
| NULL | Boleh belum memiliki nilai. |
| UUID | ID internal yang dibuat backend/database, bukan berasal dari prompt. |
| TEXT | Teks dengan batas panjang/format yang divalidasi pada API dan constraint yang relevan. |
| TIMESTAMPTZ | Waktu absolut; aplikasi mengirim UTC RFC3339. Zona layanan disimpan sebagai nama zona IANA. |
| INTEGER / BIGINT | Angka bulat untuk versi, percobaan, urutan, ukuran, atau nominal. |
| JSONB | Data bervariasi yang tetap memiliki schema validasi aplikasi. Bukan pengganti semua relasi/FK. |
| TEXT[] | Daftar nilai teks, misalnya scope; nilainya divalidasi terhadap allowlist. |
| VECTOR(d) | Embedding pgvector dengan dimensi sesuai model embedding. |

Relasi **one-to-many** berarti satu baris memiliki banyak baris terkait. **Many-to-many** memakai tabel penghubung. **One-to-zero-or-one** memakai FK nullable dengan unique constraint.

## 2 Aturan dasar semua tabel

Kecuali dinyatakan berbeda, setiap tabel di schema aplikasi `app` mempunyai:

```text
tenant_id    UUID          NN, FK → tenants.id
id           UUID          NN
created_at   TIMESTAMPTZ   NN, default waktu server
updated_at   TIMESTAMPTZ   NN, default waktu server
PK (tenant_id, id)
```

ID internal ditangani bersama tenant pada query dan FK. API boleh menampilkan id saja karena tenant sudah diperoleh dari konteks request, tetapi database tidak boleh menghubungkan data dengan id saja.

Contohnya:

```text
cases(tenant_id, queue_id)
    → service_queues(tenant_id, id)
```

FK ini mencegah kasus tenant A menunjuk queue tenant B. PK `(tenant_id,id)` sekaligus memenuhi keunikan pasangan yang diminta kontrak API. UUID tetap dibuat acak; keamanan tidak bergantung pada sulitnya menebak UUID.

Tabel event/audit bersifat append-only untuk role runtime: `updated_at` tetap sama dengan `created_at`; tidak ada izin UPDATE/DELETE biasa. Penghapusan sesuai retensi dilakukan proses pemeliharaan yang terpisah dan diaudit.

FK memakai ON DELETE RESTRICT sebagai default. Jangan memakai CASCADE untuk menghapus riwayat kasus, consent, atau email tanpa proses lifecycle yang eksplisit. Principal/akun staf dinonaktifkan lebih dahulu agar referensi historis tetap utuh.

Nilai status memakai TEXT + CHECK agar enum mudah dimigrasikan. Field bahasa memakai CHECK `id` atau `en`. Versi INTEGER dimulai dari 1 dengan CHECK > 0; penghitung BIGINT/INTEGER tidak negatif. Seluruh timestamp keamanan memakai waktu server.

## 3 Tenant organisasi dan antrean

### tenants

Pelanggan aplikasi, misalnya Bandara A. Tabel ini tidak memakai common tenant_id karena merupakan akar tenant.

| Kolom | Tipe | Aturan |
| --- | --- | --- |
| id | UUID | PK |
| slug | TEXT | NN, UQ |
| display_name | TEXT | NN |
| timezone | TEXT | NN, validasi zona IANA |
| supported_languages | TEXT[] | NN, subset id/en, tidak kosong |
| active | BOOLEAN | NN, default true |
| created_at / updated_at | TIMESTAMPTZ | NN |

### tenant_hosts

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| hostname | TEXT | NN, UQ global, format hostname canonical tanpa port/path |
| active | BOOLEAN | NN |

Relasi: **tenant 1 → N tenant_hosts**. Field API `allowed_hosts` diproyeksikan dari tabel ini. Host dipakai untuk memilih tenant situs, bukan bukti bahwa pengguna berhak mengakses data tenant. Lookup sebelum login hanya mengembalikan konfigurasi publik; sesi tetap diperiksa.

### organizations

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| name | TEXT | NN |
| active | BOOLEAN | NN |

Relasi: **tenant 1 → N organizations**. Organisasi ini merupakan unit di dalam tenant, bukan tenant baru.

### service_queues

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| organization_id | UUID | NN, FK → organizations |
| service_type | TEXT | NN, general/parking/lounge |
| name | TEXT | NN |
| active | BOOLEAN | NN |

Tambahkan UQ `(tenant_id,id,organization_id)` agar kasus dapat memeriksa kesesuaian queue dan organisasi dengan FK tiga kolom. Relasi: **organization 1 → N queues**.

## 4 Identitas kontak dan akses staf

### principals

Identitas pengguna yang dikenali backend. Penumpang/pengunjung tidak memerlukan tabel akun terpisah.

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| display_name | TEXT | NULL |
| source_subject_ref | TEXT | NULL, referensi eksternal; partial UQ per tenant jika terisi |
| active | BOOLEAN | NN |

Tidak menyimpan role staf sebagai field yang dapat diubah pelanggan. Customer verification tidak memberi membership staf.

### contacts

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| principal_id | UUID | NULL sebelum verifikasi/pengikatan; FK → principals |
| email_address | TEXT | NN, alamat asli tervalidasi |
| email_lookup_key | TEXT | NN, UQ per tenant |
| verified_at | TIMESTAMPTZ | NULL sebelum verifikasi |

Relasi: **principal 1 → N contacts**, contact 0..1 principal. Domain email dinormalisasi; jangan menghapus titik atau plus-tag secara universal karena alamat yang berbeda tidak boleh digabung secara keliru. Strategi lookup harus sama pada verifikasi, source adapter, dan email. Ownership contact tidak boleh direbind hanya karena request menyebut email yang sama. verified_at terisi mengharuskan principal_id terisi.

Field kontrak `verified_email`/`verified_at` berasal dari contacts. Memisahkan tabel tidak menambah hak akses. Contact belum terverifikasi dapat dipakai untuk menyiapkan kasus umum, tetapi tidak menerima data privat.

### staff_memberships

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| principal_id | UUID | NN, FK → principals |
| organization_id | UUID | NN, FK → organizations |
| active | BOOLEAN | NN |
| version | INTEGER | NN |

UQ `(tenant_id,principal_id,organization_id)` dan `(tenant_id,id,organization_id)` untuk FK organisasi pada membership_queues. **Principal M ↔ N organizations melalui memberships**. Kombinasi peran didukung melalui tabel berikut, bukan satu role tunggal.

### membership_roles dan membership_queues

| Tabel | Kolom tambahan | Aturan |
| --- | --- | --- |
| membership_roles | membership_id UUID NN, role TEXT NN | FK → staff_memberships; CHECK role cs/supervisor/admin; UQ `(tenant_id,membership_id,role)` |
| membership_queues | membership_id UUID NN, queue_id UUID NN, organization_id UUID NN | FK `(tenant_id,membership_id,organization_id)` → staff_memberships dan `(tenant_id,queue_id,organization_id)` → service_queues; UQ `(tenant_id,membership_id,queue_id)` |

Keduanya tetap memiliki common id/PK. API `roles` dan `allowed_queue_ids` merupakan hasil penggabungan tabel penghubung. organization_id pada membership_queues harus cocok dengan organisasi membership dan queue melalui dua FK tiga kolom. Ini menolak pasangan lintas organisasi serta perubahan parent yang merusak relasi, tanpa trigger lintas tabel. Layanan identitas tetap memvalidasi kewenangan sebelum commit. Kombinasi admin+CS harus diberikan eksplisit dan diaudit; admin saja tidak memperoleh akses isi kasus.

### sessions

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| principal_id | UUID | NULL untuk anonim; FK → principals |
| session_kind | TEXT | NN, anonymous/customer/staff |
| token_hash | TEXT | NN, UQ; token mentah hanya pada cookie pengguna |
| csrf_secret_hash | TEXT | NN |
| expires_at | TIMESTAMPTZ | NN |
| revoked_at | TIMESTAMPTZ | NULL |
| replaced_by_session_id | UUID | NULL, FK → sessions |

CHECK principal_id NULL hanya untuk anonymous; customer/staff wajib principal. Aktif/tidak aktif tetap memerlukan waktu dan membership terbaru, bukan flag dari model. Relasi **principal 1 → N sessions**. `anonymous_conversation_scope` API diwakili conversations.anonymous_session_id, bukan daftar ID yang bebas ditulis client.

### verification_challenges

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| contact_id | UUID | NN, FK → contacts |
| purpose | TEXT | NN, allowlist tujuan verifikasi pelanggan/kontak kasus |
| contact_hash | TEXT | NN |
| hashed_code | TEXT | NN, verifier berkunci dengan secret di luar database |
| origin_session_id | UUID | NULL untuk verifikasi email tanpa browser; FK → sessions |
| binding_nonce_hash | TEXT | NULL, wajib pada challenge upgrade browser |
| case_id | UUID | NULL, FK → cases untuk verifikasi tindak lanjut |
| expires_at | TIMESTAMPTZ | NN |
| attempts / max_attempts | INTEGER | NN, 0 ≤ attempts ≤ max_attempts, max_attempts > 0 |
| consumed_at | TIMESTAMPTZ | NULL |

Challenge dikonsumsi atomik sekali. Upgrade browser memeriksa sesi asal dan nonce: pindahkan percakapan yang terbukti, revoke sesi lama, terbitkan sesi baru, cancel draf pending dalam satu transaksi. Pengetahuan conversation/challenge ID saja tidak mengadopsi riwayat.

## 5 Kasus pengajuan dan consent

### cases

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| public_reference | TEXT | NN, UQ per tenant; bukan token akses |
| owner_contact_id | UUID | NULL hanya untuk kasus umum dalam sesi anonim tanpa kontak; FK → contacts |
| owner_anonymous_session_id | UUID | NULL, FK → sessions |
| organization_id | UUID | NN, FK → organizations |
| queue_id | UUID | NN; `(tenant_id,queue_id,organization_id)` FK → service_queues |
| category | TEXT | NN, information/booking_issue/transaction_issue |
| service_type | TEXT | NN, general/parking/lounge |
| booking_reference / transaction_reference | TEXT | NULL, referensi eksternal, bukan FK database aplikasi |
| summary / customer_report | TEXT | NN; laporan pengguna tetap terpisah dari fakta sumber |
| source_observations | JSONB | NN, default []; schema fakta minimum, asal dan waktu pembacaan |
| resource_security_labels | JSONB | NN, default []; resource references dan required_source_scopes |
| status | TEXT | NN, new/in_progress/awaiting_customer/awaiting_external/closed |
| assigned_membership_id | UUID | NULL, FK → staff_memberships |
| next_actor | TEXT | NN, staff/customer/external/none |
| next_action | TEXT | NULL; wajib ketika menunggu |
| close_reason | TEXT | NULL, resolved/customer_cancelled/duplicate/no_response |
| resolution | TEXT | NULL; wajib ketika closed |
| duplicate_of_case_id | UUID | NULL, FK → cases |
| last_followup_message_id | UUID | NULL, FK → messages; bukti tindak lanjut untuk no_response |
| needs_staff_review | BOOLEAN | NN, default false |
| version | INTEGER | NN, default 1 |
| closed_at | TIMESTAMPTZ | NULL |

Minimal salah satu ownership contact/session terisi; pemindahan dari anonim ke kontak dilakukan setelah verifikasi, tanpa membuat kasus kedua. owner_anonymous_session_id dapat dipertahankan sebagai provenance tetapi tidak memberi akses setelah sesi dicabut. Kasus terkait data sumber wajib mempunyai pemilik kontak terverifikasi sebelum dibuka ke pelanggan. Kasus umum anonim tanpa kontak tetap dapat meminta live CS; tidak dijanjikan tindak lanjut email. Offline submission wajib contact dan konfirmasi.

CHECK status closed mengharuskan close_reason, resolution tidak kosong, closed_at dan next_actor none. Untuk status selain closed, ketiganya NULL; riwayat penutupan sebelumnya tetap di case_history. Untuk awaiting_customer/awaiting_external wajib assignee dan next_action, dengan next_actor customer/external. in_progress wajib assignee. new tidak berarti ada petugas aktif; assignment dapat tersedia sebelum acceptance.

duplicate membutuhkan duplicate_of_case_id yang bukan dirinya. no_response membutuhkan bukti tindak lanjut petugas. Keabsahan scope assignee aktif, transisi status, dan isi bukti diperiksa dalam layanan bisnis; keberadaan FK saja tidak memenuhinya. Relasi: **contact 1 → N cases; queue 1 → N cases; membership 1 → N assigned cases**.

### case_participants

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| case_id / principal_id | UUID | NN, FK → cases / principals |
| scopes | TEXT[] | NN, allowlist kemampuan kasus |
| relation_origin | TEXT | NN, verified_case_contact/source_owner/source_recorded_representative/source_beneficiary |
| verified_at | TIMESTAMPTZ | NN |
| revoked_at | TIMESTAMPTZ | NULL |

UQ `(tenant_id,case_id,principal_id)`. **Principal M ↔ N cases**. Sesi anonim mengakses melalui ownership case/conversation, bukan participant principal fiktif. Source relation hanya dibaca, tidak dibuat oleh tabel ini. Participant case_read tidak otomatis memberi payment read.

### action_drafts

Implementasi CASE-01 pada revision0006_action_drafts menerima create_case/request_handoff mandiri dan state pending/cancelled/expired. Selain actor XOR, created_session_id UUID NN mengikat semua draf ke sesi pembuat dengan composite FK tenant/sessions. Payload berisi laporan pengguna tervalidasi; destination JSON menyimpan snapshot queue/organisasi dan harus cocok dengan destination_queue_id. Language, created_at, fixed expiry15menit dan seluruh material pratinjau ikut hash canonical. Version fisik BIGINT. Case_id wajib NULL dan resource_security_labels wajib[]; committed/result pointers/consent serta existing-case action ditambahkan pada CASE-02/fitur terkait. Trigger menahan perubahan terminal dan mencegah edit pending setelah deadline; RLS memeriksa sesi aktif, pemilik, sesi pembuat dan percakapan. [Panduan draf](action_drafts.md) menjelaskan invariannya.

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| conversation_id | UUID | NN, FK → conversations |
| case_id | UUID | NULL, FK → cases |
| actor_principal_id / actor_session_id | UUID | Masing-masing NULL, FK → principals / sessions; tepat satu terisi |
| created_session_id | UUID | NN, FK → sessions; semua draf terikat sesi pembuat, termasuk aktor principal |
| action_type | TEXT | NN, create_case/add_case_information/request_handoff |
| destination_queue_id | UUID | NN, FK → service_queues; backend memilih tujuan sah |
| payload | JSONB | NN, sesuai schema action type |
| displayed_summary | TEXT | NN |
| payload_hash | TEXT | NN, hash canonical payload+tujuan+versi |
| version | INTEGER | NN |
| state | TEXT | NN, pending/committed/cancelled/expired |
| expires_at | TIMESTAMPTZ | NN |
| result_case_id / result_message_id | UUID | NULL, FK → cases / messages |
| committed_at | TIMESTAMPTZ | NULL |

create_case dapat menghasilkan kasus baru; request_handoff dapat mengacu kasus lama. Result pointer tidak dibuat UNIQUE pada case_id karena beberapa tindakan sah dapat menuju kasus sama. Yang unik adalah commit per draft dan result untuk logical action, bukan larangan beberapa tindakan pada satu kasus. state committed wajib result_case_id dan committed_at serta consent record. State lain tidak memiliki result commit.

### action_consents

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| draft_id | UUID | NN, FK → action_drafts, UQ per tenant |
| confirmed_version | INTEGER | NN |
| payload_hash | TEXT | NN, sama dengan versi ringkasan yang ditampilkan |
| actor_principal_id / actor_session_id | UUID | Tepat satu terisi, FK → principals / sessions |
| confirmed_at | TIMESTAMPTZ | NN |
| request_id | TEXT | NN |

**Draft 1 → 0..1 consent**. Consent tersimpan hanya pada commit berhasil, bersama hasil, audit dan outbox. Edit draft menambah version dan membatalkan persetujuan versi lama; draft committed tidak diedit. Constraint lintas tabel/transaction service memastikan actor dan hash cocok; model tidak dapat menulis consent langsung.

### idempotency_records

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| actor_principal_id / actor_session_id | UUID | Tepat satu terisi, FK → principals / sessions |
| operation / key / request_hash | TEXT | NN |
| draft_id | UUID | NULL, FK → action_drafts |
| result_case_id / result_message_id | UUID | NULL, FK → cases / messages |
| state | TEXT | NN, processing/committed |
| response_code | INTEGER | NULL sebelum commit |

Dua partial unique indexes: `(tenant_id,actor_principal_id,operation,key)` ketika principal terisi; pasangan setara actor_session_id untuk anonim. Request hash berbeda pada key sama memberi conflict. Lock draft + record hasil mencegah double commit walau client memakai key berbeda. Record/action tidak dibuang saat retry masih mungkin; penghapusan mengikuti lifecycle kasus.

### case_history

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| case_id | UUID | NN, FK → cases |
| actor_id / actor_session_id | UUID | NULL, FK → principals / sessions; sistem memakai actor_kind system |
| actor_kind | TEXT | NN, customer/staff/system |
| event_type | TEXT | NN |
| previous_version / resulting_version | INTEGER | NN |
| reason | TEXT | NULL kecuali aksi yang mewajibkannya |
| changes | JSONB | NN, perubahan minimum yang dapat ditelusuri |
| occurred_at | TIMESTAMPTZ | NN |

**Case 1 → N history**; UQ `(tenant_id,case_id,resulting_version)` untuk satu event perubahan domain per versi. Event pesan biasa bukan kenaikan case version kecuali memang mengubah domain; urutan pesan memakai sequence sendiri.

## 6 Percakapan pesan event dan checkpoint

Implementasi BEHAV-01 pada revision0005_conversations adalah subset bertahap rancangan berikut. Conversations sudah memiliki owner XOR, language, version BIGINT, generation_epoch awal1, counter pesan last_sequence, case_id yang masih wajib NULL dan labels yang wajib[]. Messages sudah memiliki conversation_id, author_principal_id/author_session_id XOR, author_type customer, channel web, visibility customer, content_class private, source_kind user_report, body, language, client_message_id, sequence dan labels[]. Messages tidak memiliki case_id sebelum tabel cases tersedia. FK tenant-composite, partial unique dedupe dan FORCE RLS sudah executable. Handler, mode selain ai, event/checkpoint serta FK/binding cases ditambahkan bersama fitur terkait, bukan dianggap sudah terpasang. [Panduan percakapan](conversations.md) mencatat transaksi dan batas adopsi AUTH-02.

### conversations

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| customer_principal_id | UUID | NULL, FK → principals |
| anonymous_session_id | UUID | NULL, FK → sessions |
| adopted_from_session_id / adopted_at | UUID / TIMESTAMPTZ | NULL bersama; composite FK sesi asal; terisi hanya setelah ownership menjadi principal melalui AUTH-02 |
| case_id | UUID | NULL, FK → cases |
| handler_membership_id | UUID | NULL, FK → staff_memberships |
| language | TEXT | NN, id/en |
| mode | TEXT | NN, ai/waiting_staff/human/ended |
| generation_epoch | BIGINT | NN, default 0 |
| last_event_sequence | BIGINT | NN, default 0 |
| version | INTEGER | NN |
| resource_security_labels | JSONB | NN, default [] |

CHECK tepat satu owner principal/session. CHECK handler terisi jika dan hanya jika mode human. UQ `(tenant_id,id,case_id)` untuk relasi pesan yang memuat keduanya. Partial UQ `(tenant_id,case_id)` WHERE mode IN (waiting_staff,human) membatasi satu live handoff per kasus dalam MVP. Percakapan lain tetap boleh dalam mode ai/ended dengan akses yang benar. **Case 1 → N conversations**.

Accept/reassign/close mengambil lock kasus lalu percakapan dalam urutan ID tetap bila lebih dari satu. Handler dan assigned_membership_id harus konsisten; migration menambahkan pemeriksaan integritas transaksi lintas tabel, sementara layanan tetap memeriksa aktif/scope. Close mengakhiri seluruh percakapan terkait dan menaikkan epoch; tidak menyalakan AI. Pemeriksaan optimistic version dilakukan bersama perubahan, bukan read-then-write tanpa lock.

### messages

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| conversation_id / case_id | UUID | NULL, FK → conversations / cases; minimal satu terisi |
| author_type | TEXT | NN, customer/ai/staff/system |
| author_principal_id / author_session_id | UUID | NULL, FK → principals / sessions |
| origin_session_id | UUID | NULL, composite FK sesi; provenance pesan anonim yang kemudian diatribusikan ke principal terverifikasi |
| channel | TEXT | NN, chat/email |
| visibility | TEXT | NN, customer/internal |
| content_class | TEXT | NN, private/generic_notification |
| body | TEXT | NN |
| language | TEXT | NN, id/en |
| client_message_id | UUID | NULL |
| source_kind | TEXT | NN, user_report/source_fact/staff_reply/assistant/system |
| resource_security_labels | JSONB | NN, diwariskan dari kasus/observasi |

Jika conversation_id dan case_id keduanya terisi, FK tiga kolom memastikan conversation.case_id cocok. Jika conversation yang sudah berkasus mendapat pesan baru, case_id wajib ikut; penautan kasus ke percakapan memutakhirkan relasi pesan sebelumnya secara terkontrol. Pesan private mewarisi pembatasan agregat kasus, bukan disaring ulang oleh model.

Client-message dedupe memakai empat partial unique indexes untuk kombinasi principal/session dan conversation/case-only. Contoh: `(tenant_id,author_principal_id,conversation_id,client_message_id)` ketika seluruhnya terisi. Jangan mengandalkan UNIQUE dengan kolom NULL yang membiarkan duplikat. AI/system memakai job/action key server-side. **Conversation 1 → N messages; case 1 → N messages**.

generic_notification dibuat dari template server yang tidak memuat detail pribadi sumber, bukan label yang bebas diberikan model/client untuk melewati grants. Internal note tidak tampil di portal pelanggan, konteks model pelanggan, atau outbound pelanggan.

### conversation_events

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| conversation_id | UUID | NN, FK → conversations |
| sequence | BIGINT | NN, > 0 |
| event_type | TEXT | NN |
| message_id | UUID | NULL, FK → messages |
| visibility | TEXT | NN, customer/internal |
| actor_id | UUID | NULL, FK → principals |
| payload | JSONB | NN, data event minimum |
| resource_security_labels | JSONB | NN |

UQ `(tenant_id,conversation_id,sequence)`. Sequence dialokasikan atomik melalui counter percakapan, bukan MAX+1 tanpa lock. Cursor tidak memberi akses sendiri. Event dengan message_id harus mengacu pesan percakapan yang sama, diperiksa constraint lintas tabel dan layanan. **Conversation 1 → N events**.

### agent_checkpoints

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| conversation_id | UUID | NN, FK → conversations |
| checkpoint_key | TEXT | NN, ID format orkestrator |
| parent_checkpoint_id | UUID | NULL, FK → agent_checkpoints |
| generation_epoch | BIGINT | NN |
| state | JSONB | NN, data minimum bukan credential/bukti akses |
| model_version / workflow_version | TEXT | NN |
| resource_security_labels | JSONB | NN |

UQ `(tenant_id,conversation_id,checkpoint_key)`. Parent harus berasal dari percakapan sama. **Conversation 1 → N checkpoints**; format internal LangGraph dipetakan adapter penyimpanan tanpa membuang tenant boundary. Resume memeriksa ulang izin dan state domain, bukan mempercayai hasil lama.

## 7 Email provider dan worker

### email_accounts

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| provider | TEXT | NN |
| provider_account_ref | TEXT | NN |
| account_alias | TEXT | NN, UQ global untuk routing webhook |
| inbound_domain / sending_identity | TEXT | NN |
| credential_secret_ref | TEXT | NN, referensi secret di luar database |
| active | BOOLEAN | NN |

**Tenant 1 → N accounts**. Endpoint webhook memetakan account_alias ke tenant, lalu memverifikasi signature; alias bukan autentikasi. Provider namespace + provider_account_ref unik global pada konfigurasi mapping agar satu event tidak ambigu ke dua tenant.

### email_messages

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| case_id | UUID | NN, FK → cases |
| app_message_id | UUID | NULL sebelum materialisasi inbound, FK → messages |
| email_account_id | UUID | NN, FK → email_accounts |
| direction | TEXT | NN, inbound/outbound |
| rfc_message_id | TEXT | NULL sampai header tersedia; ID header Message-ID |
| in_reply_to | TEXT | NULL, header RFC bukan UUID aplikasi |
| references_header | TEXT[] | NN, default [] |
| reply_token_hash | TEXT | NULL, hash token reply-to |
| sender_address / recipient_address | TEXT | NN |
| recipient_contact_id | UUID | NULL inbound; FK → contacts untuk tujuan outbound |
| verification_state | TEXT | NN, pending/verified/rejected |
| delivery_status | TEXT | NULL inbound; outbound queued/sending/accepted/delivered/bounced/failed/delivery_unknown |
| provider_message_id | TEXT | NULL sebelum tersedia |
| provider_status_at | TIMESTAMPTZ | NULL |
| delivery_key | TEXT | NULL inbound; NN outbound, unik per account |
| content_ref | TEXT | NULL, lokasi payload privat atau field body dari app_message |
| resource_security_labels | JSONB | NN |

Partial UQ `(tenant_id,app_message_id)` saat terisi: **message 1 → 0..1 email_message**. Ini satu email per pesan aplikasi dan satu penerima per outbound dalam MVP; tidak ada CC/BCC massal. Partial UQ `(email_account_id,direction,provider_message_id)` saat provider ID terisi. RFC Message-ID tidak dipakai sebagai bukti identitas atau unique global yang bisa dikendalikan pengirim.

Thread token dibangkitkan server dan dicocokkan dalam account yang benar. Lookup RFC headers dibatasi account/case serta pengirim; duplicate header atau subject tidak membuka akses. Kontak penerima diverifikasi lagi sebelum send. Status provider diterima/delivered bukan bukti pelanggan membaca.

### email_inbox

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| email_account_id | UUID | NN, FK → email_accounts |
| provider_event_id | TEXT | NN |
| provider_message_id | TEXT | NN |
| event_type | TEXT | NN |
| email_message_id | UUID | NULL sebelum dicocokkan, FK → email_messages |
| signature_checked | BOOLEAN | NN, harus true pada inbox yang diterima |
| received_at | TIMESTAMPTZ | NN |
| processing_state | TEXT | NN, received/processing/processed/retry/review |
| content_reference | TEXT | NULL |
| payload | JSONB | NN, minimum dan tidak masuk log publik |

UQ `(email_account_id,provider_event_id)`. **Email message 1 → N inbox events**. provider_message_id tidak unique pada inbox karena accepted/delivered/bounced adalah kejadian berbeda. Materialisasi inbound dideduplikasi pada email_messages; processing inbox dan pembuatan app message dilakukan atomik. ACK dikirim setelah inbox tersimpan.

### outbox_jobs

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| kind | TEXT | NN, allowlist pekerjaan worker |
| case_id | UUID | NULL, FK → cases |
| email_message_id / email_inbox_id | UUID | NULL, FK → email_messages / email_inbox |
| action_draft_id | UUID | NULL, FK → action_drafts |
| dedupe_key | TEXT | NN, UQ per tenant |
| state | TEXT | NN, queued/running/retry/done/failed/review |
| attempts / max_attempts | INTEGER | NN, tidak negatif; max_attempts > 0 |
| next_attempt_at | TIMESTAMPTZ | NN |
| lease_token | UUID | NULL kecuali running |
| lease_until | TIMESTAMPTZ | NULL kecuali running |
| last_error_code | TEXT | NULL, kode tersanitasi |
| payload | JSONB | NN, identitas kerja minimum tanpa secret |

Kind tertentu mewajibkan FK yang sesuai: email_send memerlukan email_message_id; email_fetch memerlukan email_inbox_id. Jangan membuat `entity_id` polimorfik tanpa validasi relasi. Partial UQ `(tenant_id,email_message_id)` WHERE kind=email_send memastikan satu logical send job, bukan satu job baru setiap retry. body/source snapshot dibaca ulang secara terotorisasi, tidak disalin ke payload scheduler.

Claim row menggunakan lock singkat lalu lease, jaringan di luar transaksi claim. Update hasil harus cocok lease_token. delivery_unknown tidak dikirim ulang sebelum rekonsiliasi; tanpa dukungan provider, review petugas. Tidak ada klaim exactly-once pengiriman email lintas jaringan.

## 8 Knowledge versi dan embedding

### knowledge_documents

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| title | TEXT | NN |
| document_key | TEXT | NN, UQ per tenant |

### knowledge_versions

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| document_id | UUID | NN, FK → knowledge_documents |
| version | INTEGER | NN |
| source_uri | TEXT | NN, sumber resmi/terkelola |
| service_type | TEXT | NN, general/parking/lounge |
| visibility | TEXT | NN, public/staff |
| organization_id | UUID | NULL, FK → organizations untuk pembatasan internal |
| allowed_roles | TEXT[] | NN, default [] |
| status | TEXT | NN, draft/published/withdrawn |
| valid_from / valid_to | TIMESTAMPTZ | valid_from NN; valid_to NULL atau > valid_from |
| applicability | JSONB | NN, aturan kecocokan layanan/pesanan |
| policy_reference | TEXT | NULL, pemetaan versi kebijakan sumber |
| supersedes_version_id | UUID | NULL, FK → knowledge_versions pada document sama |
| content_hash | TEXT | NN |
| published_at | TIMESTAMPTZ | NULL sebelum diterbitkan |
| published_by | UUID | NULL, FK → principals |

UQ `(tenant_id,document_id,version)`. published wajib published_at dan published_by. Overlap masa berlaku tidak otomatis dilarang karena applicability dapat berbeda; layanan publikasi menolak konflik yang tidak dapat dipilih secara tegas. Withdrawal tidak mengubah kebijakan historis menjadi versi lain. **Document 1 → N versions**.

### knowledge_chunks

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| knowledge_version_id | UUID | NN, FK → knowledge_versions |
| chunk_index | INTEGER | NN, ≥ 0 |
| text | TEXT | NN |
| embedding_model_version | TEXT | NN |
| embedding | VECTOR(d) | NN |
| content_hash | TEXT | NN |

UQ `(tenant_id,knowledge_version_id,chunk_index,embedding_model_version)`. ACL efektif diwarisi dari knowledge_versions, bukan diisi model. Filter tenant/visibility/organisasi/role/status/applicability diterapkan pada query retrieval sebelum teks masuk model. **Version 1 → N chunks**.

Dimensi d dikunci bersama model embedding pada migration; bukan tipe berdimensi berubah bebas per baris. Penggantian model berdimensi lain memakai kolom/tabel indeks versi baru dan migrasi terencana. Vector index dipilih setelah ukuran dataset/retrieval diukur; tidak menganggap HNSW wajib sejak awal.

## 9 Lampiran rating dan audit

### attachments

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| case_id / draft_id | UUID | NULL, FK → cases / action_drafts; minimal satu terisi |
| uploader_principal_id / uploader_session_id | UUID | Tepat satu terisi, FK → principals / sessions |
| blob_key | TEXT | NN, UQ per tenant, lokasi privat |
| mime_type | TEXT | NN, allowlist JPG/PNG/PDF |
| byte_size | BIGINT | NN, > 0 dan ≤ 5 MiB untuk batas MVP |
| content_hash | TEXT | NN |
| scan_status | TEXT | NN, pending/clean/rejected |
| resource_security_labels | JSONB | NN |

Jika case_id dan draft_id terisi, keduanya harus merujuk pengajuan/kasus yang sama. Lampiran draf ditautkan ke hasil kasus secara atomik saat commit, tanpa mengubah pemilik. Maksimal tiga lampiran per pengajuan diperiksa dalam transaksi yang mengunci parent; CHECK per baris saja tidak membatasi jumlah saudara. Scan clean wajib sebelum diikutkan dalam pengajuan/outbound atau diberikan ke model. **Case/draft 1 → N attachments**.

### ratings

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| case_id / conversation_id | UUID | Tepat satu terisi, FK → cases / conversations |
| customer_principal_id / customer_session_id | UUID | Tepat satu terisi, FK → principals / sessions |
| score | SMALLINT | NN, CHECK 1 ≤ score ≤ 5; tanpa default |
| comment | TEXT | NULL |

Empat partial unique indexes untuk kombinasi case/conversation dan principal/session. API target_type/target_id/customer_scope dipetakan ke FK nyata di atas. Upgrade sesi memetakan rating milik anonim ke principal secara transaksional dan menyelesaikan duplikasi target tanpa menciptakan rating baru. Tidak memberi rating tidak membuat row; rating tidak mengubah status kasus.

### audit_events

| Kolom tambahan | Tipe | Aturan |
| --- | --- | --- |
| actor_principal_id / actor_session_id | UUID | NULL, FK → principals / sessions |
| actor_kind | TEXT | NN, customer/staff/system |
| action | TEXT | NN |
| subject_type / subject_reference | TEXT | NN, referensi audit bukan relasi operasional |
| request_id / trace_id | TEXT | NN / NULL |
| reason | TEXT | NULL atau wajib untuk aksi sensitif |
| metadata | JSONB | NN, tersanitasi |
| occurred_at | TIMESTAMPTZ | NN |

Audit append-only, tidak menyimpan credential/kode verifikasi/raw body email. subject_reference boleh teks karena audit juga merekam objek yang sudah dihapus atau perubahan konfigurasi, bukan untuk melakukan join otorisasi. **Principal 1 → N audit events**.

## 10 Label keamanan dan data turunan

resource_security_labels mempunyai bentuk tervalidasi, bukan instruksi AI:

```json
[{"source":"reference","resource_type":"transaction","reference":"TX-A-001","required_source_scopes":["payment_status:read"]}]
```

Sumber, jenis resource dan scope memakai allowlist. Backend membentuk label dari data yang dibaca. Case labels adalah gabungan kebutuhan semua konten privat; menambahkan observasi pembayaran meningkatkan pembatasan agregat kasus. Pesan, event, email, checkpoint dan lampiran tidak boleh memiliki pembatasan lebih lemah dari agregat yang terkait. Penghapusan label bukan operasi endpoint pelanggan/AI.

Untuk konten sebelum kasus dibuat, conversation/draft payload membawa label yang sama dan diwariskan saat commit. Draf memiliki labels dalam payload terstruktur atau kolom resource_security_labels pada migration; implementasi memilih satu representasi canonical dan adapter tidak boleh menjatuhkannya. Untuk konsistensi tabel final, gunakan kolom resource_security_labels JSONB NN default [] juga pada action_drafts.

Keanggotaan kasus dan seluruh grants sumber diperiksa ulang pada read, retrieval konteks model dan sebelum outbound. Bila sumber tidak dapat memeriksa grants, akses privat fail closed. RLS tenant saja tidak membuktikan otorisasi per pelanggan atau grants eksternal. generic_notification hanya template server tanpa data privat; tidak mendeklasifikasi isi kasus.

## 11 Sistem sumber terpisah

Service referensi memiliki database/logical store terpisah, credential berbeda, dan dataset fiktif. Aplikasi tidak melakukan join SQL lintas database ini. Source IDs pada app adalah referensi API.

| Tabel sumber | Kolom inti | Relasi |
| --- | --- | --- |
| source_subjects | tenant_id UUID, id UUID, verified_contact_mapping | Identitas sumber dipetakan setelah verifikasi server, bukan prompt. |
| service_policies | tenant_id UUID, id UUID, reference TEXT, version INTEGER, content JSONB, valid_from/to TIMESTAMPTZ | Policy 1 → N bookings; reference/version unik per tenant. |
| bookings | tenant_id UUID, id UUID, reference TEXT, service_type TEXT, status TEXT, start_at/end_at TIMESTAMPTZ, service_timezone TEXT, location TEXT, policy_id UUID, source_updated_at TIMESTAMPTZ | FK composite ke policy; reference UQ per tenant. |
| transactions | tenant_id UUID, id UUID, reference TEXT, booking_id UUID NULL, amount_minor BIGINT, currency TEXT, currency_exponent SMALLINT, status TEXT, refund_status TEXT NULL, source_updated_at TIMESTAMPTZ | Booking 1 → N transactions; booking nullable mendukung transaksi tanpa booking yang ditemukan; reference UQ per tenant. |
| source_resource_relations | tenant_id UUID, id UUID, subject_id UUID, booking_id UUID NULL, transaction_id UUID NULL, relation_type TEXT, scopes TEXT[], active BOOLEAN | Tepat satu target; FK composite ke subject/target; M:N akses resource melalui relasi. |

Policy/reference dan nilai status mengikuti source API contract. Nominal memakai integer minor units, currency dan exponent yang konsisten; jangan mengasumsikan semua mata uang dua desimal. Field source_resource_relations bukan fitur kuasa aplikasi; data owner/recorded_representative/beneficiary disiapkan oleh sumber/fixture. Pembayaran dan relasi tidak ditulis oleh backend app. Seed/admin source pengujian bukan endpoint pelanggan.

## 12 Pemetaan nama database dan API

| Nama di kontrak API | Representasi database |
| --- | --- |
| tenants.allowed_hosts | tenant_hosts.hostname |
| verified_email / verified_at | contacts.email_address / verified_at |
| staff roles / allowed_queue_ids | membership_roles / membership_queues |
| conversations.handler_staff_id | conversations.handler_membership_id, bukan principal_id |
| cases.assigned_to / assignee_id | cases.assigned_membership_id |
| booking_ref / booking_reference | cases.booking_reference |
| transaction_ref / transaction_reference | cases.transaction_reference |
| action result_ref | result_case_id dan result_message_id diproyeksikan sesuai action_type |
| email message_id pada pesan aplikasi | email_messages.app_message_id UUID FK → messages |
| email Message-ID header | email_messages.rfc_message_id TEXT |
| email provider ID | email_messages.provider_message_id TEXT |
| thread_token_hash | email_messages.reply_token_hash |
| provider_account_id | email_account_id FK; adapter memetakan provider_account_ref |
| jobs/outbox entity_id | FK eksplisit sesuai kind, bukan ID tanpa target |
| ratings target_type/target_id/customer_scope | FK case/conversation dan principal/session dengan CHECK |

Mapping diperiksa contract tests. Bentuk response tidak berubah hanya karena penyimpanan dinormalisasi. RFC header, UUID pesan aplikasi, dan ID provider tidak boleh saling dipakai sebagai pengganti.

## 13 Indeks untuk pola akses

PK/UQ otomatis menyediakan indeksnya. Tambahkan indeks FK dan query berikut sesuai pola aktual, bukan mengindeks semua kolom JSON secara membabi buta:

| Tabel | Indeks/pola |
| --- | --- |
| cases | `(tenant_id,queue_id,status,updated_at,id)` untuk antrean; `(tenant_id,assigned_membership_id,status,updated_at,id)` untuk petugas; `(tenant_id,owner_contact_id,updated_at,id)` untuk pelanggan |
| case_participants | UQ case/principal dan indeks `(tenant_id,principal_id,case_id)` untuk daftar kasus |
| conversations | `(tenant_id,customer_principal_id,updated_at,id)`; `(tenant_id,anonymous_session_id)`; `(tenant_id,case_id)` |
| messages | `(tenant_id,conversation_id,created_at,id)` dan `(tenant_id,case_id,created_at,id)`; read event memakai sequence, bukan timestamp saja |
| conversation_events | UQ tenant/conversation/sequence melayani polling cursor |
| case_history | UQ tenant/case/resulting_version dan `(tenant_id,case_id,occurred_at,id)` |
| sessions | token_hash lookup; expires_at untuk cleanup yang dibatasi batch |
| verification_challenges | `(tenant_id,contact_hash,purpose,created_at)` dan expires_at untuk limit/cleanup |
| email_messages | delivery_key UQ per account, provider message partial UQ; `(tenant_id,case_id,created_at,id)`; thread lookup account/token/hash |
| email_inbox | account/event UQ; `(tenant_id,processing_state,received_at,id)` |
| outbox_jobs | partial `(next_attempt_at,tenant_id,id)` untuk queued/retry; lease_until untuk running; dedupe_key UQ per tenant |
| knowledge_versions | `(tenant_id,document_id,version)` UQ dan filter status/service/visibility; applicability diperiksa sebelum retrieval |
| knowledge_chunks | version/model/chunk UQ; vector index terukur setelah filter ACL diterapkan |
| agent_checkpoints | tenant/conversation/key UQ dan urutan created_at/id untuk resume |

Pengujian query plan dan beban memakai ukuran dataset serta role runtime yang sama dengan deployment; jumlah koneksi pool dan polling juga dihitung.

## 14 RLS hak database dan batas otorisasi

Semua tabel tenant-scoped memakai ENABLE dan FORCE ROW LEVEL SECURITY dengan tenant context transaction-local. Policies memasang USING dan WITH CHECK yang menolak konteks tenant kosong/salah. tenants/tenant_hosts mempunyai jalur bootstrap terbatas untuk metadata publik, bukan izin membaca seluruh isi tenant.

Role migration terpisah dari runtime. Role API dan worker tidak memiliki superuser, owner, BYPASSRLS, DDL, TRUNCATE, atau hak mengubah policy. Keanggotaan/peran dan keamanan objek tetap diperiksa layanan domain. Pool menggunakan konteks di dalam BEGIN/COMMIT/ROLLBACK; tidak menyimpan tenant dalam variabel sesi yang dapat bocor pada peminjam koneksi berikutnya.

Scheduler lintas tenant hanya mengklaim metadata job melalui operasi terbatas. Jika memakai SECURITY DEFINER, fungsi punya owner terpisah, search_path tetap, privilege minimum, REVOKE PUBLIC, tidak menerima SQL bebas, dan hanya mengembalikan job ID/tenant/lease. Worker memuat payload setelah membuka transaksi tenant job. Tidak memberikan role worker akses SELECT lintas tenant untuk memudahkan antrean.

Policies dan repo filters tidak menggantikan pemeriksaan grants source. Source relation yang dicabut harus menghalangi pembacaan snapshot/riwayat terkait walau barisnya tetap ada. Audit teknis memakai role runtime yang sesungguhnya, termasuk query setelah exception dan koneksi pool dipakai tenant lain.

## 15 Integritas transaksi dan migration

| Operasi | Perubahan yang harus atomik |
| --- | --- |
| Konfirmasi pengajuan | Lock draft dan idempotency; verifikasi versi/hash/akses; consent, case/message, participant yang sah, history, labels, outbox dan result commit. |
| Accept/reassign/close | Lock case lalu conversation; version check; assignee/handler/mode/epoch dan history; notifikasi close satu logical action. |
| Reopen | Identitas/izin dan intent sah; status new atau in_progress sesuai assignee aktif; riwayat tetap. |
| Upgrade anonim | Challenge consumed sekali, sesi rotate/revoke, ownership dipindahkan, pending draft cancelled, rating dipetakan. |
| Inbound materialisasi | Dedup event/message, app message, review flag atau reopen yang sah, job lanjutan dan audit. |
| Worker claim/result | Claim row dan lease dalam transaksi singkat; hasil memakai lease fencing; network di luar lock. |
| Knowledge publish | Versi dan ACL tervalidasi, indeks siap, status publish dan invalidasi versi cache dicatat. |

CHECK tidak bisa sendiri memeriksa isi tabel lain. Gunakan composite FK untuk tenant/relasi yang dapat dinyatakan; gunakan constraint trigger deferred yang terfokus untuk invariant lintas tabel seperti handler/case, pesan/conversation, dan draft/consent. Layanan domain tetap melakukan pemeriksaan akses serta state transition. Semua mekanisme perlu test negatif, bukan sekadar komentar schema.

Pembuatan tabel yang saling mengacu dilakukan berurutan lalu ADD CONSTRAINT setelah keduanya tersedia; misalnya cases/conversations/messages dan challenge/case. Seed data memperhatikan siklus FK, bukan menonaktifkan RLS/constraint pada runtime. Tidak ada kebutuhan FK global ke UUID eksternal.

Urutan migration: tenant/identity → organisasi/membership → case/conversation → message/draft/consent/history → email/jobs → knowledge/checkpoint/attachment/rating/audit → FK lintas tabel, indeks dan RLS. Migration diuji dari database kosong dan upgrade versi sebelumnya. Expand/contract menjaga kompatibilitas rollback aplikasi; destructive migration tidak disisipkan dalam rollout biasa.

## 16 Pembuktian sebelum klaim production ready

Schema dianggap siap diimplementasikan setelah audit konsistensi; kualitas runtime dibuktikan lewat migration dan tes:

- SEC: akses pelanggan/tenant/role, perwakilan, RLS pool dan data turunan yang izinnya dicabut.
- WF: konfirmasi versi/hash, cancellation dan pengiriman ulang tanpa double commit.
- CHAT/CASE: accept melawan reassign/close, stale send, state dan handler yang konsisten.
- MAIL/REL: beberapa event satu email, materialisasi idempotent, worker restart/lease, dan send dengan hasil belum pasti.
- RAG: applicability, versi, visibility, organisasi dan indeks embedding.
- DELIVERY/PERF: migration, backup/restore, retention, query plan, resource, pool serta beban.

Hasil yang belum dijalankan dicatat sebagai belum diuji, bukan lulus. Nomor kasus ditutup atau email diterima provider bukan bukti kepuasan pelanggan maupun kesiapan production seluruh sistem.


## 17 Migration fondasi DB-02

Revision Alembic `0001_foundation` mencakup sembilan tabel: tenants, tenant_hosts, organizations, service_queues, principals, contacts, staff_memberships, membership_roles dan membership_queues. Tabel schema_migrations menyimpan revision; bukan entitas pengguna.

Seluruh ID memakai default gen_random_uuid(), timestamps memakai CURRENT_TIMESTAMP dan trigger updated_at. Batas nama 200 karakter, source_subject_ref 256, slug 63 dan hostname 253 diterapkan bersama pemeriksaan nonkosong/format. Email memakai batas 320 dan guard dasar; validasi email lengkap serta lookup canonical tetap milik layanan identitas, tanpa menghapus plus-tag/titik secara universal. Bahasa wajib array satu dimensi nonkosong berisi id/en tanpa elemen NULL. Timezone diperiksa ke pg_catalog.pg_timezone_names lewat trigger ber-search_path tetap.

Migration dijalankan melalui direct credential dalam satu transaksi, termasuk schema/version. Advisory transaction lock per schema mencegah dua runner meng-upgrade bersamaan. Migration gagal di-rollback; tidak menggunakan CREATE TABLE IF NOT EXISTS untuk menutupi drift. Tidak ada runtime/public access grant atau seed pelanggan; least privilege/RLS mengikuti DB-03. Downgrade destruktif tidak tersedia melalui CLI; koreksi schema membutuhkan recovery migration yang direview.


## 18 Implementasi fondasi RLS DB-03

Revision `0002_runtime_rls` memakai role LOGIN `airport_assistant_runtime` dengan NOINHERIT/NOSUPERUSER/NOBYPASSRLS/NOCREATEDB/NOCREATEROLE/NOREPLICATION, tanpa role induk, ownership app, CREATE schema/table atau TEMP. Provisioner memakai migration credential untuk membuat role dan mengganti hanya DATABASE_URL lokal secara internal setelah login baru diverifikasi. DATABASE_DIRECT_URL tetap terpisah. PUBLIC CREATE/TEMP pada database dan PUBLIC CREATE pada schema public dicabut untuk menutup hak yang diwarisi secara publik.

Sembilan tabel fondasi memakai ENABLE dan FORCE RLS, dengan USING/WITH CHECK yang relevan. Context `app.tenant_id`, `app.principal_id` dan `app.actor_kind` dipasang transaction-local dari DatabaseContext server, selalu direset saat transaksi dimulai. Missing/malformed context tidak membuka data. Ini bukan autentikasi; service AUTH harus membentuk context dari sesi yang sah. Pengguna/model tidak mendapat SQL, credential database atau kemampuan memilih context.

| Fondasi | Hak runtime yang tersedia |
| --- | --- |
| tenants / tenant_hosts / organizations / service_queues | Baca sesuai tenant. Tanpa context hanya lookup_tenant_host(host) yang mengembalikan UUID tenant aktif dari host aktif. |
| principals | Baca diri sendiri, update display_name sendiri. Tidak ada INSERT/DELETE atau perubahan source_subject_ref/active. |
| contacts | Baca kontak principal sendiri. Tidak ada bind/verifikasi/rebind melalui SQL runtime biasa. |
| staff_memberships / membership_roles / membership_queues | Baca membership diri sendiri atau dalam organisasi admin yang sah; mutasi hanya oleh principal staf aktif dengan membership admin aktif pada organisasi terkait. |

Creation/binding identitas dan tindakan operasional lain baru dibuka melalui use case/operasi terbatas pada AUTH. Kontrol RLS ini tidak otomatis memberi CS akses semua kontak pelanggan atau memberi admin akses isi kasus; kasus dan izin operasionalnya belum diimplementasikan.

Helper untuk role/grants dan bootstrap dimiliki role NOLOGIN `airport_assistant_policy_owner`, bukan role runtime dan bukan BYPASSRLS. Role ini mendapat SELECT hanya pada tabel metadata identitas yang diperlukan serta policy SELECT khusus, bukan DML. SECURITY DEFINER mempunyai search_path pg_catalog, tabel terqualifikasi, hasil bool/UUID terbatas dan PUBLIC EXECUTE dicabut. Runtime tidak menjadi anggota role owner. Lookup host tidak memberi data personal atau akses SQL bebas.

Startup pool menolak login privileged, owner, role dengan parent membership, CREATE/TEMP atau facade SET ROLE. Ini melindungi konfigurasi runtime; migration dan fixture administrator tetap terpisah. Isolasi fondasi diuji dengan login runtime sungguhan, termasuk commit/rollback/error/cancellation/timeout dan pergantian tenant melalui pool satu koneksi. Pengujian SEC untuk case/blob/email/agent tetap mengikuti fitur-fitur tersebut.

Mutasi membership/role/queue untuk principal aktor sendiri ditolak, termasuk ketika ia admin. Positive admin management hanya menarget principal lain dalam organisasi yang diizinkan. Runtime hanya boleh mengubah active/version pada membership, role pada membership_roles, dan queue_id pada membership_queues; tenant/principal/organization/membership target tidak dapat diretarget lewat UPDATE. Perubahan scope membutuhkan operasi eksplisit terhadap record yang sesuai, dengan pemeriksaan bisnis/audit pada fitur AUTH/admin berikutnya.

[Pola migration DB-04](migration_workflow.md) memperinci penambahan tabel berikutnya dan bukti upgrade; tidak menambah entitas produk pada tahap ini.


## 19 Implementasi AUTH-01

Revision0003_customer_auth menambahkan sessions, verification_challenges dan auth_rate_limits, seluruhnya dengan PK tenant/id, timestamp, ENABLE+FORCE RLS dan indexes untuk lookup/FK/expiry/window. Session dan challenge menyimpan digest HMAC64hex, bukan token/kode/key mentah. Role runtime tidak mempunyai direct SELECT/INSERT/UPDATE tabel auth; operasi berjalan lewat fungsi terbatas milik auth-owner NOLOGIN non-BYPASS. Kontak/principal di-bind hanya melalui verifikasi yang sah; tidak ada grant staf dalam fungsi tersebut.

Schema challenges AUTH-01 hanya purpose customer_login, origin_session_id dan binding_nonce_hash wajib karena flow ini browser-bound. Kolom case_id serta tujuan/verifikasi tanpa browser ditambahkan bersama fitur kasus/email; belum menjadi kolom atau flow yang bisa digunakan pada AUTH-01. Schema0007 AUTH-02 menyimpan provenance conversation/message, memindahkan ownership asal dan membatalkan pending draft bersama rotasi/revoke. Body/ID/sequence pesan tetap utuh; atribusi principal mempertahankan deduplikasi setelah login. Runtime tidak dapat menulis provenance dan auth-owner hanya mendapat metadata/update terfokus, bukan body pesan. [Adopsi anonim](anonymous_adoption.md) memuat kontrak executable.

Table auth_rate_limits mempunyai common columns serta scope TEXT(boot/contact/issue_ip/issue_session/verify_ip/verify_session), subject_hash TEXT64hex, window_start TIMESTAMPTZ dan hits INTEGER positif. UQ tenant/scope/subject_hash/window_start mendukung UPSERT atomic. Nilai window berasal dari waktu server dan hit tetap commit saat permintaan ditolak. Rate helper tidak dapat dipanggil runtime secara langsung.

Detail flow, konfigurasi dan batas proof berada di [authentication guide](authentication.md). Migration/provision owner dan session key disiapkan script lokal; key tidak disimpan pada tabel atau ditampilkan dalam output.


## 20 Implementasi AUTH-03

Revision0004_staff_auth menambah staff_credentials: PK `(tenant_id,principal_id)` sekaligus FK composite ke principals; contact_id UUID NN FK composite ke contacts dan UQ `(tenant_id,contact_id)`; password_hash TEXT NN Argon2id dengan panjang terbatas; credential_version UUID NN; active BOOLEAN NN; created_at/updated_at TIMESTAMPTZ NN. Satu principal mempunyai paling banyak satu credential lokal dalam tenant; kontak login tidak dapat dipindahkan lewat rotasi CLI. Kontak harus terverifikasi dan terikat principal tersebut saat provision/login/request.

sessions.staff_credential_version UUID NULL untuk anonymous/customer dan wajib terisi untuk staff melalui CHECK. UUID versi disalin pada login; trigger UPDATE credential selalu menghasilkan versi baru sehingga rotasi/deaktivasi tidak menghidupkan sesi lama. Tidak ada FK ke versi yang berubah; fungsi sesi membandingkan versi terbaru serta active status. Password/hash/version tidak masuk DTO.

Credential ENABLE/FORCE RLS, tanpa direct runtime grants. Auth-owner mendapat SELECT dan UPDATE(credential_version) untuk row lock; policy UPDATE USING(true) WITH CHECK(false) menolak penulisan. SECURITY DEFINER staff begin/finish/memberships memeriksa proof dan identitas, memakai fixed search_path serta private ACL. Rate menambah scope staff_ip,staff_contact,staff_session. Penulisan credential hanya oleh migrator; alur [staff authentication](staff_authentication.md) mencatat batas audit/SSO/MFA yang belum tersedia.

## 21 Implementasi CASE-02 — schema0008_case_commit

Tabel baru memakai PK `(tenant_id,id)` UUID, created_at TIMESTAMPTZ dan FK tenant; seluruh FK antardomain composite tenant. Panduan desain domain di bagian sebelumnya tetap menjadi target bertahap. Tabel berikut menggambarkan schema executable CASE-02.

| Tabel | Kolom dan relasi utama |
| --- | --- |
| cases | public_reference TEXT unique per tenant; origin_draft_id UUID FK action_drafts dan unique (satu draf paling banyak satu kasus); origin_conversation_id UUID FK conversations (conversation 1:N cases); created_session_id UUID FK sessions. Owner principal/session UUID XOR FK; owner_contact_id UUID FK wajib untuk principal; followup_contact_id UUID nullable, bila terisi sama dengan owner contact. organization_id/queue_id UUID FK composite queue+organisasi. summary/customer_report TEXT, category/service_type/language TEXT enum CHECK; referensi TEXT nullable. source_observations/resource_security_labels JSONB wajib []; status new, next_actor staff, version BIGINT. initial_message_id UUID FK deferred ke pesan milik kasus yang sama. |
| action_consents | draft_id UUID FK unique, case_id UUID FK; confirmed_version BIGINT, payload_hash TEXT, confirmed_at TIMESTAMPTZ, request_id UUID. Actor principal/session UUID XOR FK. Satu consent per draf; actor awal tidak ditulis ulang saat adopsi. |
| case_participants | case_id/principal_id UUID FK dan pasangan unique; scopes TEXT[] tepat case:read, relation_origin TEXT verified_case_contact, verified_at/revoked_at TIMESTAMPTZ. CASE-02 hanya menambah pemilik terverifikasi, bukan akses perwakilan baru. Case 1:N participants. |
| case_history | case_id UUID FK; actor principal/session UUID XOR FK; event_type created/identity_verified, previous_version/resulting_version BIGINT dengan selisih1. Unique case/resulting_version. Case 1:N history. |
| audit_events | case_id/action_draft_id UUID FK; event_type case_confirmed/identity_verified; actor XOR FK, request_id UUID nullable, metadata JSONB tepat {}. Unique draft/event_type. |
| outbox_jobs | case_id/action_draft_id UUID FK; kind case_created, state queued; dedupe_key TEXT unique tenant dan unique case/kind. payload JSONB tepat dua ID, labels[]; attempts INTEGER0, max_attempts INTEGER5, next_attempt_at TIMESTAMPTZ. Lease/worker transitions belum tersedia. |
| idempotency_records | Actor principal/session UUID XOR FK, created_session_id UUID FK; operation confirm_draft, key TEXT, request_hash TEXT64hex; draft_id/result_case_id/result_message_id UUID FK dan result_ref TEXT; state committed, response_code INTEGER201. Partial unique tenant/actor/operation/key. Case dapat memiliki beberapa receipt key dengan hasil sama. |

messages.conversation_id menjadi nullable, case_id UUID FK ditambah. CHECK mewajibkan tepat satu parent conversation atau case. Pesan snapshot awal kasus tidak memindahkan pesan percakapan asal. Unique sequence dan dedupe actor/client_message_id juga berlaku per kasus. Link percakapan live mengikuti CHAT-01.

action_drafts menerima state committed dan empat kolom hasil: result_case_id/result_message_id UUID FK, result_ref TEXT, committed_at TIMESTAMPTZ. Keempatnya wajib hanya ketika committed, selain itu null. case_id input tetap null pada scope ini. Hash tetap menyegel input awal; perubahan hasil tidak mengubah persetujuan. Trigger menolak mutasi terminal dan commit setelah deadline.

Ketujuh tabel baru ENABLE/FORCE RLS, PUBLIC revoked, runtime SELECT/INSERT saja. Policy memeriksa sesi aktif, tenant, owner dan labels kosong; child membaca izin parent. Constraint trigger deferred memeriksa graph lengkap dan kecocokan draf/case/pesan/consent/history/audit/outbox/receipt saat commit, termasuk null/hilang pada JSON. Runtime tidak dapat menghapus bukti setelah validasi. Index queue/status/created_at dan anonymous owner mendukung pencarian berikutnya; endpoint operasional tetap CASE-03.

Runtime mendapat UPDATE(name) pada organisasi/queue dan UPDATE(email_address) pada kontak hanya agar SELECT FOR SHARE sah; policy WITH CHECK(false) melarang actual UPDATE. Aturan penerima email tetap di modules/cases, bukan adapter. Helper auth_adopt_report_cases privat milik auth-owner hanya memindahkan metadata kasus umum anonim dengan proof auth_verify; runtime tidak mendapat EXECUTE dan auth-owner tidak mendapat SELECT body pesan.

Detail transaksi, izin, receipt, batas source-derived data dan uji konkurensi berada di [case_confirmation.md](case_confirmation.md). Scope ini tidak mengklaim assignment/closure, live chat, eksekusi outbox atau delivery email sudah tersedia.

# Konfirmasi pengajuan kasus — CASE-02

Backend menerima persetujuan eksplisit atas draf yang sudah ditinjau, lalu menyimpan satu pengajuan kasus. Ini pengajuan bantuan customer service: tidak membuat booking, mengubah pembayaran, mengirim email, atau menjalankan worker.

## Alur dan kontrak HTTP

1. Pelanggan membuat atau mengoreksi draf melalui CASE-01. Pratinjau memuat laporan, tujuan, bahasa, versi, dan hash.
2. UI mengirim `POST /api/v1/action-drafts/{id}/confirm` dengan cookie sesi, Origin yang diizinkan, CSRF, satu `Idempotency-Key`, serta body `{"expected_version":1,"payload_hash":"<64 hex lowercase>"}`. Model tidak mendapat tool konfirmasi.
3. Backend memeriksa sesi pembuat, tenant, pemilik, hash, versi, deadline, tujuan, dan kontak. Draf yang berubah harus ditinjau serta dikonfirmasi lagi.
4. Respons201 memuat `state=committed`, `case_id`, `message_id`, dan `result_ref` berbentuk `CASE-<32 hex>`. Retry yang sah mengembalikan hasil yang sama.
5. Bila respons hilang, UI memeriksa `GET /api/v1/actions/{draft_id}`. Receipt committed memuat versi persetujuan, case_id, result_ref, dan expires_at null. Receipt membuktikan pengajuan tersimpan; belum membuktikan petugas menerima atau menyelesaikannya.

Key berisi16–128karakter ASCII, diawali alfanumerik, sisanya alfanumerik atau `._:-`. Scope key adalah tenant, aktor dan operasi. Digest mengikat draft_id, versi dan hash. Key sama dengan request berbeda menghasilkan409 IDEMPOTENCY_CONFLICT. Beberapa key pada draf yang sama tetap menunjuk satu kasus. Retry memakai key semula; key baru bukan cara memperbaiki konflik.

## Siapa yang boleh mengajukan

Pelanggan terverifikasi dapat membuat `create_case` dengan email pada draf yang cocok dengan kontak terverifikasi miliknya. Email tidak ada menghasilkan422 CASE_CONTACT_REQUIRED; email yang tidak memenuhi syarat menghasilkan422 CASE_CONTACT_VERIFICATION_REQUIRED. Backend tidak memilih alamat tindak lanjut lain secara diam-diam.

`request_handoff` boleh tanpa email. Untuk pelanggan terverifikasi, kontak miliknya tetap dicatat sebagai identitas pemilik, sementara followup_contact_id tetap null tanpa pilihan email eksplisit. Pengguna anonim hanya boleh mengajukan handoff informasi umum, service general, tanpa referensi booking/transaksi atau email. Belum ada janji tindak lanjut di luar sesi untuk pengajuan anonim.

Referensi booking/transaksi dalam laporan adalah klaim pelanggan, bukan bukti status sumber atau hak akses resource. CASE-02 menerima laporan saja: source observations dan security labels harus kosong; attachments, existing-case actions dan add_case_information menunggu fitur terkait. Data privat dari sumber tetap memerlukan SRC-03B dan pengujian izin turunannya.

## Satu transaksi, satu hasil

Transaksi memakai gerbang sesi bersama. Lock key aktor mendahului conversation/draft; organisasi lalu queue dan kontak dibaca dengan SHARE lock. Tujuan harus masih aktif dan sama persis dengan pratinjau. Perubahan nama, service atau tujuan menghasilkan409 CASE_DESTINATION_CHANGED, tanpa reroute tersembunyi. Koreksi draf menampilkan tujuan baru sebelum persetujuan berikutnya.

Savepoint menyimpan case, pesan snapshot laporan, consent versi/hash/request ID, participant pemilik terverifikasi, history created, audit event, outbox case_created, hasil draf committed dan receipt idempotency. Constraint deferred memeriksa hubungan seluruh graph saat commit; unique constraints membatasi satu kasus per draf dan satu hasil per key aktor. Fault membatalkan seluruh graph. Deadline draf yang habis di tengah proses menghasilkan410 dan marker expired tersimpan; sesi yang hilang menghasilkan401 dan rollback.

Outbox hanya berisi ID kasus/draf, berstatus queued. Tidak berisi body laporan atau alamat email. Worker, lease, retry eksekusi dan pengiriman email mengikuti JOB/MAIL; queued bukan sent.

## Percakapan, ownership dan adopsi

Case menyimpan origin_conversation_id. Pesan awal kasus adalah snapshot laporan yang disetujui, dengan case_id terisi dan conversation_id null. Percakapan asal tetap mandiri. Link percakapan live, mode menunggu petugas dan polling event mengikuti CHAT-01; request_handoff saat ini mencatat permintaan tersebut.

Receipt committed mengikuti pemilik kasus saat ini. Browser lain dari principal yang sama dapat membaca receipt, tetapi tidak membuka pratinjau atau mengonfirmasi draf sesi lama. Preview, edit, cancel dan confirm tetap terikat sesi pembuat. Edit/cancel committed ditolak409 dengan result_ref. Hasil committed tidak kedaluwarsa bersama TTL draf.

Ketika anonim memverifikasi email dari sesi asal, AUTH-02 juga memindahkan kasus umum miliknya, metadata pesan snapshot dan provenance; menambahkan participant, history identity_verified dan audit. Semua perubahan berada dalam transaksi verifikasi yang sama. Consent lama tetap merekam aktor anonim; tidak dibuat consent/outbox/email baru. Pending draft tetap dibatalkan. Helper adopsi privat tidak dapat dipanggil runtime dan tidak membaca body pesan.

## Batas database

Migration `0008_case_commit` menambahkan graph kasus dan hasil draf, composite FK tenant, ownership XOR, unique dedupe dan ENABLE/FORCE RLS. Runtime hanya mendapat SELECT/INSERT pada graph; tidak dapat menghapus atau menulis ulang consent, riwayat dan receipt. Privilege UPDATE kolom konfigurasi/kontak hanya memungkinkan row lock; policy WITH CHECK(false) tetap melarang mutasi sebenarnya.

## Pengujian

Unit: `backend/tests/unit/test_case_rules.py`. Integrasi Neon: `backend/tests/integration/test_case_commit.py`, opt-in dengan RUN_NEON_AUTH_TESTS=1. Jalankan provisioning Neon satu rangkaian pada satu waktu, termasuk QA. Script memuat credential secara internal; jangan mencetak atau membuka file env.

Skenario mencakup graph lengkap, retry dan race key/draf, hash lama, cancel vs confirm, rollback fault, expiry di tengah transaksi, kehilangan respons HTTP setelah commit, isolasi tenant/browser/pemilik, kontak, destination lock serta penolakan mutasi konfigurasi, adopsi anonim dan receipt setelah login. Tes SQL langsung membuktikan orphan graph dan nilai JSON null/hilang ditolak saat commit.

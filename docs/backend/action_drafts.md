# Draf pengajuan sebelum konfirmasi

CASE-01 membuat pratinjau pengajuan. Pengguna dapat membaca isinya, mengoreksi, atau membatalkan sebelum tindakan bisnis dijalankan. Menyimpan draf belum membuat kasus, mengirim email, memanggil petugas, membuat booking atau mengubah pembayaran.

## Yang tersedia

| Endpoint | Fungsi |
| --- | --- |
| POST /api/v1/action-drafts | Membuat draf pada percakapan yang berhak diakses, dengan action_type dan payload laporan. |
| GET /api/v1/action-drafts/{id} | Membaca pratinjau, tujuan, versi, hash dan masa berlaku. |
| PATCH /api/v1/action-drafts/{id} | Mengganti seluruh payload dengan expected_version yang cocok. |
| DELETE /api/v1/action-drafts/{id} | Membatalkan draf pending secara persisten, tanpa menghapus barisnya. |
| GET /api/v1/actions/{id} | Membaca state pending/cancelled/expired dan result_ref null. |

Action type yang tersedia adalah create_case dan request_handoff untuk pengajuan baru pada percakapan mandiri. Existing case, add_case_information, attachments dan data sumber belum dapat disisipkan. Endpoint confirm serta state committed baru tersedia pada CASE-02 setelah AUTH-02; tahap ini tidak menerima consent atau menampilkan referensi kasus palsu.

Semua endpoint memerlukan sesi yang sah. Mutasi memakai HTTPS, Origin dan CSRF; respons privat memakai no-store. Sesi anonim boleh menyiapkan laporan tanpa harus membuktikan kepemilikan booking. Referensi dalam laporan adalah klaim pengguna, bukan hasil pemeriksaan sumber. Email dalam draf juga belum menjadi kontak terverifikasi atau penerima email yang berhak.

## Isi dan tujuan draf

Payload berisi category information/booking_issue/transaction_issue, service_type general/parking/lounge, summary1–200karakter dan customer_report1–4000karakter. Booking reference, transaction reference dan contact_email bersifat opsional. Attachment_ids harus kosong sampai alur upload/scan tersedia. Summary/laporan wajib berisi teks selain whitespace dan tidak memuat NUL. Referensi memakai format ASCII sumber yang disepakati; email memakai normalisasi yang sama dengan AUTH-01.

Payload PATCH adalah pengganti utuh, bukan deep merge. Menghilangkan field opsional mengosongkannya. Field tenant, aktor, status, label keamanan, source observations dan destination_queue_id tidak diterima dari client/model.

Backend memilih queue aktif pada organisasi aktif tenant yang sama, sesuai service_type. Jika tidak tersedia, backend mencoba queue general. Jika ada beberapa kandidat, UUID queue terkecil dipilih secara deterministik; ini aturan routing awal, bukan perkiraan antrean atau pemilihan petugas. Tidak ada queue yang sah menghasilkan503 DRAFT_DESTINATION_UNAVAILABLE, tanpa menyimpan draf. Operator harus menyediakan organisasi/queue sebelum fitur dipakai; migration tidak membuat mitra atau petugas fiktif di database aplikasi.

Tujuan tersimpan sebagai FK queue serta snapshot nama queue/organisasi. GET tidak memindahkan tujuan diam-diam ketika konfigurasi berubah. Koreksi menghitung tujuan lagi dan menampilkan snapshot baru, disertai versi serta hash baru. CASE-02 tetap harus memeriksa tujuan aktif dan izin terbaru pada saat commit.

Ringkasan dibuat server secara deterministik dalam bahasa percakapan. Ringkasan menampilkan jenis tindakan, kategori, layanan, summary, laporan, referensi, email dan nama tujuan. Response juga memuat payload terstruktur sehingga detail tidak hanya tersembunyi di dalam hash. Mengganti bahasa percakapan tidak mengubah pratinjau yang sudah tersimpan; koreksi berikutnya membuat ringkasan dengan bahasa percakapan saat itu.

## Versi, hash dan sesi pembuat

Draf terikat tenant, percakapan, aktor dan created_session_id. Untuk anonim, actor_session_id terisi. Untuk pelanggan terverifikasi, actor_principal_id terisi. Keduanya tetap terikat sesi pembuat: browser/sesi lain principal yang sama boleh memiliki akses ke percakapannya, tetapi tidak melanjutkan draf ini. Pemilik atau tenant lain mendapat404 yang sama seperti ID tidak ditemukan. Staf tidak mengakses draf pelanggan melalui endpoint ini.

Setiap koreksi menaikkan version, termasuk pengiriman ulang payload yang sama. Hash SHA-256 canonical mengikat payload lengkap, ringkasan yang ditampilkan, tujuan, bahasa, versi, tenant/draft/conversation/actor/session IDs serta waktu pembuatan dan expiry. Hash bukan token autentikasi dan tidak memberi izin akses. Data dari database diperiksa ulang terhadap hash sebelum dikembalikan.

Guard internal require_confirmation memeriksa integritas, pending state, deadline, expected_version dan payload_hash. Hash atau versi lama ditolak409. Guard ini hanya prasyarat untuk implementasi CASE-02; ia tidak menggantikan sesi/CSRF, ACL, grants sumber, routing, consent dan transaksi commit.

## Expiry dan pembatalan

Draf berlaku15menit menurut waktu database. Koreksi tidak memperpanjang expires_at. Hanya pending yang dapat berubah. Cancel berulang mengembalikan hasil cancelled yang sama. Draf terminal tidak bisa diedit atau dipulihkan menjadi pending; DELETE tidak menghapus data.

Expiry dinormalisasi saat draf diakses oleh sesi yang berhak. GET/status mengembalikan state expired. Edit/cancel yang terlambat mengembalikan410 DRAFT_EXPIRED, sambil tetap menyimpan transisi expired. Error tidak dilempar di dalam transaksi sebelum transisi itu berhasil disimpan. Deadline yang terlewati saat UPDATE berlangsung juga tidak menghasilkan500 atau menghidupkan ulang draf. Belum ada scheduler pembersihan global; lewatnya deadline sudah cukup untuk menolak konfirmasi meskipun state tersimpan belum dinormalisasi.

Sesi yang kedaluwarsa/revoked berbeda dari expiry draf. Gerbang sesi bersama BEHAV-01 memeriksa sesi saat transaksi dimulai, setelah lookup/lock wait dan sebelum hasil/commit. Hilangnya sesi menghasilkan401 dan rollback perubahan parsial. Kegagalan INSERT RLS dikonfirmasi setelah rollback; tidak semua error database dianggap kehilangan sesi dan tidak ada retry penulisan otomatis.

## Data dan transaksi

Migration0006_action_drafts menambahkan tabel dengan composite FK tenant untuk conversation, principal, session pembuat dan queue. Schema menerima pending/cancelled/expired, case_id NULL dan security labels[]. Payload hanya laporan pengguna, bukan fakta sumber; label kosong di sini tidak membuka data booking/pembayaran.

ENABLE/FORCE RLS memeriksa sesi aktif, tenant, aktor, sesi pembuat serta akses percakapan. Runtime tidak mendapat DELETE atau UPDATE ownership/action_type/conversation/expiry/labels. Trigger menahan perubahan draf terminal. Destination JSON harus mengacu FK queue yang sama.

Layanan bisnis berada di modules/drafts; repository menyediakan unit transaksi yang sama dengan percakapan. Urutan lock adalah sesi SHARE, conversation UPDATE, lalu draft UPDATE. Koreksi bersamaan dengan expected_version sama hanya memiliki satu pemenang. Cancel yang bersaing dengan koreksi tetap menghasilkan terminal state tanpa lost update. Tidak ada pemanggilan provider di dalam transaksi ini.

AUTH-02 memindahkan ownership percakapan yang terbukti dan membatalkan pending draft sesi asal secara atomik bersama verifikasi. Draf terminal dipertahankan; deadline yang sudah lewat menjadi expired. Draf tidak dipindahkan ke sesi baru atau dipakai ulang sebagai persetujuan. Lihat [adopsi anonim](anonymous_adoption.md); pengajuan baru memerlukan draf dan konfirmasi baru.

## Pengujian

Dari root proyek:

```powershell
$env:RUN_NEON_AUTH_TESTS='1'
.\local\db01-venv\Scripts\python.exe -m pytest backend/tests/integration/test_drafts.py -q --tb=short
Remove-Item Env:RUN_NEON_AUTH_TESTS
```

Tes menggunakan schema/role Neon sementara dan sender verifikasi terkontrol; credential dimuat script, tidak dicetak. Jalankan satu rangkaian setup Neon pada satu waktu, termasuk audit QA, karena provisioning role menyentuh ACL database bersama. Tes unit memakai backend/tests/unit/test_draft_rules.py. Skenario mencakup preview/koreksi/hash lama, session/tenant/owner, routing, expiry/cancel, race, integritas simpanan, restart, privilege runtime dan sesi yang habis di tengah operasi.

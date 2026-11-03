# Percakapan dan pesan dasar

BEHAV-01 membuat percakapan tersimpan tanpa agent. Pelanggan memilih bahasa Indonesia atau Inggris, lalu mengirim pesan. Respons `received` berarti pesan sudah tersimpan; belum berarti AI atau petugas menjawab, dan belum membuat kasus.

## Endpoint yang tersedia

Endpoint aktif bersama autentikasi ketika layanan conversations terpasang di composition. Mutasi memakai HTTPS, cookie sesi, Origin dan CSRF dari AUTH-01/AUTH-04. Pelanggan tidak menentukan tenant, pemilik, penulis pesan atau klasifikasi data.

| Endpoint | Input | Hasil |
| --- | --- | --- |
| POST /api/v1/conversations | language: id/en | 201: conversation_id, language, version, generation_epoch, last_sequence. |
| GET /api/v1/conversations/{id} | ID percakapan | Metadata terbaru untuk pemilik yang berhak. |
| POST /api/v1/conversations/{id}/messages | body dan client_message_id UUID | 201: message_id, sequence, processing_state: received. |
| PATCH /api/v1/conversations/{id}/language | language dan expected_version | Metadata baru, atau409 jika versi sudah berubah. |

Semua respons privat menggunakan `Cache-Control: no-store`. Query tambahan dan header claim identitas ditolak. UUID yang tidak valid/field tambahan/format salah menghasilkan422 tanpa memantulkan isi pesan. Bahasa harus termasuk daftar bahasa tenant, bukan hanya allowlist aplikasi.

POST percakapan belum memiliki kontrak idempotency key. Mengulang create dapat membuat percakapan baru. Deduplikasi di tahap ini berlaku untuk pengiriman pesan; klien harus menyimpan conversation_id dan client_message_id untuk retry pengiriman pesan.

## Pemilik dan batas akses

Percakapan mempunyai tepat satu pemilik. Untuk anonim, pemiliknya adalah sesi browser yang dibuat server. Untuk pelanggan terverifikasi, pemiliknya principal pelanggan, sehingga dua sesi sah principal yang sama dapat membaca metadata dan mengirim pesan pada percakapan tersebut. Sesi orang lain dan tenant lain mendapat404 yang sama seperti objek tidak ditemukan. Staf tidak memakai jalur pelanggan ini; akses percakapan petugas mengikuti tugas handoff nanti.

Ownership hanya berlaku untuk percakapan mandiri. Migration0005 menolak case_id terisi dan security labels sumber terisi. Repository membentuk proyeksi ownership_only hanya bila keduanya kosong. Kasus atau data sumber harus melewati gerbangnya sendiri ketika dihubungkan nanti; melonggarkan constraint schema saja tidak membuka akses lewat fallback ownership.

Pesan pada tahap ini selalu laporan pengguna (`source_kind=user_report`), channel web, visibility customer, author_type customer dan content_class private. Pesan pengguna bukan fakta booking/pembayaran dari sumber. Model/client tidak dapat mengubahnya menjadi source_fact, staff_reply, system atau generic_notification.

AUTH-02 pada schema0007 merotasi/mencabut sesi sekaligus memindahkan percakapan mandiri dari browser asal ke principal terverifikasi. Message IDs/sequence/body tetap utuh, retry tetap dedupe, provenance anonim tersimpan dan pending draft dibatalkan. Mengetahui ID percakapan/challenge atau kode pada browser lain tidak cukup. [Panduan adopsi](anonymous_adoption.md) menjelaskan batas sumber/kasus dan kegagalan atomik.

## Urutan, retry dan transaksi

Aturan bisnis berada di `modules/conversations/service.py`. Repository PostgreSQL menyediakan satu unit transaksi untuk setiap operasi. API hanya memvalidasi input HTTP, memeriksa proof sesi dan memanggil layanan tersebut.

Urutan mutasi adalah kunci sesi dengan SHARE, periksa sesi aktif, lalu kunci conversation dengan UPDATE. Nomor sequence dan kenaikan version disimpan bersama pesan dalam transaksi yang sama. Jika insert gagal, seluruh perubahan counter/version ikut dibatalkan. Dua pengiriman bersamaan pada percakapan yang sama diserialisasi oleh row lock. Percakapan berbeda tidak memakai satu lock global.

`client_message_id` dideduplikasi menurut tenant, pengirim dan percakapan. Pengirim anonim memakai session_id; pengirim terverifikasi memakai principal_id. Partial unique indexes menangani kedua bentuk tanpa celah NULL. Retry dengan ID dan body identik mengembalikan hasil tersimpan yang sama. ID sama dengan body berbeda menghasilkan409 MESSAGE_CONFLICT. ID tersebut boleh dipakai di percakapan lain; ia bukan kunci global.

Body disimpan persis sebagaimana diterima, panjang1–4000karakter, wajib mengandung isi selain whitespace dan tidak boleh memuat NUL. Sistem tidak mengubah tanggal, referensi atau nominal dalam teks. Bahasa pesan ditetapkan dari bahasa percakapan pada saat commit. Pergantian bahasa menaikkan version dan generation_epoch; pesan lama tetap berbahasa dan berisi semula. Mengganti ke bahasa yang sama dengan versi valid tidak menaikkan counter. Retry pesan lama tetap mendapatkan hasil aslinya setelah bahasa berubah.

Mode awal database bernama ai sesuai kontrak state machine, tetapi belum ada model/job AI yang dijalankan. Epoch adalah dasar guard generasi mendatang. Sequence saat ini mengurutkan pesan tersimpan; durable events, event cursor dan polling mengikuti CHAT-01, termasuk menjaga pemetaan histori pesan ke stream event. GET metadata tidak mengembalikan riwayat pesan atau berfungsi sebagai event polling.

## Pembuktian sesi dan RLS

DatabaseContext membawa session_id opsional dari identitas server. RuntimeDatabase mengatur app.session_id secara transaction-local, termasuk mengosongkannya ketika transaksi tidak mempunyai sesi. Koneksi yang dipakai ulang tidak mewarisi sesi request sebelumnya.

Fungsi SECURITY DEFINER `conversation_session_valid` dimiliki auth-owner NOLOGIN dengan search_path pg_catalog dan EXECUTE publik dicabut. Hasilnya hanya boolean. Ia memeriksa tenant, ID dan jenis sesi, principal, masa berlaku, revoke, serta principal/tenant aktif. Lock SHARE pada sesi mencegah logout/upgrade yang memerlukan UPDATE menyelesaikan pencabutan di tengah transaksi pesan yang sudah berjalan. Request setelah pencabutan ditolak, termasuk ketika membawa snapshot AccessIdentity lama. Ini tidak memberi hak kepada pelanggan untuk mengubah sesi atau identitas.

Lock tidak membekukan waktu kedaluwarsa. Layanan memeriksa sesi lagi setelah lookup/lock wait dan sebelum mengembalikan hasil atau menyelesaikan transaksi, termasuk replay pesan. Jika sesi expired di tengah operasi, respons401 dan perubahan dibatalkan. UPDATE tanpa baris diperiksa ulang, sehingga tidak dianggap hasil sukses. Bila INSERT ditolak RLS dengan42501, repository terlebih dahulu membatalkan transaksi, lalu melakukan satu pemeriksaan sesi baru. Hanya kehilangan sesi yang terkonfirmasi diterjemahkan menjadi401 oleh layanan bisnis; error privilege dengan sesi masih valid dan error database lain tetap error. Tidak ada retry penulisan otomatis.

Conversations dan messages memakai ENABLE/FORCE RLS dengan tenant dan pemilik. Runtime bukan owner, tidak mendapat DELETE, tidak dapat mengubah ownership/case/labels dan tidak mendapat UPDATE pesan. Referensi principal/session/conversation memakai FK composite tenant. UPDATE conversation hanya untuk bahasa, version, epoch dan counter; aturan versi/urutan tetap diperiksa layanan bisnis, bukan disimpulkan dari privilege SQL.

## Cara menguji

Dari root proyek, gunakan environment backend yang sudah terpasang:

```powershell
$env:RUN_NEON_AUTH_TESTS='1'
.\local\db01-venv\Scripts\python.exe -m pytest backend/tests/integration/test_conversations.py -q --tb=short
Remove-Item Env:RUN_NEON_AUTH_TESTS
```

Tes memuat credential secara internal, membuat schema/role pengujian terisolasi dan membersihkannya. Tidak perlu menampilkan atau membaca `.env` di chat. Sender verifikasi email dalam tes bersifat terkontrol; pengiriman provider nyata tetap MAIL-05.

Suite memeriksa ownership anonim/terverifikasi, batas tenant, RLS langsung, retry dan konflik bersamaan, urutan multi-koneksi, rollback counter, persistensi setelah aplikasi dibuat ulang, language/version, validasi input serta logout melawan pesan yang sedang berjalan. Unit tests juga membuktikan konteks case/source-bound ditolak oleh jalur ownership. Draft, kasus, agent, polling dan handoff belum menjadi hasil BEHAV-01.

Regresi expiry mencakup create, read metadata, alokasi counter, INSERT pesan, replay dan perubahan bahasa sebelum/sesudah UPDATE. Semuanya menuntut401 tanpa perubahan parsial pada pesan, version, epoch atau counter. Tes privilege langsung juga membuktikan runtime tidak dapat mengganti pemilik, menulis ulang pesan atau membuat laporan pengguna dengan klasifikasi source_fact.

# Riwayat anonim setelah verifikasi

AUTH-02 melanjutkan percakapan anonim setelah pengguna memverifikasi email di browser asal. Verifikasi yang berhasil memindahkan ownership percakapan ke principal pelanggan, merotasi cookie, mencabut sesi lama dan membatalkan pending draft dalam satu transaksi. Browser lain yang hanya mengetahui conversation ID, challenge ID atau kode tidak mengambil riwayat itu.

## Perubahan yang terlihat pengguna

Endpoint challenge/verify dan bentuk respons login tetap sama. Setelah verifikasi berhasil, browser menerima sesi customer baru. Conversation IDs, message IDs, sequence, client_message_id, isi dan bahasa pesan tetap sama. GET metadata memakai ownership principal yang baru, sehingga riwayat dapat dilanjutkan.

Version dan generation_epoch percakapan naik sekali pada saat adopsi. Klien perlu mengambil metadata terbaru sebelum mengirim perubahan yang memakai expected_version. Ini membatalkan snapshot versi/epoch lama; pengujian publication AI yang sebenarnya tetap mengikuti implementasi agent/handoff nanti.

Pesan lama diatribusikan ke principal yang telah diverifikasi. origin_session_id menyimpan asal anonimnya, dan conversation menyimpan adopted_from_session_id/adopted_at. Identitas asal tidak dihapus dari catatan internal, dan token browser tidak disimpan sebagai provenance. Dengan atribusi ini, retry client_message_id setelah login tetap menemukan message_id/sequence lama, bukan memasukkan pesan kedua.

Draf tidak diadopsi sebagai persetujuan. Pending draft sesi asal menjadi cancelled; pending draft yang deadline-nya sudah lewat dinormalisasi menjadi expired oleh guard lifecycle. Draf terminal tidak ditulis ulang. Hash, versi dan sesi pembuat tetap merekam pratinjau semula. Sesi customer baru mendapat404 untuk draf lama dan harus menyusun draf baru lalu memberi konfirmasi baru pada CASE-02. Cookie lama tidak lagi dapat mengakses riwayat atau melanjutkan tindakan.

## Riwayat yang boleh dipindahkan

Hanya percakapan anonim milik origin_session_id challenge yang dipindahkan, dalam tenant yang sama. Percakapan browser lain tidak diambil. Jika email sudah terkait principal aktif, riwayat asal ditautkan ke principal itu; percakapan existing tetap memiliki ID/version/epoch semula. Tidak ada penggabungan percakapan berdasarkan kesamaan teks atau email yang diketik tanpa bukti.

Setelah adopsi, sesi sah lain milik principal yang sama memiliki akses ownership normal ke percakapan tersebut. Ini berbeda dari mencuri kode challenge browser lain: sesi tersebut harus membuktikan login sendiri. Draf tetap terikat sesi pembuat dan tidak ikut terbuka antar sesi.

Scope AUTH-02 adalah percakapan mandiri. Case-bound atau source-labeled conversation tidak dibuka melalui fallback ownership. Function menolak upgrade bila menemukan percakapan asal yang membutuhkan penanganan akses tambahan. Saat ini constraint BEHAV-01/CASE-01 memang membatasi percakapan/draf ke konteks mandiri; perluasan kasus/grants harus ditangani bersama fitur terkait.

Verifikasi tidak membuat source_subject_ref, relasi perwakilan, grants booking/pembayaran, membership staf atau credential staf. Mapping sumber yang sudah ada tetap utuh. Email follow-up tanpa browser memakai alur MAIL-04 terpisah; tidak ada endpoint menerima conversation ID sebagai pengganti proof browser asal.

## Transaksi dan kondisi gagal

Migration0007 mengganti implementasi auth_verify dengan signature yang sama. Proof tenant, sesi anonim aktif, CSRF, challenge asal, nonce, kode HMAC, purpose, batas percobaan dan deadline tetap diperiksa sebelum adopsi. Tidak ada endpoint atau helper adopsi publik tersendiri.

Urutan lock adalah sesi asal UPDATE, challenge, kontak, percakapan menurut ID lalu draf menurut ID. Operasi pesan/draf yang sudah berjalan memegang session SHARE lebih dulu. Verifikasi menunggunya selesai; hasil yang sudah commit ikut dipindahkan. Operasi lama yang datang sesudah sesi dicabut ditolak401, termasuk bila membawa snapshot identitas lama.

Pembuatan/resolusi principal, verifikasi kontak, sesi pengganti, ownership percakapan, atribusi pesan, pembatalan draf, konsumsi challenge dan revoke sesi lama adalah satu hasil atomik. Error database di tengah proses membatalkan semuanya; tidak ada browser mendapat sesi baru sementara riwayat setengah berpindah.

Deadline diperiksa ulang setelah lock dan sebelum hasil berhasil. Subtransaction internal membatalkan seluruh perubahan identitas/adopsi jika sesi atau challenge habis di tengah proses. Rate/attempt accounting yang sudah sah tetap tersimpan di luar subtransaction. Sesi yang habis menghasilkan401; challenge yang habis menghasilkan422 seperti pemeriksaan awal. Error database lain tidak disamarkan menjadi keberhasilan verifikasi.

Ada batas statement/lock database; proses tidak melakukan network provider di dalam transaksi. Jika histori terlalu besar atau lock gagal diselesaikan dalam budget, transaksi gagal utuh, bukan memindahkan sebagian. Tahap ini tidak mengklaim pengujian beban histori tak terbatas.

## Batas kewenangan database

Auth-owner tetap NOLOGIN dan bukan role runtime. Grant baru hanya metadata yang diperlukan serta UPDATE ownership/provenance/message identity/draft state. Auth-owner tidak mendapat SELECT body pesan atau UPDATE isinya. Function memakai SECURITY DEFINER, search_path pg_catalog, tabel terqualifikasi, dan EXECUTE publik dicabut.

RLS auth-owner dibatasi transaction-local adoption tenant/session/principal yang diisi function dari proof yang sudah diverifikasi. Role runtime tidak memperoleh kebijakan itu. Mengisi app.adoption_* sendiri tidak menaikkan role atau membuka baris lain. Penanda ini hilang pada akhir transaksi; ia bukan tiket izin yang disimpan di pool/job.

Runtime tidak dapat menulis kolom provenance. INSERT conversation/message diperketat ke kolom DAO yang sah, tanpa mengubah bentuk API. FK provenance tetap composite tenant; atribusi pesan tetap memenuhi owner XOR dan index deduplikasi principal. ENABLE/FORCE RLS yang sudah tersedia tetap berlaku.

## Cara menguji

Dari root proyek:

```powershell
$env:RUN_NEON_AUTH_TESTS='1'
.\local\db01-venv\Scripts\python.exe -m pytest backend/tests/integration/test_anonymous_adoption.py -q --tb=short
Remove-Item Env:RUN_NEON_AUTH_TESTS
```

Suite memakai schema/role Neon sementara dan sender email terkontrol. Credential dimuat script, tidak dicetak. Jalankan setup Neon secara berurutan dengan audit QA agar perubahan ACL database pengujian tidak berbenturan.

Pengujian mencakup browser asal, kode/challenge dicuri, tenant lain, existing principal, source mapping tetap utuh, pending/terminal draft, retry pesan, replay verifikasi, race append vs upgrade, GUC spoof/column privileges, fault injection setelah sebagian mutasi, dan late expiry dengan rollback. Fixtures BEHAV-01/CASE-01 tetap menguji prefix schema0005/0006; suite ini menguji perilaku adopsi schema0007.

Pengiriman OTP provider nyata tetap MAIL-05. Normal API tetap mengembalikan503 bila sender belum tersedia; tidak ada kode debug atau bypass verifikasi untuk mendemokan adopsi.

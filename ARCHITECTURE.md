# Airport AI Assistant Architecture

Dokumen ini menjelaskan rancangan implementasi berdasarkan [PRODUCT_CONTRACT.md](PRODUCT_CONTRACT.md). Ini adalah spesifikasi desain, bukan laporan hasil pengujian. Kontrak data berada di [DATA_AND_API_CONTRACTS.md](DATA_AND_API_CONTRACTS.md); pembuktian kualitas di [TEST_PLAN.md](TEST_PLAN.md). Jika terjadi konflik scope, product contract menjadi acuan dan dokumen turunan diperbaiki.

## 1 Tujuan dan batas

Website multilingual Indonesia/Inggris menyediakan informasi umum bandara, bantuan booking parkir/lounge yang sudah ada, pengecekan kendala transaksi, pengajuan dan pelacakan kasus, chat petugas, serta email dua arah. Booking baru, perubahan booking, pemrosesan pembayaran/refund, dan pemberian kuasa tidak menjadi tindakan aplikasi.

Enam kelompok pengguna dilayani oleh portal pelanggan dan ruang kerja internal dengan akses berbasis peran. Tenant adalah organisasi pelanggan seperti Bandara A; organisasi penyedia dan antrean layanan berada di dalam tenant. Pengelola mitra dan pelaksana lapangan tidak memiliki portal tersendiri.

## 2 Komponen dan deployment

```text
Browser pelanggan / CS / supervisor / admin
                    |
             HTTPS reverse proxy
             /             /api/v1
      React + TypeScript       |
                        FastAPI modular
                          |    |     |
                   Neon PostgreSQL  |---- Booking/transaction reference API
                   + pgvector       |---- Hosted model API
                          |
                   PostgreSQL outbox/jobs
                          |
                   Worker dari codebase sama
                          |
                   Email provider <---- Email pelanggan
                          |
                 Webhook HTTPS --> FastAPI inbox
```

Frontend, API, dan worker adalah unit deployment; API dan worker berbagi modul domain, bukan microservices yang memiliki data bisnis masing-masing. Service sumber referensi dipisahkan untuk menunjukkan integrasi API baca dengan data uji; tidak diklaim sebagai integrasi vendor booking nyata.

| Komponen | Rancangan |
| --- | --- |
| Frontend | React/TypeScript, navigasi kategori, chat, kasus, dan antarmuka internal. |
| Backend | Python/FastAPI dengan validasi request dan layanan domain. |
| Agent | LangGraph sebagai orkestrator awal; checkpoint di PostgreSQL, izin tool diperiksa layanan domain. |
| Storage | PostgreSQL di Neon; migration portabel; pgvector untuk embedding knowledge. |
| Worker | Pekerjaan database dengan lease, percobaan terbatas, dan outbox transaksi. |
| Model | Adapter API hosted; konfigurasi model tidak disimpan dalam prompt pengguna. |
| Email | Adapter send/receive dengan receipt, thread mapping, signature verification, dan rekonsiliasi. |

Framework mengikuti kandidat product contract; versi dipin pada lockfile saat implementasi. Vendor model/email dikonfigurasi melalui adapter; pemilihannya tidak mengubah kontrak domain.

## 3 Modul dan kepemilikan data

| Modul | Tanggung jawab dan larangan |
| --- | --- |
| Identity/access | Verifikasi kontak, sesi pelanggan, identitas staf, tenant dan scope layanan. Tidak membuat relasi perwakilan. |
| Conversation | Pesan, urutan, bahasa, mode AI/antrean/manusia, reconnect. |
| Agent | Klarifikasi, retrieval, read tools, dan penyusunan draf tindakan. Tidak memiliki kredensial database bebas. |
| Knowledge | Versi informasi, persetujuan publikasi, masa berlaku, chunk dan retrieval terotorisasi. |
| Cases | Konfirmasi, pencatatan, status, assignment, reopen, audit, dan rating. |
| Source adapters | Membaca booking, pembayaran, kebijakan yang berlaku dan relasi sumber. Tidak menyediakan operasi tulis bisnis. |
| Mail/jobs | Email keluar, inbound inbox, pengambilan isi, retry dan delivery status. |
| Observability | Trace lintas request/job, metrik dan catatan keputusan tanpa menyimpan rahasia. |

Database aplikasi adalah acuan kasus/percakapan; source API adalah acuan booking/pembayaran/relasi pesanan. Snapshot sumber memuat waktu observasi dan tidak diubah menjadi status pembayaran baru. Laporan pengguna, hasil sumber, dan keputusan petugas disimpan dengan asal yang berbeda.

## 4 Identitas dan isolasi tenant

Informasi umum memakai sesi anonim ber-token acak; riwayat anonim hanya tersedia dalam sesi itu. Pengetahuan nomor percakapan tidak memberikan akses. Pemulihan riwayat lintas perangkat memerlukan verifikasi kontak dan hubungan peserta.

Pelanggan memverifikasi email dengan kode sekali pakai melalui kanal verifikasi, lalu mendapat sesi HttpOnly/Secure dengan masa berlaku dan pencabutan server-side. Ini bukan OTP pembayaran. Respons challenge tidak membocorkan apakah email/booking ada; TTL, batas percobaan dan rate limit dikonfigurasi dan diuji. Kode tidak masuk model/log. Tidak ada kewajiban membuat password pelanggan.

Upgrade sesi anonim hanya mengikat percakapan yang dimiliki token sesi anonim yang masih dibuktikan pada challenge dan verify di browser yang sama. Transaksi verify merotasi token, menautkan percakapan ke principal tenant yang diverifikasi, mencabut akses token anonim lama, dan membatalkan draf pending agar disusun ulang dengan konteks akses baru. Riwayat yang sah dipertahankan; consent lama tidak dibawa. ID percakapan atau kode dari browser lain tidak cukup untuk mengklaim riwayat. Verifikasi kontak lewat email tanpa sesi browser terkait tidak mengadopsi percakapan anonim.

Staf memakai identitas terverifikasi dan membership yang dibuat admin melalui mekanisme autentikasi staf terpisah dari login pelanggan. Verifikasi email pelanggan tidak dapat menaikkan peran menjadi CS. Mutasi membership dicegah dari eskalasi privilege sendiri; audit menyimpan pemberi dan penerima izin. Akun provisioning pertama dibuat melalui perintah administratif, bukan endpoint publik.

Tenant situs dipetakan dari host/konfigurasi server. Pengguna tidak menentukan tenant efektif lewat prompt atau body. Backend memvalidasi sesi/membership terhadap tenant situs; konflik ditolak. Tidak ada UI admin lintas tenant dalam MVP.

Setiap request dan job memiliki konteks tenant dan aktor yang diverifikasi. Semua query menggunakan scope tenant dan izin objek. Data multi-tenant memakai tenant_id, composite foreign keys, serta Row-Level Security dengan default deny. Role aplikasi/worker bukan owner, superuser, atau BYPASSRLS; tabel memakai FORCE ROW LEVEL SECURITY. Migrasi memakai kredensial terpisah.

Pada pooled connection, konteks tenant dipasang transaction-local di dalam transaksi eksplisit dan hilang saat commit/rollback. Tidak memakai SET sesi sebagai penyimpanan identitas. Pengujian memakai kredensial runtime, termasuk setelah error dan pergantian tenant pada pool.

Role scheduler hanya dapat mengklaim metadata job melalui operasi terbatas. Payload pelanggan dibaca worker setelah memasuki konteks tenant job yang tervalidasi. Model tidak dapat memilih tenant job, membership, atau checkpoint tenant lain. Public reference bukan otorisasi.

Pemesan perwakilan hanya mengakses sesuai relasi dan scope sumber yang aktif. Verifikasi email membuktikan penguasaan kontak; source API tetap menentukan hubungan pesanan. Akses booking tidak otomatis berarti seluruh data pembayaran boleh dilihat.

Hak case participant tidak menghapus batas sumber. Semua salinan data sumber pada summary, pesan, attachment, checkpoint dan source_observations memiliki label resource_reference dan required_source_scopes yang diwariskan pada agregat kasus. Untuk MVP pelanggan membaca kasus privat sebagai satu agregat hanya jika menjadi peserta dan masih memiliki seluruh grants sumber yang diwajibkan isi kasus. Jika satu grant tidak cukup, akses agregat privat ditolak; tidak memakai model untuk menyensor free text secara ad hoc. Kasus umum tanpa sumber memakai kepemilikan kontak kasus saja.

Izin tersebut diperiksa ulang pada case/event/blob read, penyusunan konteks model, dan sebelum email outbound dikirim, bukan hanya saat snapshot dibuat. Jika sumber gagal mengecek grants, akses privat fail closed. Pencabutan relasi membuat riwayat turunannya tidak lagi dapat dibaca pelanggan itu. Email berisi detail hanya dibuat dari proyeksi yang diperiksa untuk penerima; job tertunda melakukan pemeriksaan ulang sebelum send. Pesan pelanggan dan draf petugas yang mungkin memuat rincian sumber mewarisi batas kasus, bukan dianggap aman karena berlabel customer-facing. Petugas tetap menangani dengan scope operasionalnya dan dapat mengirim penjelasan generik tanpa data privat kepada kontak terverifikasi.

## 5 Alur agent dan konfirmasi

1. Simpan pesan dan periksa tenant, sesi, mode percakapan serta batas penggunaan.
2. Tentukan kebutuhan; menu/form dapat menyelesaikan tindakan pasti tanpa LLM.
3. Retrieval atau tool read hanya melalui backend terotorisasi. Referensi kebijakan disertakan pada jawaban yang memuat ketentuan.
4. Jika perlu mengirim informasi atas nama pelanggan, backend membuat action draft dengan payload, tujuan, versi dan expiry.
5. Pelanggan melihat ringkasan dan mengonfirmasi versi tertentu melalui UI. Ucapan persetujuan di chat diarahkan ke kontrol konfirmasi; model tidak membuat bukti persetujuan sendiri.
6. Backend memeriksa ulang akses, versi draf dan mode; transaksi mencatat tindakan, kasus/pesan, audit serta outbox.
7. Hasil berasal dari pencatatan server. Timeout tidak dianggap sukses/gagal sebelum status aksi diperiksa.

Koreksi payload membatalkan konfirmasi sebelumnya. Model dapat menyusun draf, tidak memanggil endpoint commit sebagai aktor pelanggan. Draf CS untuk terjemahan juga tidak mengirim otomatis; staf meninjau dan menekan kirim.

## 6 Chat petugas dan konsistensi antarreplika

Pesan serta perubahan mode tersimpan sebelum diakui ke browser. Browser mengirim pesan melalui HTTP dan membaca event durable dengan cursor polling; tidak bergantung pada sticky session, LISTEN/NOTIFY pooled, atau Redis. Interval polling dikonfigurasi berdasarkan profil beban. Event diperiksa otorisasinya setiap request, memakai urutan monoton per percakapan, dan dapat dibaca ulang tanpa menggandakan pesan.

Mode percakapan: ai, waiting_staff, human, ended. Status ini terpisah dari status kasus. Handoff pada kasus baru memakai draf terkonfirmasi; kasus yang sudah ada dihubungkan hanya jika aktor berhak. Kategori dan ringkasan singkat cukup untuk akses langsung petugas tanpa percakapan AI. Penutupan kasus mengakhiri percakapan terkait menjadi ended; kelanjutan masalah dapat melalui email atau reopen dengan handoff baru.

Penerimaan petugas memeriksa versi percakapan dan kasus dalam satu transaksi. Kasus new yang belum ditugaskan berubah menjadi in_progress dan assigned_to petugas penerima; penerimaan pada kasus milik petugas lain ditolak kecuali supervisor lebih dahulu melakukan penugasan ulang yang diaudit. Case assignee dan petugas percakapan aktif harus konsisten. Reassignment saat chat aktif memindahkan handler dan menambah epoch secara atomik; petugas lama tidak dapat mengirim pesan baru. Dua penerimaan bersamaan hanya memiliki satu pemenang.

Accept pada closed case ditolak sampai reopen yang sah. Close memeriksa versi kasus dan percakapan, mengakhiri handler aktif serta menaikkan epoch secara atomik. Accept/reassign/close mengambil urutan lock yang sama pada kasus lalu percakapan; stale version tidak mengubah sebagian state. Tidak ada jalur close yang menyalakan AI kembali.

Mulai waiting_staff, hentikan penjadwalan jawaban AI baru. Saat mode berubah, generation_epoch bertambah. Jawaban AI yang sedang dibuat harus memeriksa epoch dan mode lagi saat commit; hasil lama dibuang. Dalam mode human hanya petugas handler yang berwenang mengirim jawaban pelanggan.

Reconnect mengulang event setelah cursor dengan akses terverifikasi. Kehilangan koneksi tidak menghapus kasus atau menganggap selesai. Saat offline, kontak tindak lanjut diminta dan pengajuan dicatat setelah konfirmasi. Kontak yang belum ada tidak dijanjikan menerima balasan luar sesi.

## 7 Kasus dan aturan mutasi

Status domain: new, in_progress, awaiting_customer, awaiting_external, closed. CS dan supervisor berwenang mengubahnya dengan version check dan audit. Pending status menyebut informasi yang ditunggu, penanggung jawab serta next_actor. Penutupan manual mewajibkan alasan dan hasil yang disampaikan lewat chat atau outbox email.

Balasan terverifikasi yang jelas meminta kelanjutan masalah sama membuka kasus sesuai product contract; pesan terima kasih tidak otomatis reopen. Tombol lanjut memberi intent eksplisit; balasan email ambigu ditandai needs_staff_review dan diangkat pada antrean CS untuk klasifikasi. Model boleh memberi saran, tidak menjadi penentu keputusan penutupan atau pengelompokan kasus secara bebas. Kebutuhan berbeda memerlukan pengajuan baru yang dikonfirmasi.

Tidak ada jaminan deduplikasi semua laporan semantik. Deduplikasi teknis menggunakan action ID, message ID dan idempotency key; kasus terkait ditawarkan untuk dilanjutkan tanpa membuka kasus orang lain.

## 8 Email dan pekerjaan latar belakang

Kasus, pesan outbound, audit, dan job ditulis dalam satu transaksi. Worker mengklaim job dengan row lock singkat, lease_token dan lease_until; jaringan dipanggil setelah transaksi claim selesai. Pembaruan hasil wajib memeriksa lease_token agar worker lama tidak menimpa hasil baru. Retry memakai backoff, jumlah percobaan terbatas dan penanda pemeriksaan petugas saat habis.

Satu pesan memiliki stable delivery key ke provider. Jika send mungkin diterima tetapi respons hilang, status menjadi delivery_unknown. Rekonsiliasi dengan provider receipt/status atau idempotency resmi dilakukan sebelum pengiriman ulang. Provider tanpa mekanisme itu tidak dijanjikan exactly-once; pengiriman unknown ditahan untuk pemeriksaan petugas. Provider accepted, delivered, bounced dan failed dipisahkan; delivered bukan bukti dibaca.

Inbound webhook diverifikasi signature dan jendela timestamp sebelum ditulis ke inbox durable. Mapping tenant berasal dari konfigurasi akun/domain provider, bukan subject email. Inbox deduplikasi berdasarkan provider_account dan provider_event_id; satu provider_message_id boleh memiliki beberapa event lifecycle yang berbeda. Tabel pesan deduplikasi konten inbound menurut provider_account, direction dan provider_message_id agar satu email tidak menjadi beberapa pesan kasus. Event delivered/bounced yang baru tidak boleh terbuang karena message_id sudah ada. ACK hanya setelah inbox commit; body/attachment diambil worker bila provider mengirim metadata saja.

Thread dipetakan menggunakan token reply-to acak dan referensi pesan server. Subject atau nomor kasus saja tidak cukup. Token thread bukan bukti identitas; sender dan bukti autentikasi email diperiksa. Forward/From asing tidak mendapatkan akses otomatis. Untuk pesan yang perlu verifikasi, kirim challenge tanpa data pribadi ke kontak yang berhak dan izinkan balasan kode melalui email; portal tidak wajib dibuka. Pesan belum terverifikasi tidak memicu data read, perubahan izin, atau reopen otomatis.

Attachment sebagai bukti dibatasi ukuran/tipe, diperiksa, tidak dieksekusi atau dimasukkan ke prompt sebagai instruksi. Blob berada di penyimpanan privat terenkripsi dengan metadata tenant dan kontrol download; tidak pada filesystem pod yang tidak persisten. Provider storage dipilih pada konfigurasi deployment. Isi kasus tidak dibawa dalam log delivery.

## 9 Knowledge dan model

Admin menerbitkan versi dengan source, visibility, service, valid_from/valid_to, supersedes dan applicability. Chunk mewarisi ACL/tenant; filter dilakukan sebelum hasil diserahkan ke model. Dokumen draft tidak muncul. Untuk booking lama gunakan policy reference sumber; jika tidak tersedia, jangan menebak kebijakan historis.

Cache retrieval/jawaban tidak menyimpan data pelanggan lintas sesi; key publik mencakup tenant, visibility, bahasa dan versi knowledge. Publikasi mencabut cache versi lama. Pergantian versi embedding dilakukan dengan indeks/version yang dapat ditelusuri.

Instruksi tool dan guardrails berasal dari aplikasi. Dokumen/email/output tool dianggap data tidak tepercaya. Tool keluaran hanya field yang diizinkan; URL pembayaran berasal dari allowlist sumber dan tidak diambil dari instruksi pengguna. Tool dibatasi jumlah langkah/waktu, tanpa arbitrary SQL atau URL fetch. Jika retrieval tidak memadai, jawab keterbatasan dan tawarkan handoff.

Checkpoint tidak menjadi sumber status booking atau bukti otorisasi. Checkpoint tenant/conversation disimpan dengan versi, data minimum, dan pembacaan ulang akses saat resume. Pemanggilan model yang gagal tidak mengulang side effect yang sudah committed.

## 10 Operasi dan deployment

Rancangan deployment memakai VPS Linux untuk frontend dan API pada origin yang sama melalui HTTPS. AWS Lightsail menjadi pilihan VPS dalam rancangan ini, K3s mengelola API dan worker, dan Neon menyediakan PostgreSQL di luar cluster. Pemulihan proses, penempatan replika, dan backup database menjadi bagian dari operasi layanan.

API liveness memeriksa proses; readiness memeriksa dependensi inti untuk melayani request tanpa memanggil model setiap probe. Email outage tidak mematikan seluruh portal; pekerjaan ditahan dan status terlihat. Shutdown menghentikan penerimaan kerja baru dan menyelesaikan/menyerahkan lease secara aman. Resource requests/limits mengikuti pengukuran, bukan asumsi minimum cluster saja.

Migration memakai direct connection Neon dan langkah terkontrol sebelum rollout. Runtime menggunakan pool terbatas. Perubahan schema memakai expand/contract agar rollback aplikasi tetap mungkin; migrasi destruktif tidak dilakukan bersamaan dengan rollout. Secret bukan teks publik di repository; cluster secrets memerlukan pembatasan akses dan perlindungan at rest pada konfigurasi deployment.

Rancangan pipeline rilis mencakup lint/type/schema, pengujian domain, isolasi dan kontrak, serta build image dengan versi/digest. Deployment dilengkapi smoke check dan regresi pada konfigurasi rilis.

## 11 Observability dan data lifecycle

Trace menghubungkan request, conversation, action, case, job dan provider receipt menggunakan identifier internal. Token verifikasi, credential, raw email dan isi sensitif tidak masuk log umum. Audit berisi aktor, tenant, perubahan, versi dan alasan dengan akses terbatas. Tidak menyimpan penalaran internal model sebagai bukti kualitas.

Metrik: latency per tahap, error, penggunaan token, perkiraan biaya, kedalaman/umur antrean, retry, delivery_unknown, handoff dan penolakan akses. Rating adalah umpan balik opsional, bukan status penyelesaian.

Retensi ditetapkan per kelas data dan tenant melalui konfigurasi administratif teknis; penghapusan mencakup pesan, checkpoint, embedding, blob dan cache. Backup mengikuti masa retensinya dan prosedur penghapusan ulang setelah restore. Konfigurasi deployment menetapkan lokasi pemrosesan, retensi, dan akses vendor.

Backup database dan blob disimpan dengan akses terpisah. Restore dilakukan ke lingkungan terisolasi dengan email outbound dinonaktifkan dan job dikarantina; kiriman lama tidak diputar ulang. Uji restore memeriksa tenant, riwayat serta cursor pesan, bukan hanya keberhasilan impor database.

## 12 Urutan pengerjaan

1. Fondasi schema, tenant, akses dan source reference API.
2. Kasus, konfirmasi, idempotency, status, audit dan outbox.
3. UI pelanggan/internal, polling event dan handoff tanpa AI.
4. Email dua arah dengan pencocokan identitas serta riwayat kasus.
5. Knowledge, tool read dan agent dengan checkpoint serta konfirmasi.
6. Evaluasi tiga alur, keamanan, beban, biaya dan manfaat AI terhadap baseline.
7. Deployment VPS/K3s, pengujian restart/rollback/restore dan pengujian ulang rilis.

Target, kondisi eksekusi dan bukti pengujian mengikuti TEST_PLAN.md.

## Referensi teknis

- [PostgreSQL Row Security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html): default deny, owner/BYPASSRLS dan FORCE RLS.
- [Neon connection pooling](https://neon.com/docs/connect/connection-pooling): transaction pooling dan batas fitur sesi.
- [Kubernetes Deployments](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/): rollout dan revisi deployment.
- [Resend receiving](https://resend.com/docs/dashboard/receiving/introduction) dan [isi email](https://resend.com/docs/dashboard/receiving/get-email-content): contoh kontrak provider, bukan penetapan vendor.

# Rencana tugas backend Airport AI Assistant

Dokumen ini mengurutkan pekerjaan backend: apa yang dibuat, apa prasyaratnya, dan bukti apa yang dibutuhkan sebelum tugas disebut selesai. **BE-01, BE-02, BE-03, DB-01, DB-02, DB-03, DB-04 dan AUTH-01 selesai**; **AUTH-03 selesai**; **AUTH-04 selesai**; **SRC-01 selesai**; **SRC-02 selesai**; tugas lainnya **belum mulai**. Status implementasi dan bukti pemeriksaan dicatat di bagian akhir dokumen.

Acuannya adalah [product contract](../../PRODUCT_CONTRACT.md), [arsitektur sistem](../../ARCHITECTURE.md), [arsitektur backend](backend_architecture.md), [schema database](database_schema.md), [kontrak data dan API](../../DATA_AND_API_CONTRACTS.md), serta [test plan](../../TEST_PLAN.md).

## Cara memakai rencana ini

Setiap ID di bawah merupakan pekerjaan yang bisa direview sendiri. Saat tugas dimulai, catat status, perubahan kode, perintah pemeriksaan, hasil, dan commit atau PR terkait. Bila suatu tugas masih terlalu besar, pecah menjadi subtugas dengan ID turunannya sebelum mengerjakan, tanpa mengubah tujuan atau kriteria selesai.

Status tugas: **belum mulai → dikerjakan → menunggu review → selesai**. Gunakan **terhambat** jika akses atau prasyarat belum tersedia, dengan alasan dan langkah berikutnya. Terhambat tidak berarti lulus. Revisi setelah review kembali menjadi dikerjakan.

Kode yang sudah ditulis belum otomatis selesai. Tugas selesai ketika perubahan dapat dijalankan, pemeriksaan yang relevan lulus dengan bukti, dan temuan materialnya ditutup. Penerimaan fitur lintas komponen tetap menunggu pengujian integrasi atau end-to-end yang diperlukan.

## Aturan yang berlaku di semua tahap

- API dan worker memanggil layanan bisnis; keputusan izin dan tujuan email tetap di modul bisnis.
- Konektor provider tidak memegang aturan hak akses. Model tidak mempunyai jalur untuk melewati consent atau mengubah data bisnis sumber.
- Setiap tabel dan jalur runtime menjaga tenant serta akses objek. Pemeriksaan dilakukan juga pada data turunan, pekerjaan worker, dan konteks model.
- Manifest dependency, lockfile, konfigurasi migration dan perintah menjalankan komponen dibuat bersama implementasi executable. Jangan menambah banyak konfigurasi kosong.
- Tes backend berada di `backend/tests/`; source di `reference_service/tests/`; frontend di `frontend/tests/`; alur lintas komponen di `tests/e2e/`. Evaluasi model/RAG berada di `evaluations/`.
- Data awal fiktif dan credential tersimpan di luar Git. Tidak ada pembelian akun, paket, domain atau provisioning berbayar tanpa persetujuan pengguna.
- Jangan mengklaim integrasi provider nyata jika hanya adapter palsu atau reference service yang diuji.
- Tidak membuat booking baru, mengeksekusi pembayaran/refund, atau menambah fitur pemberian kuasa.

## Ringkasan urutan

| Tahap | Hasil yang dicapai | Bergantung pada |
| --- | --- | --- |
| 1 | Backend dapat dijalankan dan diperiksa | Dokumen dan scaffold |
| 2 | Database, migration, dan fondasi isolasi | Tahap 1 |
| 3 | Sesi, identitas, membership dan akses | Tahap 2 |
| 4 | API sumber referensi serta konektor baca | Tahap 2 dan identitas tahap 3 untuk integrasi |
| 5 | Percakapan dasar, draf, konfirmasi dan kasus | Tahap 3–4; kasus umum dapat dibuat lebih dahulu |
| 6 | Penanganan petugas dan handoff konsisten | Tahap 5 |
| 7 | Worker dan email dua arah lengkap | Tahap 3, 5–6 |
| 8 | Knowledge dan retrieval terotorisasi | Tahap 2–3 |
| 9 | Agent memakai layanan bisnis yang sudah teruji | Tahap 4–6 dan 8 |
| 10 | Kelengkapan pelacakan, internal dan alur MVP | Tahap 5–9 |
| 11 | Bukti evaluasi dan penerimaan backend | Tahap terkait sudah executable |
| 12 | Deployment, pemulihan dan pemeriksaan rilis | Tahap 11 serta akses deployment |

Tahap menunjukkan dependency, bukan kewajiban menunggu seluruh tahap sebelumnya untuk menulis tes atau mengecek risiko. Pemeriksaan email/model kecil boleh dilakukan lebih awal. Pengujian keamanan, logging dan tracing tumbuh bersama fitur, bukan baru ditambahkan di akhir.

## 1 Backend minimal yang dapat dijalankan

| ID | Pekerjaan | Kriteria selesai dan bukti |
| --- | --- | --- |
| BE-01 | Siapkan paket Python, FastAPI, konfigurasi dan entry point API | Manifest serta lockfile tersedia. Instalasi dari checkout bersih berhasil; perintah menjalankan API terdokumentasi. Config wajib yang hilang gagal dengan penjelasan tanpa menampilkan secret. |
| BE-02 | Tambahkan health endpoint, format error, request ID, dan batas input dasar | Respons/error mengikuti kontrak. Health proses tidak mengaku seluruh integrasi sehat. Unit/API tests memeriksa input tidak valid, field tambahan dan redaksi error. |
| BE-03 | Pasang interface repository/provider dan dependency wiring | Route tidak menulis aturan bisnis atau memanggil SDK provider langsung. Review struktur dependency serta pemeriksaan otomatis yang relevan mendeteksi pelanggaran batas modul. |

Frontend belum diperlukan untuk smoke test tahap ini. Tiga placeholder `main.py`, `config.py`, dan `worker.py` diganti hanya ketika implementasinya tersedia; worker tidak diklaim executable sebelum tugasnya dibuat.

## 2 Neon migration dan fondasi database

| ID | Prasyarat | Pekerjaan | Kriteria selesai dan bukti |
| --- | --- | --- | --- |
| DB-01 | BE-01 | Atur koneksi pooled/runtime dan direct/migration ke database pengujian Neon | Koneksi dan rollback teruji tanpa menampilkan URL credential. Tidak mengulang riset dasar Neon; fokus pada konfigurasi proyek. |
| DB-02 | DB-01 | Buat migration tenant, host, organisasi, queue, principal, contact dan membership | PK/FK composite, roles/queues dan constraints sesuai schema. Migration dari kosong dan tes pelanggaran relasi dijalankan. |
| DB-03 | DB-02 | Terapkan role runtime, RLS, konteks transaction-local dan fixtures A/B | Role runtime bukan owner/BYPASSRLS. Bagian SEC-01/02/05/07 yang menyangkut tabel/objek fondasi diuji, termasuk missing context, rollback dan pergantian tenant pada pool. Pengujian seluruh objek kasus/email/tool menyusul QUALITY-01; bukan syarat menutup tugas fondasi ini. |
| DB-04 | DB-03 | Sediakan pola migration untuk kelompok tabel berikutnya dan pemeriksaan upgrade | Setiap fitur menambah tabel/constraint/index/RLS yang diperlukan bersama implementasinya. Siklus FK diselesaikan tanpa menonaktifkan keamanan runtime. Bukti upgrade dan kompatibilitas dicatat. |

Jangan membuat seluruh tabel tanpa endpoint/layanan atau tes yang memakainya. Indeks dan migration berikutnya mengikuti kebutuhan tahap, dengan schema sebagai acuan keseluruhan.

## 3 Identitas sesi dan hak akses

| ID | Prasyarat | Pekerjaan | Kriteria selesai dan bukti |
| --- | --- | --- | --- |
| AUTH-01 | DB-03 | Implementasikan anonymous session, contact challenge dan sesi pelanggan | Kode di-hash, sekali pakai, expired/rate limit berlaku. Secret tidak masuk model/log. Uji memakai adapter pengiriman terkontrol terlebih dahulu; bukan klaim email live. |
| AUTH-02 | AUTH-01, BEHAV-01, CASE-01 | Implementasikan upgrade anonim ke terverifikasi | Ownership percakapan dipindahkan hanya dari sesi asal terbukti; token rotate/revoke dan draf pending dibatalkan. SEC-12 memeriksa browser lain tidak mengambil riwayat. |
| AUTH-03 | DB-03 | Autentikasi staf terprovisi, membership aktif dan kebijakan akses | Pelanggan tidak dapat menjadi staf lewat challenge. Role gabungan didukung; admin tanpa CS tidak membaca kasus. Pencabutan membership berlaku pada request berikutnya. |
| AUTH-04 | AUTH-01/03 | Satukan pemeriksaan tenant, organisasi, queue dan akses objek | Kebijakan akses bersama lulus tes terfokus dengan objek/fixture yang tersedia; 404/403 tidak membocorkan tenant lain. Penerapannya pada case, cursor, blob dan tool diperiksa saat endpoint masing-masing dibangun dan dituntaskan pada QUALITY-01. Tugas fondasi ini tidak mengklaim seluruh jalur sudah diuji. |

AUTH-02 diselesaikan setelah percakapan/draf BEHAV-01/CASE-01 tersedia; pada tahap awal implementasikan sesi dan kontrak upgrade dahulu. Urutan kerja yang dipilih setelah AUTH-03 adalah AUTH-04 → SRC-01 → SRC-02 → SRC-03A → BEHAV-01 → CASE-01 → tuntaskan/audit AUTH-02 → CASE-02. AUTH-02 tidak dilewati atau dianggap selesai sebelum bukti adopsi riwayat dan pembatalan draf lulus. Endpoint privat yang memerlukan verifikasi email nyata belum boleh dinyatakan lulus live sebelum MAIL-05. Ini urutan implementasi bertahap, bukan bypass verifikasi production.

## 4 Source reference dan konektor read-only

| ID | Prasyarat | Pekerjaan | Kriteria selesai dan bukti |
| --- | --- | --- | --- |
| SRC-01 | BE-01, DB-02 | Bangun service sumber dan fixtures terpisah | Ada booking parkir/lounge, transaksi, kebijakan dan relasi owner/perwakilan/beneficiary. Penyimpanannya bukan tabel kasus aplikasi. Setup/test source memiliki manifest dan perintah sendiri. |
| SRC-02 | SRC-01, AUTH-04 | Buat konektor booking, transaksi langsung maupun via booking, policy dan relasi | Source memverifikasi caller/tenant; source adapter menormalisasi output/error. API tidak memiliki write booking/payment/relasi. Contract tests memeriksa source 404, 503, invalid payload serta scopes berbeda. |
| SRC-03A | SRC-02 | Bangun pemeriksaan grants dan pembentukan security labels | Scope pembayaran tidak diwarisi otomatis dari booking. Tes terfokus pada source boundary/repository fixtures memeriksa pencabutan izin dan source unavailable menghasilkan fail closed. Ini prasyarat commit kasus privat, bukan kelulusan SEC-11 seluruh jalur. |
| SRC-03B | SRC-03A, CASE-02/04, MAIL-01/04, AI-02 | Buktikan pembatasan data turunan pada integrasi sebenarnya | Jalankan SEC-11 pada case/events/blob, checkpoint/konteks model dan job outbound, termasuk revocation setelah snapshot serta sebelum send. Bukti integrasi lengkap wajib sebelum QUALITY-01/04 selesai. |

Referensi transaksi saja harus cukup untuk mencoba lookup terotorisasi. Jika sumber tidak menemukan data, pelanggan tetap dapat melaporkan kendala tanpa aplikasi mengarang ownership atau status sumber.

## 5 Percakapan dasar dan pengajuan kasus tanpa AI

| ID | Prasyarat | Pekerjaan | Kriteria selesai dan bukti |
| --- | --- | --- | --- |
| BEHAV-01 | AUTH-01, DB-04 | Buat percakapan dan pesan dasar tanpa agent | Kepemilikan anonim/terverifikasi jelas; urutan pesan dan deduplikasi client message bekerja. Setelah ini integrasikan AUTH-02. |
| CASE-01 | BEHAV-01, AUTH-04 | Implementasikan action draft, koreksi, expiry dan cancel | Aktor/sesi, tujuan, versi/hash dan ringkasan tersimpan. Draft yang berubah tidak menerima persetujuan versi lama. AUTH-02 membatalkan pending draft saat upgrade. |
| CASE-02 | CASE-01, AUTH-02, SRC-03A untuk kasus privat | Implementasikan commit terkonfirmasi dan idempotency | Consent, case/message, participant, history, labels dan outbox atomik. Konfirmasi bersamaan/retry hanya satu hasil; timeout setelah commit dapat diperiksa melalui action status. WF-01–06 diuji pada alur yang tersedia; penerapan labels lintas model/outbound dituntaskan SRC-03B. |
| CASE-03 | CASE-02, AUTH-03 | Implementasikan status, assignment, penutupan manual dan reopen | Alasan/hasil/next actor wajib sesuai state. Pelanggan diam bukan rating puas. Closed reply ambigu ditinjau staf; kebutuhan berbeda tidak digabung otomatis. CASE suite diuji dengan fixtures. |
| CASE-04 | CASE-02 | Tambahkan attachment privat dan pengikatan draf/kasus | Batas tipe/ukuran/jumlah dan pemeriksaan file berlaku. Download terotorisasi, labels diwariskan, blob bukan filesystem pod sementara. FILE-01 diuji. |

Pembuatan kasus umum anonim untuk live CS tetap tersedia tanpa data booking. Tindak lanjut di luar sesi memerlukan kontak yang sesuai. Migrasi outbox dasar dibuat bersama CASE-02; eksekusi worker menyusul tahap 7.

## 6 Antrean dan handoff petugas

| ID | Prasyarat | Pekerjaan | Kriteria selesai dan bukti |
| --- | --- | --- | --- |
| CHAT-01 | BEHAV-01, CASE-02 | Implementasikan durable events, cursor polling dan request handoff | Satu konfirmasi cukup untuk kasus baru. Existing case hanya ditautkan jika berhak. Reconnect tidak menggandakan pesan; akses diperiksa setiap polling. |
| CHAT-02 | CHAT-01, CASE-03 | Implementasikan accept/reassign/close atomik | Versi case+conversation, urutan lock, handler/assignee/mode/epoch konsisten. Race dua accept dan accept vs reassign/close diuji lintas koneksi. |
| CHAT-03 | CHAT-02 | Implementasikan staff messaging serta guard publication AI | Stale send ditolak. Waiting/human tidak menerima balasan AI baru. Sebelum agent tersedia, uji guard memakai fake generator yang sengaja diperlambat, bukan hasil live model. |

Tugas tahap 9 harus mengulang race dengan orkestrator sebenarnya. Antrean sederhana cukup; tidak menambah routing canggih atau estimasi tunggu.

## 7 Worker email dan tindak lanjut

| ID | Prasyarat | Pekerjaan | Kriteria selesai dan bukti |
| --- | --- | --- | --- |
| JOB-01 | CASE-02, DB-03 | Buat entry point worker, claim lease dan retry persisten | Network di luar lock transaksi. Lease fencing, worker restart, batas retry dan scheduler privilege diuji; dua worker tidak menyelesaikan job dengan token stale. |
| MAIL-01 | JOB-01, SRC-03A | Buat outbound email dan pemeriksaan penerima oleh layanan bisnis | Sender/recipient, app/RFC/provider IDs dan stable delivery key terpisah. Pemeriksaan grants diulang sebelum send. Adapter tidak mengambil keputusan izin. |
| MAIL-02 | MAIL-01 | Implementasikan delivery lifecycle dan send unknown | Accepted tidak dianggap delivered/read. Timeout setelah provider menerima menghasilkan rekonsiliasi atau review; tidak blindly resend tanpa dukungan idempotency/status. |
| MAIL-03 | JOB-01, AUTH-04 | Implementasikan webhook inbox, fetch body dan event dedupe | Signature/timestamp/account mapping diperiksa. Event berbeda pada satu message ID tetap diterima; pesan inbound hanya dimaterialisasi sekali. |
| MAIL-04 | MAIL-03, CASE-03, AUTH-01 | Implementasikan correlation, sender verification dan reply handling | Balasan menjadi pesan pada kasus sama tanpa portal. Subject saja tidak cukup. Pengirim asing tidak mendapat data; kasus umum dan kebutuhan verifikasi via email bekerja. |
| MAIL-05 | MAIL-01–04 | Jalankan round-trip provider nyata dan sambungkan pengiriman challenge AUTH-01 | Kirim ke alamat penguji, terima, balas, periksa riwayat case, delivery failure serta grants. Bukti receipt dan log tersanitasi tersedia. Kredensial/domain/provider terbatas menyebabkan tugas terhambat, bukan pass simulator. |

Percobaan kecil akses/send/receive boleh dilakukan sejak tahap 1 dengan batas biaya dan alamat pengujian yang diizinkan. Hasilnya tidak menggantikan pengujian workflow terintegrasi di MAIL-05.

## 8 Knowledge dan retrieval

| ID | Prasyarat | Pekerjaan | Kriteria selesai dan bukti |
| --- | --- | --- | --- |
| KNOW-01 | DB-04, AUTH-03 | Buat dokumen, versi, ACL, applicability dan publikasi admin | Draft tidak muncul sebagai sumber jawaban. Publication/withdrawal diaudit dan versi kebijakan lama tetap dapat dipilih sesuai booking. |
| KNOW-02 | KNOW-01 | Buat chunking, embedding dan retrieval pgvector | Model/dimensi embedding tercatat. Filter tenant dan ACL diterapkan sebelum konteks ke model. Retrieval dataset dapat diukur terpisah dari jawaban. |
| KNOW-03 | KNOW-02, SRC-02 | Uji pembaruan/cache/kebijakan historis dan sumber tidak memadai | Versi berlaku digunakan; konflik/hilang tidak menghasilkan tebakan. RAG-01–04 dan SEC-08/09 diuji. |

Tidak otomatis menambah OCR, hybrid search atau reranker; gunakan jika evaluasi menunjukkan kebutuhan yang sesuai kontrak.

## 9 Agent dengan layanan bisnis yang sama

| ID | Prasyarat | Pekerjaan | Kriteria selesai dan bukti |
| --- | --- | --- | --- |
| AI-01 | SRC-03A, CASE-02, KNOW-03 | Pasang adapter model, allowlist read tools dan penyusunan draf | Tool terikat tenant/aktor server, parameter divalidasi. Tidak ada SQL bebas, arbitrary fetch, confirm, source write atau close_case dari model. |
| AI-02 | AI-01, CHAT-03 | Orkestrasi klarifikasi, konteks, checkpoint dan resume | Indonesia/Inggris dan koreksi pengguna dipertahankan. Restart tidak mengulang side effect. Checkpoint tidak menjadi bukti otorisasi. |
| AI-03 | AI-02 | Integrasikan agent dengan handoff serta draf terjemahan staf | Jawaban in-flight tidak muncul setelah epoch berubah. Terjemahan disetujui petugas sebelum kirim. Ulangi CHAT dan security suite dengan workflow sebenarnya. |
| AI-04 | AI-02 | Bandingkan kandidat model dan baseline menu/form | Dataset/settings sama, semua hasil termasuk kegagalan dan repeat runs dicatat. Pilihan model memiliki alasan kualitas, resource, latency dan biaya. |

API key/provider model nyata hanya dipakai dengan batas biaya. Tes fake/tool simulator tidak disebut hasil kualitas model live.

## 10 Kelengkapan API dan alur MVP

| ID | Prasyarat | Pekerjaan | Kriteria selesai dan bukti |
| --- | --- | --- | --- |
| COMPLETE-01 | CASE-03, AUTH-04 | Lengkapi Kasus saya, pembaruan informasi dan reopen | Status/next action benar, pengguna hanya melihat data berhak, konfirmasi tambahan informasi mengikuti kontrak. |
| COMPLETE-02 | CASE-03, AUTH-03, KNOW-01 | Lengkapi endpoint CS/supervisor/admin dan rating | Role gabungan dan pencabutan diuji. Rating opsional tersimpan tanpa mengubah status; admin tidak otomatis membaca kasus. |
| COMPLETE-03 | Tahap 6–9, COMPLETE-01/02 | Periksa ketiga alur terhadap kontrak API | Informasi, bantuan booking dan kendala transaksi parkir/lounge dapat ditelusuri sampai pencatatan/tindak lanjut. OpenAPI/request-response dan mapping schema sinkron. |

Pengujian integrasi backend dapat berjalan sebelum frontend. Penerimaan browser lintas pelanggan–CS–email tetap menunggu frontend yang terhubung; catat sebagai belum dijalankan sampai tersedia, bukan dipalsukan lewat endpoint saja.

## 11 Penguatan evaluasi dan acceptance

| ID | Prasyarat | Pekerjaan | Kriteria selesai dan bukti |
| --- | --- | --- | --- |
| QUALITY-01 | Implementasi terkait, SRC-03B | Lengkapi regression suite domain, API, RLS, failure dan concurrency | Coverage requirement ditelusuri ke ID TEST_PLAN. Lengkapi pemeriksaan seluruh objek/jalur yang belum tersedia saat DB-03/AUTH-04 serta SEC-11 lintas data turunan. Semua pelanggaran kritis yang ditemukan ditutup dan diregresi; kode coverage saja bukan bukti benar. |
| QUALITY-02 | AI-04, COMPLETE-03 | Jalankan calibration kualitas, latency, beban, resource dan biaya | Versi dataset/model/app, konfigurasi Neon/VPS/local, tool costs, polling serta service sumber ikut dicatat. Hasil belum diberi label acceptance. |
| QUALITY-03 | QUALITY-02 | Kunci dataset holdout, profil beban/resource dan ambang nonkritis | Target disepakati sebelum acceptance. Perubahan ambang dicatat; tidak diubah diam-diam untuk meluluskan hasil. |
| QUALITY-04 | QUALITY-01/03 | Jalankan acceptance dengan target tetap dan audit bukti | Critical AI error tetap blocker meski skor agregat tinggi. Report pass/fail/blocked/not_run lengkap, tracing tersanitasi dan keterbatasan tersedia. |

Reliability mencakup kasus/pesan tidak ganda, bukan eksekusi pembayaran yang memang di luar scope. Case closed dan rating pelanggan tetap ukuran yang berbeda.

## 12 Deployment pemulihan dan serah terima

| ID | Prasyarat | Pekerjaan | Kriteria selesai dan bukti |
| --- | --- | --- | --- |
| RELEASE-01 | Backend dan source executable | Siapkan CI, image dan konfigurasi migration/deployment | Build dari checkout bersih, secret terpisah, immutable version/digest dan instruksi tersedia. CI tumbuh sejak tahap awal; ini melengkapi jalur rilis. |
| RELEASE-02 | QUALITY-04, RELEASE-01, akses/anggaran disetujui | Pasang pada VPS/K3s dengan Neon eksternal | TLS, firewall, probes, shutdown dan resource limits terukur. Konfigurasi tidak bergantung data proses lokal. Tidak mengklaim HA lintas mesin pada satu node. |
| RELEASE-03 | RELEASE-02 | Uji restart, tambah replika, rolling update dan rollback | Pesan/epoch/tenant konsisten, lease aman, stale writes ditolak. Hasil DELIVERY-01–05 dicatat pada konfigurasi rilis. |
| RELEASE-04 | RELEASE-02 | Uji backup/restore, retensi dan penghapusan | Restore terisolasi tanpa mengirim ulang email lama; integritas database/blob dan akses diperiksa. RPO/RTO yang dilaporkan berdasarkan hasil, bukan tebakan. |
| RELEASE-05 | RELEASE-03/04 dan frontend untuk browser E2E | Ulangi pengujian relevan dan siapkan runbook/demo | Security, workflow dan performa relevan diuji ulang pada rilis. Demo, bukti, batas sistem, serta prosedur operasi dapat diikuti reviewer. Browser E2E belum boleh lulus jika frontend belum tersedia. |

Lokasi data, jadwal retensi dan kebutuhan akses vendor ditetapkan sebelum data pelanggan nyata digunakan. Deployment awal memakai fixtures dan akun pengujian.

## Kapan tugas boleh disebut selesai

Setiap tugas yang ditutup harus memiliki:

1. Perubahan kode/config yang dapat ditelusuri ke requirement dan dokumen terkait.
2. Perintah menjalankan serta memeriksa hasil yang dapat diulang.
3. Hasil aktual, lingkungan/versi, bukti tersanitasi dan status tes.
4. Review QA untuk perubahan yang memengaruhi akses, transaksi, workflow, integrasi atau batas modul.
5. Catatan keterbatasan; dependency eksternal yang belum teruji tidak disamarkan sebagai pass.

Tes memakai penempatan yang sudah disepakati. Jangan membuat tes yang hanya menyalin implementasi tanpa memeriksa aturan atau hasil bisnis. QA menilai bukti implementasi, bukan hanya membaca task plan. Lead memeriksa dasar temuan dan penutupan defect; persetujuan otomatis bukan pengganti bukti.

## Latihan ownership

Latihan berikut dilakukan di samping pengerjaan, tanpa mengubahnya menjadi blocker tersembunyi bagi implementasi:

| Setelah tahap | Latihan pengguna tanpa jawaban langsung AI |
| --- | --- |
| 1 | Menjalankan API dari instruksi, menjelaskan config/secret dan membaca satu error. |
| 2–3 | Menjelaskan satu PK/FK composite dan mencoba kasus akses yang harus ditolak. |
| 5–6 | Menjelaskan kenapa dua konfirmasi hanya membuat satu kasus dan siapa yang berhak mengirim setelah handoff. |
| 7 | Menelusuri satu email gagal dari kasus sampai job/provider receipt tanpa mengirim ulang sembarangan. |
| 8–9 | Memeriksa sumber jawaban, tool call dan perbedaan fakta sumber dengan laporan pelanggan. |
| 11–12 | Membaca laporan gagal, menentukan apakah rilis boleh lanjut, dan menjelaskan batas satu-node deployment. |

Dokumentasi dan catatan pribadi boleh dipakai. Bantuan AI diberikan bertingkat: pertanyaan pengarah, petunjuk, contoh berbeda, lalu solusi jika diminta. Kemampuan dinilai dari menjelaskan, memeriksa, dan memperbaiki variasi kasus, bukan hafalan perintah.

## Tugas pertama yang dikerjakan

Implementasi dimulai dari **BE-01: backend minimal yang bisa dijalankan**. BE-02 menyediakan fondasi HTTP, dilanjutkan dependency wiring BE-03; tahap database dan fitur bisnis belum dimulai.

## Catatan pelaksanaan BE-01

**Status: selesai — 2 November 2026.**

- Implementasi: paket Python, manifest `backend/pyproject.toml`, `backend/uv.lock`, FastAPI app factory, CLI API, validasi konfigurasi, serta instruksi di `backend/README.md`.
- Lingkungan pemeriksaan: Windows, Python 3.13.6, uv 0.13.6. Dependency runtime terkunci termasuk FastAPI 0.162.0, Pydantic Settings 2.16.2, dan Uvicorn 0.54.0.
- `uv sync --locked --project backend`: berhasil. `pytest backend/tests`: **11 passed**; memeriksa konfigurasi wajib/invalid, redaksi nilai input, prioritas environment, OpenAPI dan batas dokumentasi production.
- Instalasi bersih: ekspor file Git yang akan di-commit ke direktori sementara, tanpa `.env`, virtualenv atau tooling lokal. `uv sync --locked --no-editable --project backend` berhasil, lalu 11 tes lulus dari paket wheel yang terpasang. Ini pengujian ekspor bersih, bukan klaim clone remote.
- Contoh `.env.example` lulus `--check-config`. Proses API sungguhan mengembalikan HTTP 200 pada `/openapi.json`, dengan judul aplikasi sesuai dan paths bisnis kosong; proses dihentikan sesudah pemeriksaan.
- API saat ini tidak memerlukan credential atau koneksi provider. Health endpoint, database, worker, identitas dan workflow bisnis mengikuti tugas berikutnya.

- Review QA: **lulus tanpa temuan material**. QA meninjau diff dan menjalankan ulang 11 tes, instalasi bersih non-editable, validasi konfigurasi melalui console script, serta startup HTTP. Bukti instalasi bersih juga lulus pada pemeriksaan independen QA.


## Catatan pelaksanaan BE-02

**Status: selesai - 2 November 2026.**

- Implementasi: `GET /health/live`, envelope error, normalisasi/generasi UUID request ID, batas body ASGI berdasarkan byte aktual, serta dasar schema mutasi strict/extra forbid. CLI mematikan access log URL mentah.
- Kontrak HTTP dan instruksi pengujian diperbarui pada `DATA_AND_API_CONTRACTS.md` dan `backend/README.md`. Tidak ada route bisnis atau route dummy untuk pengujian yang dipasang pada aplikasi production.
- Lingkungan: Windows, Python 3.13.6 dan uv 0.13.6; dependency tetap memakai lockfile BE-01 tanpa penambahan paket.
- `backend/.venv/Scripts/python.exe -m pytest backend/tests`: **48 passed** (14 bootstrap, 34 boundary). Meliputi schema invalid/extra fields/string limits, JSON rusak, error 404/405/422/500, header protokol, redaksi exception/log, request ID pada 20 request bersamaan, batas byte persis/berlebih, panjang body palsu/ganda/invalid, serta kegagalan sesudah headers terkirim.
- Ekspor bersih tracked files ke direktori sementara: instalasi wheel melalui `uv sync --locked --no-editable --project backend`, **48 tes lulus**, contoh konfigurasi valid. HTTP sungguhan memeriksa health 200, header UUID ternormalisasi, error 404 dan 405 beserta Allow, dan transfer chunked tanpa Content-Length ditolak 413 pada batas body konfigurasi. Query sentinel tidak muncul pada log server; proses dihentikan setelah uji.
- Batas bukti: health hanya proses; belum readiness database/provider. Proteksi transport HTTP sebelum ASGI, slow clients, proxy/deployment serta upload attachment belum menjadi klaim BE-02. Schema mutasi fitur dan kode error domain menyusul bersama implementasi masing-masing.

- Review QA: **lulus tanpa temuan material**. QA menjalankan ulang 48 tes dan helper instalasi bersih/live HTTP. Probe tambahan pada StreamingResponse FastAPI yang gagal setelah headers membuktikan hanya satu response.start, request ID tetap ada, dan exception yang keluar tersanitasi tanpa sentinel privat.


## Catatan pelaksanaan BE-03

**Status: selesai - 2 November 2026.**

- Implementasi: composition root, container service immutable per aplikasi, dependency provider FastAPI, serta pemisahan layanan health dan hasil domain dari schema HTTP. Factory menerima service injection tanpa membangun dependency default ketika injection tersedia.
- Kontrak consumer awal: `TenantRepository` untuk lookup tenant berdasarkan host canonical; `SourceGrantProvider` untuk grants per tenant/principal/tipe resource/reference/scopes. Port belum dipasang pada runtime atau diklaim sebagai otorisasi executable; adapter dan use case mengikuti tugas database/identitas/sumber. Tidak ada fake adapter production atau endpoint privat tambahan.
- Guard AST memeriksa import absolut/relatif/nested, dependency eksternal dan beberapa bypass dynamic import pada lapisan terkait. Fixture pelanggaran membuktikan guard menolak route yang memanggil SDK/infrastructure, modul bisnis yang mengimpor framework/adapter, dan worker yang memanggil adapter langsung.
- Windows, Python 3.13.6; lockfile dan dependency tidak berubah. `backend/.venv/Scripts/python.exe -m pytest backend/tests`: **80 passed** (48 regresi sebelumnya, 27 pemeriksaan dependency/guard, 5 wiring/service). Tidak ada credential yang diperlukan.
- Tes wiring memeriksa service injection melalui route health nyata, override dependency dan isolasi antarapp, container immutable, serta kegagalan service tetap memakai error BE-02 yang aman. Tidak menambahkan framework dependency injection tersendiri.
- Batas bukti: static import guard bukan bukti otorisasi atau aturan bisnis seluruh aplikasi. Implementasi SQL, grants source, lifecycle transaksi/provider dan worker mengikuti tugas masing-masing.

- Ekspor tracked files ke direktori sementara tanpa env/venv proyek: `uv sync --locked --no-editable --project backend` berhasil, seluruh **76 tes lulus** dari wheel terpasang. Startup HTTP asli tetap lulus health200, request ID ternormalisasi, envelope404, 405/Allow, chunked413 dan log tanpa query sentinel. Proses dihentikan setelah pengujian.

- Review QA: **lulus, tidak ada temuan material tersisa**. QA menjalankan suite final **80 passed** dan 10 probe tambahan pada pola dynamic import/alias di API dan modul bisnis; seluruh probe ditolak. Wiring/isolation dan regresi BE-01/02 tetap lulus.


## Catatan pelaksanaan DB-01

**Status: selesai - 2 November 2026.**

- Konfigurasi runtime dan migration dipisahkan, dengan SecretStr, validasi URL pooled/direct dan kesesuaian target. Psycopg 3.3.6/psycopg-pool 3.3.3 dipin pada lockfile bersama certifi. Startup API hanya memuat credential runtime jika APP_DATABASE_ENABLED=true.
- Pool min_size=0, default max_size=2, antrean/acquire/connect timeout terbatas; koneksi diverifikasi saat startup, pool ditutup saat shutdown. Transaksi eksplisit, parameter timeout transaction-local, autocommit pada checkout dan prepare_threshold=None.
- Deadline client-side mencakup checkout/check dan probe startup. Timeout/cancellation menutup koneksi yang tidak pasti. Review QA offline serta tes stuck-check, cancellation dan stuck-startup lulus tanpa socket DB.
- Instalasi wheel locked dari ekspor tracked files tanpa .env/venv: **107 passed, 1 skipped**, konfigurasi contoh dan regresi HTTP bootstrap lulus. Skip pada pemeriksaan offline adalah integrasi Neon opt-in.
- Integrasi nyata: `airport-assistant-db-check --live` **PASS** untuk koneksi pooled runtime, direct, TLS, commit, rollback dan pool reuse setelah SQL error. Tabel probe sementara dihapus setelah pemeriksaan.
- Suite lengkap dengan RUN_NEON_DB_TESTS=1: **108 passed**, termasuk integrasi Neon. Lingkungan Windows, Python 3.13.6; runner pengujian terisolasi `local/db01-venv`.
- Credential dimuat hanya oleh script testing sesuai izin pengguna. Isi .env/URL/password tidak ditampilkan pada output atau dimasukkan ke Git. Output pemeriksaan suite dibatasi ke ringkasan hasil.
- Batas kelulusan: ini bukti koneksi/transaksi, bukan schema bisnis, least privilege runtime, RLS, isolasi tenant atau performa skala. Implementasi dan pengujian tersebut mengikuti DB-02/DB-03 serta tahap terkait.

- QA penutupan: **lulus tanpa temuan material**. QA mengulang CLI live (exit 0, probe table removed) dan suite integrasi aktif secara independen: **108 passed**. Credential hanya dimuat dalam proses testing, dengan output audit terbatas pada hasil tersanitasi.


## Catatan pelaksanaan DB-02

**Status: selesai - 2 November 2026.**

- Alembic 1.20.1 / SQLAlchemy 2.0.55 dipin; revision `0001_foundation` membuat sembilan tabel fondasi dan version table. Migration resources disertakan di wheel; direct credential hanya dimuat script.
- PK/FK composite tenant, unique/partial unique, role/service/language checks, tenant timezone trigger, timestamp defaults/update trigger dan indexes diterapkan. membership_queues memuat organization_id dan dua FK tiga kolom untuk konsistensi organisasi pada insert maupun update parent.
- Upgrade/version/schema dibungkus satu transaksi dan advisory transaction lock. CLI aman menyediakan upgrade/current, tanpa destructive downgrade atau stamping schema yang berkonflik.
- Full suite dengan RUN_NEON_DB_TESTS=1 dan RUN_NEON_MIGRATION_TESTS=1: **144 passed** pada Neon. Tes membuat schema acak, migrasi dari kosong, mengulang upgrade, memeriksa pelanggaran relasi dan field, serta rollback DDL/version/schema ketika revision gagal. Schema pengujian dibersihkan di finally.
- Instalasi wheel non-editable dari ekspor tracked files: **115 passed, 29 skipped** untuk mode offline; resource revision dan regresi HTTP/bootstrap lulus. Credential tidak ikut ekspor/commit dan output live ditangkap script lalu dibatasi ke summary.
- Schema app terpasang revision `0001_foundation`: upgrade, current, dan repeat upgrade semuanya PASS. Pemeriksaan metadata memastikan sembilan tabel fondasi plus schema_migrations tersedia. Tidak ada seed pelanggan, grants runtime atau RLS; DB-03 menguji role/tenant policy terpisah.

- Review QA: **lulus tanpa temuan material**. QA mengulang live suite **144 passed** serta clean wheel **115 passed, 29 skipped**, memverifikasi migration resources dipakai dari paket terpasang. Bukti penerapan app dicatat setelah audit kode dan pengujian schema terisolasi lulus.


## Catatan pelaksanaan DB-03

**Status: selesai - 2 November 2026.**

- Revision `0002_runtime_rls` terpasang pada app: ENABLE/FORCE pada sembilan tabel, missing/malformed tenant context default deny, scope kontak/principal sendiri dan scope organisasi/membership admin aktif. SECURITY DEFINER memakai owner NOLOGIN non-BYPASS dengan SELECT-only metadata policies, search_path tetap, tabel terqualifikasi dan PUBLIC EXECUTE dicabut.
- Hak pribadi tidak dapat dinaikkan sendiri: mutasi membership/role/queue principal aktor ditolak. Column-level UPDATE menjaga tenant/principal/org/membership target immutable. Admin tetap dapat mengelola principal lain dalam organisasi yang diizinkan, termasuk kombinasi role yang eksplisit.
- DatabaseContext immutable berisi tenant/principal UUID dan actor kind dari server. Semua transaksi memasang/reset context transaction-local. Startup menolak owner/privileged login, parent role membership dan CREATE/TEMP. Cancellation menutup koneksi yang tidak pasti.
- Provisioner memverifikasi restricted LOGIN sebelum mengganti hanya DATABASE_URL lokal secara internal. DATABASE_DIRECT_URL tetap terpisah. Password/DSN tidak dicetak dan statement password tidak melalui logger SQLAlchemy. PUBLIC CREATE/TEMP dicabut pada database khusus proyek.
- QA mengulang pemeriksaan keamanan dan probe eskalasi memakai LOGIN runtime nyata, serta full suite setelah rollout: **191 passed**. Pemeriksaan app aktual memastikan revision0002, sembilan tabel FORCE/ENABLE, role runtime lolos guard, context kosong dan pembacaan fondasi tanpa context menghasilkan nol baris.
- Tes meliputi kontak A/B dan dua pelanggan dalam A, staff queue scope, role spoofing, self-escalation INSERT/UPDATE/DELETE, immutable target, DDL/TRUNCATE/SET ROLE/RLS-off, admin lintas organisasi, revocation, seluruh exit transaksi dan tenant konkuren pada pool satu koneksi. Schema/role acak tes dibersihkan; app tidak diberi seed pelanggan.
- Tes tambahan credential DATABASE_URL aktual pada scratch schema: konteks A/B hanya membaca kontak sendiri dan context kosong membaca nol. Role runtime aktual tidak diganti password atau dihapus oleh tes; scratch schema/helper-owner dibersihkan.
- Batas: SEC-01/02/05/07 hanya bagian fondasi yang tersedia. Auth API, case/blob/email/model dan seluruh scope CS mengikuti tugas berikutnya. Context berasal dari backend tepercaya; RLS bukan pengganti autentikasi atau pembatas untuk pemegang credential runtime yang memiliki SQL bebas.

- Penerimaan final: ketiga flag integrasi Neon aktif, **192 passed** tanpa skip. QA menyatakan lulus berdasarkan suite independen191tes, probe app dan pengulangan tes tambahan credential runtime aktual. Tidak ada temuan material tersisa.

- Build final dari ekspor tracked files: instalasi wheel non-editable berhasil, **126 passed, 66 skipped** dalam mode offline, resource migration0002 dan regresi HTTP/bootstrap lulus. Semua66tes integrasi tersebut aktif dan lulus pada suite live192tes di atas.


## Catatan pelaksanaan DB-04

**Status: selesai - 2 November 2026.**

- Panduan executable: template Alembic script.py.mako, CLI new offline tanpa credential, abort scaffold yang belum diimplementasikan, dan satu-head preflight sebelum koneksi. Pembuatan revision hanya dari editable checkout; histori0001/0002 tidak diedit.
- Guard post-upgrade memeriksa ENABLE/FORCE RLS serta non-ownership runtime pada semua tabel/partitioned table selain version table setelah tahap historis0001. Pelanggaran membatalkan transaksi DDL/data/version; guard struktural tidak menggantikan tes ekspresi policy dan grants.
- Enam tes baru Neon: snapshot semua sembilan tabel saat populated upgrade0001->0002, repeat upgrade, legacy read projection dengan context yang sah, contoh expand/cyclic composite FK dengan RLS tetap aktif, serta rollback pada exception/no-RLS/no-FORCE/runtime-owner. Tes negatif memastikan guard benar-benar terpanggil untuk pelanggaran struktural.
- Full suite dengan empat flag Neon aktif: **201 passed**. Instalasi wheel non-editable dari ekspor tracked files: **129 passed, 72 skipped** untuk mode offline dan regresi HTTP/bootstrap lulus. Tidak ada credential ikut ekspor/output/Git.
- Dua tabel contoh dan kolom demo hanya fixture di schema db04_test_UUID, bukan product revision; schema/role tes dibersihkan. App tetap revision0002 dan tidak mendapat tabel fitur yang belum dikerjakan.
- Panduan migration_workflow.md mencakup PK/FK/index/RLS, add-then-link FK, expand/backfill/contract, batas transactional runner, recovery dan bukti upgrade. Tidak mengklaim backfill skala besar, rolling release, backup/restore atau fitur bisnis berikutnya sudah diuji.

- Review QA: **lulus tanpa temuan material**. Suite independen **201 passed**, wheel resources/template terverifikasi, fixture0003 tidak masuk paket produksi, dan unit migration11tes lulus pada paket hasil build. Smoke app aktual: current dan upgrade tetap revision0002, sembilan tabel fondasi plus history utuh, tidak ada tabel/kolom demonstrasi.


## Catatan pelaksanaan AUTH-01

**Status: selesai - 2 November 2026.**

- Revision0003_customer_auth menambah sesi, challenge dan persisted rate counter dengan ENABLE/FORCE RLS. Auth-owner NOLOGIN/non-BYPASS mendapat hak minimum, runtime hanya EXECUTE operasi terbatas. Token/CSRF/OTP memakai keyed digest dengan tag/tenant terpisah; secret di luar DB.
- API HTTPS/same-origin dengan cookie __Host- Secure/HttpOnly untuk sesi, readable CSRF cookie yang terikat hash/header, penolakan duplicate Origin/Host/cookie/header, response no-store dan DTO tanpa token/hash/role staf.
- Verifikasi terikat origin-session/tenant/CSRF/purpose, TTL dan bounded attempts. Atomic consume/contact binding/tokenrotation+revocation, parallel contact verification satu principal, expired/replayed/wrongbrowser/wrongtenant ditolak. NULL hash bukan kecocokan.
- Regresi akhir **253 passed** dalam221.23detik dengan lima flag Neon aktif; database/API nyata, delivery hanya adapter terkontrol dalam tes. Provider nyata/MAIL-05 belum lulus dan default challenge API503 tanpa sender. Tidak ada OTP debug endpoint atau secret pada output/Git.
- Review QA independen: lulus tanpa temuan material tersisa; full suite253passed, pemeriksaan wheel35tes, patch Origin13tes dan latest migration/flow/ACL2tes lulus. Kontrak bootstrap CSRF diselaraskan dengan implementasi.
- Ekspor tracked files dan instalasi wheel non-editable: **153 passed, 100 skipped** dalam mode offline; konfigurasi contoh serta live HTTP health/404/405/chunked413/request-ID lulus. Tes Neon hanya dilewati pada mode offline tersebut.
- Setup app aktual menerapkan revision0003_customer_auth, menyiapkan auth-owner dan metadata demo localhost. SESSION_SECRET dihasilkan/dipertahankan dan flag diaktifkan oleh script internal; .env tidak ditampilkan. Smoke HTTPS-ASGI terhadap runtime Neon lulus: bootstrap200, me200, tanpa sender503, logout204, revoked401. Tidak ada email dikirim.
- AUTH-02 history/draft adoption dan AUTH-03 staff login tetap belum dimulai. Panduan setup, TLS/proxy, rate/expiry, batas delivery dan pekerjaan lanjutan ada di [authentication.md](authentication.md).


## Catatan pelaksanaan AUTH-03

**Status: selesai - 2 November 2026.**

- Adapter password lokal untuk staf terprovisi, endpoint staff login/me terpisah; flow customer tidak menghasilkan staff. Sesi browser bersama memerlukan logout+anon baru untuk berpindah dari customer ke staff.
- Argon2id19MiB/time2/p1, salt acak, input15–128karakter pada provisioner. Executor/semaphore global maksimal2worker/proses tanpa antrean; pembatalan tidak melepas slot sebelum thread selesai. Counter login PostgreSQL dikonsumsi sebelum KDF, tidak menahan transaksi selama hashing.
- Revision0004_staff_auth: credential RLS, session credential_version, rate staf, fungsi terbatas dan trigger version pada UPDATE. Provision/rotation hanya akun/kontak terverifikasi dengan membership aktif yang sudah ada; contact tidak diretarget, tidak memberi role. CLI menolak noninteractive/echo fallback; secret tidak tampil.
- Policy tiap membership mempertahankan organisasi/queue dan kombinasi role; admin tidak otomatis mendapat CS. Pencabutan dan perubahan credential diperiksa tiap request; endpoint kasus/admin tetap pekerjaan berikutnya.
- Panduan executable: [staff_authentication.md](staff_authentication.md). SSO/MFA/recovery/breach-list lengkap/audit/benchmark deployment belum termasuk bukti tahap ini.

- Regresi akhir seluruh backend dengan lima flag Neon aktif: **309 passed** dalam339.47detik. Termasuk18tes unit password/policy/CLI dan38tes integrasi AUTH-03. Pengiriman customer tetap terkontrol di tes; tidak ada akun staf default pada app.
- Instalasi wheel non-editable dari ekspor tracked files: **171 passed, 138 skipped** pada mode offline, disertai konfigurasi contoh dan live HTTP health/404/405/chunked413/request-ID lulus. Skips hanya untuk integrasi Neon yang opt-in.
- QA independen lulus tanpa temuan material tersisa:18unit,42tes wheel terarah, fixture integrasi, ACL/rowlock serta kebijakan scope dan dokumentasi diperiksa. Seluruh teks berubah valid UTF-8 dan diff check bersih. Penggantian server lama/baru harus terkoordinasi karena DTO lama belum mengenali staff.
- Rollout app aktual: revision0004_staff_auth terpasang. Smoke HTTPS-ASGI/runtime Neon lulus bootstrap200, staff-only401 untuk anonim, unknown-login401 generik, sesi tetap anonim, logout204 dan revoked401. Tidak memprovisi akun staf nyata atau mengirim email; credential hanya dimuat script internal.


## Catatan pelaksanaan AUTH-04

**Status: selesai - 2 November 2026.**

- AccessService menyatukan identitas/membership, konteks database tepercaya dan policy objek. /me, /staff/me dan StaffService.current/require menggunakan service yang sama; helper HTTP read/staff/mutation mempertahankan HTTPS/cookie/Origin/CSRF.
- ResourceKey tenant/kind/id wajib cocok dengan projection backend; owned conversation/draft membutuhkan owner XOR dan klasifikasi ownership_only eksplisit. Objek source/case-bound yang belum diperiksa tidak dibuka lewat fallback ownership. Staff case dan membership mempertahankan organisasi/queue/role, admin tidak mendapat CS dan self-management ditolak.
- 401 sebelum lookup; missing/cross-tenant/out-of-scope/key-mismatch404 generik; visible-disallowed403. require_resource memeriksa CSRF untuk known mutation sebelum lookup. Snapshot bukan tiket izin lintas request/job dan tidak menggantikan transaksi bisnis/RLS/source grants.
- Panduan [authorization.md](authorization.md). Tidak ada revision baru, app tetap0004_staff_auth; endpoint case/cursor/blob/tool belum dibuat. AUTH-02 tetap diselesaikan setelah BEHAV-01/CASE-01, dan pengujian seluruh jalur mengikuti SRC-03A/B serta QUALITY-01.

- Regresi akhir lima flag Neon aktif: **367 passed** dalam447.61detik, termasuk36unit policy AUTH-04 dan22integrasi shared-guard HTTP dengan identitas Neon nyata. Resource tetap fixture projection; tidak mengklaim endpoint kasus yang belum tersedia sudah lulus.
- Wheel non-editable dari ekspor tracked files: **207 passed, 160 skipped** untuk mode offline; konfigurasi contoh dan live HTTP health/404/405/chunked413/request-ID lulus. Skips hanya integrasi Neon yang opt-in.
- QA independen: lulus tanpa temuan material tersisa, mencakup36unit terbaru, target suite96tes dengan22integrasi Neon, pemeriksaan wheel/production OpenAPI, exact key binding, defaultdeny, scope role dan dokumentasi. Seluruh perubahan teks valid UTF-8 dan diff check bersih.
- Smoke app aktual HTTPS-ASGI/runtime Neon lulus missing401, bootstrap/me200 anonim, staffme401, defaultsender503, logout204 dan revoked401. AccessService terpasang dan router fixture tidak ada pada production OpenAPI. Current schema tetap0004_staff_auth; tidak ada migration/grant baru, credential/env tidak diubah dan email tidak dikirim.


## Catatan pelaksanaan SRC-01

**Status: selesai - 2 November 2026.**

- Package/API sumber mandiri di reference_service, dengan manifest/lock/tes dan SQLite terpisah. Dataset fiktif dua tenant, booking parkir/lounge, transaksi, policy historis serta owner/perwakilan/beneficiary/payer/outsider.
- Lima GET sesuai kontrak; Bearer+HMACcaller tenant/subject/TTL/rawpath/query, grants per resource, status/details/payment-link redaction, policy versi tepat dan own relations. Cursor terikat scope/generation dan filtering izin sebelum pagination. Tidak ada API write booking/payment/relasi.
- Storage mode=ro/query_only dengan snapshot transaksi request; seeder tervalidasi/FK/integrity/publikasi offline. Setup idempotent menghasilkan key lokal internal tanpa menampilkan secret; demo memakai normal HTTP authentication, localhost saja dan menolak redirect/proxy environment.
- Panduan [reference_service/README.md](../../reference_service/README.md). Tidak ada perubahan schema/credential/env aplikasi Neon. SRC-02 konektor dan SRC-03A grants/security labels sisi aplikasi belum dimulai.

- Suite sumber akhir **96 passed**; instalasi wheel non-editable dari ekspor tracked files mengulang **96 passed** dalam6.60detik, termasuk normal TCP API/demo, packaged fixture, setup/config dan ketiga console entry points. Tidak ada env/SQLite proyek ikut ekspor.
- Audit QA independen lulus tanpa temuan material tersisa; suite96tes diulang dan race publikasi file baru diregresi sehingga target yang muncul bersamaan tidak ditimpa. Reset memakai lock kooperatif dan lifecycle stop/reset/restart; tidak mengklaim hot reseed atau perlindungan dari administrator filesystem yang tidak dipercaya.
- Setup sumber lokal aktual berhasil menghasilkan key internal dan SQLite fiktif terpisah. Smoke TCP aktual lulus health, unsigned401, owner booking200, representative payment404, beneficiary status-only200, payer tanpa booking link200, outsider404. Key tidak tampil pada output/log dan server smoke sudah dihentikan.
- Regresi backend tanpa perubahan source: **207 passed, 160 skipped** (tes Neon opt-in). Suite live367 dari AUTH-04 tetap bukti terakhir untuk backend yang tidak berubah; tidak menjalankan ulang Neon untuk perubahan source mandiri ini. SRC-02/SRC-03A tetap belum dimulai.


## Catatan pelaksanaan SRC-02

**Status: selesai.**

- Adapter async source lima GET, origin tertutup/HMAC caller, proxy/redirect/retry/cookie-provider ditolak, pool/deadline/stream bytes dibatasi dan logging teredaksi. Validasi ketat type/reference/scopes/money/time/page/parent; malformed status bukan unknown yang difabrikasi.
- Tiga GET portal customer dengan shared identity AUTH-04, mapping tenant satu-ke-satu dan principal source_subject_ref melalui self RLS/active principal+tenant. Policy/relations tetap internal; tidak ada source data/identity dari request atau fallback fixture.
- SourceSettings opt-in, composition owns lifecycle client; helper local setup menyalin key secara internal tanpa customer seed/source rebind. Diagnostic localhost menggunakan adapter normal, bukan web auth bypass. Panduan [source_integration.md](source_integration.md).
- Tidak ada migration/grant database baru; SRC-03A/B untuk grants/security labels serta enforcement data turunan belum diimplementasikan.

- Regresi akhir dengan enam flag aktif (lima Neon + RUN_SOURCE_NETWORK_TESTS): **454 passed** dalam468.13detik. Termasuk77unit sumber baru dan10bridge realNeon dengan source TCP di interpreter mandiri, OTP hanya delivery terkontrol pada tes.
- Wheel non-editable dari ekspor tracked files: **284 passed, 170 skipped** pada mode offline; konfigurasi contoh serta live HTTP health/404/405/chunked413/request-ID lulus. Skips adalah tes integrasi opt-in, bukan fallback provider. Runtime HTTPX dan tzdata tersedia pada paket terkunci.
- Audit QA independen lulus tanpa temuan material tersisa;104tes terarah terbaru, bridge/kontrak dan wheel diperiksa. Requiredstatus/refund malformed ditolak dan guard HTTP production tidak bergantung pada kapitalisasi scheme. Tidak mengklaim DTO sendiri membuktikan seluruh grants link booking atau bahwa pengiriman email live sudah diuji.
- Source setup lokal aktual lulus: key disalin internal, host terdaftar dipetakan, flag source diaktifkan; credential lain dipertahankan. Tidak membuat customer/source_subject_ref/relasi baru. Smoke adapter TCP aktual lulus booking200/beneficiary status-only200, portal missing/anonymous401, logout204, clientclosed dan operator diagnostic200. Proses source milik pengguna yang sudah berjalan dipertahankan; proses smoke yang dibuat sendiri dihentikan.
- Tidak ada migration/grant database baru; schema tetap0004_staff_auth. SRC-03A menjadi tugas berikutnya untuk grants dan security labels, bukan dinyatakan selesai oleh source-read ini.

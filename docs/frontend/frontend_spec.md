# Frontend spec — Airport AI Assistant

Status: **lulus audit dokumen QA, 3 November 2026**, sebagai dasar UX dan perancangan. Keputusan produk berasal dari sesi grill me Q1–18 dan pilihan referensi pengguna. Dokumen ini menentukan pengalaman, isi layar dan kriteria penerimaan; pilihan stack serta urutan implementasi dirinci kemudian pada frontend_architecture.md dan frontend_task_plan.md. Persetujuan visual/browser menunggu tiga layar acuan; integrasi frontend belum dinyatakan selesai.

## 1. Produk dan sumber keputusan

Airport AI Assistant membantu pelanggan bandara memahami informasi, mengajukan bantuan terkait booking atau kendala transaksi, dan melanjutkan penanganan dengan petugas. Frontend mempunyai portal pelanggan dan ruang kerja petugas. Keduanya memakai identitas visual yang sama dengan kepadatan berbeda.

Sumber aturan: [PRODUCT_CONTRACT.md](../../PRODUCT_CONTRACT.md), [DATA_AND_API_CONTRACTS.md](../../DATA_AND_API_CONTRACTS.md), [task plan backend](../backend/task_plan.md), serta implementasi API pada backend/src/airport_assistant/api. Perubahan kontrak API tetap dicatat di dokumen kontrak utama, bukan menjadi kontrak kedua di sini.

Frontend tidak membuat booking baru, mengeksekusi pembayaran/refund, mengubah status transaksi sumber, atau memberi kuasa perwakilan. Referensi booking dan pengakuan keluarga tidak memberikan akses. Pengguna perwakilan memakai portal yang sama; aksesnya mengikuti relasi yang sudah tercatat di sumber.

### Keputusan yang sudah dikunci

| Keputusan | Hasil sesi pengguna |
| --- | --- |
| Q1 | Identitas portal: **Nama Bandara — AI Assistant**. |
| Q2–3 | Pengguna menjelaskan masalah dulu; verifikasi saat diperlukan sebelum review. Review memakai halaman khusus dengan tombol **Kirim permintaan bantuan**. |
| Q4–5 | Isi pengajuan dapat dikoreksi tanpa mengulang cerita. Hasil menonjolkan referensi, status aktual dan langkah berikutnya, tanpa ETA rekaan. |
| Q6, Q16 | Pergantian bahasa mengubah label/menu, mempertahankan input dan teks asli; tidak otomatis menerjemahkan riwayat atau mengganti pratinjau tersimpan. |
| Q7 | Desktop CS: antrean kiri, detail di area utama; filter/scroll dipertahankan. Mobile: daftar dan detail bergantian. |
| Q8 | Target tab antrean: Belum ditugaskan, Kasus saya, Semua dalam cakupan; status menjadi filter tambahan. Integrasinya membutuhkan DEP-05. |
| Q9 | Pindah kasus dengan input belum disimpan memunculkan pilihan Tetap mengedit atau Buang perubahan dan pindah. Error/conflict mempertahankan tulisan dalam konteks yang masih sah. |
| Q10 | Konfirmasi tambahan pada close dan reassignment; self-claim dan status biasa langsung dieksekusi setelah input wajib valid. Semua mutasi tetap memeriksa versi. |
| Q11–12 | Kondisi terbaru/next action di atas; riwayat lama dapat diperluas. Rating kecil dan opsional setelah penutupan, terpisah dari Masalah saya belum selesai. |
| Q13 | Pengguna memilih **Zendesk Sydney saja** sebagai referensi visual; tidak mencampur Intercom, GOV.UK atau referensi lain. |
| Q14 | Tema terang dahulu. Dark mode bukan syarat frontend awal. |
| Q15 | Review berupa ringkasan dengan tombol Ubah per bagian; formulir dibuka saat diperlukan, lalu kembali ke pratinjau terbaru. |
| Q17 | Saat AI tidak dapat dilanjutkan, tawarkan pilihan sah seperti coba lagi, menu, atau pengajuan petugas tanpa mengulang konteks yang masih tersedia. |
| Q18 | Closed → Masalah saya belum selesai → penjelasan masalah yang sama → Minta penanganan lanjutan. Nomor kasus dan hasil sebelumnya tetap terlihat. |

## 2. Arah visual tunggal

Referensi tunggal: Sydney, Premium Plus. Pengguna kemudian mengunci [demo yang sedang dibuka di Chrome](https://sydney.premiumplus.io/hc/en-us), light mode dengan aksen biru. [Preview Marketplace](https://resources.premiumplus.io/hubfs/Zendesk%20Marketplace/12_sydney-responsive.jpg) yang dipilih sebelumnya merupakan referensi awal; keadaan demo kini menjadi acuan konkret. Hasil ukur dan inventaris aset berada di [sydney_visual_reference.md](sydney_visual_reference.md). Ini acuan desain, bukan keputusan memakai Zendesk sebagai platform atau membeli temanya.

Yang diadaptasikan: latar terang, ruang yang lapang pada portal, pertanyaan utama yang mudah ditemukan, kategori bantuan yang jelas, aksen biru dari demo terkunci, navigasi sederhana dan bentuk komponen yang konsisten. Nama/konten/iconography disesuaikan dengan layanan bandara kita. Tidak mengambil logo, copy pemasaran atau aset tema tanpa izin penggunaan yang sesuai.

Ruang kerja CS memakai bahasa visual Sydney yang sama dengan jarak lebih rapat, label lebih ringkas dan area kerja lebih luas. Tidak menyalin homepage menjadi dashboard atau menambahkan hero/KPI/grafik tanpa pekerjaan pengguna yang jelas.

Family font dikunci **Lato** dari distribusi resmi berlisensi SIL OFL1.1, dengan400/700 sebagai bobot awal. Pengukuran warna, heading, radius dan spacing demo sudah dicatat; penerapannya menjadi token final setelah halaman pertama diperiksa pengguna. Tidak memakai font pengganti tanpa alasan yang ditinjau. Warna status dibedakan dari warna merek dan selalu disertai teks. Keterbacaan dapat memerlukan penyesuaian teks sekunder terhadap warna demo.

Target pemeriksaan awal: mobile390px, tablet768px dan desktop1440px, ditambah uji lebar320px dan zoom200%. Body portal sekitar16px, area sentuh utama sekurangnya44px; ukuran akhir ditetapkan pada layar contoh berdasarkan keterbacaan. Tidak ada overflow halaman horizontal. Tabel yang memang perlu scroll memakai area scroll tersendiri.

## 3. Aktor, identitas dan navigasi

Pelanggan adalah penumpang, pengunjung nonpenumpang, dan pemesan perwakilan yang berhak. CS dan supervisor bekerja pada organisasi/queue yang diizinkan. Administrator mengelola konfigurasi/akses, bukan otomatis membaca kasus. Pelaksana layanan tidak diberi portal khusus versi awal; pengelola mitra belum menjadi komitmen fitur.

Portal pelanggan memiliki Beranda bantuan, Percakapan, Review, Pengajuan tercatat dan Detail kasus. Kasus saya adalah target COMPLETE-01, bukan daftar yang diasumsikan sudah tersedia. Staff masuk lewat halaman login tersendiri; navigasi kerja memuat Antrean dan detail kasus. Supervisor memakai shell yang sama dengan tindakan tambahan. Area administrator terpisah sesuai COMPLETE-02/KNOW.

Nama bandara diambil dari konfigurasi deployment/metadata tenant tepercaya yang cocok dengan host, bukan input tenant bebas. Nama organisasi/queue dan nama petugas hanya ditampilkan bila sumbernya sah; UI tidak membuat nama dari UUID. Staff/customer memakai sesi host yang sama: pergantian identitas menyebabkan reset konteks/cache. Mengoperasikan identitas berbeda bersamaan membutuhkan profil browser terpisah pada implementasi saat ini.

## 4. Model keadaan yang tidak boleh dicampur

| Keadaan | Pemilik kebenaran dan arti di UI |
| --- | --- |
| Sesi anonymous/customer/staff | Auth server. /me tidak menyediakan email atau waktu expiry sesi; jangan membuat countdown sesi yang dianggap pasti. |
| Draft pending/cancelled/expired/committed | Draft API. Deadline draft tersedia; UI menolak submit pada deadline dan server tetap penentu akhir. |
| Kasus new/in_progress/awaiting_customer/awaiting_external/closed | Case API, dengan version, next_actor dan next_action. |
| Percakapan ai/waiting_staff/human/ended | Blueprint CHAT; belum dapat disimpulkan dari status kasus. BEHAV saat ini hanya menyimpan pesan, tanpa jawaban AI atau chat history-read. |
| Email queued/accepted/delivered/bounced/failed/unknown | Blueprint MAIL. Queued bukan terkirim atau dibaca; case_created outbox bukan bukti email dikirim. |
| Request UI idle/submitting/unknown/failed | State request frontend, bukan perubahan status bisnis. Timeout tidak berarti pengajuan gagal. |

Label kasus: Baru / New; Ditangani / In progress; Menunggu pelanggan / Awaiting customer; Menunggu pihak terkait / Awaiting external; Ditutup / Closed. Label dan next actor selalu terbaca tanpa mengandalkan warna. Closed/no_response tidak menyatakan pelanggan puas.

## 5. Layar pelanggan dan alurnya

| Layar | Pekerjaan, entry/exit dan isi utama | API/fase |
| --- | --- | --- |
| C-01 Beranda bantuan | Entry publik host bandara. Nama Bandara — AI Assistant, pilihan ID/EN, sapaan bantuan, empat pilihan Informasi bandara/Kendala booking/Kendala pembayaran/Hubungi petugas, serta input pertanyaan. Pilih kebutuhan → C-02. Hubungi petugas selalu dapat dicapai tanpa melalui AI. | Shell dapat dibuat sekarang; jawaban publik/AI menunggu KNOW/AI, info tenant DEP-01. |
| C-02 Percakapan atau pengumpulan masalah | Tampilkan teks asli, pilihan terstruktur dan field relevan. Cerita tidak diulang ketika beralih ke pengajuan petugas. Informasi umum tidak memaksa login. Kebutuhan privat → C-03; pengajuan siap → C-04. | BEHAV create/metadata/send/language tersedia; read history/polling/AI/agent belum. Alur awal terintegrasi boleh memakai form terstruktur, tanpa berpura-pura ada balasan AI. |
| C-03 Verifikasi email | Masukkan email, minta kode8digit, verifikasi di browser asal. Jelaskan alasan verifikasi. Keberhasilan → lanjut kebutuhan semula/C-04. Tampilkan kode salah/expired, rate limit, request baru dan unavailable. Staff tidak masuk melalui flow ini. | AUTH tersedia; sender OTP normal masih503 sampai MAIL-05. Pengujian sender terkontrol bukan pengiriman email nyata. |
| C-04 Review pengajuan | Halaman khusus: kategori/layanan, ringkasan, laporan, referensi, email bila diperlukan, bahasa pratinjau dan tujuan. Ubah → form bagian terkait → simpan → pratinjau server baru. Cancel dan Kirim permintaan bantuan jelas. Tujuan tidak diedit bebas. | Draft CRUD+confirm+receipt tersedia. create_case atau request_handoff dipilih sesuai konteks yang diizinkan. |
| C-05 Pengajuan tercatat | Entry hanya hasil committed yang terbukti. Tampilkan Permintaan bantuan tercatat / Request submitted, referensi dapat disalin, status dan next action aktual, lalu akses detail bila berhak. | Receipt dan GET case tersedia. Tidak menjanjikan online, ETA, sudah dibaca atau email terkirim. |
| C-06 Detail kasus | Entry dari hasil pengajuan atau daftar kasus berhak di masa depan. Status/next action di atas; pembaruan terbaru dan expandable history. Closed menampilkan alasan/hasil serta Masalah saya belum selesai dan rating opsional pada fasenya. | GET case dan continue tersedia; daftar Kasus saya/tambahan informasi aktif COMPLETE-01, rating COMPLETE-02. |

### Verifikasi, kontak dan review ulang

Urutan utama: jelaskan masalah → verifikasi bila perlu → buat draft → tampilkan review → konfirmasi eksplisit. Sesi anonim boleh mengajukan request_handoff information/general tanpa referensi booking/transaksi dan tanpa email. create_case memerlukan email eksplisit milik principal yang sudah terverifikasi. Verified request_handoff tanpa email tidak berarti setuju follow-up email. Tidak ada fitur pemberian kuasa baru.

Frontend mempertahankan email yang baru diverifikasi hanya selama flow/browser asal dan identitasnya tetap cocok. Keberhasilan challenge tidak membuat /me menjadi profile email. Pemulihan kontak mengikuti keadaan berikut; jangan mengambil email dari riwayat yang tidak berhak atau memberi badge terverifikasi hanya dari input.

| Keadaan | Alur yang diizinkan |
| --- | --- |
| Anonymous perlu akses privat/pengajuan customer | Bootstrap sesi anonim bila belum ada → minta challenge → verifikasi browser asal → customer. Kedua endpoint OTP saat ini hanya menerima sesi anonymous. |
| Customer masih login setelah reload, tetapi email form hilang | Pertahankan sesi customer. Pengguna boleh mengisi email miliknya yang sudah terverifikasi; backend memvalidasi kontak saat confirm. Salah/tidak berhak422 meminta koreksi alamat yang sesuai. Tidak memaksa logout atau OTP ulang hanya karena reload. Profile READ DEP-02 dapat menyediakan pilihan kontak terverifikasi setelah kontraknya tersedia. |
| Customer mengubah email ke kontak lain miliknya yang sudah terverifikasi | Pilih kontak sah dari profile bila tersedia, atau isi eksplisit; validasi backend tetap wajib. Review kembali sebelum confirm. |
| Customer ingin memakai alamat baru yang belum terverifikasi | Penambahan/verifikasi kontak pada sesi customer memerlukan kontrak WRITE DEP-02 tersendiri; profile READ saja tidak cukup. Jangan memanggil endpoint OTP anonim dengan sesi customer atau menampilkan kemampuan ini seolah sudah aktif. Gunakan kontak yang sudah sah atau jelaskan bahwa perubahan alamat belum tersedia. |
| Pengguna sengaja ingin masuk sebagai akun lain | Aksi keluar/ganti akun harus eksplisit dan memperingatkan input belum tersimpan. Hapus cache/input privat akun lama → logout → sesi anonymous baru → login sesuai jenis akun. Jangan memindahkan laporan privat atau consent antar akun secara diam-diam. |

Jika sesi sudah tidak valid, ikuti pemulihan401 dan aturan penghapusan data pada bagian9; jangan mengklaim email/sesi masih terverifikasi berdasarkan state memory lama.

Jika verifikasi terjadi saat pending draft sudah ada, AUTH-02 membatalkannya dan mengganti sesi. Hanya teks laporan milik pengguna pada flow yang sama yang boleh dipindahkan sementara dalam memory. Buat draft baru dengan sesi baru, tampilkan kembali ringkasan, tujuan, email, bahasa, deadline dan perubahan relevan, lalu minta persetujuan baru. State frontend menyimpan version/hash terbaru dari server untuk protokol confirm; keduanya tidak perlu dicetak sebagai angka teknis/hash64hex pada layar. Consent, OTP, token, private source facts dan hash draft lama tidak dipindahkan sebagai izin baru.

UI Ubah per bagian menyusun kembali payload lengkap yang sah untuk PATCH full replacement, tidak mengirim partial patch. Koreksi menghasilkan version/hash baru di state protokol, memperbarui pratinjau yang dibaca pengguna, dan tidak memperpanjang TTL15menit. Jangan menimpa input dengan respons request lama; pratinjau yang belum selesai diperbarui tidak boleh dikonfirmasi.

ID/EN tersedia pada menu, label, validasi, error dan status. Teks asli pelanggan/petugas dipertahankan. Language PATCH memakai versi conversation, bukan versi case/draft. Perubahan bahasa tidak menerjemahkan histori atau mengubah draft tersimpan secara diam-diam; jika pengguna ingin pratinjau baru, lakukan koreksi/review server dengan versi/hash baru. Terjemahan bantuan mengikuti AI-03 dan diperlihatkan terpisah, dengan pemeriksaan petugas sebelum kirim.

### Pengajuan, recovery dan kelanjutan

Saat mengirim, tombol utama disabled dan status request diumumkan; pencegahan ganda tetap memakai idempotency server. Simpan Idempotency-Key stabil untuk command confirm yang sama, bersama draft_id/expected_version/payload_hash dalam konteks memory yang sah. Bila respons tidak pasti, tampilkan Sedang memeriksa pengajuan dan GET actions terlebih dahulu. Jika committed, gunakan hasilnya; jika pending dan masih sah, retry command yang sama dengan key yang sama. Jangan otomatis membuat key/draft baru untuk mengatasi timeout.

Halaman review/hasil mempertahankan opaque draft_id sebagai pointer navigasi yang tetap ditemukan setelah refresh, misalnya route frontend /help/submission/{draft_id}; nama route final ditentukan pada architecture. Ini ID resource, bukan token akses: setiap GET actions tetap diperiksa server. OTP, cookie/token, email, payload dan idempotency key tidak dimasukkan ke URL. Reload yang kehilangan memory/key memakai pointer tersebut untuk memeriksa receipt, tanpa auto-confirm; pending memerlukan review dan keputusan kirim eksplisit yang sah, bukan replay tanpa konteks persetujuan. Data akun sebelumnya tetap dibersihkan meskipun pointer route masih ada; akun yang tidak berhak mendapat respons generik.

Jika pointer juga hilang dan belum ada lookup/daftar kasus berhak, frontend tidak mengklaim dapat memulihkan hasil. Tampilkan hasil belum dapat dipastikan, sarankan membuka kembali halaman hasil sebelumnya atau kanal bantuan resmi yang benar-benar tersedia, dan jangan otomatis membuat pengajuan pengganti. Nomor referensi yang sempat diterima tetap dapat disalin; fitur Kasus saya/lookup tidak dianggap tersedia untuk menutupi keadaan ini.

409 versi/hash/tujuan berubah: pertahankan input yang masih berhak, muat keadaan terbaru, tunjukkan perubahan, buat/perbarui draft dan minta review eksplisit; tidak auto-submit. 410: draft sudah habis masa berlaku, tidak memperpanjang deadline; tawarkan pembuatan draft baru dari input yang sah dan review baru. Preview pending terikat sesi pembuat; receipt committed mengikuti pemilik kasus saat ini dan expires_at null. Receipt bukan token akses.

Pada C-06 closed, Masalah saya belum selesai membuka form dengan nomor kasus dan hasil lama tetap terlihat. Pelanggan mengisi penjelasan, lalu Minta penanganan lanjutan mengirim intent same_issue, message, expected_version dan client_request_id stabil untuk request yang sama. Server menentukan in_progress dengan assignee aktif atau new tanpa assignee. Snapshot hasil lama tetap terlihat. Retry sama tidak menggandakan history; command berbeda dengan key sama409. Ini berbeda dari melengkapi kasus yang masih aktif. Ucapan terima kasih/masalah berbeda tidak otomatis reopen; masalah berbeda diarahkan ke pengajuan baru.

Rating1–5 bersifat opsional, tanpa default, di bawah hasil penanganan; tidak memakai pop-up yang menghalangi. Pengguna bebas melewati rating. Masalah saya belum selesai tetap tersedia terpisah. Rating belum aktif sebelum COMPLETE-02 menyediakan penyimpanan berhak.

## 6. Data sumber dan bantuan pembayaran

Pisahkan label Laporan Anda, Informasi dari sistem layanan, dan Status penanganan kasus. Referensi/cerita pelanggan bukan fakta sumber. Kasus/draft yang tersedia saat ini report-only, source observations/labels kosong dan attachments kosong. Jangan menyalin hasil source read ke draft seolah sudah ada dukungan private derived data; itu memerlukan SRC-03B dan fitur terkait.

Booking, payment status dan payment details memiliki izin berbeda. Field yang tidak diberikan karena scope tidak ditampilkan sebagai angka0 atau data kosong yang dianggap pasti. Unknown tetap Belum dapat dipastikan. 404 memakai pesan generik yang tidak membocorkan apakah resource ada;502/503 menawarkan retry atau pengajuan bantuan yang tersedia. Jangan menyimpulkan pembayaran ganda, refund berhasil atau status paid dari laporan saja.

Saat sumber tersedia, tampilkan observed_at/source_updated_at dengan label yang berbeda, waktu layanan menurut service_timezone yang tersedia, dan uang menurut currency/currency_exponent. Jika timezone layanan tidak ada pada DTO, labelkan UTC atau timezone tampilan yang dinyatakan jelas; jangan menebak zona bandara dari browser. Hilangnya sumber/izin tidak memberi akses fallback ke snapshot privat lama. Link pembayaran hanya boleh ditampilkan dari field berhak yang diberikan backend, tanpa menganggap frontend menjalankan pembayaran.

## 7. Ruang kerja CS dan supervisor

| Layar | Pekerjaan, isi dan tindakan | API/fase |
| --- | --- | --- |
| S-01 Login petugas | Login contact/password terpisah dari OTP pelanggan; validasi sesi dan baca membership. Rotasi identitas mereset cache/UI sebelumnya. | Staff login/me tersedia. |
| S-02 Antrean | Desktop: daftar di kiri dan detail terpilih di area utama. Ringkasan, referensi, status, penanggung jawab, last update. Target default Belum ditugaskan, tab Kasus saya/Semua dalam cakupan dan filter status. Memilih kasus mempertahankan filter/scroll. | Queue endpoint saat ini hanya status, maksimal100firstpage. Tab penuh/total/search/sort memerlukan DEP-05; metadata/nama/tindakan DEP-03. |
| S-03 Detail kasus | Masalah, bahasa dan referensi; status/assignee/next actor/action; pembaruan terbaru dan expandable history; tindakan sah. CS dapat Ambil kasus, ubah status dan close; supervisor dapat assign/reassign. | GET/assignment/status tersedia, pemilihan membership/assignee user-facing membutuhkan DEP-03. |
| S-04 Ubah status/close | Form next_action; closed menambah alasan+resolution. Duplicate memilih kasus terkait berhak; no_response meminta bukti tindak lanjut. Close menampilkan ringkasan sebelum konfirmasi. | Status API tersedia; pencarian kasus terkait DEP-04. |
| S-05 Percakapan/email | Area komunikasi pada kasus yang sama, original text dan draf terjemahan terpisah. Live handler, accept, send dan delivery state hanya muncul setelah integrasi sah. | CHAT/JOB/MAIL/AI-03, bukan inferred dari status kasus. |
| A-01 Administrasi | Kelola informasi layanan/knowledge, akun dan hak akses sesuai scope. Tidak membuka isi kasus bagi admin-only; peran gabungan tetap dibatasi organisasi/queue masing-masing. Konfigurasi koneksi/credential API dilakukan secara teknis di luar UI MVP. | COMPLETE-02/KNOW; bukan dashboard analytics atau halaman pengaturan koneksi API. |

CS hanya self-claim kasus unassigned untuk membership dirinya yang tepat; hasilnya in_progress. Supervisor preassign kasus new tetap new sampai diterima lewat status; supervisor dapat reassign kasus aktif. Assignee tujuan harus berasal dari pilihan sah backend. UI tidak meminta UUID, mencoba beberapa membership sampai berhasil, atau menghitung izin supervisor berdasarkan role global saja.

Self-claim/status biasa langsung dikirim setelah input wajib lengkap. Close dan pergantian penanggung jawab menampilkan konfirmasi tambahan dengan konteks kasus. Reassignment menunjukkan petugas asal/tujuan bila nama sah tersedia. Closed menolak edit/assignment staf; tidak ada tombol reopen staf. Status menunggu tetap punya assignee, informasi yang ditunggu dan cara melanjutkan. no_response memerlukan bukti tindak lanjut manual dan bukan kepuasan.

Input belum tersimpan saat pindah kasus/meninggalkan halaman memicu Tetap mengedit atau Buang perubahan dan pindah. Konflik versi tidak mengirim ulang otomatis: muat keadaan baru, pertahankan tulisan petugas pada konteks kasus yang masih berhak, lalu minta tinjau ulang. Jika request close/reassign tidak pasti, baca kasus dan history terbaru sebelum menentukan langkah; jangan menampilkan sukses tanpa bukti server atau mengulang side effect secara buta.

Pada mobile, membuka kasus mengganti daftar dengan detail; Kembali mengembalikan filter dan scroll. Tombol aksi penting tetap mudah dicapai tanpa composer/keyboard menutupi isi. Supervisor memakai shell yang sama, bukan dashboard terpisah. Nama/availability/ETA petugas tidak boleh dibuat dari data fixture ketika aplikasi terintegrasi.

## 8. Email sebagai kanal produk

Balasan petugas harus dapat dibaca dan dibalas langsung dari email tanpa mewajibkan pelanggan kembali ke portal. Portal detail kasus adalah akses tambahan. Tujuan ini tetap bagian MVP produk dan bergantung pada JOB/MAIL; belum menjadi klaim milestone frontend awal.

Jika belum ada informasi availability, tampilkan pilihan Ajukan bantuan petugas tanpa menyebut petugas online/offline atau ETA. Jika provider belum aktif, sampaikan batas layanan yang berlaku dan pilihan retry/bantuan yang benar-benar tersedia; jangan menampilkan Email sudah dikirim. Pengajuan anonim tanpa kontak tidak menjanjikan tindak lanjut di luar sesi. Kegagalan email tidak menutup kasus atau menghapus permintaan.

Setelah MAIL tersedia, queued/accepted/delivered/failed dan balasan masuk berasal dari API server, bukan timer UI. Status email bukan status kasus atau tanda pesan dibaca. Data privat hanya dikirim ke penerima yang diverifikasi dan berhak; frontend tidak memutuskan penerima hanya dari email yang diketik.

## 9. Keamanan state, input dan error

Unsent input disimpan dalam memory per tenant/aktor/sesi/kasus secara default. Tidak menyimpan OTP, password, private history atau payload privat di localStorage/URL/analytics. Logout, pergantian akun/tenant dan rotasi sesi menghapus private cache serta state lama; pengecualian sempit adalah teks user yang sengaja dipindahkan dalam memory pada flow verifikasi asal di bagian5. Kebijakan pemulihan lintas reload/persistent storage harus dirancang tersendiri sebelum diaktifkan.

401 meminta pemulihan identitas dan menghentikan request privat. 403/404 terkait kehilangan akses membersihkan data objek terkait dari layar/cache; jangan mempertahankan input privat yang tidak lagi boleh dilihat. Error404 tetap generik, bukan menjelaskan pemilik/tenant lain. Error validasi field ditampilkan dekat field, ringkasan error dapat difokuskan, dan request ID tersedia untuk bantuan. Nilai rahasia tidak ditampilkan di pesan error atau log browser.

429 menghormati Retry-After bila tersedia; tidak melakukan retry loop. Error502/503 berada dekat blok yang gagal dengan retry eksplisit. Empty, loading, unavailable, stale dan unknown adalah keadaan berbeda. Data sensitif lama tidak dipertahankan sebagai fallback ketika grants/sesi tidak lagi valid. Sukses/error tidak diumumkan hanya melalui perubahan warna.

Backend tetap memeriksa seluruh izin; menyembunyikan tombol bukan pengamanan. Cookie/HTTPS/same-origin/CSRF, cache scoping, request cancellation dan pencegahan respons lama menimpa state baru dirinci pada frontend_architecture.md. Frontend tidak memasok tenant/principal/session/source identity sebagai klaim body/header.

## 10. Pemetaan API aktual

Semua path berikut berprefix /api/v1. Ini indeks integrasi ke kontrak utama, bukan izin untuk menambah parameter yang tidak diterima server.

| Kemampuan | Endpoint aktual dan batas penting |
| --- | --- |
| Sesi/verifikasi | POST /auth/sessions, GET /me, POST /auth/challenges, POST /auth/challenges/{id}/verify, POST /auth/logout. /me hanya session_kind/principal_id; OTP delivery normal belum aktif. |
| Petugas | POST /staff/auth/login dan GET /staff/me. Membership sendiri tersedia, bukan direktori petugas atau nama/scope kasus lengkap. |
| Percakapan dasar | POST /conversations, GET /conversations/{id}, POST /conversations/{id}/messages, PATCH /conversations/{id}/language. GET metadata bukan history; received berarti pesan tersimpan, bukan dijawab AI. |
| Booking/transaksi | GET /bookings/{reference}, GET /transactions/{reference}, GET /bookings/{reference}/transactions dengan limit/cursor yang diizinkan. Hanya bila source dikonfigurasi dan customer berhak. |
| Draft/review | POST /action-drafts, GET/PATCH/DELETE /action-drafts/{id}, POST /action-drafts/{id}/confirm, GET /actions/{id}. PATCH full replacement; confirm expected_version/hash dan satu Idempotency-Key. |
| Kasus | GET /cases/{id}, GET /staff/cases/{id}, GET /staff/queues/cases?status=..., PATCH /staff/cases/{id}/assignment, PATCH /staff/cases/{id}/status, POST /cases/{id}/continue. CaseView belum berisi available_actions atau direktori assignee. |

### Dependensi yang harus ditutup sebelum layar terkait dinyatakan terintegrasi

| ID | Kebutuhan | Keputusan untuk frontend |
| --- | --- | --- |
| DEP-01 | Nama bandara, identitas visual dan informasi organisasi/queue yang boleh ditampilkan. | Gunakan konfigurasi host tepercaya atau metadata publik terotorisasi yang disepakati; bukan selector tenant bebas. |
| DEP-02 | Profile READ untuk kontak terverifikasi dan, bila kelak dipilih, kontrak WRITE terpisah untuk menambah/memverifikasi alamat baru pada sesi customer. | Anonymous memakai OTP asal. Customer setelah reload boleh mengisi kontak miliknya yang sudah terverifikasi dengan validasi confirm, tanpa logout/OTP ulang. Customer tidak memakai endpoint OTP anonim; email baru belum terverifikasi tidak didukung sampai kontrak verifikasi kontak tambahan tersedia. Profile READ tidak dianggap memberi kemampuan WRITE. |
| DEP-03 | Metadata scope/action per kasus, membership self-claim yang benar, nama dan eligible assignees terotorisasi. | Dibutuhkan untuk CS/supervisor yang bekerja normal tanpa input UUID. Definisikan minimal contract pada tahap arsitektur/task plan; available_actions tetap bukan pengganti otorisasi server. |
| DEP-04 | Pencarian kasus terkait yang berhak, pemilik sama, resolve public reference ke ID minimal yang sah. | Dibutuhkan untuk alur duplicate. Tidak membuat pencarian seluruh tenant/menampilkan detail pelanggan lain. |
| DEP-05 | Antrean unassigned/mine/all scoped, pagination/search/sort/count yang akurat sesuai kebutuhan final. | Endpoint sekarang hanya status dan100firstpage. Jangan menampilkan total global, zero-global atau tab lengkap dari filter client pada subset. Jika preview memfilter subset, labelkan cakupannya; acceptance antrean target menunggu dependency ini. |
| DEP-06 | Durable message history/events/polling, waiting/human/ended, handler dan channel communication. | CHAT-01–03; flow percakapan yang dapat dipulihkan setelah reload menunggu ini. |
| DEP-07 | Jawaban knowledge/AI, email dua arah/OTP live, tambahan informasi aktif, Kasus saya, rating/admin, attachment. | KNOW/AI, JOB/MAIL-01–05, COMPLETE-01/02, CASE-04 dan SRC-03B sesuai fitur. Tidak dianggap hilang dari produk atau sudah dikerjakan oleh frontend. |

Tidak ada dependency di tabel ini yang otomatis berarti backend sudah diubah. Draft kontrak tambahan harus diaudit sebelum implementasi. Fitur yang belum aktif tidak diberi tombol palsu pada milestone terintegrasi. Fixture desain boleh memperlihatkan target akhirnya dalam mode prototype yang jelas, tanpa dihitung sebagai bukti API/live email/AI.

## 11. Standar interaksi dan aksesibilitas

Semua input mempunyai label dan instruksi; placeholder bukan pengganti label. Tab order mengikuti urutan pekerjaan. Fokus terlihat dan kembali ke pemicu setelah dialog ditutup; modal menahan fokus dengan benar, Escape/cancel tidak membuang perubahan diam-diam, error submit mendapat announcement yang sesuai. Loading tidak menggeser posisi tombol secara mengejutkan.

Status menggunakan teks+warna/icon bila membantu. Referensi/email/nama panjang dibungkus atau dipotong dengan cara membaca nilai lengkap yang dapat diakses; tombol salin menyalin nilai lengkap. Informasi penting tetap dapat dibaca pada zoom200%, keyboard mobile terbuka dan lebar320px. Sticky action/composer tidak menutupi pesan error, field atau CTA. Portal dan staff shell menggunakan komponen/token bersama; isi tabel, dialog, form dan status mempunyai keadaan disabled/loading/error yang konsisten.

## 12. Kriteria penerimaan yang dapat diperiksa

| ID | Pemeriksaan dan hasil yang wajib |
| --- | --- |
| FE-01 | Host bandara menampilkan Nama Bandara — AI Assistant. Klaim tenant dari URL/body/header tidak memberi akses tenant lain. |
| FE-02 | Empat pilihan bantuan dan input pertanyaan jelas; Hubungi petugas dapat dicapai tanpa percakapan AI. Availability/ETA tidak direka. |
| FE-03 | Informasi umum tidak memaksa login; kebutuhan privat meminta verifikasi dan ACL server sebelum data tampil. |
| FE-04 | Verifikasi asal mempertahankan teks user yang sah, membatalkan penggunaan draft lama dan menghasilkan review baru. OTP503/429/kode salah/expired punya next action tanpa mengarang sukses. |
| FE-05 | Review menampilkan ringkasan/laporan/tujuan/email/bahasa/deadline terbaru dan perubahan relevan. Ubah bagian tidak menghilangkan field lain dan memakai full replacement. Pemeriksaan request membuktikan confirm memakai version/hash server terbaru, tanpa mewajibkan hash teknis dirender pada layar. |
| FE-06 | Submit berulang/timeout/lost response memeriksa actions dan memakai command/key yang sama; hanya satu kasus/hasil. Refresh dapat menemukan opaque draft_id dari navigasi dan memeriksa receipt tanpa auto-submit. Pointer hilang menampilkan hasil belum pasti dan safe next action, tanpa pengajuan pengganti otomatis. |
| FE-07 | 409 versi/hash/tujuan menahan tindakan dan meminta review baru;410 tidak memperpanjang TTL. Receipt committed tetap dapat ditemukan oleh principal berhak pada sesi lain, tanpa membuka preview pending sesi lama. |
| FE-08 | Hasil pengajuan membedakan tercatat, ditangani, queued dan terkirim. Nomor dapat disalin lengkap; status/next actor/action sesuai response. |
| FE-09 | Ganti ID/EN mempertahankan input, original messages dan history; tidak menerjemahkan/mengganti draft tersimpan diam-diam. |
| FE-10 | Data source scope-limited/unknown/404/502/503 tidak diubah menjadi0/paid/refund/pembayaran ganda; laporan dan fakta sumber berbeda secara visual. |
| FE-11 | CS melihat antrean/detail sesuai scope, admin-only tidak membaca kasus. Self-claim memakai membership tepat; preassign supervisor new tetap new; reassign hanya eligible assignee. Bergantung DEP-03/05. |
| FE-12 | Tab/filter/count antrean mempunyai cakupan akurat; tidak menyatakan nol/global complete dari100firstpage. |
| FE-13 | Self-claim/status biasa tidak meminta dialog tambahan; close/reassign meminta konfirmasi. Closed tidak menerima edit staf, duplicate membutuhkan kasus terkait sah, no_response membutuhkan bukti. |
| FE-14 | Konflik/fault mempertahankan tulisan hanya selama konteks akses sah; tidak auto-replay. Pindah kasus dengan dirty input menawarkan Tetap mengedit/Buang perubahan dan pindah. |
| FE-15 | Closed menampilkan hasil/riwayat dan form same_issue. Retry continuation stabil tidak menggandakan history; inactive assignee menghasilkan new; hasil penutupan lama tetap ada. |
| FE-16 | Rating opsional tanpa default dan tanpa blokir reopen; melewati rating/no_response/closed tidak dianggap puas. Aktif setelah COMPLETE-02. |
| FE-17 | Logout/rotasi/akun berbeda atau 403/404 relevan tidak menampilkan private cache/input akun sebelumnya. OTP/password tidak tersimpan di persistent store atau URL. |
| FE-18 | Mobile/desktop, keyboard/dialog focus+return, error announcement, zoom, long text dan mobile keyboard lulus pemeriksaan nyata. |
| FE-19 | Pelanggan dapat membaca/membalas balasan petugas lewat email tanpa portal setelah MAIL tersedia; queued/accepted bukan delivered/read. Tidak boleh PASS dari fixture saja. |
| FE-20 | Tiga layar acuan memakai Sydney saja, token konsisten dan data realistis; visual disetujui lewat browser/screenshots sebelum memperbanyak halaman. |

## 13. Tiga layar acuan dan bukti desain

1. C-01 mobile: identitas bandara, ID/EN, sapaan, empat kategori/input dan akses petugas; uji teks panjang dan layar sempit.
2. C-04 mobile+desktop: review lengkap, Ubah per bagian, validasi, loading, deadline expired, tujuan berubah dan konfirmasi tidak pasti.
3. S-02/S-03 desktop: antrean/detail, status/next actor/action, assignment/close, history expandable dan dirty-input conflict; versi mobile mempertahankan konteks navigasi.

Fixtures desain memakai bandara/akun fiktif yang jelas untuk prototyping, nama/referensi/email realistis, ID/EN, empty list, long report, status closed/reopen, restricted source dan delivery unavailable. Tidak memakai KPI/SLA/availability/total rekaan. Fakta yang muncul dua kali berasal dari nilai fixture yang sama.

Untuk tiap layar, catat aktor/pekerjaan, entry/exit, data/API, tindakan, keadaan gagal, fase availability, serta FE acceptance IDs. Buktikan arah desain melalui browser dan screenshot mobile/tablet/desktop, bukan hanya review Markdown. Temuan QA visual menunjuk layar/state dan masalah konkret; revisi lalu periksa ulang.

## 14. Batas persetujuan dan langkah berikutnya

Audit dokumen memeriksa keputusan Q1–18, Sydney sebagai referensi tunggal, product contract, API aktual, dependensi, keamanan dan acceptance. Lulus audit dokumen berarti spec siap menjadi dasar perancangan, bukan UI sudah lulus visual atau fitur selesai.

Urutan visual terbaru dari pengguna: kunci keadaan demo → bedah tampilannya → kerjakan **C-01 terlebih dahulu** → bandingkan render di browser dan perbaiki → baru lanjutkan dua layar acuan lainnya setelah arah halaman pertama diterima. Uji tambahan dengan isi yang menyulitkan bersifat opsional selama eksplorasi visual; penerimaan fungsional dan aksesibilitas dasar tetap mengikuti kriteria pada dokumen ini.

Susun frontend_architecture.md untuk teknologi/struktur/state/API/design-system dan frontend_task_plan.md untuk vertical slices/dependensi nyata sebelum memperluas implementasi; QA audit rancangan teknisnya. Task dan kontrak backend yang perlu ditambah diturunkan dari DEP, tanpa diam-diam mengubah scope atau mengaktifkan mock sebagai layanan nyata.

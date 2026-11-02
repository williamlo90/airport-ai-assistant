# Airport AI Assistant

Keputusan produk tentang masalah pengguna dan alur layanan

2 November 2026

Dokumen ini merangkum masalah yang ingin diselesaikan, kelompok pengguna versi awal, dan skenario layanan yang menjadi dasar pengembangan Airport AI Assistant.

## Brief masalah

Pelanggan bandara membutuhkan informasi dan bantuan yang sesuai dengan situasi mereka, dalam bahasa yang mereka pahami. Ketika rencana perjalanan berubah atau terjadi kendala pada booking dan transaksi, mereka sering belum tahu prosedur yang harus diikuti, data yang perlu disiapkan, serta pihak yang bertanggung jawab menangani masalahnya.

Contohnya, penumpang yang penerbangannya terlambat ingin mengetahui apa yang harus dilakukan terhadap booking parkirnya. Pengguna lain mengalami pembayaran error dan perlu bantuan mengajukan pengecekan. Mereka membutuhkan penjelasan serta arahan, sementara petugas tidak selalu tersedia saat dibutuhkan.

Di sisi operasional, petugas menerima pertanyaan berulang dan permintaan yang informasinya belum lengkap. Mereka harus meminta klarifikasi sebelum bisa menangani atau meneruskan kasus. Hal ini menambah pekerjaan dan memperpanjang proses bantuan.

Pusat masalahnya adalah kesulitan memperoleh bantuan yang relevan dan melanjutkan penanganan sampai status serta langkah berikutnya jelas.

## Definisi dan batas produk

Airport AI Assistant adalah asisten layanan pelanggan multilingual yang membantu pengguna bandara memahami informasi, mengurus permintaan terkait booking dan kendala transaksi, serta mendapatkan bantuan petugas yang tepat.

Asisten membaca informasi dari sistem sumber, memberikan arahan, dan membantu mengirim permintaan setelah konfirmasi pengguna. Pembuatan booking baru, pemrosesan pembayaran, dan keputusan refund tetap menjadi tanggung jawab sistem atau petugas yang berwenang. Integrasi langsung ke payment gateway tidak termasuk scope versi awal.

## Pengguna dan peran

Versi awal memiliki enam kelompok pengguna dan peran. Penumpang serta pengunjung nonpenumpang adalah pelanggan utama. Pendamping atau pemesan perwakilan merupakan konteks penggunaan yang dapat melekat pada keduanya.

| Kelompok | Kategori | Kebutuhan terkait masalah |
| --- | --- | --- |
| 1. Penumpang | Pelanggan utama: keberangkatan, kedatangan, dan transit. | Memahami informasi layanan serta mendapatkan bantuan terkait booking dan transaksi. |
| 2. Pengunjung nonpenumpang | Pelanggan utama: pengantar, penjemput, dan pengunjung fasilitas bandara. | Mendapat informasi dan bantuan untuk layanan yang digunakan, seperti parkir. |
| 3. Pendamping atau pemesan perwakilan | Konteks penggunaan; bisa sekaligus penumpang atau pengunjung. | Mengurus kebutuhan orang lain dengan kejelasan hubungan antara pemesan, pembayar, dan penerima layanan. |
| 4. Petugas customer service | Peran operasional. | Menerima permintaan dengan konteks dan informasi yang cukup untuk ditangani. |
| 5. Supervisor layanan bandara | Peran operasional. | Mengawasi kasus yang tertunda dan mengoordinasikan penanganan antarunit. |
| 6. Administrator platform | Peran administrasi dan akses. | Mengelola sumber informasi, akun, dan hak akses pengguna internal. Konfigurasi koneksi API dilakukan secara teknis di luar UI MVP. |

### Keputusan pengurangan peran

Petugas pelaksana layanan tidak menjadi pengguna langsung versi awal. Customer service tetap dapat berkoordinasi dengan pelaksana melalui proses operasional yang tersedia.

Pengelola penyedia layanan atau mitra tidak menjadi pengguna langsung versi awal. Pembaruan informasi layanan ditangani administrator. Karena itu, fitur pengelolaan mandiri oleh mitra tidak termasuk scope awal.

Enam kelompok ini merupakan peta pengguna dan peran. Setiap kelompok tidak otomatis memerlukan aplikasi atau dashboard tersendiri.

## Skenario pelanggan

### 1 Penumpang

“Penerbangan saya terlambat sehari. Booking parkir saya habis malam ini. Harus bagaimana?”

Alur layanan: Verifikasi akses ke booking → baca booking dan kebijakan → jelaskan pilihan → arahkan ke halaman pengelolaan booking atau bantu membuat pengajuan kepada customer service.

Hasil yang diharapkan: Pengguna mengetahui langkah berikutnya dan mendapat nomor kasus jika mengajukan bantuan.

### 2 Pengunjung nonpenumpang

“Saya mau jemput keluarga di Terminal 2. Di mana saya bisa menunggu?”

Alur layanan: Klarifikasi apakah pengguna ingin menunggu di mobil atau masuk terminal → ambil informasi lokasi serta ketentuan → berikan arahan.

Hasil yang diharapkan: Pengguna mengetahui area menunggu dan akses yang sesuai.

### 3 Pendamping atau pemesan perwakilan

“Saya pesan lounge untuk ibu saya. Beliau sudah di lokasi, tetapi konfirmasinya belum ditemukan. Bisa bantu cek?”

Alur layanan: Verifikasi identitas dan periksa hubungan pemesan perwakilan yang sudah tercatat di database sumber → ambil booking yang diizinkan → tampilkan status dan cara mengakses konfirmasi yang tersedia → tawarkan bantuan customer service jika kendala belum selesai. Jika hubungan perwakilan tidak tercatat, data pribadi tidak ditampilkan.

Hasil yang diharapkan: Pemesan dapat membantu penerima layanan berdasarkan informasi pesanan yang berhak diaksesnya.

## Skenario operasional dan administrasi

### 4 Petugas customer service

“Pelanggan ini mengalami kendala apa, dan informasi apa yang masih kurang?”

Alur layanan: Buka kasus → lihat ringkasan, percakapan, dan referensi terkait → terima penanganan → lanjutkan melalui chat atau email sesuai kanal → catat hasil koordinasi dan tindak lanjut → perbarui status kasus.

Hasil yang diharapkan: Petugas dapat melanjutkan kasus tanpa meminta ulang informasi yang sudah tersedia. Klarifikasi tambahan tetap dapat dilakukan bila diperlukan.

### 5 Supervisor layanan bandara

“Ada kasus yang belum mendapat penanggung jawab?”

Alur layanan: Lihat antrean → filter kasus belum ditugaskan atau tertunda → periksa hambatan → tetapkan petugas → pantau tindak lanjut.

Hasil yang diharapkan: Setiap kasus memiliki penanggung jawab dan langkah berikutnya.

### 6 Administrator platform

“Petugas ini hanya boleh mengakses kasus milik organisasinya. Bagaimana mengaturnya?”

Alur layanan: Pilih akun dan organisasi → tentukan peran serta batas akses → tinjau izin → simpan pengaturan.

Hasil yang diharapkan: Akses pengguna internal sesuai tugas dan organisasinya.

### Batas penyelesaian

Permintaan yang tercatat belum berarti booking berubah atau refund disetujui. Status kasus bantuan tetap dibedakan dari status booking dan pembayaran. Pengguna perlu mengetahui hasil yang tersedia dan pihak yang harus bertindak berikutnya.

## Kebutuhan produk dan batas MVP

### Cakupan layanan

Versi pertama mencakup informasi umum bandara serta bantuan terkait booking yang sudah ada dan kendala transaksi untuk parkir dan lounge. Ketiga alur masuk versi pertama; pengembangan tidak dibatasi pada satu alur saja.

### Pengalaman utama

Website dapat digunakan melalui desktop dan ponsel, mendukung bahasa Indonesia dan Inggris. Pengguna dapat memilih kategori atau menjelaskan kebutuhannya melalui chat.

Menu utama: **Informasi bandara · Bantuan booking · Kendala transaksi · Kasus saya · Hubungi petugas**.

### Kebutuhan wajib dan kriteria penerimaan

| Kebutuhan wajib | Batas kemampuan MVP | Kriteria penerimaan |
| --- | --- | --- |
| Informasi bandara | Menjawab berdasarkan sumber informasi yang dikelola admin; meminta klarifikasi bila diperlukan. | Jawaban sesuai sumber dan konteks. Jika informasi tidak tersedia, asisten menyatakannya dan menawarkan jalur bantuan. |
| Bantuan booking | Membaca booking yang sudah ada, menjelaskan kebijakan, dan mengarahkan pengguna atau membantu pengajuan kasus. | Hak akses diperiksa sebelum data ditampilkan. Asisten tidak menyatakan booking berubah hanya karena pengajuan terkirim. |
| Bantuan transaksi | Membaca status pembayaran serta membantu pengajuan pengecekan atau pertanyaan refund. | Status mengikuti sistem sumber. Laporan pengguna dibedakan dari fakta yang tercatat; asisten tidak menjanjikan refund. |
| Pengajuan kasus | Mengumpulkan informasi, menampilkan ringkasan yang bisa dikoreksi, dan meminta konfirmasi sebelum mengirim. | Kasus benar-benar tercatat sebelum nomor referensi ditampilkan. |
| Pelacakan kasus | Menampilkan status, pembaruan terakhir, dan langkah berikutnya. | Pengguna mengetahui apakah menunggu petugas atau perlu melengkapi informasi. |
| Chat dengan petugas | Tombol selalu tersedia tanpa wajib melewati AI. Jika petugas offline, minta email untuk tindak lanjut. Petugas mengirim jawaban langsung ke email dan pengguna dapat membalas dari email. | Petugas menerima konteks percakapan dan detail terkait. Pengguna mengetahui apakah sedang mengantre atau menunggu balasan email. Pengguna tidak wajib kembali ke portal untuk membaca atau membalas tindak lanjut. |
| Percakapan multilingual | Indonesia dan Inggris, termasuk pergantian bahasa di tengah percakapan. | Konteks, nomor referensi, tanggal, dan nominal tetap terjaga. |
| Rating kepuasan | Rating opsional setelah bantuan atau percakapan selesai. | Rating tersimpan jika diberikan. Tidak memberi rating tidak dianggap puas. |

### Kebutuhan pengguna internal

| Peran | Kemampuan wajib |
| --- | --- |
| Customer service | Membuka antrean, menerima kasus, membaca konteks, membalas pelanggan, dan memperbarui status serta hasil penanganan. |
| Supervisor | Melihat kasus belum ditugaskan atau tertunda dan menetapkan penanggung jawab. |
| Administrator | Mengelola informasi layanan, akun, dan hak akses. |

Penumpang, pengunjung nonpenumpang, serta pendamping atau pemesan perwakilan memakai portal pelanggan yang sama. Akses data mengikuti kewenangannya.

### Tindak lanjut melalui email

Balasan petugas dikirim langsung melalui email, bukan hanya pemberitahuan yang mewajibkan pengguna membuka portal. Pengguna dapat membaca dan membalas dari aplikasi emailnya. Pesan masuk dan keluar tetap terkait dengan kasus yang sama dan tersedia bagi petugas pada riwayat penanganan.

Email tindak lanjut tidak otomatis memberikan hak akses ke booking atau transaksi. Kegagalan pengiriman tidak menghapus kasus dan tidak menandakan kasus selesai. Integrasi pengiriman dan penerimaan email perlu diuji kelayakannya sebelum memilih provider.

Detail pribadi booking dan transaksi hanya dikirim ke alamat email yang sudah diverifikasi dan berhak menerima data tersebut berdasarkan sistem sumber. Jika alamat kontak berbeda, detail pribadi tidak dikirim ke alamat itu sebelum kewenangannya terverifikasi. Balasan dari alamat yang belum dikenali tidak otomatis membuka akses atau mengubah hubungan pemesan; petugas meminta verifikasi sebelum membagikan data pribadi.

Kasus umum tanpa booking terkait dengan kontak pelanggan yang diverifikasi pada kasus tersebut, tanpa mengharuskan adanya pesanan di sistem sumber. Tindak lanjut berlangsung langsung melalui email. Verifikasi dapat menggunakan mekanisme melalui email; pengguna tidak diwajibkan kembali ke portal untuk membaca atau membalas jawaban petugas.

### Hak akses pengguna

Informasi publik dan bantuan umum dapat diakses tanpa login. Sebelum menampilkan data pribadi booking, transaksi, kasus, atau riwayat percakapan, sistem memverifikasi identitas dan hak akses pada data tersebut. Verifikasi yang gagal tidak membuka data pribadi; pengguna mendapat arahan untuk mencoba kembali atau meminta bantuan petugas.

| Pengguna | Batas akses |
| --- | --- |
| Pelanggan terverifikasi | Data miliknya yang terhubung dengan identitas dan hubungan pesanan yang tercatat pada sistem sumber. |
| Pemesan perwakilan | Data pesanan sesuai kewenangan perwakilan yang sudah tercatat di database sumber, setelah identitas diverifikasi. Pengakuan hubungan keluarga atau pengetahuan nomor booking tidak otomatis memberi akses. |
| Customer service | Kasus dalam organisasi dan antrean layanan yang menjadi kewenangannya, beserta informasi pendukung yang diperlukan. |
| Supervisor | Kasus dalam organisasi atau layanan yang diawasi; dapat menetapkan atau mengganti penanggung jawab. |
| Administrator | Informasi layanan, akun, dan hak akses. Akses isi kasus pelanggan memerlukan peran operasional tambahan yang sesuai. |

Airport AI Assistant tidak membuat atau mengubah hubungan perwakilan. Penerima layanan tidak otomatis memperoleh seluruh akses pemesan atau pembayar; hak akses mengikuti data dan kewenangan yang tercatat. Metode teknis verifikasi belum dipilih; usulan nomor booking dan verifikasi email masih harus diuji terhadap sistem sumber.

### Di luar MVP

- Pembuatan booking baru melalui AI.
- Pemrosesan pembayaran dan integrasi langsung ke payment gateway.
- Eksekusi perubahan booking, pembatalan, atau refund oleh asisten.
- WhatsApp, voice, aplikasi mobile native, dan kiosk.
- Portal tersendiri untuk pelaksana layanan atau pengelola mitra.
- Pemberian kuasa atau pembuatan dan perubahan hubungan pemesan perwakilan melalui Airport AI Assistant.
- Bahasa tambahan dan analytics lanjutan.
- Halaman konfigurasi koneksi API, routing canggih, estimasi waktu tunggu, dan penutupan kasus otomatis.

### Batas kualitas

Kegagalan membaca data atau mengirim permintaan harus terlihat jelas. Sistem tidak boleh mengarang status, menganggap pengguna yang berhenti chat sudah terbantu, atau menyamakan kasus ditutup dengan pelanggan puas. Kriteria penerimaan memeriksa apakah fitur bekerja; rating kepuasan merupakan umpan balik terpisah dan opsional.

### Siklus status kasus

| Status | Makna dan langkah berikutnya |
| --- | --- |
| Baru | Kasus sudah tercatat dan belum diterima petugas. CS atau supervisor menetapkan penanggung jawab. |
| Ditangani | Petugas sudah menerima kasus dan sedang melakukan penanganan. |
| Menunggu pelanggan | Petugas membutuhkan informasi tambahan; permintaan informasi dan cara membalas ditampilkan atau dikirim melalui email. |
| Menunggu pihak terkait | Petugas menunggu pemeriksaan atau tindakan unit/penyedia layanan. Penanggung jawab kasus tetap ada dan CS mencatat hasil koordinasi kembali ke kasus. |
| Ditutup | Penanganan diakhiri; alasan dan hasil penanganan wajib dicatat serta disampaikan kepada pelanggan. |

CS yang berwenang memperbarui status; supervisor dalam cakupannya dapat mengambil alih atau mengganti penanggung jawab. AI tidak memutuskan penutupan kasus. Alasan penutupan mencakup selesai ditangani, dibatalkan pelanggan, duplikat, atau tidak ada respons setelah tindak lanjut petugas. Penutupan karena tidak ada respons dilakukan manual dengan alasan tercatat; tidak dianggap kepuasan atau keberhasilan otomatis.

Jika pelanggan terverifikasi membalas bahwa masalah yang sama belum selesai, kasus yang sama dibuka kembali menjadi Ditangani jika masih memiliki penanggung jawab aktif, atau Baru jika perlu penugasan. Kebutuhan yang berbeda dicatat sebagai kasus baru. Riwayat perubahan status dan penanganan sebelumnya dipertahankan.

### Antrean dan pengambilalihan percakapan

Pengguna dapat langsung memilih Hubungi petugas dengan kategori dan penjelasan singkat tanpa wajib melewati percakapan AI. Jika belum ada kasus, kebutuhan dan ringkasan pengajuan dikonfirmasi pengguna sebelum pencatatan; jika kasus terkait sudah ada, percakapan dihubungkan ke kasus tersebut.

Aplikasi membedakan Menunggu petugas dan Sedang berbicara dengan petugas sebagai keadaan percakapan, terpisah dari status kasus. Setelah petugas menerima percakapan, nama petugas ditampilkan dan AI berhenti membalas otomatis. Petugas menerima bahasa, ringkasan jika tersedia, percakapan, hasil verifikasi, referensi terkait, serta langkah yang sudah dilakukan. Permintaan langsung tanpa konteks AI tetap dapat diproses.

Jika pengguna terputus sebelum atau setelah diterima, kasus dan riwayat tetap tersimpan. Pengguna dapat melanjutkan setelah verifikasi; jika alamat tindak lanjut yang sesuai sudah tersedia, petugas dapat mengirim balasan langsung melalui email. Jika belum ada email dan pengguna tidak kembali, sistem tidak menjanjikan tindak lanjut di luar sesi.

Jika petugas offline, aplikasi meminta email dan konfirmasi pengajuan, lalu menampilkan referensi setelah kasus tercatat. Penugasan MVP memakai antrean sederhana serta penerimaan oleh CS atau penetapan oleh supervisor. Tidak ada janji estimasi waktu tunggu.

Layanan petugas mendukung Indonesia dan Inggris. AI boleh menyiapkan draf terjemahan untuk diperiksa petugas sebelum dikirim; pesan asli tetap tersedia. Draf tidak terkirim otomatis sebagai jawaban petugas.

### Skenario kendala transaksi

Contoh: “Pembayaran pertama error, lalu saya bayar dengan cara lain. Bisa bantu pengecekan?”

Alur: verifikasi identitas dan akses transaksi → baca catatan yang tersedia → bedakan laporan pengguna dari fakta tercatat → minta informasi atau bukti yang masih kurang → tampilkan ringkasan yang bisa dikoreksi → minta konfirmasi → buat pengajuan pemeriksaan kepada CS → tampilkan nomor kasus dan langkah berikutnya. Permintaan yang sudah memiliki kasus dilanjutkan pada kasus terkait.

Hasil: kasus pemeriksaan tercatat lengkap. Asisten tidak menyimpulkan pembayaran ganda, mengubah status pembayaran, atau menjanjikan refund hanya berdasarkan laporan pengguna.

### Skenario pelacakan dan tindak lanjut

Contoh: “Kemarin saya melaporkan pembayaran parkir. Sekarang bagaimana?”

Alur: verifikasi akses kasus → pilih kasus terkait → tampilkan status, waktu pembaruan, perkembangan, dan pihak yang perlu bertindak → pelanggan dapat melengkapi informasi atau melanjutkan bantuan. Jika kasus ditutup tetapi masalah yang sama belum selesai, ikuti aturan pembukaan kembali. Pengguna yang memakai email dapat melanjutkan melalui balasan pada kasus yang sama tanpa membuka portal.

### Kriteria penerimaan tambahan

| Kondisi | Hasil yang dapat diperiksa |
| --- | --- |
| Pengguna tidak berhak atas data atau hubungan perwakilan tidak tercatat | Booking, transaksi, kasus, dan riwayat pribadi terkait tidak ditampilkan. Tidak ada perubahan hubungan perwakilan. |
| Data sumber tidak ditemukan | Asisten menyatakan data belum ditemukan dan menawarkan pemeriksaan referensi atau bantuan, tanpa menganggap booking/pembayaran pasti tidak ada. |
| Sistem sumber tidak tersedia | Status terbaru tidak dikarang; pengguna diberi informasi gangguan dan langkah bantuan. |
| Ringkasan pengajuan belum dikonfirmasi | Kasus belum dikirim. Pengguna dapat mengoreksi atau membatalkan pengajuan. |
| Hasil pengiriman belum diketahui | Sistem memeriksa apakah kasus sudah tercatat sebelum mengirim ulang; satu pengajuan yang sama tidak menghasilkan kasus duplikat. |
| Pelanggan menunggu balasan email | Petugas dapat mengirim jawaban lengkap; balasan pelanggan muncul pada riwayat kasus yang sama. Kegagalan kirim terlihat oleh petugas dan tidak menutup kasus. |
| Pengguna mengakses Kasus saya atau melanjutkan lewat email | Hak akses kasus diperiksa; email kontak saja tidak memberikan akses booking atau transaksi. |
| Kasus menunggu pelanggan | Informasi yang diperlukan dan cara melengkapinya tercantum. |
| Petugas menerima chat | Nama petugas tampil, konteks tersedia, dan AI berhenti merespons otomatis. |
| Pelanggan kembali setelah koneksi terputus | Setelah verifikasi, kasus dan riwayat yang berhak diakses dapat dilanjutkan. |
| CS melakukan koordinasi eksternal | Hasil dan langkah berikutnya dicatat pada kasus; tidak mengubah booking atau pembayaran melalui Airport AI Assistant. |
| Supervisor menetapkan petugas | Penanggung jawab tersimpan dan terlihat pada kasus; akses tetap mengikuti organisasi dan layanan yang berwenang. |
| Akun internal mencoba kasus di luar kewenangannya | Akses ditolak. Administrator tanpa peran operasional tidak dapat membaca isi kasus pelanggan. |
| Admin memperbarui informasi layanan | Jawaban berikutnya menggunakan informasi yang berlaku; pengaturan API tidak membutuhkan UI admin MVP. |
| Petugas menutup atau membuka kembali kasus | Alasan, hasil, penanggung jawab, dan riwayat perubahan dapat diperiksa. Status kasus tidak mengubah status booking atau transaksi. |
| Percakapan berpindah Indonesia/Inggris | Konteks dan nilai referensi, tanggal, serta nominal tetap sama; draf terjemahan petugas perlu diperiksa sebelum dikirim. |
| Portal dipakai di desktop atau ponsel | Menu, chat, ringkasan konfirmasi, pelacakan, dan akses petugas dapat digunakan tanpa kontrol utama terpotong. |

### Detail teknis untuk tahap berikutnya

Provider email, cara teknis verifikasi, API/database, dan desain layar terperinci ditentukan setelah uji kelayakan stack. Kemampuan email dua arah dan pencocokan identitas/hubungan pesanan perlu dibuktikan sebelum implementasi menyeluruh. Audit QA tidak otomatis menambah scope produk.

## Aturan bisnis data dan kewenangan agent

### Sumber informasi yang dipercaya

| Jenis informasi | Sumber acuan |
| --- | --- |
| Fasilitas, lokasi, jam operasional, dan prosedur bantuan | Informasi resmi yang dikelola administrator bandara. |
| Kebijakan parkir dan lounge | Kebijakan layanan yang berlaku untuk pesanan terkait. |
| Detail dan status booking | Sistem sumber booking. |
| Nominal, instruksi, dan status pembayaran atau refund | Catatan sistem transaksi terkait. |
| Perkembangan penanganan | Sistem kasus Airport AI Assistant, termasuk pembaruan petugas. |

Pengetahuan bawaan model bukan acuan status bisnis. Ketika sumber tidak tersedia atau saling bertentangan, asisten menjelaskan keterbatasan dan menawarkan pemeriksaan petugas. Cerita pelanggan dicatat sebagai laporan pengguna, dibedakan dari fakta yang dapat dicocokkan dengan catatan sistem atau hasil pemeriksaan petugas.

### Sistem yang perlu dihubungkan

- Sumber pengetahuan untuk informasi bandara dan kebijakan layanan.
- Sumber booking dan transaksi dengan akses baca untuk pesanan parkir serta lounge; keduanya dapat berasal dari backend yang sama.
- Sistem kasus untuk membuat pengajuan, membaca perkembangan, dan menyimpan penanganan.
- Email untuk mengirim jawaban petugas dan menerima balasan pada kasus yang sama.

CRM, ERP, dan payment gateway tidak diperlukan untuk scope MVP ini. Pemilihan implementasi sistem sumber dilakukan pada tahap teknis tanpa mengubah batas kewenangan asisten.

### Data pengguna yang diperlukan

| Kebutuhan | Data minimum |
| --- | --- |
| Pertanyaan umum | Isi pertanyaan, bahasa percakapan, dan terminal atau lokasi jika relevan. |
| Bantuan booking | Identitas terverifikasi, referensi booking, dan detail kendala. Data pesanan dibaca dari sumber. |
| Kendala transaksi | Identitas dan hak akses terverifikasi, referensi transaksi atau booking terkait, penjelasan kendala, serta bukti tambahan jika diperlukan. |
| Pengajuan bantuan | Ringkasan kebutuhan, referensi terkait jika ada, serta kontak untuk tindak lanjut di luar sesi. |
| Pemesan perwakilan | Identitas terverifikasi dan hubungan perwakilan yang sudah tercatat di database sumber. |

Asisten tidak meminta ulang informasi yang sudah tersedia. Penanganan kasus tidak membutuhkan PIN, OTP pembayaran, password, atau nomor kartu lengkap; asisten tidak meminta data tersebut. Verifikasi akses layanan berbeda dari otorisasi pembayaran.

### Batas kewenangan agent

| Boleh dilakukan agent | Di luar kewenangan agent |
| --- | --- |
| Meminta klarifikasi dan menjelaskan informasi bersumber. | Menetapkan kebijakan atau memberikan pengecualian. |
| Membaca data yang berhak diakses pengguna. | Membuka data orang lain berdasarkan pengakuan hubungan keluarga. |
| Menampilkan instruksi atau tautan dari sumber yang dipercaya. | Mengarang instruksi pembayaran, nomor rekening, atau status transaksi. |
| Menyiapkan dan membuat pengajuan setelah konfirmasi. | Membuat atau mengubah booking, memproses pembayaran, atau menyetujui refund. |
| Meneruskan percakapan dan menampilkan status kasus. | Memutuskan penutupan kasus atau mengubah hubungan perwakilan. |

Backend memeriksa izin setiap kali data atau tindakan diakses; model bukan penentu hak akses. Pembukaan kembali karena balasan pelanggan mengikuti aturan siklus kasus yang telah ditetapkan, bukan keputusan bebas agent.

### Konfirmasi pengguna

- Membaca informasi yang sudah diizinkan, mencari kebijakan, dan menjelaskan status tidak membutuhkan konfirmasi tindakan tambahan; pemeriksaan hak akses tetap berlaku.
- Pembuatan pengajuan baru atau pengiriman informasi tambahan atas nama pengguna ke kasus wajib dikonfirmasi.
- Sebelum pengiriman, tampilkan ringkasan masalah, data yang disertakan, dan tujuan penanganan. Pengguna dapat mengoreksi atau membatalkan.
- Pemilihan Hubungi petugas tidak memerlukan persetujuan berulang. Jika perlu kasus baru, cukup satu konfirmasi ringkasan sebelum pencatatan.
- Balasan email pelanggan pada kasus yang sama dicatat sebagai pesan masuk tanpa mewajibkan pelanggan membuka portal untuk konfirmasi. Pencatatan pesan tidak otomatis memberikan akses data pribadi; aturan identitas dan hak akses email tetap berlaku.

### Penyerahan ke manusia

Asisten menawarkan atau menjalankan eskalasi sesuai pilihan pengguna ketika:

- Pengguna meminta petugas.
- Permintaan memerlukan keputusan, pengecualian kebijakan, atau investigasi.
- Catatan sistem berbeda dari laporan pengguna dan perbedaan tidak dapat dijelaskan berdasarkan informasi yang tersedia.
- Informasi yang diperlukan tidak tersedia atau sumber gagal diakses.
- Kebutuhan tetap belum dipahami setelah klarifikasi yang wajar.

Eskalasi mengikuti aturan konfirmasi pengajuan dan kondisi petugas online/offline. Eskalasi tidak melewati kontrol akses; petugas tetap memverifikasi kewenangan sebelum membagikan data pribadi.

### Pengajuan dan tindak lanjut

- Identifikasi layanan dan pemilik penanganan sebelum mengirim kasus. Jika layanan berada di luar cakupan sistem, jelaskan batasnya dan berikan jalur resmi yang tersedia tanpa mengklaim kasus telah diterima penyedia tersebut.
- Jika kasus yang sama masih berjalan, lanjutkan kasus terkait; jangan membuat pengajuan duplikat.
- Jika hasil pengiriman belum diketahui, periksa pencatatan sebelum mencoba lagi.
- Setelah petugas mengambil alih, AI berhenti membalas otomatis.
- Permintaan terkirim tidak berarti booking berubah, pembayaran lunas, atau refund disetujui.
- Kasus menampilkan status, pembaruan terakhir, dan siapa yang perlu bertindak berikutnya.

Laporan pelanggan, catatan sistem, dan keputusan petugas dipertahankan sebagai informasi yang berbeda. Agent menghubungkan informasi tersebut untuk membantu penanganan sesuai kewenangannya.

## Rencana pengujian dan ukuran keberhasilan

Kualitas Airport AI Assistant didefinisikan melalui tujuh bidang engineering berikut. Aplikasi belum dianggap memenuhi kualitas hanya karena chat dapat menjawab. Definisi ini menjadi dasar desain, implementasi, dan pengujian; bukan klaim bahwa seluruh kemampuan sudah tercapai.

### 1 Enterprise AI Architecture and Engineering

Bidang ini menilai apakah fondasi sistem jelas, aman, dapat dikembangkan, dan mampu melayani beberapa organisasi.

Satu tenant adalah satu organisasi pelanggan, misalnya Bandara A dan Bandara B. Setiap tenant memiliki pelanggan, petugas, knowledge, kasus, dan konfigurasi. Penumpang dan CS adalah peran pengguna, bukan tenant. Batas organisasi dan layanan di dalam satu tenant tetap mengikuti hak akses yang sudah ditetapkan; hubungan tenant diturunkan dari identitas dan konfigurasi yang diperiksa backend.

Kualitas yang dituntut:

- Isolasi tenant: data, dokumen, percakapan, dan kasus tenant A tidak dapat diakses tenant B.
- Pemisahan tanggung jawab: AI memahami percakapan; backend memeriksa izin serta menjalankan tindakan; sistem sumber menentukan status booking dan pembayaran.
- API yang andal: masukan diperiksa, kesalahan dijelaskan, pengulangan permintaan ditangani, dan tindakan dapat ditelusuri.
- Kemampuan berkembang: penambahan pelanggan tidak mengharuskan penyalinan seluruh aplikasi.
- Komponen reusable: retrieval, konektor, pengajuan kasus, dan pemeriksaan akses dapat dipakai kembali dengan konfigurasi yang sesuai.
- Keputusan arsitektur mempertimbangkan keamanan, kapasitas, latency, biaya, dan keterbatasan resource.

Contoh pengujian: pengguna tenant A mencoba membuka kasus tenant B melalui URL atau API. Akses ditolak meskipun nomor kasus diketahui. Pengujian lintas tenant juga mencakup retrieval, riwayat, dan email.

Bukti: diagram arsitektur, aliran data, batas tanggung jawab, catatan keputusan teknis, pengujian isolasi, dan hasil pengujian kapasitas. Klaim scalable disertai kondisi dan batas yang telah diuji.

### 2 AI Agents and Workflow Automation

Bidang ini menilai apakah agent memahami kebutuhan dan menggunakan kemampuan sistem dengan benar sepanjang percakapan. Workflow Airport AI Assistant tetap terbatas pada bantuan informasi, pembacaan data, pengumpulan kebutuhan, pengajuan setelah persetujuan, dan penyerahan penanganan.

Kualitas yang dituntut:

- Memahami pertanyaan, kendala booking/transaksi, dan permintaan manusia, termasuk bahasa bebas atau beberapa kebutuhan dalam satu pesan.
- Meminta klarifikasi yang relevan tanpa mengulang informasi yang sudah tersedia.
- Memilih tool dan parameter sesuai permintaan serta hak akses pengguna.
- Mempertahankan pilihan, koreksi, bahasa, dan referensi pesanan.
- Menyimpan state sehingga percakapan dan pengajuan dapat dilanjutkan setelah gangguan koneksi.
- Meminta konfirmasi sebelum tindakan yang memerlukannya.
- Menyerahkan konteks kepada petugas dan menghentikan balasan otomatis setelah pengambilalihan.
- Mematuhi batas kewenangan, termasuk larangan membuat booking, memproses pembayaran, menyetujui refund, atau menetapkan hak akses.

Contoh pengujian: pengguna mengoreksi tanggal 10 menjadi 11. Ringkasan dan pengajuan memakai tanggal 11. Jika pengguna membatalkan sebelum konfirmasi, pengajuan tidak dikirim.

Bukti: skenario percakapan, hasil pemanggilan tool, perubahan state, dan catatan pengajuan. Evaluasi memeriksa perilaku serta hasil yang dapat diamati, bukan hanya penjelasan model tentang tindakannya.

### 3 LLMs and Open Source Model Customisation

Bidang ini menilai ketepatan pemilihan model dan penyesuaiannya dengan kebutuhan produk. Model dipilih untuk memenuhi kualitas dengan latency, biaya, dan resource yang masuk akal.

Kualitas yang dituntut:

- Kandidat model dievaluasi pada kumpulan tugas Airport AI Assistant yang sama.
- Bahasa Indonesia, Inggris, campuran bahasa, dan ungkapan sehari-hari diperiksa.
- Kemampuan klarifikasi, penggunaan tool, dan kepatuhan workflow dinilai.
- Pilihan API komersial atau open-source mempertimbangkan privasi, resource, biaya operasi, dan pemeliharaan.
- Optimasi prompt, konteks, model, atau konfigurasi diperiksa dampaknya pada kualitas dan biaya.
- Fine-tuning digunakan jika ditemukan masalah yang tidak cukup diselesaikan melalui pendekatan lebih sederhana.

| Kebutuhan | Pendekatan awal |
| --- | --- |
| Kebijakan layanan terbaru | Retrieval/RAG dari sumber yang dikelola. |
| Pemeriksaan izin dan validasi status | Kode backend serta aturan. |
| Pemahaman cerita dan ringkasan kasus | LLM dengan instruksi dan konteks. |
| Perilaku khusus yang konsisten gagal | Evaluasi perubahan prompt terlebih dahulu; fine-tuning dipertimbangkan jika diperlukan. |

Contoh pengujian: kandidat menerima kasus, data, dan tool yang sama; hasil dibandingkan berdasarkan ketepatan, pelanggaran kewenangan, latency, serta biaya.

Bukti: versi dan konfigurasi model, hasil perbandingan, alasan pemilihan, dan hasil sebelum/sesudah optimasi. Self-hosting, vLLM, LoRA, dan GPU hanya menjadi implementasi jika evaluasi membenarkannya.

### 4 RAG Knowledge Systems and Document Intelligence

Bidang ini menilai ketepatan pencarian informasi dan pemakaiannya dalam jawaban. Retrieval sumber yang relevan dan kesesuaian jawaban terhadap sumber dinilai terpisah.

Kualitas yang dituntut:

- Sumber memiliki asal, cakupan layanan, tenant, serta versi atau masa berlaku yang diperlukan.
- Retrieval sesuai pertanyaan, layanan, kewenangan, dan tenant.
- Jawaban didukung sumber; ketentuan, nominal, tanggal, dan batas layanan tidak dikarang.
- Pembaruan atau pencabutan kebijakan memengaruhi jawaban berikutnya, tanpa keliru menerapkan kebijakan baru pada pesanan yang mengikuti ketentuan lama.
- Sumber yang hilang atau bertentangan menghasilkan penjelasan keterbatasan dan jalur bantuan.
- Dokumen internal dan knowledge tenant lain tidak bocor pada hasil atau jawaban.
- Jika dokumen PDF atau tabel dipakai, informasi penting diekstraksi dengan benar.

Contoh pengujian: kebijakan parkir diperbarui; pertanyaan berikutnya memakai versi yang relevan. Kebijakan untuk booking lama tetap mengikuti aturan yang berlaku bagi pesanan tersebut.

Bukti: pertanyaan uji beserta sumber acuan, hasil retrieval, penilaian jawaban, serta pengujian pembaruan dan isolasi. Hybrid search, reranking, dan OCR dipilih berdasarkan masalah; tender analysis dan proposal generation tidak masuk fungsi MVP.

### 5 Cloud Architecture and Enterprise Security

Bidang ini menilai apakah aplikasi dapat dijalankan, dilindungi, dipantau, dan dipulihkan secara terkendali.

Kualitas yang dituntut:

- Deployment dapat diulang melalui instruksi dan konfigurasi yang tersedia.
- Lingkungan serta rahasia dipisahkan; kredensial tidak tersimpan dalam repository atau ditampilkan kepada pelanggan.
- Kontrol akses dilakukan backend, bukan hanya pada UI atau instruksi AI.
- Prompt injection pada pesan, dokumen, atau email tidak memberikan kewenangan baru kepada agent.
- Aturan pengiriman, penyimpanan, logging, retensi, dan penghapusan data ditetapkan serta diperiksa.
- Lokasi pemrosesan data diketahui, termasuk pengiriman ke model, email, dan layanan eksternal.
- Backup dapat dipulihkan dan prosedur restore dicoba pada lingkungan terpisah.
- Build dan deployment memiliki pemeriksaan yang relevan serta jalur pemulihan rilis gagal.

Contoh pengujian: dokumen knowledge memerintahkan agent membuka semua booking; sistem tidak menjalankannya. Pengujian lain memulihkan kasus dan riwayat dari backup.

Bukti: desain akses dan aliran data, konfigurasi deployment, pemeriksaan rahasia, hasil pengujian serangan, dan backup/restore. Kebutuhan enterprise/government diterjemahkan menjadi kontrol spesifik; implementasi kontrol tidak otomatis berarti sertifikasi ISO 27001, SOC 2, atau pemenuhan seluruh persyaratan suatu instansi.

### 6 AI Performance Quality and Cost Optimisation

Bidang ini menilai kualitas hasil, keandalan, kecepatan, dan biaya secara bersama.

Kualitas yang dituntut:

- Accuracy: jawaban serta data sesuai sumber acuan.
- Task completion: tindakan yang disetujui mencapai hasil yang dijanjikan dalam scope.
- Reliability: pengulangan, gangguan, dan permintaan bersamaan tidak merusak konsistensi atau membuat duplikasi.
- Fallback dan recovery: pengguna mendapat status serta langkah berikutnya ketika model atau sumber gagal.
- Latency: waktu respons diukur dari pengalaman pengguna, termasuk tool dan pengajuan.
- Biaya: penggunaan model, retrieval, email, penyimpanan, dan infrastruktur diketahui.
- Observability: tahap kegagalan dapat ditelusuri dengan logging/tracing yang tidak membocorkan rahasia atau data pribadi berlebihan.

Contoh pengujian: kasus telah tersimpan tetapi respons API terputus. Percobaan ulang tidak membuat kasus kedua. Pengujian beban juga memeriksa akses tenant dan konsistensi state ketika permintaan berjalan bersamaan.

| Ukuran | Pertanyaan yang dijawab |
| --- | --- |
| Ketepatan jawaban | Apakah jawaban sesuai sumber dan situasi? |
| Keberhasilan tindakan | Apakah pengajuan tercatat atau handoff diterima? |
| Keandalan | Apakah gangguan dan pengulangan ditangani dengan benar? |
| Status penanganan | Mengapa kasus masih terbuka atau ditutup? |
| Kepuasan | Bagaimana penilaian pelanggan yang memberikan rating? |
| Latency dan biaya | Berapa waktu dan biaya untuk menyelesaikan alur tersebut? |

Bukti: evaluasi otomatis, pemeriksaan manusia pada sampel, tracing, pengujian beban/gangguan, dan laporan biaya. Angka kapasitas, latency, serta biaya ditetapkan berdasarkan uji kelayakan sebelum pengujian penerimaan, dengan resource pengembangan sebagai batas eksplisit.

Evaluasi menggunakan kasus dan hasil yang diharapkan, memeriksa retrieval dan jawaban secara terpisah, serta menjalankan ulang sebagian percakapan untuk mengukur konsistensi. Pengujian alur lengkap mencakup pelanggan, AI/menu, kasus, petugas, email, dan penutupan atau tindak lanjut.

Manfaat AI juga diperiksa terhadap menu/form untuk tugas yang sama: ketepatan hasil, waktu, jumlah langkah, pengulangan informasi, dan rating opsional. Jika AI tidak membantu suatu langkah, gunakan alur terstruktur pada langkah tersebut.

### 7 Technical Leadership and Enterprise Delivery

Bidang ini menilai apakah proyek dapat dipahami, dikerjakan, diserahkan, dan dipertanggungjawabkan.

Kualitas yang dituntut:

- Kebutuhan dapat ditelusuri ke masalah dan fitur yang dibangun.
- Scope konsisten; kemampuan model/framework bukan alasan tunggal menambah fitur.
- Alasan, konsekuensi, dan keterbatasan keputusan utama didokumentasikan.
- Risiko integrasi dan resource diuji sebelum menjadi dependensi besar.
- Temuan review ditindaklanjuti dan perbaikannya diperiksa.
- Orang lain dapat menjalankan aplikasi, mencoba skenario, dan memahami hasil.
- Klaim sesuai bukti, membedakan kemampuan teruji, keterbatasan, dan pekerjaan lanjutan.

Contoh pengujian: reviewer mengikuti panduan, menjalankan aplikasi, mengirim pengajuan pelanggan, dan memeriksa kasus pada sisi CS. Hasil ditelusuri ke kebutuhan product contract.

Bukti: product contract, dokumentasi arsitektur, catatan keputusan, rencana pengerjaan, hasil review, panduan deployment, dan laporan pengujian. Praktik delivery ditunjukkan oleh artefak serta hasil; pengalaman memimpin atau mentoring tim nyata memerlukan bukti tersendiri.

### Penerimaan hasil kerja dan status bukti

Setiap bidang mengikuti urutan: kebutuhan → keputusan desain → implementasi → pengujian → bukti → hasil dan keterbatasan.

Dokumen desain membuktikan pendekatan yang direncanakan. Implementasi membuktikan kode tersedia. Pengujian membuktikan perilaku pada kondisi yang diperiksa. Tidak ada klaim aman, scalable, atau production-grade yang diterima hanya dari penjelasan Codex.

Laporan pengujian mencatat skenario, kondisi/lingkungan, versi model dan aplikasi, hasil yang diharapkan, hasil aktual, bukti, serta keputusan lulus atau perlu perbaikan. Belum diuji tidak disamakan dengan lulus. Target numerik yang belum ditentukan ditandai terbuka sampai disepakati sebelum pengujian penerimaan.

Kualitas dievaluasi berdasarkan tujuh bidang; daftar teknologi dan kanal pada lowongan tidak otomatis menjadi fitur wajib. Booking baru, eksekusi pembayaran/refund, WhatsApp, voice, dan portal mitra tetap di luar MVP. Untuk scope Airport AI Assistant, pemeriksaan booking berarti ketepatan pembacaan dan bantuan pengajuan, sedangkan pencegahan duplikasi berarti kasus atau pesan terkait, bukan eksekusi pembayaran baru.

## Rancangan teknis awal dan rencana pengerjaan

### Status keputusan

Backend modular dan PostgreSQL yang di-host di Neon dipilih sebagai arah arsitektur. Deployment diarahkan ke VPS cloud untuk aplikasi dan worker, dengan K3s sebagai target pembuktian Kubernetes setelah uji resource. AWS Lightsail menjadi kandidat hosting pertama, bukan pembelian atau komitmen paket. Kandidat framework, model, email, ukuran server, region, dan biaya final masih memerlukan uji kelayakan.

### Arsitektur aplikasi

Portal pelanggan dan ruang kerja CS, supervisor, serta administrator mengakses backend Airport AI Assistant. Backend menangani identitas, tenant, izin, percakapan, konfirmasi, kasus, dan audit. Agent menggunakan knowledge serta konektor sumber melalui tindakan yang dibatasi backend. Konektor booking/transaksi hanya membaca; konektor sistem kasus dan email mengikuti kewenangan pengajuan serta penanganan yang sudah ditetapkan.

PostgreSQL Airport AI Assistant menyimpan percakapan, kasus, konfigurasi, dan hubungan akses yang diperlukan. Status booking dan pembayaran tetap mengacu pada sistem sumber. AI tidak mengakses database secara bebas atau menentukan hak akses.

Backend dibagi menjadi modul identitas/tenant/akses, percakapan/orkestrasi, knowledge/retrieval, kasus/penugasan/handoff, konektor sumber, email/pekerjaan latar belakang, dan observability/evaluasi. Batas modul dipertahankan meskipun berada dalam satu codebase.

### Stack awal

| Komponen | Pilihan atau kandidat | Alasan dan validasi |
| --- | --- | --- |
| Frontend | React dan TypeScript sebagai kandidat | Portal pelanggan serta ruang kerja internal dalam satu aplikasi; validasi alur desktop dan ponsel. |
| Backend | Python dan FastAPI sebagai kandidat utama | API, validasi data, dan integrasi AI; verifikasi kompatibilitas serta biaya operasional. |
| Orkestrasi agent | LangGraph sebagai kandidat | Alur bertahap, konfirmasi, state persistence, dan handoff; perlu membuktikan perilaku restart serta kecocokan penyimpanan state. |
| Database | PostgreSQL di Neon | Memindahkan beban database dari laptop dan mempertahankan penggunaan PostgreSQL standar. |
| Vector search | pgvector di PostgreSQL sebagai kandidat | Menghindari database vector tambahan; validasi retrieval dan isolasi tenant. |
| Model | API model hosted untuk pendekatan awal; vendor/model belum dipilih | Menghindari beban LLM lokal; pilihan berdasarkan evaluasi kualitas, latency, biaya, dan aliran data. |
| Email | Layanan pengiriman dan penerimaan email; provider belum dipilih | Wajib membuktikan email dua arah terkait kasus dan kontrol identitas. |
| Pekerjaan latar belakang | Worker ringan dengan antrean persisten di PostgreSQL sebagai kandidat | Email dan retry terkontrol tanpa langsung menambah Redis; uji klaim pekerjaan, retry, restart, dan duplikasi. |
| Pengujian | pytest dan pengujian browser sebagai kandidat | Pengujian backend, integrasi, dan alur lengkap; alat browser final ditentukan kemudian. |

Framework adalah pilihan implementasi yang perlu dibuktikan; perubahan kandidat setelah pengujian dicatat beserta alasannya tanpa mengubah scope produk.

### Backend modular dan pemisahan proses

Arsitektur awal tidak memecah seluruh domain menjadi microservice. Tujuannya mengendalikan deployment, debugging, resource, konsistensi data, dan pengujian sambil mempertahankan pemisahan tanggung jawab. Backend dapat direplikasi dan worker dapat dijalankan terpisah dari codebase yang sama; pemisahan proses ini tidak otomatis merupakan microservices.

Komponen yang dijalankan adalah frontend, backend API modular, dan worker, dengan Neon di luar cluster. Jika pemrosesan dokumen atau agent memerlukan kapasitas, isolasi kegagalan, atau siklus rilis berbeda berdasarkan hasil pengukuran, pemisahan service dievaluasi. Pemisahan memerlukan keputusan kepemilikan data, kontrak API, autentikasi antarlayanan, dan penanganan gangguan jaringan.

Kubernetes merupakan pilihan deployment, terpisah dari keputusan pembagian aplikasi. Kualitas production dinilai melalui tujuh bidang dan bukti pengujian, bukan jumlah service.

### Neon dan portabilitas database

Gunakan driver PostgreSQL standar, migration yang tersimpan dalam repository, dan prosedur export/restore yang diuji. Fitur khusus Neon digunakan jika manfaatnya jelas. Pemilihan Neon tidak menghilangkan kewajiban pengujian isolasi maupun backup/restore.

Validasi koneksi, latency berdasarkan region backend/database, connection pooling, dan konteks tenant ketika koneksi digunakan bergantian. Periksa hak database aplikasi dan kebijakan isolasi dengan kredensial runtime yang sesungguhnya. Ukur respons setelah idle/compute resume terpisah dari respons saat aktif. Batas paket, storage, koneksi, biaya, lokasi data, dan retensi pemulihan harus diperiksa sebelum paket dipilih.

### Integrasi nyata dan simulasi

| Komponen | Rencana implementasi dan batas bukti |
| --- | --- |
| Model AI | Penggunaan API nyata setelah akses, kualitas, dan biaya lolos uji kelayakan. |
| Booking dan transaksi | Service sumber referensi terpisah dengan data uji serta API baca; mencakup hubungan identitas, tenant, dan perwakilan. Bukan klaim integrasi vendor booking/payment. |
| Sistem kasus | Bagian Airport AI Assistant dengan pengajuan, penugasan, status, dan riwayat yang benar-benar tersimpan. |
| Email | Kirim dan terima nyata pada alamat pengujian, dicocokkan ke kasus yang sama. |
| Gangguan | Simulator untuk timeout, sumber tidak tersedia, data tidak ditemukan, dan pesan/permintaan berulang; dibedakan dari integrasi nyata. |

Service sumber referensi menjaga pengembangan dan demo tidak terikat ketersediaan vendor booking. Konektor vendor dapat menjadi pengembangan berikutnya tanpa menjadi syarat MVP. Konektor mengganti cara akses sistem, bukan aturan otorisasi atau hasil bisnis.

### Deployment dan Kubernetes

Arah deployment adalah VPS Linux cloud untuk frontend, FastAPI, dan worker; Neon menyediakan database di luar VPS. AWS adalah pilihan penyedia cloud dan VPS adalah bentuk komputasi, sehingga keduanya tidak saling eksklusif. AWS Lightsail menjadi kandidat pertama untuk dibandingkan dengan VPS lain berdasarkan biaya, region, resource, akses jaringan, dan operasional.

Frontend dapat disajikan dari VPS yang sama. Vercel tidak menjadi dependensi MVP; dapat dievaluasi jika preview/frontend hosting memberi manfaat. Seluruh backend dan worker tidak diasumsikan mengikuti model eksekusi Functions.

K3s dipilih sebagai kandidat distribusi Kubernetes di VPS setelah kelayakan resource terbukti. EKS tidak menjadi pilihan deployment awal karena biaya cluster dan resource tambahan perlu pembenaran. Ukuran cluster dan paket belum dipilih. Tidak ada pembelian atau provisioning berbayar yang dilakukan melalui keputusan rancangan ini.

Kubernetes diuji untuk pemulihan proses, penambahan replika dengan state/tenant yang konsisten, readiness, rolling update/rollback, dan resource requests/limits berdasarkan pengukuran. Worker diuji agar retry/restart tidak menggandakan kasus atau email. Rancangan komunikasi chat dan pembaruan antarreplika harus diputuskan sebelum klaim horizontal scaling diuji; state lokal proses tidak boleh diasumsikan tersedia pada replika lain.

Satu VPS atau satu node tidak memberikan high availability terhadap kegagalan mesin. Uji pemulihan pod tidak dianggap bukti ketahanan terhadap node mati. Ketahanan lintas mesin memerlukan rancangan serta pengujian tambahan; tidak diklaim dari deployment satu node.

Pengembangan harian tidak wajib menyalakan Kubernetes. Docker digunakan bila membantu; seluruh stack tidak diwajibkan selalu berjalan dalam Docker. Cluster pengujian dinyalakan saat pengujian deployment sesuai resource dan anggaran. Pengelolaan VPS meliputi patch OS, firewall, TLS, monitoring, dan pemulihan.

### Risiko dan uji kelayakan awal

| Risiko | Bukti awal yang dicari |
| --- | --- |
| Email dua arah sulit, terbatas akses, atau biaya tidak sesuai | Kirim balasan petugas, balas dari email pelanggan, cocokkan kasus, dan uji kegagalan serta identitas pengirim. |
| Sumber tidak menyediakan hubungan akses yang diperlukan | Periksa kontrak data booking/transaksi, kepemilikan, tenant, dan perwakilan; jangan mengandalkan hubungan yang tidak tersedia. |
| Kebocoran tenant | Minimal dua tenant dengan percobaan akses silang sejak fondasi backend. |
| Model mahal atau tidak konsisten | Kumpulan tugas yang sama, pencatatan kualitas, latency, token, dan biaya. |
| Resource lokal atau VPS tidak cukup | Jalankan stack minimum dan ukur RAM/CPU sebelum menambah komponen atau cluster. |
| Pengajuan/pesan ganda saat gangguan | Simulasikan pencatatan berhasil tetapi respons hilang, retry, restart, serta pesan berulang. |
| Koneksi atau compute Neon terputus/idle | Uji koneksi pulih, pooling, waktu resume, konteks tenant, serta operasi ulang yang aman. |

### Biaya dan batas investasi

Anggaran memisahkan biaya tetap (VPS, database, penyimpanan, email/domain), biaya penggunaan (model, embedding, email, dan trafik), serta waktu setup/debugging/pemeliharaan. Nominal belum dikunci; harga, paket, region, dan batas akun harus diverifikasi sebelum memilih.

Setiap uji kelayakan diberi batas waktu dan batas pengeluaran sebelum dimulai. Langganan dan resource berbayar tidak ditambahkan tanpa kebutuhan serta biaya yang jelas dan persetujuan pengguna. Jika kandidat tidak layak, catat kendala dan evaluasi alternatif, bukan menggantungkan seluruh produk pada kandidat tersebut.

### Urutan implementasi

1. Uji dependensi berisiko: email dua arah, model, kontrak akses sumber, Neon, dan resource lokal/deployment.
2. Bangun fondasi tenant, identitas, hak akses, database, dan API dasar.
3. Bangun proses customer service: kasus, penugasan, status, percakapan petugas, serta email; alur dasar dapat diperiksa tanpa AI.
4. Tambahkan knowledge dan agent: retrieval, klarifikasi, pembacaan data, konfirmasi, serta pengajuan.
5. Lengkapi ketiga alur MVP: informasi, bantuan booking, dan kendala transaksi untuk parkir dan lounge.
6. Kalibrasi melalui spike dan baseline kualitas, keamanan, reliability, resource, latency, serta biaya. Setelah kalibrasi, kunci versi dataset, kondisi resource/beban, dan ambang penerimaan sebelum menjalankan acceptance serta regresi dengan ambang tetap. Perubahan ambang harus dicatat dan tidak dipakai untuk meluluskan hasil yang sebelumnya gagal secara diam-diam.
7. Deploy dengan konfigurasi yang dapat diulang; uji Kubernetes, rollback dan pemulihan. Jalankan kembali pengujian penerimaan/regresi yang relevan pada konfigurasi rilis final; hasil lokal tidak otomatis membuktikan hasil deployment. Siapkan bukti portfolio serta keterbatasan yang ditemukan.

Ketiga alur tetap target MVP; urutan pengerjaan membantu pelacakan kegagalan dan tidak menghapus scope. Penyusunan repository mengikuti keputusan produk dan rancangan ini. Pilihan vendor, akses akun, anggaran, dan rincian teknis yang belum terbukti tetap ditandai sebagai kandidat, bukan hasil uji.

### Referensi untuk keputusan awal

- [Microservices architecture Microsoft](https://learn.microsoft.com/en-us/azure/architecture/microservices/) untuk tradeoff kompleksitas dan operasional.
- [Kubernetes production environment](https://kubernetes.io/docs/setup/production-environment/) untuk pemisahan lingkungan belajar dan tuntutan production.
- [Neon pgvector](https://neon.com/docs/extensions/pgvector), [connection pooling](https://neon.com/docs/connect/connection-pooling), dan [scale to zero](https://neon.com/docs/introduction/scale-to-zero).
- [AWS Lightsail FAQ](https://aws.amazon.com/lightsail/faq/), [EKS FAQ](https://aws.amazon.com/eks/faqs/), dan [persyaratan K3s](https://docs.k3s.io/installation/requirements).
- [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python) dan [Functions limits](https://vercel.com/docs/functions/limitations). Kapabilitas dan batas provider diperiksa kembali pada uji kelayakan.

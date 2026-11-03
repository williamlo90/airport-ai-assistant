# Acuan visual Sydney — pemeriksaan sebelum coding

Tanggal pemeriksaan: 3 November 2026. Pengguna menunjuk tab Chrome **Theme Sydney** pada [demo Sydney](https://sydney.premiumplus.io/hc/en-us). Acuan sekarang adalah keadaan tab tersebut: light mode, aksen biru, homepage dengan foto latar, judul dan search di area tengah, serta navigasi yang berubah menjadi biru saat halaman digulir. Preview hijau dari Marketplace tetap sumber pengenalan tema, bukan acuan warna utama lagi.

Dokumen ini mencatat pengamatan DOM/computed style dan screenshot yang dilihat di browser. Angka adalah ukuran referensi, bukan kode tema yang disalin atau token aplikasi yang semuanya sudah disetujui. Belum ada halaman Airport AI Assistant yang dibuat atau dinilai setara secara visual.

## 1. Tampilan yang dikunci

- URL: https://sydney.premiumplus.io/hc/en-us.
- Root light mode; header type1/style1/fixed dan welcome type1/style1/large pada DOM saat diperiksa.
- Header di atas hero bersifat transparan dengan teks putih. Pada scroll400px, header yang terlihat memiliki bidang biru dan tetap berada di atas.
- Foto latar menutupi area hero dengan lapisan gelap; judul putih, search putih dan tombol search biru.
- Container hero berada di tengah halaman, tetapi **teks judul di dalamnya rata kiri**, bukan center aligned.
- Tiga helper referensi adalah FAQ, Guides dan Submit a request, dengan ilustrasi garis hitam, lingkaran putih dan caption. Ini bukan tiga layanan produk kita yang harus disalin.
- Toolbar50px di paling atas berisi Sydney, Dark mode, Color variant, Layout options, Buy theme dan Get a custom theme. Ini kontrol demonstrasi tema, **tidak dimasukkan ke aplikasi**.
- Tidak mengubah pilihan warna, layout, style atau dark mode milik pengguna saat audit. Viewport sementara dikembalikan ke ukuran desktop awal setelah pemeriksaan.

## 2. Pengukuran desktop

Browser awal: viewport CSS1920×855, DPR1, area dokumen1905px karena scrollbar15px. Posisi y di bawah termasuk toolbar demo50px; saat membandingkan aplikasi, toolbar tersebut harus dikeluarkan dari perhitungan. Nilai pecahan dibulatkan ke dua desimal.

| Bagian | Yang terukur |
| --- | --- |
| Header tema | Tinggi88px; logo visual di tengah, Categories/Submit a request di kiri, pencarian/Sign in di kanan. |
| Hero | Tinggi607.80px, dari y50 sampai657.80. Konten memakai ruang atas/bawah yang besar; ini perlu dinilai lagi dengan isi bandara. |
| Container hero | max-width880px, padding horizontal32px; lebar konten aktual816px. |
| Judul utama | Lato700,48px, line-height52.8px; margin bawah48px; rata kiri. |
| Search utama | Lebar816px, tinggi63px; input18px/27px, padding18px16px. Sudut kiri sekitar8px; bagian kanan mengikuti komposisi tombol, bukan semua sudut pill. |
| Tombol/link header | Tinggi40px, teks16px/24px, padding7px23px, radius50px; putih transparan saat di atas hero. |
| Container helper | Tiga kolom, tiap kolom410.66px termasuk padding16px kiri/kanan; lebar helper378.66px. Jarak antar konten kolom32px. |
| Helper | Padding24px, konten rata tengah; ikon50×50px dalam lingkaran80×80px. Jarak lingkaran ke judul24px. |
| Judul helper | Lato700,24px/31.2px, margin bawah12px. |
| Deskripsi helper | Lato400,16px/24px, lebar teks232px, dua baris pada sampel yang dilihat. |
| Ritme section | Helper48px atas/bawah; category blocks64px; knowledge/activity96px. Bukan satu padding yang dipaksakan ke semua bagian. |
| Tombol CTA bawah | Tinggi48px,16px/24px weight700, padding11px31px, radius50px. |

Kombinasi ukuran font, font weight, lebar baris dan jarak antarbagian harus diperiksa bersama. Mempertahankan warna saja tidak cukup menghasilkan proporsi yang sama. Search tambahan milik overlay/drawer ada di DOM, tetapi bukan search utama yang selalu terlihat; jangan mengukurnya sebagai komponen hero.

## 3. Warna yang diamati

| Peran | Nilai computed style |
| --- | --- |
| Aksen tombol/link utama | #1863BC, rgb(24,99,188). |
| Teks utama | #000C20, rgb(0,12,32). |
| Surface pucat | #F1F4FA, rgb(241,244,250), juga muncul pada pseudo-element beberapa section. |
| Putih | #FFFFFF pada input, lingkaran helper dan teks hero. |
| Deskripsi sekunder referensi | #8290A8, rgb(130,144,168). |
| Tombol header di atas hero | rgba(255,255,255,0.16) dengan teks putih. |

Nilai ini hasil ukur, bukan izin untuk mengabaikan keterbacaan. Rasio pasangan #8290A8 pada #F1F4FA sekitar2.93:1 dari perhitungan luminance; token teks sekunder aplikasi perlu digelapkan untuk target proyek4.5:1 pada teks normal. Perubahan kecil untuk keterbacaan dicatat saat membandingkan, bukan dianggap kegagalan mengikuti Sydney. Kontras teks hero juga bergantung pada foto pengganti dan overlay akhirnya.

## 4. Pengamatan responsif

| Viewport yang diuji | Perilaku yang terukur |
| --- | --- |
| Desktop1920×855 | Judul48px/52.8px; hero607.80px; helper tiga kolom. |
| Tablet768×900 | Area dokumen753px; judul48px/52.8px dengan lebar721px; hero439.33px; helper masih tiga kolom, masing-masing229.66px. |
| Mobile390×844 | Area dokumen375px; judul24px/31.2px, margin atas24px/bawah16px; lebar konten343px dengan gutter16px; hero264.44px; header72px. |
| Mobile390×844, helper | Susunan satu kolom; lebar343px, tinggi240px per helper dan padding24px. Section helper memakai24px atas/bawah. Search utama tinggi50px. |

Screenshot awal segera setelah resize dapat masih memperlihatkan desktop yang diperkecil. Audit memeriksa ulang innerWidth/computed layout dan screenshot setelah reflow; ukuran mobile di tabel berasal dari layout mobile yang sudah berubah. Hanya titik ukuran di atas yang diuji, bukan klaim seluruh breakpoint sudah ditemukan.

Untuk halaman kita, empat pilihan bantuan tetap mengikuti frontend spec. Sydney yang diamati memiliki tiga helper, sehingga komposisi empat pilihan perlu ditunjukkan pada prototipe C-01 dan dinilai pengguna. Jangan membuang satu kebutuhan demi menyesuaikan contoh. Akses Hubungi petugas tetap dapat ditemukan tanpa melewati AI. Susunan dan tinggi setiap helper pada mobile tidak disalin tanpa memeriksa posisi tindakan utama.

## 5. Font yang dapat digunakan

Computed font-family pada body, judul, link dan search adalah **Lato, sans-serif**. Halaman menunjuk stylesheet Google Fonts untuk Lato weights100/300/400/700/900. Sampel elemen utama yang diukur memakai400 dan700. Ini memverifikasi family dan konfigurasi CSS; inventaris aset browser tidak mengekspos berkas font pada sesi ini, sehingga tidak dijadikan bukti nama berkas/versi biner atau glyph yang benar-benar dirender.

Distribusi [Lato di Google Fonts](https://github.com/google/fonts/tree/main/ofl/lato), dengan [OFL.txt resmi](https://raw.githubusercontent.com/google/fonts/main/ofl/lato/OFL.txt), memakai **SIL Open Font License1.1**. Ini menjelaskan font demo yang diamati; proyek tidak membundel Lato.

Pilihan implementasi pengguna: **Tuffy 1.28 regular400/bold700**, dari [penulis asli](https://tulrich.com/fonts/) yang merilisnya sebagai public domain, dengan pemberian izin tanpa syarat bila pelepasan public domain tidak dimungkinkan. Berkas font dan pernyataan asli disimpan lokal pada prototype. Sumber, checksum, dan provenance hero berada di [README prototype](../../frontend/prototype/README.md). Periksa Tuffy terpakai sebelum menilai lebar teks dan line wrapping; metriknya berbeda dari Lato.

## 6. Inventaris hak penggunaan aset

| Aset yang diamati | Temuan dan keputusan |
| --- | --- |
| Lato | Lisensi distribusi resmi diperiksa; dapat dibundel dengan kepatuhan OFL di atas. |
| Logo HelpCenter/Sydney/Premium Plus | Ganti dengan wordmark Nama Bandara — AI Assistant dan identitas kita. Tidak mengambil logo demo. |
| Foto meja, kopi, buku dan ponsel pada hero | Dimuat dari /hc/theming_assets milik demo. Tidak ditemukan bukti lisensi ulang aset individual dari halaman/panduan yang diperiksa. Belum disetujui untuk aset aplikasi; gunakan foto milik sendiri atau sumber berlisensi terverifikasi yang sesuai bandara. |
| Ilustrasi helper50px dan versi alternatif160px | Berupa image terpisah pada CDN tema. Keberadaan stylesheet Font Awesome tidak membuktikan ilustrasi ini adalah aset Font Awesome Free. Buat SVG sendiri atau pilih paket ikon dengan lisensi jelas; jangan mengambil PNG tema begitu saja. |
| Font Awesome6.4.2 | Stylesheet terdeteksi. [Lisensi distribusi Free versi6.4.2](https://raw.githubusercontent.com/FortAwesome/Font-Awesome/6.4.2/LICENSE.txt) membedakan SVG/JS icons CC BY4.0, font OFL dan code MIT. Jika kelak dipilih, gunakan distribusi Free resmi dan pertahankan attribution, bukan menganggap semua ikon demo otomatis tercakup. |
| Gambar artikel/CTA, custom CSS/JS tema | Tidak dibundel ke proyek. Izin memakai tema asli harus dinilai dari lisensi yang sesuai jika kelak ingin memakai berkasnya. Implementasi aplikasi ditulis sendiri. |
| Tracking pixel dan script analytics demo | Tidak dibutuhkan, tidak disalin, dan bukan bagian rancangan aplikasi. |

Panduan [penggantian gambar Premium Plus](https://guide.premiumplus.io/hc/en-us/articles/5848248297876-How-to-add-images-in-a-Premium-Plus-theme) menjelaskan mekanisme mengganti aset, bukan pemberian izin terpisah untuk mengambil gambar demo. Status belum disetujui berarti provenance/lisensi belum cukup untuk pemakaian kita; bukan pernyataan bahwa pemilik tidak mungkin memberikan izin.

## 7. Penerapan ke halaman pertama

Implementasi pertama hanya **C-01 Beranda bantuan**. Gunakan Tuffy pilihan pengguna, ritme ruang, keluarga warna biru/putih/pucat dan hirarki Sydney sebagai acuan, lalu isi dengan identitas serta kebutuhan bandara kita. Header demo/buy-theme, copy pemasaran dan foto asli demo tidak dipakai. Foto hero dihasilkan khusus untuk proyek dan ikon ditulis sebagai SVG sendiri; provenance berada di README prototype.

Bandingkan di browser pada viewport yang sama: proporsi header/hero, lebar konten, posisi dan alignment judul, wrapping teks, tinggi input, ukuran ikon, jarak kategori dan perilaku mobile. Perbandingan mempertimbangkan adaptasi yang sengaja dipilih: branding, empat kebutuhan bantuan, aset asli dan perbaikan keterbacaan. Tidak memakai skor pixel-diff seluruh halaman sebagai klaim kesamaan karena kontennya memang berbeda.

Perbaiki halaman pertama sampai arah visualnya diterima pengguna, baru lanjut ke review pengajuan dan ruang kerja CS dengan token yang sama. Tidak mendesain ketiga halaman sekaligus sebelum kualitas halaman pertama terlihat. Uji tambahan dengan laporan/nama sangat panjang bersifat opsional pada tahap eksplorasi visual ini; pemeriksaan dasar keterbacaan, mobile, fokus dan tindakan utama tetap dilakukan sesuai frontend spec.

Pratinjau visual statis C-01 tersedia di [frontend/prototype](../../frontend/prototype/README.md), terpisah dari implementasi React/TypeScript yang direncanakan. Tidak mengambil berkas tema atau mengirim data/form kepada pemilik demo. Persetujuan visual pengguna dan integrasi aplikasi tetap merupakan tahap berikutnya.

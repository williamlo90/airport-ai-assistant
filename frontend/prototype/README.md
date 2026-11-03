# C-01 / C-02 — Beranda dan percakapan pelanggan

Pratinjau visual Airport AI Assistant. Beranda C-01 membuka percakapan C-02 dari empat kategori bantuan. Pencarian panduan, FAQ, dan pilihan ID/EN tetap tersedia di beranda.

Bahasa utama antarmuka adalah **Inggris**, dengan identitas **Nusantara Airport — AI Assistant**. Bahasa Indonesia tetap tersedia melalui tombol ID atau `lang=id`; tanpa parameter bahasa, beranda dan percakapan memakai Inggris. Pesan yang sudah ditulis tetap dalam bahasa aslinya.

## Menjalankan

Dari root proyek, jalankan di PowerShell:

```powershell
python -m http.server 4173 --bind 127.0.0.1 --directory frontend/prototype
```

Buka **http://127.0.0.1:4173**. Hentikan dengan Ctrl+C. Tidak memerlukan `.env`, database, Docker, atau dependency frontend. Muat ulang browser setelah mengubah berkas.

Langsung ke percakapan: **http://127.0.0.1:4173/conversation.html?topic=booking**. Pilihan `topic`: `information`, `booking`, `payment`, `contact`. `lang=en` membuka label Inggris. URL hanya memuat kategori dan bahasa, bukan cerita pengguna.

## Mencoba C-02

1. Buka kategori Bantuan booking → Parkir → Penerbangan saya terlambat. Baca arahan dan buka Panduan contoh.
2. Tulis pesan sendiri atau pilih balasan terstruktur. Jika cerita sudah disampaikan sebelum layanan dipilih, klarifikasi layanan tidak meminta cerita yang sama lagi.
3. Tulis tambahan tanpa mengirim, lalu ganti ID/EN. Tulisan dan pesan asli dipertahankan; riwayat tidak diterjemahkan otomatis. Balasan contoh berikutnya mengikuti bahasa yang dipilih.
4. Pilih Petugas, Hubungi petugas dari menu topik, atau pilihan bantuan petugas di percakapan. Cerita dan layanan tetap dibawa. Lihat konteks permintaan menampilkan pesan yang sudah dikirim, bukan tulisan yang masih ada di composer.
5. Buka Pratinjau desain → Simulasikan jawaban gagal berikutnya → Mengerti → kirim pesan. Ada loading lalu error. Coba lagi menjawab pesan yang sama tanpa menambahkan salinan pesan pengguna; jalur petugas tetap tersedia.
6. Mencoba topik lain atau kembali ke beranda dengan pesan/tulisan yang ada memerlukan konfirmasi. Refresh dan navigasi browser memakai peringatan browser bila didukung. Percakapan hanya berada di memori halaman; meninggalkan atau memuat ulang halaman menghapusnya.

Belum ada verifikasi email C-03 atau review/pengiriman C-04. Konteks petugas adalah pratinjau lokal, bukan kasus tercatat. Tidak membuat referensi kasus, status pembayaran, booking, atau konfirmasi pengiriman palsu.

QA C-02, 3 November 2026: PASS untuk prototype lokal setelah perbaikan kesinambungan konteks petugas, nama aksesibel tombol kembali mobile, dan guard navigasi. Pengujian browser mencakup320/390/768/1440px, ID/EN, draft, klarifikasi, error/retry tanpa duplikasi, dan konfirmasi navigasi. Peringatan native Back/refresh bergantung pada dukungan serta interaksi browser; tidak menjamin pemulihan riwayat setelah keluar. Inspector final0FAIL/1WARN: ruang kosong pada percakapan awal dipertahankan agar composer stabil saat pesan bertambah. Viewport pendek juga diperiksa oleh lead; ini bukan pengujian keyboard OS mobile atau audit WCAG penuh.

## Batas tahap ini

HTML/CSS/JS statis dipisahkan sebagai acuan visual sebelum dipindahkan ke React/TypeScript sesuai arsitektur proyek. Tidak mengganti struktur aplikasi frontend di `frontend/src/`.

Bandara Nusantara adalah nama contoh. FAQ merupakan panduan ilustratif dari scope produk, bukan informasi operasional bandara nyata. Pencarian lokal membaca kedua bahasa dan mempertahankan teks pengguna saat bahasa tampilan berubah. Hasil filter serta jumlah panduan dihitung dari konten yang ditampilkan.

AI, sumber booking, autentikasi, pengiriman kasus, dan email belum dihubungkan ke halaman ini. Balasan C-02 merupakan skenario deterministik lokal, bukan inferensi model atau hasil retrieval. Dialog Panduan contoh menjelaskan bahwa isinya ilustratif, bukan kebijakan bandara nyata. Tidak ada data disimpan di local storage atau dikirim keluar.

## Desain dan aset

Referensi tunggal: Sydney blue/light yang dipilih pengguna; ukuran dan alasan adaptasi tercatat dalam [audit visual](../../docs/frontend/sydney_visual_reference.md). Implementasi, ikon SVG, dan isi ditulis untuk proyek ini. Tidak memakai berkas tema atau foto demo.

- Font **Tuffy 1.28**, regular400/bold700, diambil dari [arsip resmi 2012-06-14](https://tulrich.com/fonts/tuffy-20120614.zip). [Pernyataan penulis](https://tulrich.com/fonts/) menempatkannya dalam public domain, dengan pemberian izin tanpa syarat bila pelepasan public domain tidak dimungkinkan. Salinan asli [LICENSE.txt](assets/fonts/LICENSE.txt) ikut dibundel. Font disajikan lokal.
- Hero **assets/airport-terminal.png**, dibuat menggunakan image generation untuk proyek ini; bukan foto lokasi bandara nyata. Mode: generate, latar opaque. Prompt disimpan di bawah.
- Logo dan empat ikon: SVG yang ditulis khusus di proyek ini. Tidak memerlukan Font Awesome.

SHA256 aset:

| Berkas               | SHA256                                                           |
| -------------------- | ---------------------------------------------------------------- |
| Tuffy.ttf            | B97CD5005278B24AABB69B8B6EAD98860955599558F6E8119483B575A5FDCE98 |
| Tuffy_Bold.ttf       | EAD68BA8BEF71D5F6411F2A585DCA28A15DC7E5B7729FD64B9FAFF05730DDFDE |
| airport-terminal.png | EE23A2DF9AA0460372A2772FB65E6A061C12449CBA0873A6A9F381568016258F |

### Prompt hero

> Create one photorealistic editorial airport-terminal photograph to be used as a website background, not a UI mockup. Wide horizontal composition, approximately 2.5:1 aspect ratio. A calm modern international airport concourse viewed from inside: tall floor-to-ceiling windows on the RIGHT, pale architectural roof beams and generous open floor, a few rows of dark navy boarding seats toward the sides, a small distant white passenger aircraft outside. Natural late afternoon daylight, understated cool blue-grey palette, realistic materials and soft reflected light. Center-left portion deliberately calm and low-detail to support a white heading and white search box placed by HTML later. Avoid dramatic sunset, avoid close-up people, no readable signs, no words, no logos, no brands, no watermark. This is an original fictional airport scene for the fictional Bandara Nusantara AI Assistant prototype. Make it feel like a trustworthy service website photograph, with accurate perspective, restrained contrast and quiet premium composition.

## Pemeriksaan

Pemeriksaan visual pada 1440/768/390px dan lebar sempit320px. Inspector awal menghasilkan 0 FAIL; peringatan ukuran h1 desktop48px dipertahankan karena mengikuti acuan Sydney yang dipilih pengguna, bukan judul dashboard internal. Layar ini tetap menunggu persetujuan visual pengguna; hasil ukur tidak menggantikan penilaian desain.

Uji interaksi manual mencakup pencarian cocok/tidak cocok, reset, filter kategori, accordion, perpindahan bahasa dengan input tetap, dialog dan penutupan via Escape, serta menu mobile. Tangkapan layar dan laporan lokal berada di `local/frontend-preview/` dan tidak dibundel sebagai hasil evaluasi production.

QA browser independen, 3 November 2026: PASS untuk scope prototype statis setelah perbaikan fokus filter/reset dan menu/dialog mobile. QA memeriksa 1440/768/390/320px, pemuatan Tuffy, pencarian dua bahasa, tidak ada overflow horizontal, serta tidak ada klaim pengiriman nyata. Saran tambahan lebar target tombol bahasa ditindaklanjuti menjadi44px dan diperiksa ulang oleh lead. Ini bukan audit WCAG penuh atau pengujian integrasi backend.

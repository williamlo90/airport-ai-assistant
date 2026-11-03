# C-01 — Beranda bantuan

Pratinjau visual pertama Airport AI Assistant. Buka beranda, coba kategori bantuan, pencarian panduan contoh, pilihan ID/EN, accordion, menu mobile, dan dialog petugas.

## Menjalankan

Dari root proyek, jalankan di PowerShell:

```powershell
python -m http.server 4173 --bind 127.0.0.1 --directory frontend/prototype
```

Buka **http://127.0.0.1:4173**. Hentikan dengan Ctrl+C. Tidak memerlukan `.env`, database, Docker, atau dependency frontend. Muat ulang browser setelah mengubah berkas.

## Batas tahap ini

HTML/CSS/JS statis dipisahkan sebagai acuan visual sebelum dipindahkan ke React/TypeScript sesuai arsitektur proyek. Tidak mengganti struktur aplikasi frontend di `frontend/src/`.

Bandara Nusantara adalah nama contoh. FAQ merupakan panduan ilustratif dari scope produk, bukan informasi operasional bandara nyata. Pencarian lokal membaca kedua bahasa dan mempertahankan teks pengguna saat bahasa tampilan berubah. Hasil filter serta jumlah panduan dihitung dari konten yang ditampilkan.

AI, sumber booking, autentikasi, pengiriman kasus, dan email belum dihubungkan ke halaman ini. Dialog menjelaskan batas tersebut, tanpa membuat status berhasil atau nomor kasus. Tidak ada data disimpan di local storage atau dikirim keluar.

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

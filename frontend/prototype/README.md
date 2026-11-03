# Customer design prototypes — C-01 through C-04

Pratinjau visual Airport AI Assistant. Beranda C-01 membuka percakapan C-02 dari empat kategori bantuan. Pencarian panduan, FAQ, dan pilihan ID/EN tetap tersedia di beranda.

Bahasa utama antarmuka adalah **Inggris**, dengan identitas **Westbridge Airport — AI Assistant**. Bahasa Indonesia tetap tersedia melalui tombol ID atau `lang=id`; tanpa parameter bahasa, beranda dan percakapan memakai Inggris. Pesan yang sudah ditulis tetap dalam bahasa aslinya.

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

C-03/C-04 kini tersedia sebagai contoh visual terpisah pada request.html. Percakapan C-02 belum meneruskan data ke halaman itu; request.html memakai fixture sendiri. Konteks petugas tetap pratinjau lokal, bukan kasus tercatat. Tidak membuat referensi kasus, status pembayaran, booking, atau konfirmasi pengiriman nyata.

QA C-02, 3 November 2026: PASS untuk prototype lokal setelah perbaikan kesinambungan konteks petugas, nama aksesibel tombol kembali mobile, dan guard navigasi. Pengujian browser mencakup320/390/768/1440px, ID/EN, draft, klarifikasi, error/retry tanpa duplikasi, dan konfirmasi navigasi. Peringatan native Back/refresh bergantung pada dukungan serta interaksi browser; tidak menjamin pemulihan riwayat setelah keluar. Inspector final0FAIL/1WARN: ruang kosong pada percakapan awal dipertahankan agar composer stabil saat pesan bertambah. Viewport pendek juga diperiksa oleh lead; ini bukan pengujian keyboard OS mobile atau audit WCAG penuh.

## Batas tahap ini

HTML/CSS/JS statis dipisahkan sebagai acuan visual sebelum dipindahkan ke React/TypeScript sesuai arsitektur proyek. Tidak mengganti struktur aplikasi frontend di `frontend/src/`.

Westbridge Airport adalah nama contoh. FAQ merupakan panduan ilustratif dari scope produk, bukan informasi operasional bandara nyata. Pencarian lokal membaca kedua bahasa dan mempertahankan teks pengguna saat bahasa tampilan berubah. Hasil filter serta jumlah panduan dihitung dari konten yang ditampilkan.

AI, sumber booking, autentikasi, pengiriman kasus, dan email belum dihubungkan ke halaman ini. Balasan C-02 merupakan skenario deterministik lokal, bukan inferensi model atau hasil retrieval. Dialog Panduan contoh menjelaskan bahwa isinya ilustratif, bukan kebijakan bandara nyata. Tidak ada data disimpan di local storage atau dikirim keluar.

## Desain dan aset

Referensi tunggal: Sydney blue/light yang dipilih pengguna. Implementasi, ikon SVG, dan isi ditulis untuk proyek ini. Tidak memakai berkas tema atau foto demo.

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

## FE-VIS-03 — email verification and request review

Open [C-03 — Verify email](http://127.0.0.1:4173/request.html?step=verify) or [C-04 — Review request](http://127.0.0.1:4173/request.html?step=review). Both use the existing prototype server on4173. Add `&lang=id` for Indonesian. This is an isolated visual prototype, not the React application at5173 or the integrated HTTPS environment.

The **Preview state** selector exposes12 design fixtures: email address,8-digit code, invalid/expired code, rate limit, sender unavailable, verifying; and review, validation error, sending, expired review, changed destination, unknown result. No request is sent. The code input accepts any8digits solely to explore the review screen; it grants no identity/access. Email is an example.com fixture. Input and edits exist only in memory, with no local/session storage or private URL parameters.

Try:

1. Request a sample code; enter an incomplete code to see inline validation. Request a new code and confirm the old code is cleared. There is no invented code-expiry countdown. The rate-limit fixture explicitly uses a sample Retry-After60seconds and remains paused until another fixture is selected.
2. Choose **Sender unavailable**, then **Try again**: it returns to email entry while retaining the address and report. It does not claim an email was sent.
3. In review, edit request details, original report, references or reply details. Save updates the local sample preview and returns focus to the edit trigger. Escape/Cancel discards that editor's changes. The fixture accepts only the selected example email as verified; new addresses require verification and are not silently accepted.
4. Switch EN/ID: interface labels change, original report and summary remain as written. Request language is separate from interface language. Changing service updates the sample destination; destination itself is read-only.
5. Expired review disables Send; get a new sample preview and review again. Changed destination requires reviewing the updated preview before Send. Unknown result disables editing/resubmission and offers a status check, which reports that status is unavailable in this preview. It never creates a receipt or automatically retries.
6. Cancel request opens a confirmation. Keep reviewing returns to the same report; cancellation only closes this sample review. Edited reports prompt on header navigation and register best-effort browser unload protection. Reload cannot restore edits or submission state.

C-03/C-04 follow the approved Tuffy/Sydney/Westbridge visual direction. New assets, styles and JS are confined to request.html, request.css and request.js; C-01/C-02 user adjustments remain untouched. Backend validation, email sending, verification, server-generated previews/hashes/deadlines, status lookup and stable-idempotency recovery are implemented in their own tasks.

Visual QA and evidence are recorded privately under docs/frontend/fe_vis_03.md and local/fe-vis-03/. The user approved the C-03/C-04 appearance on3November2026.

QA FE-VIS-03: PASS for the isolated visual prototype after rechecking resend-code clearing, recovery from sender unavailable, and44px Edit targets. Actual desktop/mobile widths, dialogs/focus, EN/ID retention, edits and failure/unknown states were checked. This does not prove real verification, API submission, email delivery or full accessibility conformance. User approved the C-03/C-04 appearance on3November2026.

## FE-VIS-04 — staff queue and case detail

Open [Staff workspace](http://127.0.0.1:4173/workspace.html). Add `?lang=id` for Indonesian. This isolated design prototype uses only local sample cases/staff, with no API, login, provider or email calls. It is not the integrated React staff application.

- Desktop shows scoped navigation, queue and selected detail. Mobile opens a case in place of the list; Back keeps the selected view, filter and list position.
- **Sample profile** switches between airport CS, supervisor, lounge partner CS and administrator-only. The partner sees only partner cases; admin-only sees no cases. This selector never grants access in the actual application.
- Try Unassigned/My cases/All in scope and the status filter. Select a new case, Take case, then fill Next action. Switching case/profile with an unsaved update asks Keep editing or Discard changes and leave. Language changes retain the original report and your update.
- Supervisor assignment/reassignment shows the current and target staff before confirmation. Assigning a New case leaves it New; acceptance through a status update is separate. Eligible names are scoped fixtures, not a live directory.
- Status updates require a next action and offer only the lifecycle transitions allowed for the current state. Close opens a separate review: resolution is required; duplicate offers only related cases for the same customer; No response requires follow-up evidence. No response/Closed never implies satisfaction or email delivery. Closed cases keep the recorded result and cannot be edited or reopened by staff.
- **Preview state** offers normal/empty/loading/unavailable/conflict/unknown/revoked scenarios. Conflict requires loading the latest sample before another explicit save while retaining authorized text. Unknown blocks repeating the action and offers a status check without claiming success. Revoked clears previous case text. All fixture updates are marked as local samples.

Fixture lists are small, explicitly scoped samples; they do not demonstrate a live queue count, cursor/pagination, staff capabilities or server eligibility. Those API gaps remain in the task plan. Updated fixtures are lost on reload; no private data is placed in persistent storage or URLs.

Source: workspace.html/css/js. Private evidence and audit record: docs/frontend/fe_vis_04.md and local/fe-vis-04/. FE-VIS-04 visual approval is pending.

QA FE-VIS-04: PASS for this isolated visual prototype. Actual desktop/mobile widths, role/scope examples, lifecycle prerequisites, dirty-input retention, mobile Back/scroll, dialogs, closure validation and recovery states were checked. User visual approval is pending. Real authentication, authorization, staff directory, queue APIs and mutations require their implementation tasks.

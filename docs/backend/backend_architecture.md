# Arsitektur backend Airport AI Assistant

Backend dari aplikasi ini terdiri dari dua proses, yaitu **API dan worker**. Keduanya memakai aturan bisnis yang sama, tapi mengerjakan jenis pekerjaan yang berbeda.

- **API** menerima permintaan dari frontend dan webhook.
- **Worker** menjalankan pekerjaan latar belakang seperti pengiriman email dan retry.
- **Neon PostgreSQL** menyimpan data aplikasi.
- **Integrasi sumber** membaca booking dan transaksi.
- **Agent AI** membantu memahami percakapan dan menyiapkan tindakan sesuai batas kewenangan.

Dokumen ini memperinci [ARCHITECTURE.md](../../ARCHITECTURE.md). Scope dan aturan produknya tetap mengikuti [PRODUCT_CONTRACT.md](../../PRODUCT_CONTRACT.md). Struktur data dan perilaku API mengikuti [DATA_AND_API_CONTRACTS.md](../../DATA_AND_API_CONTRACTS.md), sedangkan pembuktiannya mengikuti [TEST_PLAN.md](../../TEST_PLAN.md).

## Alur request

Alur request-nya seperti ini:

```text
Frontend
   ↓
API — periksa request, sesi, dan tenant
   ↓
Layanan bisnis dalam modules/
   ├── periksa kewenangan
   ├── terapkan aturan bisnis
   ├── baca/tulis data melalui repository
   └── panggil konektor jika diperlukan
           ↓
    Database / sistem sumber / model
```

Contohnya, pengguna ingin mengecek booking parkir. API menerima nomor booking dan mengambil identitas pengguna. Layanan bisnis memeriksa apakah pengguna berhak melihat pesanan itu. Setelah diizinkan, konektor membaca booking dari sistem sumber. Hasil yang boleh dilihat pengguna dikembalikan melalui API.

Nomor booking saja tidak cukup untuk memberikan akses. Tenant dan kewenangan tetap diperiksa oleh backend, bukan ditentukan dari ucapan pengguna atau jawaban AI.

## Fungsi tiap bagian

| Bagian | Tanggung jawab | Contoh |
| --- | --- | --- |
| `api/` | Menerima request, memvalidasi bentuk input, mengambil identitas, dan mengembalikan respons. | Endpoint konfirmasi pengajuan. |
| `modules/identity/` | Verifikasi, sesi, membership, dan kebijakan akses. | Memastikan pelanggan berhak membaca pesanan. |
| `modules/conversations/` | Pesan, konteks percakapan, antrean, dan pengambilalihan petugas. | Menghentikan jawaban AI ketika CS menerima chat. |
| `modules/cases/` | Draf pengajuan, konfirmasi, kasus, assignment, status, dan rating. | Memastikan satu pengajuan hanya menghasilkan satu kasus. |
| `modules/knowledge/` | Versi informasi, publikasi, dan retrieval sesuai tenant serta akses. | Mengambil kebijakan parkir yang relevan. |
| `modules/agent/` | Mengatur langkah percakapan dan penggunaan tool. | Meminta detail yang kurang dan menyiapkan ringkasan pengajuan. |
| `integrations/` | Menerjemahkan komunikasi dengan model, email, dan sistem sumber. | Mengubah respons provider menjadi format internal. |
| `infrastructure/` | Implementasi database, antrean, storage, dan observability. | Menyimpan kasus atau mengklaim pekerjaan email. |

Folder-folder tersebut berada di `backend/src/airport_assistant/`. Backend tetap satu aplikasi modular. API dan worker bisa dijalankan sebagai proses terpisah dari codebase yang sama; kita tidak membuat seluruh modul menjadi microservice.

## Aturan dependency

Route API dan entry point worker memanggil layanan bisnis. Layanan bisnis memakai **kontrak atau interface** untuk repository, sumber data, model, dan email. Implementasi konektornya dipasang ketika aplikasi dijalankan.

Interface menjelaskan apa yang dibutuhkan layanan bisnis. Misalnya, layanan kasus membutuhkan kemampuan menyimpan kasus, sementara layanan penanganan membutuhkan kemampuan mengirim balasan. Cara menyimpan ke PostgreSQL atau memanggil API email ditangani implementasinya.

Pembagiannya seperti ini:

- **API dan worker memanggil layanan bisnis.** Aturan bisnis tidak ditulis ulang di route atau proses worker.
- **Layanan bisnis menetapkan apa yang boleh dilakukan.** Keputusan izin, konfirmasi, perubahan status, dan tujuan penerima tetap berada di sini.
- **Repository menangani akses penyimpanan.** Layanan bisnis tidak menyisipkan query database ke dalam adapter provider.
- **Adapter menangani komunikasi provider.** Adapter menerjemahkan request, respons, dan error; adapter tidak menetapkan kewenangan pengguna.
- **Komponen dipasang saat startup.** Entry point memilih implementasi yang dipakai, sehingga modul bisnis tidak perlu bergantung langsung pada SDK provider tertentu.

Dengan begitu, aturan **“email ini boleh dikirim ke siapa”** tetap dimiliki layanan bisnis. Adapter email hanya mengirim pesan yang telah diotorisasi. Worker juga harus memanggil pemeriksaan tersebut sebelum mengirim, karena izin saat pekerjaan dibuat bisa saja sudah berubah.

## Wiring awal yang sudah executable (BE-03)

- `composition.py` membuat `ApplicationServices` per aplikasi. Default sekarang hanya memuat layanan liveness; repository/provider tidak disimulasikan sebagai koneksi nyata.
- `api/dependencies.py` mengambil layanan dari instance aplikasi. Route health memanggil kontrak `HealthService`, lalu memetakan hasil domain menjadi respons HTTP.
- `modules/system/health.py` menyediakan kontrak, hasil domain immutable dan implementasi liveness yang tidak bergantung framework.
- `modules/identity/ports.py` menjadi pemilik kontrak `TenantRepository` dan `SourceGrantProvider`. Lookup tenant sudah memakai PostgreSQL. SRC-03A memasang `CurrentSourceGrants` dan gerbang `PrivateSourceAccess` di composition; pembentukan/union label berada dalam modul source. Pemakai case/blob/model/outbound dihubungkan saat fitur tersebut dibuat dan dibuktikan lewat SRC-03B.
- `modules/conversations/service.py` memegang aturan create, ownership, deduplikasi body dan perubahan bahasa. Repository menyediakan unit transaksi; urutan lock sesi lalu conversation memastikan counter dan message tersimpan atomik serta tidak menulis sesudah revoke yang sudah selesai. API tidak menjalankan SQL dan worker/email tidak menentukan izin percakapan.
- `modules/drafts` memegang routing server, bentuk laporan, ringkasan, hash dan lifecycle draf. Gerbang sesi transaksi bersama berada di `modules/identity/customer_transaction.py`; repository draf memakai unit transaksi PostgreSQL yang sama. Draf mandiri memerlukan owner serta sesi pembuat, sementara commit kasus/consent tetap CASE-02.
- `backend/tests/contracts/test_dependencies.py` memeriksa batas import API, modules, integrations, infrastructure dan entry point. Tes wiring membuktikan override service bekerja dan tidak bocor ke instance aplikasi lain. Guard import tidak menggantikan tes aturan kewenangan.

Saat repository/provider nyata tersedia, composition menyuntikkannya ke constructor layanan bisnis. Service aplikasi tidak menahan transaksi atau state tenant/aktor mutable; lifecycle koneksi dan transaksi ditentukan bersama implementasinya. Worker belum executable dan tidak diberi startup palsu pada BE-03.

## API mengerjakan apa

API menjadi pintu masuk permintaan. Tugasnya antara lain:

- Memeriksa format input dan menolak field yang tidak diizinkan.
- Mengambil sesi, identitas, dan tenant yang sah.
- Memanggil layanan bisnis yang sesuai.
- Mengembalikan hasil atau error yang bisa dipahami frontend.
- Menerima webhook provider, memeriksa keasliannya, lalu mencatat event untuk diproses.

API tidak menyatakan pengajuan berhasil sebelum kasus benar-benar tercatat. Jika hasil request belum diketahui karena koneksi terputus, frontend dapat memeriksa hasil tindakan yang sama, bukan langsung membuat pengajuan baru.

## Worker mengerjakan apa

Worker mengerjakan proses yang tidak perlu menahan request pengguna, terutama pengiriman email, pengambilan isi email masuk, dan percobaan ulang yang terkontrol.

Alurnya seperti ini:

1. Worker mengambil pekerjaan yang siap diproses dari antrean database.
2. Pekerjaan diklaim sementara agar tidak dikerjakan bersamaan oleh worker lain.
3. Worker memanggil layanan bisnis untuk memeriksa konteks tenant, izin, dan tindakan yang masih berlaku.
4. Layanan tersebut memakai adapter atau repository untuk menjalankan pekerjaan.
5. Hasilnya dicatat. Jika gagal, sistem menentukan retry atau pemeriksaan petugas sesuai jenis kegagalannya.

Worker tidak mengirim ulang email secara membabi buta. Jika provider mungkin sudah menerima email tetapi responsnya hilang, sistem perlu memeriksa hasilnya terlebih dahulu. Status yang belum pasti harus terlihat sebagai belum pasti.

## Data disimpan di mana

**Neon PostgreSQL menyimpan data milik aplikasi**, seperti percakapan, kasus, penanggung jawab, riwayat penanganan, konfigurasi, dan pekerjaan latar belakang.

**Sistem sumber tetap menjadi acuan booking dan pembayaran.** Backend membaca informasi tersebut melalui konektor. Mencatat laporan “saya sudah bayar” tidak mengubah status sumber menjadi lunas.

Setiap akses penyimpanan memperhatikan tenant dan izin. Pemeriksaan dilakukan pada layanan bisnis dan diperkuat oleh pembatasan database. API, worker, pencarian knowledge, dan riwayat percakapan tidak boleh memakai jalur yang melewati isolasi tersebut.

## Contoh lengkap pengajuan kendala pembayaran

Misalnya pelanggan berkata, “Pembayaran pertama error, lalu saya bayar pakai cara lain. Bisa bantu pengecekan?”

1. API memeriksa sesi dan tenant.
2. Layanan bisnis memeriksa akses transaksi.
3. Konektor mengambil catatan dari sistem sumber.
4. Agent membantu meminta detail dan menyusun draf.
5. Pelanggan memeriksa ringkasan lalu menekan konfirmasi.
6. Layanan kasus memeriksa ulang izin dan versi draf.
7. **Satu transaksi database** menyimpan kasus, bukti konfirmasi, riwayat, dan pekerjaan tindak lanjut yang diperlukan.
8. API memberikan nomor kasus setelah pencatatan berhasil.
9. CS menerima kasus dan menyiapkan balasan.
10. Worker memeriksa tujuan serta hak akses, lalu mengirim email melalui adapter.

Kalau API timeout setelah langkah tujuh, pengecekan hasil dan idempotency mencegah kasus kedua. Idempotency berarti percobaan ulang atas tindakan yang sama mengembalikan hasil yang sama, bukan menjalankan pengajuan baru.

Kalau email gagal, kasus tetap ada dan kegagalan terlihat oleh petugas. Pengajuan bantuan juga tidak otomatis berarti pembayaran berubah atau refund disetujui.

## Agent tidak menjadi pintu belakang

Tool AI memanggil layanan bisnis yang sama dengan fitur biasa, lengkap dengan pemeriksaan akses dan konfirmasi.

Agent boleh membantu memahami kebutuhan, membaca informasi yang diizinkan, mencari kebijakan, dan menyiapkan draf. Agent tidak memiliki akses SQL bebas, tidak membuat bukti persetujuan sendiri, dan tidak mengambil keputusan hak akses.

Agent juga tidak membuat booking baru, memproses pembayaran, menyetujui refund, menutup kasus, atau membuat hubungan pemesan perwakilan.

## Modul tidak bebas mengubah tabel modul lain

Misalnya, agent tidak langsung mengubah status kasus. Agent meminta tindakan melalui layanan kasus. Begitu juga adapter email tidak langsung menutup kasus karena email berhasil dikirim.

Operasi lintas kasus dan percakapan, seperti menerima chat, dikoordinasikan dalam transaksi agar penanggung jawab dan handler tetap konsisten. Jika dua petugas menerima chat bersamaan, hanya satu yang berhasil. Jika supervisor memindahkan penanganan, petugas lama tidak boleh tetap mengirim seolah masih menjadi handler.

Setelah petugas mengambil alih, AI berhenti menjawab otomatis. Pemeriksaan ini juga dilakukan saat jawaban AI hendak disimpan, supaya jawaban yang telanjur diproses sebelum handoff tidak muncul setelah CS menerima percakapan.

## Penempatan pengujian

- `backend/tests/unit/` memeriksa aturan bisnis dan perilaku modul secara terfokus.
- `backend/tests/integration/` memeriksa kerja sama dengan database serta komponen terkait, termasuk transaksi, RLS, worker, dan state.
- `backend/tests/contracts/` memeriksa kesesuaian bentuk serta perilaku API dan konektor.
- `frontend/tests/` memeriksa komponen serta perilaku frontend.
- `reference_service/tests/` memeriksa service sumber dan data miliknya.
- `tests/e2e/` memeriksa perjalanan lintas komponen dari pelanggan sampai penanganan petugas.

Skenario tidak disalin ke beberapa tempat tanpa tujuan berbeda. Tes aturan konfirmasi dapat memeriksa logika secara terfokus, sedangkan tes end-to-end memeriksa bahwa pengguna benar-benar dapat melihat ringkasan, mengonfirmasi, dan mendapatkan nomor kasus.

## Cara mulai mengimplementasikan

Mulai dari backend minimal yang bisa dijalankan, konfigurasi environment, dan koneksi database. Manifest dependency, lockfile, konfigurasi migration, serta perintah menjalankan aplikasi ditambahkan bersama komponen yang sudah executable.

Setelah itu, bangun fondasi tenant dan akses, lalu alur kasus tanpa AI. Ketika pengajuan, penugasan, status, dan handoff sudah bekerja, agent menggunakan layanan yang sama untuk membantu pengguna melalui percakapan.

[Schema database](database_schema.md) memperinci tabel, relasi, constraint, indeks, dan RLS. [Rencana tugas backend](task_plan.md) mengurutkan implementasi serta bukti yang harus tersedia sebelum suatu pekerjaan disebut selesai.

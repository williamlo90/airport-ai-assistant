# Izin sumber dan label keamanan

SRC-03A menambahkan dua pemeriksaan yang terpisah. `CurrentSourceGrants` mengecek izin pelanggan ke resource sumber saat ini. `SecurityLabels` mencatat izin yang diperlukan oleh data privat dan hasil turunannya. Label bukan bukti bahwa pelanggan masih memiliki izin.

## Pemeriksaan izin saat ini

`ApplicationServices.source_grants` mengimplementasikan port `SourceGrantProvider`. Layanan bisnis memberikan tenant dan principal dari konteks server yang sudah diotorisasi. Provider tidak menerima claim tenant, subject, hubungan keluarga atau role dari pelanggan/model.

Setiap panggilan membaca ulang `source_subject_ref` principal aktif pada tenant aktif melalui runtime PostgreSQL dan RLS. Mapping tenant sumber berasal dari konfigurasi server. Provider kemudian melakukan GET booking atau transaksi tersebut melalui konektor SRC-02. Tidak ada cache relasi, izin, atau keputusan akses. Port ini juga dapat digunakan untuk pelanggan penerima outbound; memanggilnya tidak membuktikan sesi, keanggotaan kasus, persetujuan atau penerima email.

- Booking hanya memeriksa `booking:read`.
- Transaksi memeriksa `payment_status:read` dan/atau `payment_details:read` pada transaksi itu sendiri. Booking yang bisa dibaca tidak memberi izin pembayaran.
- Kebutuhan kosong, resource/scope yang tidak dikenal, serta referensi tidak valid ditolak sebelum source dipanggil.
- Mapping/subject tidak ditemukan atau source404 menghasilkan `False`. Source unavailable, respons invalid dan kegagalan database diteruskan sebagai kegagalan. Pemanggil tidak boleh menangkapnya lalu mengizinkan akses.
- Pemeriksaan ini menggunakan konektor GET terbatas SRC-02; tidak membuat booking, mengubah pembayaran atau memberi kuasa.

Tidak perlu menarik seluruh daftar relasi untuk membuktikan satu resource. GET resource mengembalikan scopes yang diizinkan saat itu. Ini menghindari keputusan dari halaman pertama relasi yang belum lengkap.

## Label mengikuti isi data

`labels_for_observation` membentuk kebutuhan dari DTO sumber tervalidasi secara utuh, dengan tenant aplikasi dari konteks server saat pembacaan. Fungsi ini bukan parser request publik dan tidak boleh diberi data atau tenant hasil claim model/client.

Contoh label transaksi yang hanya memuat status:

```json
[{"source":"reference","resource_type":"transaction","reference":"TX-A-001","required_source_scopes":["payment_status:read"]}]
```

Jika DTO memuat nominal, mata uang dan refund, label transaksi mensyaratkan kedua scope pembayaran. Bila transaksi juga menampilkan `booking_reference`, label menambahkan `booking:read` untuk booking tersebut. Dengan demikian, tautan booking tidak ikut terbuka kepada pembayar yang hanya berhak membaca transaksi.

`labels_for_policy` mengharuskan referensi versi kebijakan dan jenis layanan cocok dengan booking yang dibaca; kebutuhan aksesnya mengikuti booking itu. Fungsi ini tidak memilih kebijakan versi terbaru atau menebak hubungan dari teks. Observasi tetap harus berasal dari pembacaan sumber yang sah.

`merge_labels` menggabungkan kebutuhan setiap resource dengan union. Menambahkan isi tidak mengurangi batas akses. Tenant berbeda ditolak. Label tervalidasi bersifat immutable, resource/scope memakai allowlist, kebutuhan wajib terisi, duplikasi scope ditolak dan agregat dibatasi64resource. Melebihi batas menghasilkan kegagalan; data tidak dipotong diam-diam.

`to_records` menghasilkan bentuk JSON canonical untuk kolom `resource_security_labels`. Repository menggunakan `from_records(tenant_dari_baris, records)` untuk memvalidasi ulang data simpanan dan menyatukan label resource yang sama. Tenant tidak ditambahkan ke format JSON kontrak karena sudah dimiliki baris tabel. Baris harus diperoleh melalui pemeriksaan tenant/objek dan RLS; mengganti tenant saat restore bukan operasi yang diizinkan. Label hilang/kosong, tipe salah atau scope tidak dikenal tidak dapat mengizinkan konten privat sumber.

Tidak ada operasi untuk menurunkan label atau menghapus kebutuhan. Parser repository ini juga bukan endpoint untuk pelanggan mengatur label sendiri. Konten umum yang tidak menggunakan data sumber dan template notifikasi generik memerlukan jalur bisnis tersendiri; jangan membuat label kosong untuk melewati pemeriksaan data privat.

## Pemeriksaan agregat privat

`ApplicationServices.private_source_access.allows` merupakan gerbang tambahan setelah ACL objek. Layanan bisnis memberikan principal pelanggan/penerima yang memang berhak atas objek. Gerbang memeriksa tenant label dan seluruh kebutuhan resource. Satu izin tidak lengkap menolak seluruh agregat. Kegagalan sumber tidak berubah menjadi hasil sukses atau data kosong.

Hasil pemeriksaan tidak disimpan sebagai tiket izin. Read berikutnya, penyusunan konteks model dan pengiriman outbound harus memeriksa lagi. Snapshot yang sempat diambil tidak mempertahankan akses ketika izin dicabut. Role admin/staf dan `case_read` tidak menggantikan grants pelanggan; hak staf mengikuti ACL operasionalnya sendiri, sedangkan pengungkapan kepada pelanggan mengikuti izin pelanggan tersebut.

Pemeriksaan beberapa resource memakai GET berurutan, bukan transaksi atomik lintas PostgreSQL dan sistem sumber. Izin dapat berubah setelah sebuah GET. SRC-03A membuktikan pemeriksaan ulang pada batas operasi yang tersedia, bukan penguncian izin sumber sepanjang operasi. Implementasi kasus/email mendatang harus memeriksa lagi pada batas eksekusinya.

## Bukti dan batas tahap ini

Tes unit memeriksa pembentukan label status/detail/link, kebijakan versi tepat, union yang tidak melemah, round-trip repository, malformed/missing labels, tenant mismatch, kebutuhan kosong, pencabutan dan source unavailable.

Tes bridge menggunakan role runtime Neon, principal pelanggan terverifikasi dengan sender email terkontrol, dan source mandiri melalui TCP. Matriks owner/representative/beneficiary/payer/outsider membuktikan perbedaan scopes; referensi yang sama antar tenant tidak membocorkan hak. Fixture repository JSON mempertahankan label dari snapshot, menolak beneficiary untuk isi detail pembayaran, lalu menolak owner setelah izin sumber dicabut. Koneksi ke port yang tidak tersedia menghasilkan503.

Tidak ada tabel/migration baru; revision aplikasi tetap `0004_staff_auth`. Kasus, event, blob, checkpoint/model dan job email belum menjadi bukti SRC-03A. Penerapan label pada objek serta seluruh jalur tersebut merupakan SRC-03B/SEC-11. Pengiriman dan verifikasi email provider nyata tetap MAIL-05.

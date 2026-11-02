# Pola migration backend

Panduan DB-04 untuk menambah schema bersama fitur yang benar-benar dibuat. Acuan struktur tetap [database schema](database_schema.md); izin dan state tetap mengikuti product/API contract. Tabel berikutnya tidak dibuat sekaligus hanya karena tercantum dalam rancangan.

## Membuat revision

Dari root repository dengan instalasi editable:

```powershell
uv run --locked --project backend airport-assistant-migrate new --message "AUTH-01 sessions and verification challenges"
```

Perintah new tidak memuat .env atau membuka database. Ia menggunakan backend/migrations/script.py.mako dan menghubungkan revision baru ke satu head yang ada. Hasil awal sengaja menggagalkan upgrade sampai DDL, constraints, grants dan RLS diimplementasikan. Jangan commit contoh revision kosong sebagai fitur selesai. Generator menolak instalasi wheel; generate dari checkout, bukan mengubah paket deployment.

Revision yang sudah diterapkan tidak diedit. SQL dan helper khusus revision dibekukan di file revision tersebut, bukan mengimpor model/helper aplikasi yang bisa berubah kemudian. Versi baru memperbaiki schema lama lewat revision lanjutan. Diagram/schema/API contract diperbarui bila implementasi merinci atau mengubah rancangan.

## Isi yang mengikuti fitur

Setiap tabel tenant-scoped memiliki tenant_id, id, timestamp, PK(tenant_id,id), FK tenant dan FK composite ke objek lain. Tambahkan constraint status/versi/keunikan serta index untuk FK dan pola query yang benar-benar dipakai. FK menggunakan ON DELETE RESTRICT kecuali ada keputusan lifecycle yang eksplisit.

Dalam transaksi migration yang sama, pasang ENABLE dan FORCE ROW LEVEL SECURITY, policy USING/WITH CHECK sesuai aktor/objek, serta grant minimum. Role runtime tidak menjadi owner dan tidak memperoleh CREATE/TRUNCATE/TRIGGER/REFERENCES untuk memudahkan migration. Jangan mengasumsikan tenant filter cukup untuk data pribadi antar pelanggan dalam tenant yang sama. Policy untuk case/payment-derived data harus mengikuti grants source dan akses objek saat fitur tersebut dibuat.

Function SECURITY DEFINER harus punya alasan yang sempit, owner NOLOGIN terpisah, search_path tetap, referensi tabel terqualifikasi, hasil terbatas dan PUBLIC EXECUTE dicabut. Jangan menambah SQL dinamis dari user/model. Tambahan kolom perlu review sensitivitas: table-level SELECT grant juga mencakup kolom baru; jangan menyisipkan secret/internal data lalu menganggap ACL kolomnya otomatis aman.

## Foreign key yang berputar

Buat kedua tabel lebih dahulu, kemudian tambahkan FK bernama dengan ALTER TABLE. Selalu sertakan tenant; sertakan principal/organisasi pada pasangan FK jika integritas ownership/scope membutuhkan itu.

Jika dua row harus saling menunjuk sejak insert, gunakan FK DEFERRABLE INITIALLY IMMEDIATE. Dalam satu transaksi data, defer hanya FK bernama yang relevan, insert kedua row, lalu biarkan PostgreSQL memeriksa relasi sebelum commit. Alternatifnya gunakan FK nullable jika state bisnis memang mengizinkan tahap sementara tersebut. DEFERRABLE tidak membuat aksi ON DELETE RESTRICT dapat ditunda; purge memerlukan lifecycle tersendiri. Jangan menonaktifkan RLS, trigger, atau session_replication_role untuk melewati siklus.

Contoh executable hanya berada di backend/tests/fixtures/migrations/0003_expand_cycle.py. Dua tabel demonstrasi di sana tidak menjadi tabel produk. File membatasi eksekusi ke schema db04_test_UUID; test menyalin revision production ke direktori sementara dan menambahkan contoh tersebut. Schema app tetap memakai revision produk terakhir.

## Expand lalu contract

Awali dengan perubahan additive: tabel baru, kolom nullable atau default yang aman, dan endpoint/query yang tetap membaca proyeksi lama. Jalankan pembacaan versi sebelumnya dalam context otorisasi yang sah. Kompatibilitas tidak berarti mempertahankan query lama yang melewati otorisasi; pembacaan tanpa context tetap harus ditolak.

Backfill data melalui pekerjaan terukur dengan batch, progress, retry/idempotency dan budget yang sesuai. Setelah semua penulis/pembaca sudah beralih dan data memenuhi invariant, revision contract terpisah dapat memperketat NOT NULL atau menghapus field lama melalui keputusan rilis. Jangan menjadwalkan drop/rename destruktif bersamaan dengan rollout aplikasi yang masih memerlukan field tersebut.

Runner saat ini menjalankan upgrade dalam satu transaksi dengan statement timeout 30 detik dan lock timeout 3 detik. CREATE INDEX CONCURRENTLY tidak cocok dengan transaksi ini: bila benar-benar dibutuhkan, siapkan jalur operasional terpisah yang direview beserta rekonsiliasi kegagalannya. Jangan menambahkan commit manual ke revision untuk memaksa operasi itu bekerja. Pengujian backup/restore dan rolling deployment tetap tugas release; DB-04 tidak mengklaim keduanya sudah terbukti.

## Pemeriksaan runner

Upgrade menuntut satu head Alembic sebelum membuka koneksi. Advisory transaction lock mengurutkan runner pada schema yang sama. Schema, DDL, data mutation dan revision tracking berada dalam satu transaksi.

Setelah upgrade melewati tahap historis0001, runner memeriksa semua tabel/partitioned table dalam schema, kecuali schema_migrations: ENABLE+FORCE RLS harus tetap terpasang dan owner bukan runtime. Pelanggaran menggagalkan upgrade sebelum commit, termasuk perubahan data dan revision tracking-nya. Revision0001 tetap dapat dituju secara eksplisit untuk tes tahap sebelum RLS; itu bukan target deployment final.

Guard ini pemeriksaan struktural, bukan pembuktian bahwa ekspresi policy atau semua grants benar. Policy USING(true) yang keliru masih perlu ditangkap lewat pengujian runtime dan review. Setiap fitur harus punya tes semantik aksesnya sendiri, termasuk principal/queue/source grants yang relevan.

## Bukti upgrade

Uji perubahan pada schema pengujian terisolasi dengan fixture A/B, bukan memakai schema app sebagai tempat eksperimen. Bandingkan data sebelum/sesudah, ID/FK, field default, status dan data historis yang harus utuh. Uji repeat upgrade, pembacaan proyeksi lama, missing context, akses runtime A/B, kegagalan revision dan rollback lengkap. Untuk siklus FK, uji commit valid serta referensi tenant/principal salah tanpa mematikan RLS.

```powershell
$env:RUN_NEON_DB_TESTS = '1'
$env:RUN_NEON_MIGRATION_TESTS = '1'
$env:RUN_NEON_RLS_TESTS = '1'
$env:RUN_NEON_UPGRADE_TESTS = '1'
try { uv run --locked --project backend pytest backend/tests } finally {
  Remove-Item Env:RUN_NEON_DB_TESTS, Env:RUN_NEON_MIGRATION_TESTS, Env:RUN_NEON_RLS_TESTS, Env:RUN_NEON_UPGRADE_TESTS
}
```

Script testing boleh memuat credential lokal secara internal; jangan menampilkan .env, DSN, password atau raw traceback yang bisa membawa credential. Default offline melewati integrasi Neon dan bukan pengganti hasil live. Schema/role acak pengujian dibersihkan di finally; jangan drop app untuk mereset tes.

## Sebelum rilis

Review scope perubahan, kompatibilitas aplikasi, data/FK/index, policy/grant, waktu lock serta recovery yang dapat dijalankan. Catat revision awal/akhir, versi aplikasi, perintah tes dan hasil actual. Jalankan migration dengan direct credential, kemudian current dan smoke check runtime. Credential migration tidak diberikan kepada proses API/worker. Kegagalan di runner ini di-rollback; masalah setelah rilis ditangani lewat keputusan recovery/forward migration, bukan downgrade destruktif otomatis.

Rujukan: [Alembic tutorial/templates](https://alembic.sqlalchemy.org/en/latest/tutorial.html), [Alembic commands](https://alembic.sqlalchemy.org/en/latest/api/commands.html), [PostgreSQL constraints](https://www.postgresql.org/docs/current/ddl-constraints.html).

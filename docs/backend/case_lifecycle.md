# Penanganan dan kelanjutan kasus — CASE-03

Kasus yang sudah dikonfirmasi dapat ditangani petugas, ditutup secara manual, dan dibuka kembali atas permintaan eksplisit pelanggan terverifikasi. Seluruh perubahan membawa versi dan riwayat. AI tidak memiliki kewenangan penutupan atau assignment.

## Endpoint yang tersedia

Semua endpoint berada di `/api/v1`. Identitas dan tenant berasal dari sesi backend. Mutasi memerlukan Origin yang diizinkan serta CSRF; header identitas buatan client ditolak. Respons memakai Cache-Control no-store.

| Endpoint | Kegunaan |
| --- | --- |
| GET /cases/{id} | Pemilik anonim atau pelanggan terverifikasi membaca laporan, status, langkah berikutnya, hasil penutupan dan riwayat publik kasusnya. |
| GET /staff/cases/{id} | CS/supervisor dalam organisasi dan queue yang sah membaca kasus. Admin saja tidak memperoleh akses. |
| GET /staff/queues/cases?status=... | Daftar maksimal100kasus yang berhak dibaca, urutan updated_at/id; filter status opsional. Cursor/pagination lengkap mengikuti COMPLETE-02. |
| PATCH /staff/cases/{id}/assignment | Body expected_version dan assignee_id UUID membership, bukan principal. Nilai null hanya untuk supervisor yang mengosongkan penugasan kasus new. |
| PATCH /staff/cases/{id}/status | Body expected_version, status dan next_action; alasan/hasil serta bukti tambahan mengikuti aturan penutupan. |
| POST /cases/{id}/continue | Pelanggan terverifikasi mengirim expected_version, intent same_issue, message dan client_request_id UUID. |

## Penugasan dan status

CS hanya boleh mengambil kasus yang belum ditugaskan untuk dirinya sendiri. Self-claim sekaligus menerima kasus menjadi in_progress. Supervisor dapat preassign kasus new tanpa mengubah status, kemudian menerima melalui perubahan status, atau mengganti petugas pada kasus aktif. Petugas tujuan harus memiliki principal, membership, organisasi, queue, role CS/supervisor, credential lokal aktif dan kontak login terverifikasi yang sesuai. Peran admin saja tidak memenuhi syarat. Penugasan lintas tenant/organisasi/queue atau kepada petugas tidak aktif ditolak.

| Status asal | Status berikutnya | Syarat |
| --- | --- | --- |
| new | in_progress | Assignee aktif dan berwenang sudah ada. |
| in_progress | awaiting_customer | next_action menjelaskan informasi yang ditunggu dan cara pelanggan membalas. next_actor customer. |
| in_progress | awaiting_external | Assignee tetap ada; next_action menjelaskan koordinasi berikutnya. next_actor external. |
| awaiting_customer / awaiting_external | in_progress | Petugas melanjutkan, next_actor staff. |
| Semua status aktif | closed | Alasan dan hasil wajib, next_actor none. |
| closed | in_progress / new | Hanya kelanjutan eksplisit pelanggan untuk masalah yang sama; aturan di bawah. |

Petugas dapat memperbarui next_action pada in_progress, awaiting_customer atau awaiting_external yang sama, dengan versi dan riwayat baru. Penugasan tidak boleh dikosongkan pada in_progress atau status menunggu. Closed menolak assignment dan perubahan status oleh petugas; tidak dibuka kembali diam-diam.

Semua mutasi memeriksa expected_version setelah row lock. Write lama mendapat409 VERSION_CONFLICT. Dua self-claim hanya mempunyai satu pemenang; close bersamaan dengan pembaruan lain mempunyai satu urutan commit. Sistem tidak menutup otomatis dan no_response bukan rating puas.

## Penutupan manual dan hasil yang dapat dibaca

close_reason menerima resolved, customer_cancelled, duplicate atau no_response. resolution wajib menjelaskan hasil, next_action tetap wajib dan closed_at dicatat server. Duplicate memerlukan related_case_id yang berbeda, berhak dibaca petugas, dan mempunyai pemilik yang sama; ini mencegah referensi kasus orang lain muncul sebagai hasil penanganan. no_response memerlukan followup_evidence nonkosong dari petugas. Bukti ini disimpan dalam snapshot history, tanpa menganggap diam sebagai keberhasilan.

Hasil penutupan tersimpan pada kasus dan history publik sehingga pelanggan dapat membacanya melalui GET kasus. CASE-03 belum mengirim email, membuat close-email job, atau mengklaim hasil sudah diterima pelanggan. Notifikasi/delivery email mengikuti MAIL. Percakapan asal masih mandiri; mode human/ended serta handler atomik mengikuti CHAT-01/02.

## Kelanjutan masalah yang sama

Endpoint continue menerima pesan asli pelanggan dan intent eksplisit same_issue. Ini bukan pengajuan yang dikarang model. Anonim, pelanggan lain, dan staf tidak dapat memakainya. Pada kasus closed, assignee lama diperiksa ulang: bila masih memenuhi seluruh syarat, status menjadi in_progress; selain itu assigned_membership_id dikosongkan dan status new. Close fields saat ini dikosongkan, sementara snapshot hasil penutupan sebelumnya tetap ada dalam history.

client_request_id dideduplikasi per kasus dan principal. Digest mengikat seluruh command termasuk expected_version dan pesan. Retry request sama tidak mengulangi mutasi atau menambah history; respons memberi tampilan kasus saat ini. Key sama dengan request berbeda409 IDEMPOTENCY_CONFLICT. Write lama dengan key baru409 VERSION_CONFLICT. Continue pada kasus yang masih aktif409 CASE_NOT_CLOSED.

Tidak ada classifier yang menebak intent dari ucapan terima kasih atau teks ambigu. intent lain ditolak; kebutuhan berbeda diajukan sebagai kasus baru. Pemrosesan email ambigu menjadi review petugas mengikuti MAIL-04, bukan dinyatakan selesai oleh endpoint ini.

## Transaksi dan akses database

Migration0009_case_lifecycle memperluas schema0008 tanpa menulis ulang migration lama. Case menyimpan status, assignment, next_action/actor, version, closure fields dan updated_at. History menyimpan snapshot setiap perubahan, aktor dan versi, serta pesan/key/digest untuk continuation. FK assignment mengikat tenant dan organisasi. Constraint memeriksa bentuk status, close_reason, resolution, duplicate serta bukti no_response.

Helper metadata SECURITY DEFINER memakai auth-owner NOLOGIN, fixed search_path dan ACL privat. Ia memeriksa sesi/credential terbaru serta scope role/queue, lalu mengambil SHARE lock pada metadata agar pencabutan izin tidak mendahului commit tindakan yang sudah diotorisasi. Mutasi mengunci kasus; riwayat dan perubahan kasus disimpan atomik. Kegagalan di tengah proses membatalkan seluruhnya. Sesi yang kedaluwarsa selama transaksi menghasilkan401 dengan rollback.

Runtime hanya mendapat UPDATE kolom lifecycle. INSERT kasus dibatasi kolom input pengajuan; status/assignment/closure awal memakai default database, tidak dapat dipasok pelanggan. Immediate trigger memeriksa kewenangan dan transisi; deferred trigger mewajibkan history tepat untuk versi, assignment/status dan hasil yang berubah. History tidak dapat diubah/dihapus. Event identity_verified hanya dapat ditulis private auth transaction, bukan customer runtime. Auth-owner dapat mengambil row lock principal tetapi tidak menulis ulang ID principal; policy lama FOR ALL dipecah menjadi SELECT/INSERT dan UPDATE lock-only. Helper assignee memeriksa sesi dan pemilik/scope secara eksplisit sehingga penanda internal adopsi tidak dapat dipakai untuk membaca kasus orang lain.

GET membatasi history ke resulting_version paling tinggi sebesar versi kasus yang sudah dibaca. Karena history immutable dan commit kasus/history atomik, respons merupakan snapshot konsisten pada pembacaan awal meskipun ada update berikutnya. Ini tidak menjanjikan status paling baru pada akhir request.

Kasus tetap laporan pelanggan dengan source observations/labels kosong. Receipt/draft CASE-02 dan adopsi anonim AUTH-02 dipertahankan; adopsi memiliki guard ownership khusus dan berjalan dalam transaksi verifikasi yang sama. Runtime tidak memperoleh UPDATE ownership atau EXECUTE helper adopsi. Bukti izin data privat dari sumber tetap SRC-03B.

## Pengujian

Unit berada di backend/tests/unit/test_case_lifecycle_rules.py; tes Neon opt-in berada di backend/tests/integration/test_case_lifecycle.py. Jalankan dengan RUN_NEON_AUTH_TESTS=1, satu provisioning runner pada satu waktu. Script memuat credential internal; tidak perlu membuka file env.

Dari root repository, unit tanpa provider:

```powershell
uv run --locked --project backend pytest backend/tests/unit/test_case_lifecycle_rules.py -q
```

Integrasi pada database pengujian yang sudah dikonfigurasi:

```powershell
$env:RUN_NEON_AUTH_TESTS='1'
try {
    uv run --locked --project backend pytest backend/tests/integration/test_case_lifecycle.py -q --tb=short
} finally {
    Remove-Item Env:RUN_NEON_AUTH_TESTS
}
```

Tes mencakup matriks transisi/alasan penutupan, assignment dan reopen, race, stale version, idempotency, ACL tenant/pelanggan/staf/admin, CSRF, fault rollback, expiry, pencabutan membership, snapshot GET bersamaan dengan update, raw SQL history/provenance guard, privilege principal auth-owner serta regresi adopsi/receipt CASE-02 pada schema terbaru.

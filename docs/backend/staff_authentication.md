# Autentikasi staf AUTH-03

AUTH-03 menyediakan adapter password lokal untuk akun staf yang telah diprovisi. Login pelanggan lewat email tidak memberikan sesi staf, walaupun email tersebut juga tercatat sebagai kontak staf. Adapter ini memakai database proyek; tidak memerlukan provider identitas eksternal. Ini bukan implementasi SSO atau MFA.

## Alur browser

1. Browser melakukan bootstrap anonim melalui POST /api/v1/auth/sessions dengan HTTPS dan Origin yang sama. Perlindungan Host, Origin, cookie dan CSRF mengikuti [AUTH-01](authentication.md).
2. POST /api/v1/staff/auth/login menerima contact dan password saja. Header X-CSRF-Token wajib terikat sesi anonim. Tenant ditentukan dari host server; caller tidak mengirim principal, role, membership atau session_kind.
3. Backend mengonsumsi counter PostgreSQL per IP, kontak dan sesi sebelum melakukan pemeriksaan Argon2. Transaksi/lock database sudah ditutup selama pekerjaan hash berjalan. Identitas tidak dikenal, nonaktif dan password salah mengikuti jalur dummy verifier dan respons401 STAFF_LOGIN_FAILED yang sama.
4. Jika password benar, transaksi baru memeriksa lagi sesi asal, CSRF, versi credential, ikatan kontak terverifikasi, principal/tenant aktif, serta membership dengan role pada organisasi aktif. Backend membuat sesi staff baru dan mencabut sesi anonim lama. Dua login bersamaan dari satu sesi anonim hanya mempunyai satu pemenang.
5. GET /api/v1/staff/me mengembalikan session_kind, principal_id dan memberships aktif. Setiap membership berisi id, organization_id, version, roles dan queue_ids aktif. Password/hash/credential_version tidak masuk DTO. GET /api/v1/me dan bootstrap sesi aktif juga mengenali staff tanpa memberikan rincian membership.
6. Logout tetap POST /api/v1/auth/logout dengan CSRF. Satu browser pada origin ini memakai satu konteks sesi; pelanggan yang ingin login staf melakukan logout dan bootstrap anonim baru dahulu. Tidak ada upgrade pelanggan menjadi staf lewat endpoint customer.

Contoh payload login (nilai fiktif):

```json
{"contact":"staff@example.com","password":"A locally provisioned passphrase"}
```

Respons sukses200 berisi session_kind=staff dan principal_id. Cookie sesi/CSRF dirotasi dengan atribut AUTH-01. Kesalahan proof403, input422, pembatasan429 dengan Retry-After, dan kapasitas hash penuh503. Respons login/me/error autentikasi tidak boleh disimpan cache. Tidak ada endpoint mengambil credential atau membuat role sendiri.

## Password dan pembatasan resource

Argon2id memakai memory19456KiB, time2, parallelism1, salt16byte dan hash32byte. Salt acak menghasilkan hash berbeda untuk password yang sama. Input password tidak dinormalisasi atau dipotong. Provisioner menerima15–128karakter termasuk spasi/Unicode dan menolak beberapa nilai umum serta pola terlalu berulang. Ini daftar penolakan lokal terbatas, bukan pemeriksaan seluruh database password bocor.

Pekerjaan hash memakai executor dan semaphore bersama dalam satu proses Python: maksimal2worker, tanpa antrean menunggu. Jika penuh, permintaan mendapat503. Pembatalan request tidak melepaskan slot sampai worker benar-benar selesai. Event loop tidak menjalankan KDF langsung. Batas ini berlaku lintas instance service dalam proses sama; jumlah proses deployment mengalikan budget CPU/memory. Kapasitas dan target latensi harus dibuktikan pada deployment; tes saat ini membuktikan admission, cancellation dan responsiveness, bukan load benchmark produksi.

TTL staf8jam. Counter login memakai fixed-window APP_AUTH_RATE_WINDOW (default900detik), IP100/kontak10/sesi10 percobaan per window. Hit tersimpan meskipun password salah, identitas tidak dikenal, atau request dibatalkan. Limit awal perlu dikalibrasi terhadap NAT, beban dan risiko deployment.

## Provision dan rotasi

CLI berikut hanya mengatur credential principal/kontak/membership yang sudah ada. CLI tidak membuat principal, memverifikasi kontak, memberikan role, atau mengubah organisasi. Pengelolaan akun/membership tetap pekerjaan admin berikutnya. Tidak ada akun staf demo atau password default pada app.

```powershell
uv run --locked --project backend airport-assistant-staff-credential --tenant TENANT_UUID --principal PRINCIPAL_UUID --contact CONTACT_UUID
```

Jalankan secara interaktif dengan credential migration lokal. CLI meminta password dan konfirmasi melalui input tersembunyi; tidak ada argumen/env password. Terminal noninteraktif atau GetPassWarning menggagalkan proses sebelum membaca fallback yang dapat menampilkan input. Parameter UUID di atas diganti dengan ID metadata milik akun yang sudah diprovisi, bukan email/password.

Mengulang perintah merotasi password pada contact yang sama. Contact credential tidak dapat dipindahkan melalui operasi rotasi. Setiap UPDATE credential menghasilkan credential_version UUID baru melalui trigger; sesi dengan versi lama ditolak pada request berikutnya. Untuk menonaktifkan:

```powershell
uv run --locked --project backend airport-assistant-staff-credential --tenant TENANT_UUID --principal PRINCIPAL_UUID --disable
```

Isi .env dan password tidak ditampilkan. Credential direct tidak dipasang pada API. CLI memberi status umum, bukan mencetak verifier atau payload database. Audit perubahan credential yang append-only mengikuti fitur audit; jangan menganggap penyimpanan audit tersebut sudah tersedia.

## Membership dan batas akses

Service membaca ulang membership aktif pada setiap permintaan, tanpa menaruh roles dalam cookie atau cache sesi. Pencabutan role/queue, deaktivasi membership/organisasi/principal, dan rotasi/disable credential berlaku pada permintaan berikutnya. Penghapusan queue dari membership menghilangkan akses queue tersebut meskipun identitas staf masih sah.

Aturan berlaku dalam membership yang sama, bukan gabungan role seluruh organisasi:

| Tindakan | Syarat |
| --- | --- |
| read_case / reply_case | CS atau supervisor, organisasi cocok dan queue aktif yang tercantum dalam membership. |
| assign_case | Supervisor dengan organisasi dan queue yang cocok. |
| manage_memberships | Admin dalam organisasi tersebut; pemeriksaan target/self-escalation tetap wajib saat endpoint admin dibuat. |
| Tindakan tidak dikenal | Tolak. |

Admin organisasiA + CS organisasiB tidak menjadi CS organisasiA. Admin tanpa CS/supervisor tidak membaca isi kasus. AUTH-03 membuktikan kebijakan ini dengan fixture objek/queue dan identitas nyata; endpoint kasus belum ada. AUTH-04 dan fitur kasus harus memasang pemeriksaan yang sama pada transaksi tindakan, bukan mengandalkan hasil pemeriksaan yang lama.

## Kompatibilitas dan rollout

Versi AUTH-03 memperluas DTO /me dan bootstrap agar menerima kind staff. Server AUTH-01 lama hanya mengenali anonymous/customer. Karena itu, hentikan instance lama dan jalankan versi baru secara terkoordinasi sebelum memakai sesi staff; jangan menyebut pergantian campuran server lama/baru sudah kompatibel untuk cookie staff. Migrasi menambah struktur tanpa mengubah histori0001–0003; rollback aplikasi lama memerlukan rencana pencabutan sesi staff atau forward recovery yang direview.

## Database dan batas rilis

Revision0004_staff_auth menambah staff_credentials, kolom sessions.staff_credential_version, scope rate staf dan fungsi auth_staff_begin/finish/memberships. Tabel credential ENABLE/FORCE RLS; runtime tidak punya direct SELECT/DML. Fungsi SECURITY DEFINER memakai auth-owner NOLOGIN, search_path pg_catalog dan PUBLIC EXECUTE dicabut. Lock credential memerlukan SELECT serta UPDATE(credential_version); policy UPDATE hanya memberi visibilitas lock, WITH CHECK(false) tetap menolak perubahan. Penulisan credential hanya oleh administrator migration terpisah.

Password lokal membuktikan alur AUTH-03 tanpa integrasi IdP. Penggunaan akun berhak istimewa pada deployment nyata tetap memerlukan keputusan rilis mengenai MFA/IdP, recovery, pemeriksaan password bocor, audit, proxy/TLS, retensi serta benchmark kapasitas. AUTH-03 tidak menutup pekerjaan deployment atau menyatakan sertifikasi enterprise/government terpenuhi.

## Cara menguji

```powershell
$env:RUN_NEON_AUTH_TESTS='1'
try {
  uv run --locked --project backend pytest backend/tests/unit/test_staff_passwords.py backend/tests/integration/test_staff_auth.py -v
} finally { Remove-Item Env:RUN_NEON_AUTH_TESTS }
```

Tes memakai schema/role/akun fiktif sementara dan membersihkannya. Bukti mencakup login/rotasi/logout, scope organisasi+queue, role gabungan, pencabutan, perubahan identitas di antara begin/finish, concurrency, rate sebelumKDF, hash/secret redaction, input tersembunyi, pembatalan worker dan ACL. Pengujian customer pada head terbaru memastikan email staf tetap menghasilkan sesi customer melalui flow pelanggan.

Rujukan: [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html), [OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html), [Python getpass](https://docs.python.org/3.13/library/getpass.html), [Python concurrent futures](https://docs.python.org/3.13/library/concurrent.futures.html).

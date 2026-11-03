# Pemeriksaan akses bersama AUTH-04

AUTH-04 menyatukan identitas sesi, membership dan izin objek pada AccessService. /api/v1/me dan /api/v1/staff/me memakai dependency identitas bersama. StaffService.current/require juga memanggil layanan yang sama. Health dan bootstrap sesi tetap tersedia menurut kontraknya; bootstrap bukan akses ke objek privat.

Tahap ini menyediakan fondasi yang executable. Percakapan, draf dan kasus pada tes masih projection metadata fiktif. Belum ada endpoint produk untuk case, conversation, cursor, blob, booking, payment atau tool. Pemeriksaan pada jalur tersebut wajib dipasang dan dibuktikan bersama fitur masing-masing; QUALITY-01 menutup pengujian lintas jalurnya.

## Identitas berasal dari backend

Dependency HTTP memilih tenant dari hostname terdaftar, memeriksa HTTPS/Host/cookie, lalu AccessService.resolve memvalidasi sesi melalui AuthService.current. Untuk staff, membership dibaca ulang dari fungsi database yang juga memeriksa sesi, credential version dan eligibility terbaru. Membership kosong tidak memakai snapshot lama. Tidak ada role atau owner dari body/model yang menjadi identitas.

AccessIdentity menyimpan Session dan tuple membership; roles/queue_ids menjadi frozenset. database_context dihasilkan dari tenant/principal/kind yang sudah dibuktikan, untuk digunakan repository/RLS. Identitas dan hash internal tidak menjadi response DTO. /me tetap hanya session_kind/principal_id; staff/me menambahkan metadata membership yang diizinkan, tanpa credential/hash/token.

Snapshot ini berlaku untuk satu pemeriksaan/request, bukan tiket izin yang boleh disimpan pada singleton, checkpoint, job atau request berikutnya. Pemanggilan baru membaca ulang sesi dan scope. Query sesi dan membership saat ini terpisah; hasil ini tidak mengunci izin sepanjang operasi bisnis. Endpoint mutasi mendatang wajib memeriksa ulang sesi/ACL/state/version dalam transaksi tindakan yang sama, disertai RLS dan pemeriksaan grants sumber yang diperlukan.

## Tiga dependency HTTP

| Dependency | Tugas |
| --- | --- |
| get_identity | Identitas sesi aktif dari host/cookie server, termasuk anonymous/customer/staff. |
| get_staff_identity | Identitas aktif yang kind-nya staff, dengan membership terbaru. |
| get_mutation_identity | Identitas aktif ditambah Origin dan CSRF server-bound untuk permintaan mutasi. |

Host/Origin ganda, port Origin yang berbeda, cookie ganda dan header CSRF ganda tetap ditolak. Proteksi cookie __Host-, HTTPS, expiry, revocation, no-store dan redaksi mengikuti AUTH-01/03. Missing/inactive session menghasilkan401 sebelum lookup objek. Tidak ada fallback akses saat composition tidak memasang AccessService.

## Kunci dan metadata objek

ResourceKey memuat tenant_id, kind dan id yang diminta. Tenant berasal dari identitas server; caller hanya menunjuk ID yang akan dicari. ObjectRepository.find adalah port backend, bukan endpoint publik. Adapter fitur nantinya mencari metadata dengan key lengkap dan scope transaksi yang benar.

AccessService.require_object membandingkan tenant/kind/id projection terhadap key persis sebelum memeriksa pemilik atau role. Projection objekA tidak dapat dipakai untuk mengizinkan permintaan objekB. Objek yang diotorisasi dikembalikan sebagai projection yang sama; consumer tidak boleh menggantinya dengan hasil fetch bebas yang lain.

| Projection | Data backend dan batas |
| --- | --- |
| OwnedObject | kind conversation/draft, UUID tenant/id dan tepat satu owner_principal_id atau owner_session_id. ownership_only default false; backend harus secara eksplisit menetapkan true hanya untuk konten mandiri yang cukup dilindungi oleh ownership. |
| StaffObject case | UUID tenant/id/organization_id/queue_id wajib. Scope CS/supervisor mengikuti membership yang sama. |
| StaffObject membership | UUID tenant/id/organization_id/target_principal_id wajib; tidak membawa queue. Target principal wajib diketahui untuk mencegah self-escalation. |

Projection dibangun dari data backend yang dipercaya, tidak dideserialisasi dari request, jawaban model atau pengakuan keluarga. Field yang hilang/ambigu atau kind tidak dikenal ditolak. OwnedObject tidak menyediakan kind case/booking/payment/blob/cursor. Conversation/draft yang klasifikasi aksesnya belum diketahui atau membutuhkan pemeriksaan case/source tetap ditolak meskipun owner cocok; ownership_only tidak boleh diambil dari client.

## Aturan izin

- Anonymous hanya membaca/menggunakan conversation atau draft mandiri milik session_id persis. ID percakapan atau session lama tidak cukup untuk mengambil ownership.
- Customer hanya mengakses projection mandiri yang owner_principal_id-nya cocok. AUTH-02 memindahkan riwayat asal hanya melalui proof verifikasi browser-bound yang atomik; ID atau claim ownership tidak mengadopsi riwayat. Draf sesi asal dibatalkan, bukan diwariskan sebagai izin/consent.
- Staff tidak menggunakan jalur ownership pelanggan walaupun principal_id sama.
- Conversation mandiri: read_conversation/append_message. Draft mandiri: read_draft/edit_draft/confirm_draft/cancel_draft. Ini pemeriksaan ACL; expiry, state, consent, expected_version dan commit tidak digantikan oleh izin tersebut.
- Case staff: read_case/reply_case memerlukan CS atau supervisor dan queue aktif dalam organisasi yang sama. assign_case memerlukan supervisor pada scope tersebut. Admin tidak otomatis mendapat akses kasus; admin organisasiA + CS organisasiB tidak menjadi CS organisasiA.
- Membership: staff membaca membership dirinya yang cocok, atau admin membaca target dalam organisasinya. manage_memberships memerlukan admin dan target_principal_id bukan principal aktor. Pemeriksaan ini menolak seluruh membership diri sendiri, bukan hanya satu membership ID. Endpoint admin nanti tetap memeriksa scope lama/baru, state dan audit.
- Aksi lain ditolak. Tidak ada pengecualian universal untuk admin.

## 401, 404 dan 403

401 berarti sesi tidak sah/jenis staf diperlukan. Setelah identitas valid, objek tidak ditemukan, tenant berbeda, projection tidak cocok atau objek di luar scope memberi404 RESOURCE_NOT_FOUND yang sama. Aksi tidak dikenal pada objek tersembunyi tetap404. Jika objek boleh dilihat tetapi tindakannya dilarang, respons403 ACTION_FORBIDDEN. Error tidak menyebut ID/pemilik/organisasi objek tersembunyi.

StaffService.require dan AccessService.require_staff_scope adalah pemeriksaan capability organisasi/queue tanpa lookup objek. Kegagalannya403; helper ini tidak boleh dipakai sebagai pengganti pemeriksaan objek yang seharusnya tersembunyi404 atau sebagai izin mengubah membership diri sendiri.

## Memakai service pada fitur berikutnya

AccessService.require_resource memuat identitas/proof sebelum memanggil ObjectRepository.find, membatasi kind yang tersedia, memeriksa key projection, dan mengembalikan projection yang diizinkan. CSRF otomatis diwajibkan untuk aksi mutasi yang dikenal walaupun caller tidak mengirim mutation=True; flag tersebut tetap tersedia untuk operasi tambahan. Helper ini tidak melakukan side effect atau mengunci operasi bisnis sampai commit.

require_object adalah pemeriksaan ACL murni atas snapshot yang sudah dibuktikan. Endpoint menggunakannya setelah get_identity untuk read atau get_mutation_identity untuk mutasi. Memanggilnya dengan snapshot lama tidak membuktikan sesi/grants masih aktif. Mutasi, pagination/list/cursor, event/polling, blob dan tool harus mengikuti aturan di atas pada transaksi/query fitur masing-masing; jangan memfilter hasil list setelah pagination lalu menganggap total/cursor aman.

Tidak ada customer case_read atau booking/payment grant baru pada AUTH-04. SRC-03A/B harus memeriksa security labels dan source scopes. Worker outbound dan model/tool juga harus memeriksa izin saat eksekusi, bukan membawa AccessIdentity request lama.

## Pengujian

```powershell
$env:RUN_NEON_AUTH_TESTS='1'
try {
  uv run --locked --project backend pytest backend/tests/unit/test_access_policy.py backend/tests/integration/test_access_http.py -v
} finally { Remove-Item Env:RUN_NEON_AUTH_TESTS }
```

Unit menguji matrix owner/role/queue, metadata invalid, exact key binding, immutable collections, fail closed pada source-protected object, proof sebelum lookup dan pembacaan ulang membership. Integrasi memakai sesi pelanggan/staf dan role Neon nyata dalam schema sementara; resource yang diproteksi masih fixture projection. Router _auth04 hanya dipasang tes, bukan pada aplikasi atau wheel produksi. Uji memeriksa401 sebelum lookup, concealed404 identik, visible403, CSRF/Origin, penolakan klaim client, rotasi sesi serta pencabutan queue/role/membership/credential/expiry. Tidak mengklaim state transition atau query case/blob/cursor/tool yang belum dibangun sudah teruji.

Tidak ada migration baru: schema app tetap0004_staff_auth, hak database tidak diperluas dan histori migration tidak berubah. Tidak ada akun/credential baru atau perubahan .env yang diperlukan.

Rujukan: [OWASP Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html): deny by default, pemeriksaan setiap request, dan pengujian aturan izin.

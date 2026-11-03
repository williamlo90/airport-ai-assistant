'use strict';
// C-05 visual fixtures only: no fetch, storage, cookie access, auth or submission.
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[
        c
      ],
  );
const DEMO_DRAFT = '11111111-1111-4111-8111-111111111111';
const fixture = Object.freeze({
  reference: 'CASE-7b30e724f1e74d7d89d907c352d408d1',
  summary: 'Flight delayed; parking booking ends tonight',
  email: 'alex.morgan@example.com',
  caseStatus: 'new',
  nextActor: 'staff',
  nextAction: 'Review your report and decide the next step.',
});
const scenes = [
  'receipt',
  'receipt-only',
  'unknown',
  'checking',
  'recover',
  'pending',
  'expired',
  'missing',
  'unavailable',
  'restricted',
];
const params = new URLSearchParams(location.search);
let lang =
  params.getAll('lang').length === 1 && params.get('lang') === 'id'
    ? 'id'
    : 'en';
const uuid = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
let pointer =
  params.getAll('draft').length === 1 && uuid.test(params.get('draft'))
    ? params.get('draft')
    : null;
// Explicit preview chooses a design fixture ONCE, then is removed from the URL.
// Reload with only an opaque pointer cannot assert a committed result.
let scene =
  params.getAll('preview').length === 1 &&
  scenes.includes(params.get('preview'))
    ? params.get('preview')
    : pointer
      ? 'recover'
      : 'missing';
if (
  params.getAll('preview').length === 1 &&
  scenes.includes(params.get('preview'))
)
  pointer = scene === 'missing' ? null : DEMO_DRAFT;
let hasReviewContext =
  params.has('preview') && ['receipt', 'receipt-only'].includes(scene);
let lookup = 'committed',
  feedback = '',
  copyFeedback = '',
  generation = 0;
const copy = {
  en: {
    back: 'Help centre',
    preview: 'Design preview · Sample outcomes · No request is sent',
    state: 'Preview state',
    lookup: 'Sample lookup result',
    reload: 'Reload this link',
    journey: 'Your support request',
    steps: [
      'Your situation',
      'Verify email',
      'Review request',
      'Request status',
    ],
    note: 'Keep your reference for any follow-up about this request.',
    recorded: 'Your support request is recorded',
    recordedIntro:
      'Keep the reference below. Your report is available to the support team for review.',
    receipt: 'Request submitted',
    reference: 'Your request reference',
    copy: 'Copy reference',
    copied: 'Reference copied.',
    copyFailed:
      'Copy is unavailable. Select the reference and copy it manually.',
    copyHint: 'Use the full reference when asking about this request.',
    handling: 'What happens next',
    status: 'Case status',
    new: 'Awaiting assignment',
    nextActor: 'Next actor',
    staff: 'Support team',
    nextAction: 'Next step',
    next: 'Review your report and decide the next step.',
    summary: 'Your report',
    destination: 'Handling team',
    team: 'Parking support team · Westbridge Airport Services',
    contact: 'Replies about this request',
    email: 'Reply address',
    delivery: 'Email delivery',
    deliveryUnknown: 'Not confirmed',
    emailNote:
      'This address is recorded on your request. A recorded request does not mean an email has been sent or read.',
    details: 'View case details',
    detailsTitle: 'Case details',
    detailsNote:
      'Case details are not connected in this design preview. No private case data is loaded here.',
    closeDetails: 'Back to request status',
    home: 'Back to help centre',
    recoveredContact:
      'Reply details are not available in this recovered view. No email delivery is confirmed.',
    partialIntro:
      'Your reference is available, but current case updates could not be loaded.',
    partialTitle: 'Case updates are unavailable',
    partialText:
      'Your request is still recorded. Retry the case update lookup; do not submit another request.',
    notAvailable: 'Not available right now',
    retryUpdates: 'Retry case updates',
    noUpdates:
      'Case updates are still unavailable in this preview. The recorded request has not changed.',
    unknown: 'We could not confirm the result',
    unknownIntro:
      'Your request may have reached the service. Check its status before taking another step.',
    unknownBody:
      'Do not submit again or create another request while the result is unknown.',
    check: 'Check submission status',
    safeNote: 'Checking status does not send a new support request.',
    checking: 'Checking your submission',
    checkingIntro: 'Please wait while the submission status is checked.',
    checkingBody: 'The result is not confirmed yet. Sending again is disabled.',
    pending: 'Your request still needs a review',
    pendingIntro: 'No completed submission was found in this sample lookup.',
    pendingBody:
      'Review the latest request details and confirm again before sending. Reloading this link never gives permission to submit automatically.',
    review: 'Review request again',
    reviewNote:
      'This opens the separate C-04 review design. It does not recover server data or send the pending request.',
    expired: 'This review has expired',
    expiredIntro: 'This sample draft can no longer be confirmed.',
    expiredBody:
      'Start with a fresh review of the request details. The expired deadline cannot be extended by sending again.',
    freshReview: 'Open a fresh review',
    recover: 'Check the status of your request',
    recoverIntro:
      'You returned to a saved request link. Its outcome has not been checked yet.',
    recoverBody:
      'This link identifies the request to look up. It does not prove that a case was created.',
    missing: 'We cannot recover the submission from this link',
    missingIntro: 'The request link is missing or incomplete.',
    missingBody:
      'Do not assume the request failed. If you saved a case reference, keep it for your follow-up. This screen cannot safely check the submission without its original link.',
    missingNote:
      'No replacement request has been created. Return to the help centre for guidance before submitting again.',
    unavailable: 'Submission status is unavailable',
    unavailableIntro:
      'We could not check the result. Your request may still have been recorded.',
    unavailableBody:
      'Keep this link and try the status check again. No support request will be resent.',
    retry: 'Try status check again',
    restricted: 'This request is not available',
    restrictedIntro: 'We could not open this request in the current session.',
    restrictedBody:
      'Check your session and access before returning to this link. This page does not confirm whether a request exists or who owns it.',
    verify: 'Verify your email',
    verifyNote:
      'The verification screen is a separate visual preview. Access is not granted by this link.',
    fixture:
      'Sample receipt and recovery states only. No case, email or payment is created.',
    restored:
      'Sample lookup completed. This is a visual fixture, not a server response.',
  },
  id: {
    back: 'Pusat bantuan',
    preview: 'Pratinjau desain · Hasil contoh · Tidak ada pengiriman',
    state: 'Kondisi contoh',
    lookup: 'Hasil pengecekan contoh',
    reload: 'Muat ulang tautan ini',
    journey: 'Pengajuan bantuan Anda',
    steps: [
      'Situasi Anda',
      'Verifikasi email',
      'Tinjau pengajuan',
      'Status pengajuan',
    ],
    note: 'Simpan referensi untuk tindak lanjut terkait pengajuan ini.',
    recorded: 'Permintaan bantuan Anda tercatat',
    recordedIntro:
      'Simpan referensi di bawah. Laporan Anda tersedia untuk ditinjau tim bantuan.',
    receipt: 'Pengajuan tercatat',
    reference: 'Referensi pengajuan Anda',
    copy: 'Salin referensi',
    copied: 'Referensi disalin.',
    copyFailed:
      'Penyalinan belum tersedia. Pilih referensi dan salin secara manual.',
    copyHint: 'Gunakan referensi lengkap saat menanyakan pengajuan ini.',
    handling: 'Apa langkah berikutnya',
    status: 'Status kasus',
    new: 'Menunggu penugasan',
    nextActor: 'Pihak berikutnya',
    staff: 'Tim bantuan',
    nextAction: 'Langkah berikutnya',
    next: 'Tinjau laporan dan tentukan langkah penanganan.',
    summary: 'Laporan Anda',
    destination: 'Tim penanganan',
    team: 'Tim bantuan parkir · Westbridge Airport Services',
    contact: 'Balasan terkait pengajuan ini',
    email: 'Alamat balasan',
    delivery: 'Pengiriman email',
    deliveryUnknown: 'Belum dipastikan',
    emailNote:
      'Alamat ini tercatat pada pengajuan Anda. Pengajuan tercatat tidak berarti email sudah dikirim atau dibaca.',
    details: 'Lihat detail kasus',
    detailsTitle: 'Detail kasus',
    detailsNote:
      'Detail kasus belum terhubung dalam pratinjau desain ini. Tidak ada data kasus privat yang dimuat.',
    closeDetails: 'Kembali ke status pengajuan',
    home: 'Kembali ke pusat bantuan',
    recoveredContact:
      'Informasi balasan tidak tersedia pada tampilan pemulihan ini. Pengiriman email belum dipastikan.',
    partialIntro:
      'Referensi Anda tersedia, tetapi pembaruan kasus belum dapat dimuat.',
    partialTitle: 'Pembaruan kasus belum tersedia',
    partialText:
      'Pengajuan Anda tetap tercatat. Coba pengecekan pembaruan kasus; jangan kirim pengajuan lain.',
    notAvailable: 'Belum tersedia saat ini',
    retryUpdates: 'Coba pembaruan kasus',
    noUpdates:
      'Pembaruan kasus masih belum tersedia pada pratinjau ini. Pengajuan tercatat tidak berubah.',
    unknown: 'Hasil belum dapat dipastikan',
    unknownIntro:
      'Permintaan Anda mungkin telah diterima layanan. Periksa status sebelum mengambil langkah lain.',
    unknownBody:
      'Jangan kirim ulang atau membuat pengajuan lain saat hasil belum pasti.',
    check: 'Periksa status pengajuan',
    safeNote: 'Pengecekan status tidak mengirim permintaan bantuan baru.',
    checking: 'Memeriksa pengajuan Anda',
    checkingIntro: 'Tunggu saat status pengajuan diperiksa.',
    checkingBody: 'Hasil belum dipastikan. Pengiriman ulang dinonaktifkan.',
    pending: 'Pengajuan Anda masih perlu ditinjau',
    pendingIntro:
      'Pengecekan contoh ini belum menemukan pengajuan yang selesai dikirim.',
    pendingBody:
      'Tinjau informasi pengajuan terbaru dan konfirmasi kembali sebelum mengirim. Memuat ulang tautan ini tidak memberikan izin kirim otomatis.',
    review: 'Tinjau pengajuan lagi',
    reviewNote:
      'Ini membuka desain tinjauan C-04 yang terpisah. Tidak memulihkan data server atau mengirim draft tertunda.',
    expired: 'Tinjauan ini sudah kedaluwarsa',
    expiredIntro: 'Draft contoh ini tidak dapat dikonfirmasi lagi.',
    expiredBody:
      'Mulai dengan tinjauan baru atas informasi pengajuan. Klik kirim tidak memperpanjang batas waktu yang sudah lewat.',
    freshReview: 'Buka tinjauan baru',
    recover: 'Periksa status pengajuan Anda',
    recoverIntro:
      'Anda kembali ke tautan pengajuan yang tersimpan. Hasilnya belum diperiksa.',
    recoverBody:
      'Tautan ini mengidentifikasi permintaan yang perlu diperiksa. Tautan tidak membuktikan kasus sudah dibuat.',
    missing: 'Pengajuan belum dapat dipulihkan dari tautan ini',
    missingIntro: 'Tautan pengajuan hilang atau tidak lengkap.',
    missingBody:
      'Jangan menganggap pengajuan gagal. Jika Anda menyimpan referensi kasus, gunakan untuk tindak lanjut. Layar ini belum dapat mengecek pengajuan dengan aman tanpa tautan aslinya.',
    missingNote:
      'Tidak ada pengajuan pengganti yang dibuat. Kembali ke pusat bantuan untuk panduan sebelum mengirim ulang.',
    unavailable: 'Status pengajuan belum tersedia',
    unavailableIntro:
      'Hasil belum dapat diperiksa. Pengajuan Anda mungkin tetap sudah tercatat.',
    unavailableBody:
      'Simpan tautan ini dan coba periksa status kembali. Tidak ada pengiriman ulang pengajuan.',
    retry: 'Coba periksa status lagi',
    restricted: 'Pengajuan ini tidak tersedia',
    restrictedIntro: 'Pengajuan belum dapat dibuka pada sesi saat ini.',
    restrictedBody:
      'Periksa sesi dan akses Anda sebelum kembali ke tautan ini. Halaman ini tidak memastikan apakah pengajuan ada atau siapa pemiliknya.',
    verify: 'Verifikasi email Anda',
    verifyNote:
      'Layar verifikasi adalah pratinjau visual terpisah. Tautan ini tidak memberikan hak akses.',
    fixture:
      'Hanya contoh bukti pengajuan dan pemulihan. Tidak membuat kasus, email, atau pembayaran.',
    restored:
      'Pengecekan contoh selesai. Ini fixture visual, bukan respons server.',
  },
};
const t = () => copy[lang];
function syncURL() {
  const query = new URLSearchParams();
  if (pointer) query.set('draft', pointer);
  query.set('lang', lang);
  history.replaceState(null, '', location.pathname + '?' + query);
}
function sizeReference() {
  const field = $('reference');
  if (!field) return;
  field.style.height = 'auto';
  field.style.height = field.scrollHeight + 2 + 'px';
}
function action(label, value, kind = 'primary', disabled = false) {
  return `<button type="button" class="${kind}" data-action="${value}" ${disabled ? 'disabled' : ''}>${label}</button>`;
}
function link(label, target, kind = 'secondary') {
  return `<a class="${kind}" href="${target}">${label}</a>`;
}
function choose(next, explicit = false) {
  scene = next;
  feedback = '';
  copyFeedback = '';
  generation++;
  if (explicit) {
    hasReviewContext = ['receipt', 'receipt-only'].includes(next);
    pointer = next === 'missing' ? null : DEMO_DRAFT;
  }
  syncURL();
  render();
  $('main').focus();
}
function render() {
  const c = t();
  document.documentElement.lang = lang;
  document.title =
    c[
      scene === 'receipt' || scene === 'receipt-only'
        ? 'recorded'
        : scene === 'receipt-only'
          ? 'recorded'
          : scene
    ] + ' — Westbridge Airport';
  $('back-text').textContent = c.back;
  $('back-link').setAttribute('aria-label', c.back);
  $('back-link').href = $('brand').href = 'index.html?lang=' + lang;
  $('language').textContent = lang === 'en' ? 'ID' : 'EN';
  $('language').setAttribute(
    'aria-label',
    lang === 'en' ? 'Switch to Indonesian' : 'Ganti ke Inggris',
  );
  $('preview-label').textContent = c.preview;
  $('scene-label').textContent = c.state;
  $('lookup-label').textContent = c.lookup;
  $('reload-example').textContent = c.reload;
  $('journey-title').textContent = c.journey;
  $('journey-note').textContent = c.note;
  $('steps').innerHTML = c.steps
    .map(
      (s, i) =>
        `<li ${i === 3 ? 'aria-current="step"' : ''}><span class="step-number">${i + 1}</span>${s}</li>`,
    )
    .join('');
  const names =
    lang === 'en'
      ? [
          'Recorded request',
          'Recorded; case updates unavailable',
          'Result unknown',
          'Checking',
          'Reload recovery',
          'Pending; review again',
          'Expired draft',
          'Missing link',
          'Lookup unavailable',
          'Request unavailable',
        ]
      : [
          'Pengajuan tercatat',
          'Tercatat; pembaruan kasus belum tersedia',
          'Hasil belum pasti',
          'Memeriksa',
          'Pemulihan setelah reload',
          'Tertunda; tinjau lagi',
          'Draft kedaluwarsa',
          'Tautan hilang',
          'Pengecekan belum tersedia',
          'Pengajuan tidak tersedia',
        ];
  $('scene').innerHTML = scenes
    .map(
      (s, i) =>
        `<option value="${s}" ${s === scene ? 'selected' : ''}>${names[i]}</option>`,
    )
    .join('');
  const outcomes =
    lang === 'en'
      ? ['Recorded', 'Pending', 'Expired', 'Unavailable', 'Restricted']
      : [
          'Tercatat',
          'Tertunda',
          'Kedaluwarsa',
          'Belum tersedia',
          'Akses dibatasi',
        ];
  $('lookup').innerHTML = [
    'committed',
    'pending',
    'expired',
    'unavailable',
    'restricted',
  ]
    .map(
      (s, i) =>
        `<option value="${s}" ${s === lookup ? 'selected' : ''}>${outcomes[i]}</option>`,
    )
    .join('');
  $('lookup').disabled = ![
    'unknown',
    'recover',
    'unavailable',
    'checking',
  ].includes(scene);
  $('screen').innerHTML = ['receipt', 'receipt-only'].includes(scene)
    ? receipt(c)
    : recovery(c);
  sizeReference();
  document
    .querySelectorAll('[data-action]')
    .forEach((b) => (b.onclick = () => act(b.dataset.action)));
}
function receipt(c) {
  const partial = scene === 'receipt-only';
  const fields = partial
    ? `<dt>${c.status}</dt><dd>${c.notAvailable}</dd><dt>${c.nextActor}</dt><dd>${c.notAvailable}</dd><dt>${c.nextAction}</dt><dd>${c.notAvailable}</dd>`
    : `<dt>${c.status}</dt><dd>${c[fixture.caseStatus]}</dd><dt>${c.nextActor}</dt><dd>${c[fixture.nextActor]}</dd><dt>${c.nextAction}</dt><dd lang="en">${esc(fixture.nextAction)}</dd><dt>${c.summary}</dt><dd lang="en">${esc(fixture.summary)}</dd>${hasReviewContext ? `<dt>${c.destination}</dt><dd>${c.team}</dd>` : ''}`;
  return `<div class="page-heading"><h1>${c.recorded}</h1><p>${partial ? c.partialIntro : c.recordedIntro}</p></div>
  <section class="paper"><div class="receipt-head"><p class="receipt-status"><span class="receipt-dot" aria-hidden="true"></span>${c.receipt}</p>
  <label class="receipt-label" for="reference">${c.reference}</label><div class="reference-line"><textarea class="reference-value" id="reference" readonly rows="2">${fixture.reference}</textarea>${action(c.copy, 'copy', 'secondary')}</div><p id="copy-status" role="status" class="result-message">${copyFeedback ? c[copyFeedback] : ''}</p><p class="reference-hint">${c.copyHint}</p></div>
  <div class="receipt-details"><h2>${c.handling}</h2>${partial ? `<div class="notice warning" role="status"><h2>${c.partialTitle}</h2><p>${c.partialText}</p></div>` : ''}<dl>${fields}</dl>${partial ? `<div class="actions">${action(c.retryUpdates, 'case-updates', 'secondary')}</div><p class="result-message" id="case-update-status" role="status">${feedback ? c[feedback] : ''}</p>` : ''}</div>
  <div class="receipt-details"><h2>${c.contact}</h2><dl>${hasReviewContext ? `<dt>${c.email}</dt><dd>${fixture.email}</dd>` : ''}<dt>${c.delivery}</dt><dd>${c.deliveryUnknown}</dd></dl><p class="receipt-note">${hasReviewContext ? c.emailNote : c.recoveredContact}</p></div></section>
  <div class="receipt-actions">${action(c.details, 'details')}${link(c.home, 'index.html?lang=' + lang)}</div><p class="status-explanation">${c.fixture}</p>`;
}
function recovery(c) {
  const keys = {
    unknown: ['unknown', 'unknownIntro', 'unknownBody'],
    checking: ['checking', 'checkingIntro', 'checkingBody'],
    recover: ['recover', 'recoverIntro', 'recoverBody'],
    pending: ['pending', 'pendingIntro', 'pendingBody'],
    expired: ['expired', 'expiredIntro', 'expiredBody'],
    missing: ['missing', 'missingIntro', 'missingBody'],
    unavailable: ['unavailable', 'unavailableIntro', 'unavailableBody'],
    restricted: ['restricted', 'restrictedIntro', 'restrictedBody'],
  };
  const [title, intro, body] = keys[scene];
  let controls = '',
    note = c.safeNote;
  if (['unknown', 'recover', 'unavailable'].includes(scene))
    controls = action(scene === 'unavailable' ? c.retry : c.check, 'lookup');
  if (scene === 'checking')
    controls = action(c.checking, 'lookup', 'primary', true);
  if (scene === 'pending' || scene === 'expired') {
    controls = link(
      scene === 'pending' ? c.review : c.freshReview,
      'request.html?step=review&lang=' +
        lang +
        (scene === 'pending' && pointer ? '&draft=' + pointer : ''),
      'primary',
    );
    note = c.reviewNote;
  }
  if (scene === 'restricted') {
    controls = link(c.home, 'index.html?lang=' + lang, 'primary');
    note = c.fixture;
  }
  if (scene === 'missing') {
    controls = link(c.home, 'index.html?lang=' + lang, 'primary');
    note = c.missingNote;
  }
  return `<div class="page-heading"><h1>${c[title]}</h1><p>${c[intro]}</p></div><section class="paper recovery-paper" ${scene === 'checking' ? 'aria-busy="true"' : ''}><p ${scene === 'checking' ? 'role="status"' : 'role="alert"'}>${c[body]}</p><div class="actions">${controls}</div><p class="status-explanation">${note}</p></section>${!['missing'].includes(scene) ? `<div class="receipt-actions">${link(c.home, 'index.html?lang=' + lang)}</div>` : ''}<p class="result-message" role="status">${feedback ? c[feedback] : ''}</p>`;
}
async function act(kind) {
  if (kind === 'lookup') {
    if (!pointer) return choose('missing');
    if (pointer !== DEMO_DRAFT) return choose('restricted');
    choose(lookup === 'committed' ? 'receipt' : lookup);
    feedback = 'restored';
    render();
    $('main').focus();
    return;
  }
  if (kind === 'case-updates') {
    feedback = 'noUpdates';
    $('case-update-status').textContent = t().noUpdates;
    return;
  }
  if (kind === 'details') {
    $('detail-title').textContent = t().detailsTitle;
    $('detail-copy').textContent = t().detailsNote;
    $('close-detail').textContent = t().closeDetails;
    $('detail-note').showModal();
    return;
  }
  if (kind === 'copy') {
    const at = generation;
    try {
      await navigator.clipboard.writeText(fixture.reference);
      if (at !== generation) return;
      copyFeedback = 'copied';
      $('copy-status').textContent = t().copied;
    } catch {
      if (at !== generation) return;
      copyFeedback = 'copyFailed';
      $('copy-status').textContent = t().copyFailed;
      $('reference').focus();
      $('reference').select();
    }
  }
}
$('close-detail').onclick = () => $('detail-note').close();
$('scene').onchange = (e) => choose(e.target.value, true);
$('lookup').onchange = (e) => (lookup = e.target.value);
$('language').onclick = () => {
  lang = lang === 'en' ? 'id' : 'en';
  syncURL();
  render();
};
$('reload-example').onclick = () => location.reload();
window.addEventListener('resize', sizeReference);
document.fonts?.ready?.then(sizeReference);
syncURL();
render();

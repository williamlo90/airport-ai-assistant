'use strict';
// C-06 design fixtures only. No backend, authentication, email or persistent data.
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[
        c
      ],
  );
const DEMO_CASE = '33333333-3333-4333-8333-333333333333';
const REFERENCE = 'CASE-7b30e724f1e74d7d89d907c352d408d1';
const original = Object.freeze({
  summary: 'Flight delayed; parking booking ends tonight',
  report:
    'My flight has been delayed until tomorrow. My parking booking ends at 11 pm tonight. Could you explain my options and help me contact the parking team?',
  booking: 'WB-PK-20481',
});
const closures = Object.freeze({
  resolved: Object.freeze({
    reason: 'resolved',
    resolution:
      'Explained the parking extension procedure and where to manage the existing booking. No booking or payment was changed.',
    next: 'No further action is pending. The customer may request follow-up on the same issue.',
  }),
  no_response: Object.freeze({
    reason: 'no_response',
    resolution:
      'Closed after documented manual follow-up without a customer response. This does not establish customer satisfaction.',
    next: 'No further action is pending. The customer may request follow-up on the same issue.',
  }),
});
const scenes = [
  'new',
  'active',
  'waiting',
  'closed',
  'closed-no-response',
  'followup',
  'sending',
  'unknown',
  'retry',
  'conflict',
  'continued',
  'continued-new',
  'observed',
  'recover',
  'unavailable',
  'restricted',
];
const params = new URLSearchParams(location.search),
  uuid = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
let lang =
  params.getAll('lang').length === 1 && params.get('lang') === 'id'
    ? 'id'
    : 'en';
let caseId =
  params.getAll('case').length === 1 && uuid.test(params.get('case'))
    ? params.get('case')
    : null;
const preview =
  params.getAll('preview').length === 1 &&
  scenes.includes(params.get('preview'))
    ? params.get('preview')
    : null;
let phase = preview || (caseId === DEMO_CASE ? 'recover' : 'restricted');
if (preview) caseId = DEMO_CASE;
let explanation = '',
  sameIssue = false,
  command = null,
  applied = false,
  version = 7,
  reason = 'resolved',
  feedback = '',
  observedStatus = 'in_progress',
  sendOutcome = 'assigned',
  readOutcome = 'closed',
  pendingNavigation = null,
  errors = [];
const copy = {
  en: {
    back: 'Request receipt',
    preview: 'Design preview',
    state: 'Preview state',
    outcome: 'Sample send result',
    lookup: 'Sample status check',
    rail: 'Your support case',
    reference: 'Case reference',
    railNote: 'Keep this reference when asking about the same issue.',
    help: 'Help centre',
    title: 'Your case, at a glance',
    new: 'Awaiting assignment',
    in_progress: 'In progress',
    awaiting_customer: 'Waiting for your information',
    closed: 'Closed',
    status: 'Current case status',
    nextActor: 'Next actor',
    staff: 'Support team',
    customer: 'You',
    none: 'No action pending',
    nextAction: 'Next step',
    newNext: 'Review your report and decide the next step.',
    activeNext: 'Review the parking policy and explain the available options.',
    waitingNext: 'Confirm which parking booking you need help with.',
    continuedNext: 'Customer requested follow-up for the same issue.',
    updated: 'Latest update: 4 Nov 2026 · UTC',
    lastResult: 'Previous handling result',
    closedResult: 'Handling result',
    closedOn: 'Recorded closure · 4 Nov 2026, 15:05 UTC',
    reason: 'Closure reason',
    resolved: 'Resolved',
    no_response: 'No response',
    noResponseNote:
      'No response does not mean you were satisfied. You can still ask for help with the same issue.',
    closureNote:
      'This result stays visible if you request further handling. Case closure does not confirm email delivery.',
    unresolved: 'My issue is not resolved',
    other: 'This is a different issue',
    otherNote:
      'For a different issue, start a separate support request. A thank-you does not reopen this case.',
    followTitle: 'What still needs attention?',
    followBody:
      'Explain what remains unresolved about this same issue. The previous result above will stay on the case.',
    message: 'Your explanation',
    hint: 'Do not include payment-card details, passwords or verification codes. Maximum 4,000 characters.',
    same: 'This is about the same issue shown above.',
    send: 'Request further handling',
    cancel: 'Cancel',
    requiredMessage: 'Explain what is still unresolved.',
    requiredSame: 'Confirm this concerns the same issue.',
    errorTitle: 'Check your follow-up request',
    original: 'Your original report',
    originalNote:
      'Your words are kept as written. These references are part of your report, not verified booking or payment facts.',
    booking: 'Booking reference',
    transaction: 'Transaction reference',
    notProvided: 'Not provided',
    history: 'View case history',
    created: 'Support request recorded.',
    assigned: 'Case accepted by the support team.',
    closedEvent: 'Case closed.',
    reopenedEvent: 'Customer requested further handling.',
    readEvent:
      'Latest case status changed; this check does not identify which request caused it.',
    latest: 'Latest case update',
    activeNote:
      'Adding information to an active case is not available in this preview. Keep your reference and use the help centre if you need guidance.',
    followRecorded: 'Follow-up request recorded',
    followRecordedNote:
      'This sample response records your request on the same case. It does not confirm an email was sent or that a staff member has read it.',
    explanationRecord: 'Your follow-up explanation',
    sending: 'Sending your follow-up…',
    sendingNote: 'The outcome is not confirmed yet. Sending again is disabled.',
    unknown: 'Your follow-up is not confirmed',
    unknownNote:
      'The service may have received your request. Check the latest case before deciding what to do. Do not send another request.',
    check: 'Check latest case',
    retryTitle: 'The latest case is still closed',
    retryNote:
      'The earlier request is still unconfirmed. You may retry the same request with its original contents; it will not be replaced by a new request.',
    retry: 'Retry the same request',
    readonly:
      'Your original follow-up is kept unchanged while its result is uncertain.',
    conflict: 'This case changed before your update',
    conflictNote:
      'Load the latest case and review your explanation before sending again. No update has been retried automatically.',
    loadLatest: 'Load latest case',
    loaded:
      'Latest sample case loaded. Review your explanation and confirm the same issue before sending again.',
    observed: 'Latest case status loaded',
    observedNote:
      'This check does not confirm that your earlier follow-up was accepted. No request has been resent.',
    recover: 'Check the latest case first',
    recoverNote:
      'You returned to a case link. Unsent text and previous request information are not recovered from the URL. Loading this link never sends a follow-up.',
    recoverCheck: 'Load latest case',
    unavailable: 'Case details are unavailable',
    unavailableNote:
      'Your case could not be loaded. Try reading its latest status again. No follow-up request will be sent.',
    retryRead: 'Try reading case again',
    restricted: 'This case is not available',
    restrictedNote:
      'We cannot open this case in the current view. Check your session and access. This page does not confirm whether a case exists or who owns it.',
    home: 'Back to help centre',
    leaveTitle: 'Leave this case?',
    leaveNote:
      'Leaving discards unsent text and local recovery information. It does not cancel a request that may have reached the service.',
    stay: 'Stay on this case',
    discard: 'Discard and leave',
    fixture:
      'Visual example only. No real case update, email or payment is made.',
    pendingNote: 'Check the result before editing or sending anything else.',
    loadingRecovery: 'Read the latest case before continuing.',
    caseDetails: 'Case details',
    caseNewActor: 'Support team',
  },
  id: {
    back: 'Bukti pengajuan',
    preview: 'Pratinjau desain',
    state: 'Kondisi contoh',
    outcome: 'Hasil pengiriman contoh',
    lookup: 'Pengecekan status contoh',
    rail: 'Kasus bantuan Anda',
    reference: 'Referensi kasus',
    railNote: 'Gunakan referensi ini saat menanyakan masalah yang sama.',
    help: 'Pusat bantuan',
    title: 'Ringkasan kasus Anda',
    new: 'Menunggu penugasan',
    in_progress: 'Ditangani',
    awaiting_customer: 'Menunggu informasi Anda',
    closed: 'Ditutup',
    status: 'Status kasus saat ini',
    nextActor: 'Pihak berikutnya',
    staff: 'Tim bantuan',
    customer: 'Anda',
    none: 'Tidak ada tindakan tertunda',
    nextAction: 'Langkah berikutnya',
    newNext: 'Review your report and decide the next step.',
    activeNext: 'Review the parking policy and explain the available options.',
    waitingNext: 'Confirm which parking booking you need help with.',
    continuedNext: 'Customer requested follow-up for the same issue.',
    updated: 'Pembaruan terbaru: 4 Nov 2026 · UTC',
    lastResult: 'Hasil penanganan sebelumnya',
    closedResult: 'Hasil penanganan',
    closedOn: 'Penutupan tercatat · 4 Nov 2026, 15.05 UTC',
    reason: 'Alasan penutupan',
    resolved: 'Terselesaikan',
    no_response: 'Tidak ada respons',
    noResponseNote:
      'Tidak ada respons bukan berarti Anda puas. Anda tetap dapat meminta bantuan untuk masalah yang sama.',
    closureNote:
      'Hasil ini tetap terlihat ketika Anda meminta penanganan lanjutan. Kasus ditutup tidak membuktikan email terkirim.',
    unresolved: 'Masalah saya belum selesai',
    other: 'Ini masalah yang berbeda',
    otherNote:
      'Untuk masalah berbeda, mulai pengajuan terpisah. Ucapan terima kasih tidak membuka kembali kasus ini.',
    followTitle: 'Apa yang masih perlu ditangani?',
    followBody:
      'Jelaskan bagian dari masalah yang sama yang belum terselesaikan. Hasil sebelumnya tetap disertakan pada kasus.',
    message: 'Penjelasan Anda',
    hint: 'Jangan sertakan data kartu pembayaran, kata sandi, atau kode verifikasi. Maksimal 4.000 karakter.',
    same: 'Ini terkait masalah yang sama dengan laporan di atas.',
    send: 'Minta penanganan lanjutan',
    cancel: 'Batal',
    requiredMessage: 'Jelaskan masalah yang masih belum terselesaikan.',
    requiredSame: 'Pastikan ini terkait masalah yang sama.',
    errorTitle: 'Periksa permintaan lanjutan',
    original: 'Laporan asli Anda',
    originalNote:
      'Tulisan Anda dipertahankan seperti aslinya. Referensi adalah bagian laporan, bukan fakta booking atau pembayaran yang sudah diverifikasi.',
    booking: 'Referensi booking',
    transaction: 'Referensi transaksi',
    notProvided: 'Belum diisi',
    history: 'Lihat riwayat kasus',
    created: 'Pengajuan bantuan tercatat.',
    assigned: 'Kasus diterima oleh tim bantuan.',
    closedEvent: 'Kasus ditutup.',
    reopenedEvent: 'Pelanggan meminta penanganan lanjutan.',
    readEvent:
      'Status kasus terbaru berubah; pengecekan ini tidak menunjukkan permintaan mana yang menyebabkannya.',
    latest: 'Pembaruan kasus terbaru',
    activeNote:
      'Penambahan informasi pada kasus aktif belum tersedia dalam pratinjau ini. Simpan referensi dan gunakan pusat bantuan jika membutuhkan panduan.',
    followRecorded: 'Permintaan lanjutan tercatat',
    followRecordedNote:
      'Respons contoh ini mencatat permintaan pada kasus yang sama. Tidak membuktikan email dikirim atau laporan sudah dibaca petugas.',
    explanationRecord: 'Penjelasan lanjutan Anda',
    sending: 'Mengirim permintaan lanjutan…',
    sendingNote: 'Hasil belum dipastikan. Pengiriman ulang dinonaktifkan.',
    unknown: 'Permintaan lanjutan belum dipastikan',
    unknownNote:
      'Layanan mungkin sudah menerima permintaan Anda. Periksa kasus terbaru sebelum mengambil tindakan. Jangan kirim permintaan lain.',
    check: 'Periksa kasus terbaru',
    retryTitle: 'Kasus terbaru masih ditutup',
    retryNote:
      'Hasil permintaan sebelumnya masih belum pasti. Anda dapat mencoba permintaan yang sama dengan isi aslinya; bukan membuat permintaan pengganti.',
    retry: 'Coba lagi permintaan yang sama',
    readonly:
      'Penjelasan asli tetap dipertahankan tanpa perubahan selama hasilnya belum pasti.',
    conflict: 'Kasus berubah sebelum pembaruan Anda',
    conflictNote:
      'Muat kasus terbaru dan tinjau penjelasan Anda sebelum mengirim lagi. Tidak ada retry otomatis.',
    loadLatest: 'Muat kasus terbaru',
    loaded:
      'Kasus contoh terbaru dimuat. Tinjau penjelasan dan pastikan masalah yang sama sebelum mengirim lagi.',
    observed: 'Status kasus terbaru dimuat',
    observedNote:
      'Pengecekan ini tidak memastikan permintaan lanjutan sebelumnya sudah diterima. Tidak ada pengiriman ulang.',
    recover: 'Periksa kasus terbaru terlebih dahulu',
    recoverNote:
      'Anda kembali ke tautan kasus. Tulisan yang belum dikirim dan informasi permintaan lama tidak dipulihkan dari URL. Memuat tautan ini tidak mengirim permintaan lanjutan.',
    recoverCheck: 'Muat kasus terbaru',
    unavailable: 'Detail kasus belum tersedia',
    unavailableNote:
      'Kasus belum dapat dimuat. Coba membaca status terbaru lagi. Tidak ada permintaan lanjutan yang dikirim.',
    retryRead: 'Coba baca kasus lagi',
    restricted: 'Kasus ini tidak tersedia',
    restrictedNote:
      'Kasus belum dapat dibuka pada tampilan ini. Periksa sesi dan akses Anda. Halaman ini tidak memastikan apakah kasus ada atau siapa pemiliknya.',
    home: 'Kembali ke pusat bantuan',
    leaveTitle: 'Tinggalkan kasus ini?',
    leaveNote:
      'Meninggalkan halaman membuang tulisan yang belum dikirim dan informasi pemulihan lokal. Ini tidak membatalkan permintaan yang mungkin sudah diterima layanan.',
    stay: 'Tetap pada kasus ini',
    discard: 'Buang perubahan dan pindah',
    fixture:
      'Hanya contoh visual. Tidak membuat pembaruan kasus, email, atau pembayaran nyata.',
    pendingNote: 'Periksa hasil sebelum mengedit atau mengirim apa pun lagi.',
    loadingRecovery: 'Baca kasus terbaru sebelum melanjutkan.',
    caseDetails: 'Detail kasus',
    caseNewActor: 'Tim bantuan',
  },
};
const c = () => copy[lang];
const uncertain = () => command !== null && !applied && phase !== 'conflict';
const dirty = () =>
  (['followup', 'conflict'].includes(phase) &&
    (explanation.trim() !== '' || sameIssue)) ||
  uncertain();
const hasClosure = () => !['new', 'active', 'waiting'].includes(phase);
function syncURL() {
  const q = new URLSearchParams();
  if (caseId) q.set('case', caseId);
  q.set('lang', lang);
  history.replaceState(null, '', location.pathname + '?' + q);
}
function sampleCommand() {
  return Object.freeze({
    client_request_id: '22222222-2222-4222-8222-222222222222',
    expected_version: 7,
    intent: 'same_issue',
    message:
      'The parking page still does not let me extend my booking. Could you help me with this same issue?',
  });
}
function configure(next) {
  phase = next;
  explanation = '';
  sameIssue = false;
  command = null;
  applied = false;
  version = 7;
  feedback = '';
  errors = [];
  reason = next === 'closed-no-response' ? 'no_response' : 'resolved';
  observedStatus = 'in_progress';
  if (
    [
      'sending',
      'unknown',
      'retry',
      'continued',
      'continued-new',
      'observed',
    ].includes(next)
  ) {
    command = sampleCommand();
    explanation = command.message;
    sameIssue = true;
    applied = ['continued', 'continued-new'].includes(next);
  }
  if (next === 'followup' || next === 'conflict') explanation = '';
}
function button(label, action, kind = 'primary', disabled = false) {
  return `<button type="button" class="${kind}" data-action="${action}" ${disabled ? 'disabled' : ''}>${label}</button>`;
}
function notice(title, text, actions = '', kind = 'warning') {
  return `<div class="case-notice ${kind}" role="status"><h2>${title}</h2><p>${text}</p>${actions}</div>`;
}
function currentStatus() {
  if (phase === 'new' || phase === 'continued-new') return 'new';
  if (['active', 'continued'].includes(phase)) return 'in_progress';
  if (phase === 'waiting') return 'awaiting_customer';
  if (phase === 'observed') return observedStatus;
  return 'closed';
}
function nextAction() {
  const t = c();
  if (phase === 'observed')
    return observedStatus === 'new' ? t.newNext : t.activeNext;
  if (['continued', 'continued-new'].includes(phase)) return t.continuedNext;
  const status = currentStatus();
  return status === 'new'
    ? t.newNext
    : status === 'in_progress'
      ? t.activeNext
      : status === 'awaiting_customer'
        ? t.waitingNext
        : closures[reason].next;
}
function actor() {
  return currentStatus() === 'awaiting_customer'
    ? c().customer
    : currentStatus() === 'closed'
      ? c().none
      : c().staff;
}
function render() {
  const t = c();
  document.documentElement.lang = lang;
  document.title = t.caseDetails + ' — Westbridge Airport';
  $('back-text').textContent = t.back;
  $('back-link').setAttribute('aria-label', t.back);
  $('back-link').href =
    'submission.html?draft=11111111-1111-4111-8111-111111111111&lang=' + lang;
  $('brand').href = $('help-link').href = 'index.html?lang=' + lang;
  $('help-link').textContent = t.help;
  $('language').textContent = lang === 'en' ? 'ID' : 'EN';
  $('language').setAttribute(
    'aria-label',
    lang === 'en' ? 'Switch to Indonesian' : 'Ganti ke Inggris',
  );
  $('preview-note').textContent = t.preview;
  $('scene-label').textContent = t.state;
  $('outcome-label').textContent = t.outcome;
  $('lookup-label').textContent = t.lookup;
  $('rail-title').textContent = t.rail;
  $('reference-label').textContent = t.reference;
  $('rail-reference').textContent = [
    'recover',
    'unavailable',
    'restricted',
  ].includes(phase)
    ? '—'
    : REFERENCE;
  $('rail-note').textContent = t.railNote;
  const labels =
    lang === 'en'
      ? [
          'New case',
          'In progress',
          'Waiting for customer',
          'Closed — resolved',
          'Closed — no response',
          'Same-issue form',
          'Sending',
          'Result unknown',
          'Retry same request',
          'Version conflict',
          'Continued — in progress',
          'Continued — unassigned',
          'Status observed; result unconfirmed',
          'Reload recovery',
          'Case unavailable',
          'Access unavailable',
        ]
      : [
          'Kasus baru',
          'Ditangani',
          'Menunggu pelanggan',
          'Ditutup — terselesaikan',
          'Ditutup — tanpa respons',
          'Form masalah yang sama',
          'Mengirim',
          'Hasil belum pasti',
          'Coba lagi permintaan sama',
          'Konflik versi',
          'Lanjutan — ditangani',
          'Lanjutan — belum ditugaskan',
          'Status dibaca; hasil belum pasti',
          'Pemulihan setelah reload',
          'Kasus belum tersedia',
          'Akses belum tersedia',
        ];
  $('scene').innerHTML = scenes
    .map(
      (s, i) =>
        `<option value="${s}" ${s === phase ? 'selected' : ''}>${labels[i]}</option>`,
    )
    .join('');
  const sendLabels =
    lang === 'en'
      ? [
          'Recorded — assigned team',
          'Recorded — unassigned',
          'Timeout / outcome unknown',
          'Version conflict',
          'Service unavailable',
        ]
      : [
          'Tercatat — ada petugas',
          'Tercatat — belum ditugaskan',
          'Timeout / hasil belum pasti',
          'Konflik versi',
          'Layanan belum tersedia',
        ];
  $('outcome').innerHTML = [
    'assigned',
    'unassigned',
    'timeout',
    'conflict',
    'unavailable',
  ]
    .map(
      (s, i) =>
        `<option value="${s}" ${s === sendOutcome ? 'selected' : ''}>${sendLabels[i]}</option>`,
    )
    .join('');
  $('outcome').disabled = !['followup', 'retry'].includes(phase);
  const readLabels =
    lang === 'en'
      ? [
          'Still closed',
          'In progress',
          'Awaiting assignment',
          'Unavailable',
          'Access unavailable',
        ]
      : [
          'Masih ditutup',
          'Ditangani',
          'Menunggu penugasan',
          'Belum tersedia',
          'Akses belum tersedia',
        ];
  $('lookup').innerHTML = [
    'closed',
    'active',
    'new',
    'unavailable',
    'restricted',
  ]
    .map(
      (s, i) =>
        `<option value="${s}" ${s === readOutcome ? 'selected' : ''}>${readLabels[i]}</option>`,
    )
    .join('');
  $('lookup').disabled = !['unknown', 'recover', 'unavailable'].includes(phase);
  $('screen').innerHTML = ['recover', 'unavailable', 'restricted'].includes(
    phase,
  )
    ? boundaryView()
    : caseView();
  document
    .querySelectorAll('[data-action]')
    .forEach((b) => (b.onclick = () => act(b.dataset.action)));
  bindForm();
}
function boundaryView() {
  const t = c();
  const title =
    phase === 'recover'
      ? t.recover
      : phase === 'unavailable'
        ? t.unavailable
        : t.restricted;
  const body =
    phase === 'recover'
      ? t.recoverNote
      : phase === 'unavailable'
        ? t.unavailableNote
        : t.restrictedNote;
  return `<section class="case-paper failure-card"><h1>${title}</h1><p>${body}</p><div class="actions">${phase === 'restricted' ? `<a class="subtle-link" href="index.html?lang=${lang}" data-leave>${t.home}</a>` : button(phase === 'recover' ? t.recoverCheck : t.retryRead, 'read')}</div><p class="status-note">${t.fixture}</p></section>`;
}
function caseView() {
  const t = c(),
    status = currentStatus();
  let banner = '';
  if (phase === 'sending') banner = notice(t.sending, t.sendingNote, '', '');
  if (phase === 'unknown')
    banner = notice(
      t.unknown,
      t.unknownNote,
      button(t.check, 'read', 'secondary'),
    );
  if (phase === 'retry')
    banner = notice(
      t.retryTitle,
      t.retryNote,
      button(t.retry, 'send', 'secondary'),
    );
  if (phase === 'conflict')
    banner = notice(
      t.conflict,
      t.conflictNote,
      button(t.loadLatest, 'reload-conflict', 'secondary'),
    );
  if (phase === 'observed') banner = notice(t.observed, t.observedNote, '', '');
  if (['continued', 'continued-new'].includes(phase))
    banner = notice(t.followRecorded, t.followRecordedNote, '', '');
  if (feedback) banner += notice(t.loaded, '', '', '');
  return `<div class="page-heading"><h1>${t.title}</h1><p lang="en">${esc(original.summary)}</p></div>${banner}<div class="case-paper"><section class="case-current"><div class="case-state-line"><span class="case-badge ${status}">${t[status]}</span><span class="case-time">${t.updated}</span></div><dl><dt>${t.nextActor}</dt><dd>${actor()}</dd><dt>${t.nextAction}</dt><dd lang="en">${esc(nextAction())}</dd></dl></section>${hasClosure() ? closureView() : `<section class="case-section"><p class="followup-disabled">${t.activeNote}</p></section>`}${followupView()}<section class="case-section"><h2>${t.original}</h2><p class="original-report" lang="en">${esc(original.report)}</p><dl style="margin-top:18px"><dt>${t.booking}</dt><dd>${original.booking}</dd><dt>${t.transaction}</dt><dd>${t.notProvided}</dd></dl><p class="context-note">${t.originalNote}</p></section><section class="case-section">${historyView()}</section></div><p class="status-note">${t.fixture}</p>`;
}
function closureView() {
  const t = c(),
    old = currentStatus() !== 'closed',
    snapshot = closures[reason];
  return `<section class="case-section"><h2>${old ? t.lastResult : t.closedResult}</h2><p class="closure-date">${t.closedOn}</p><dl><dt>${t.reason}</dt><dd>${t[snapshot.reason]}</dd></dl><div class="closure-result"><p lang="en">${snapshot.resolution}</p></div><p class="context-note">${snapshot.reason === 'no_response' ? t.noResponseNote : t.closureNote}</p>${['closed', 'closed-no-response'].includes(phase) ? `<div class="actions">${button(t.unresolved, 'open-form')}</div><p class="context-note">${t.otherNote}</p><a class="subtle-link" href="conversation.html?topic=contact&lang=${lang}" data-leave>${t.other}</a>` : ''}</section>`;
}
function followupView() {
  const t = c();
  if (phase === 'followup' || phase === 'conflict') {
    return `<section class="case-section" id="followup-section"><h2>${t.followTitle}</h2><p>${t.followBody}</p><form id="followup-form" novalidate>${errors.length ? `<div id="error-summary" class="case-error-summary" tabindex="-1" role="alert"><h3>${t.errorTitle}</h3>${errors.map((key) => `<a href="#${key === 'message' ? 'explanation' : 'same-issue'}">${key === 'message' ? t.requiredMessage : t.requiredSame}</a>`).join('')}</div>` : ''}<label for="explanation">${t.message}</label><textarea id="explanation" maxlength="4000" aria-describedby="explanation-hint explanation-error" ${phase === 'conflict' ? 'disabled' : ''} ${errors.includes('message') ? 'aria-invalid="true"' : ''}>${esc(explanation)}</textarea><p class="hint" id="explanation-hint">${t.hint}</p><p class="field-error" id="explanation-error">${errors.includes('message') ? t.requiredMessage : ''}</p><label class="same-issue"><input type="checkbox" id="same-issue" ${sameIssue ? 'checked' : ''} ${phase === 'conflict' ? 'disabled' : ''} ${errors.includes('same') ? 'aria-invalid="true"' : ''} aria-describedby="same-error"><span>${t.same}</span></label><p class="field-error" id="same-error">${errors.includes('same') ? t.requiredSame : ''}</p><div class="actions"><button class="primary" type="submit" ${phase === 'conflict' ? 'disabled' : ''}>${t.send}</button>${button(t.cancel, 'cancel-form', 'secondary', phase === 'conflict')}</div></form></section>`;
  }
  if (
    command &&
    [
      'sending',
      'unknown',
      'retry',
      'continued',
      'continued-new',
      'observed',
    ].includes(phase)
  )
    return `<section class="case-section"><h2>${t.explanationRecord}</h2><p class="recorded-message" >${esc(command.message)}</p><p class="context-note">${['continued', 'continued-new'].includes(phase) ? t.followRecordedNote : t.readonly}</p></section>`;
  return '';
}
function historyView() {
  const t = c();
  const events = [];
  if (applied) events.push(['15:20', t.reopenedEvent, command?.message]);
  else if (phase === 'observed') events.push(['15:20', t.readEvent, '']);
  if (hasClosure())
    events.push(['15:05', t.closedEvent, closures[reason].resolution]);
  if (phase !== 'new') events.push(['14:40', t.assigned, '']);
  events.push(['14:32', t.created, '']);
  return `<details class="case-history"><summary>${t.history}</summary><ol>${events.map(([at, title, body]) => `<li><time>4 Nov 2026 · ${at} UTC</time><strong>${title}</strong>${body ? `<p lang="en">${esc(body)}</p>` : ''}</li>`).join('')}</ol></details>`;
}
function bindForm() {
  if ($('explanation')) {
    $('explanation').oninput = (e) => (explanation = e.target.value);
    $('same-issue').onchange = (e) => (sameIssue = e.target.checked);
    $('followup-form').onsubmit = (e) => {
      e.preventDefault();
      errors = [];
      if (!explanation.trim()) errors.push('message');
      if (!sameIssue) errors.push('same');
      if (errors.length) {
        render();
        $('error-summary')?.focus();
        return;
      }
      send();
    };
    document.querySelectorAll('.case-error-summary a').forEach(
      (a) =>
        (a.onclick = (e) => {
          e.preventDefault();
          document.querySelector(a.getAttribute('href')).focus();
        }),
    );
  }
  document.querySelectorAll('[data-leave]').forEach(
    (a) =>
      (a.onclick = (e) => {
        e.preventDefault();
        navigate(() => (location.href = a.href));
      }),
  );
}
function send() {
  if (applied || !['followup', 'retry'].includes(phase)) return;
  if (!command)
    command = Object.freeze({
      client_request_id: crypto.randomUUID(),
      expected_version: version,
      intent: 'same_issue',
      message: explanation,
    });
  if (sendOutcome === 'conflict') {
    phase = 'conflict';
    render();
    $('main').focus();
    return;
  }
  if (['timeout', 'unavailable'].includes(sendOutcome)) {
    phase = 'unknown';
    render();
    $('main').focus();
    return;
  }
  applied = true;
  version++;
  phase = sendOutcome === 'assigned' ? 'continued' : 'continued-new';
  errors = [];
  render();
  $('main').focus();
}
function readLatest() {
  if (caseId !== DEMO_CASE) {
    explanation = '';
    sameIssue = false;
    command = null;
    phase = 'restricted';
    render();
    $('main').focus();
    return;
  }
  if (readOutcome === 'restricted') {
    explanation = '';
    sameIssue = false;
    command = null;
    applied = false;
    phase = 'restricted';
  } else if (readOutcome === 'unavailable') phase = 'unavailable';
  else if (command) {
    if (readOutcome === 'closed') phase = 'retry';
    else {
      observedStatus = readOutcome === 'new' ? 'new' : 'in_progress';
      phase = 'observed';
    }
  } else if (readOutcome === 'closed') phase = 'closed';
  else phase = readOutcome === 'new' ? 'new' : 'active';
  render();
  $('main').focus();
}
function act(action) {
  if (action === 'open-form') {
    phase = 'followup';
    errors = [];
    render();
    $('explanation').focus();
    return;
  }
  if (action === 'cancel-form') {
    navigate(() => {
      explanation = '';
      sameIssue = false;
      command = null;
      errors = [];
      phase = reason === 'no_response' ? 'closed-no-response' : 'closed';
      render();
      $('main').focus();
    });
    return;
  }
  if (action === 'send') {
    send();
    return;
  }
  if (action === 'read') {
    readLatest();
    return;
  }
  if (action === 'reload-conflict') {
    version++;
    command = null;
    phase = 'followup';
    sameIssue = false;
    errors = [];
    feedback = 'loaded';
    render();
    $('explanation').focus();
  }
}
function navigate(callback) {
  if (!dirty()) {
    callback();
    return;
  }
  pendingNavigation = callback;
  $('leave-title').textContent = c().leaveTitle;
  $('leave-copy').textContent = c().leaveNote;
  $('stay').textContent = c().stay;
  $('leave').textContent = c().discard;
  $('leave-dialog').showModal();
}
function stay() {
  pendingNavigation = null;
  $('leave-dialog').close();
  if ($('explanation')) $('explanation').focus();
  else document.querySelector('[data-action="read"]')?.focus();
}
$('stay').onclick = stay;
$('leave-dialog').addEventListener('cancel', (e) => {
  e.preventDefault();
  stay();
});
$('leave').onclick = () => {
  const next = pendingNavigation;
  pendingNavigation = null;
  explanation = '';
  sameIssue = false;
  command = null;
  $('leave-dialog').close();
  next?.();
};
$('scene').onchange = (e) => {
  const next = e.target.value;
  e.target.value = phase;
  navigate(() => {
    configure(next);
    caseId = DEMO_CASE;
    syncURL();
    render();
    $('main').focus();
  });
};
$('outcome').onchange = (e) => (sendOutcome = e.target.value);
$('lookup').onchange = (e) => (readOutcome = e.target.value);
$('language').onclick = () => {
  lang = lang === 'en' ? 'id' : 'en';
  syncURL();
  render();
};
['back-link', 'brand', 'help-link'].forEach(
  (id) =>
    ($(id).onclick = (e) => {
      e.preventDefault();
      navigate(() => (location.href = $(id).href));
    }),
);
window.addEventListener('beforeunload', (e) => {
  if (dirty()) {
    e.preventDefault();
    e.returnValue = '';
  }
});
configure(phase);
syncURL();
render();

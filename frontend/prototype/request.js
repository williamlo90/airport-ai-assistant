'use strict';
// Isolated visual fixtures. No fetch, storage, real verification or submission.
const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
let lang = params.get('lang') === 'id' ? 'id' : 'en';
let scene = params.get('step') === 'review' ? 'review' : 'email';
let email = 'alex.morgan@example.com',
  code = '',
  edited = false,
  editing = '',
  leaving = null;
const report = {
  category: 'booking_issue',
  service: 'parking',
  summary: 'Flight delayed; parking booking ends tonight',
  story:
    'My flight has been delayed until tomorrow. My parking booking ends at 11 pm tonight. Could you explain my options and help me contact the parking team?',
  booking: 'WB-PK-20481',
  transaction: '',
  contact: 'alex.morgan@example.com',
  language: 'en',
};
const copy = {
  en: {
    back: 'Back to conversation',
    preview: 'Design preview · Sample data only · Nothing is sent',
    state: 'Preview state',
    journey: 'Your support request',
    note: 'Your story stays with your request. You will not need to explain it again.',
    steps: ['Your situation', 'Verify email', 'Review request'],
    verify: 'Verify your email',
    verifyIntro:
      'Verify your email to access your own booking information and arrange a reply from the support team.',
    emailTitle: 'What is your email address?',
    emailIntro:
      'Use an address you can access. In the connected service, a verification code will be sent to this address.',
    email: 'Email address',
    emailHint:
      'We use this to verify access and send replies about your request.',
    send: 'Request a code',
    codeTitle: 'Enter your verification code',
    codeIntro:
      'Use the 8-digit code in the same browser where you requested it.',
    demo: 'Sample verification for',
    code: '8-digit code',
    codeHint:
      'Design preview: enter any 8 digits to explore the next screen. No code has been sent.',
    verifyButton: 'Verify and continue',
    change: 'Change email',
    resend: 'Request a new code',
    invalid: 'We could not verify that code. Check it or request a new one.',
    rateTitle: 'Please wait before requesting another code',
    rate: 'This example represents a Retry-After of 60 seconds. Code requests are paused in this preview; no code expiry is inferred.',
    unavailableTitle: 'Email verification is unavailable',
    tryAgain: 'Try again',
    unavailable:
      'No code could be requested. Your story is still here. Try again when the service is available.',
    verifying: 'Checking your code…',
    privacy:
      'Verification confirms access to your email. It does not create a booking, charge you, or approve a refund.',
    review: 'Review your support request',
    reviewIntro:
      'Check the details below. The support team will use this information to understand your situation.',
    reportLabel: 'Your report',
    edit: 'Edit',
    request: 'Request details',
    category: 'Category',
    service: 'Service',
    summary: 'Summary',
    booking: 'Booking support',
    parking: 'Parking',
    lounge: 'Lounge',
    general: 'General airport services',
    information: 'Information',
    transaction: 'Transaction issue',
    story: 'What happened',
    refs: 'References you provided',
    bookingRef: 'Booking reference',
    transactionRef: 'Transaction reference',
    notProvided: 'Not provided',
    refNote:
      'These references are part of your report. They have not been checked against a booking or payment system.',
    contact: 'Reply details',
    contactNote:
      'Replies go directly to this address. You will not need to reopen the portal to reply once email support is connected.',
    contactVerify:
      'Use an email already verified for your account. A new address must be verified first.',
    previewLanguage: 'Request language',
    english: 'English',
    indonesian: 'Indonesian',
    original: 'Your original report is kept as written.',
    destination: 'Where your request will go',
    team: 'Parking support team',
    loungeTeam: 'Lounge support team',
    org: 'Westbridge Airport Services',
    routing:
      'Selected by the service. You cannot change this destination directly.',
    deadline:
      'Example review deadline: 4 Nov 2026, 14:15 UTC. A real deadline comes from the latest server preview.',
    sendRequest: 'Send support request',
    cancel: 'Cancel request',
    disclaimer:
      'Only this report will be submitted in the connected service. No parking booking or payment is changed.',
    expiredTitle: 'This review has expired',
    expired:
      'Get a new preview and review it before sending. An expired review cannot be extended by clicking Send.',
    refresh: 'Get a new sample preview',
    changedTitle: 'The destination has changed',
    changed:
      'This example is now routed to the Airport customer support team. Review the new destination before sending.',
    accept: 'Review updated destination',
    newTeam: 'Airport customer support team',
    unknownTitle: 'We could not confirm the result',
    unknown:
      'Your request may have reached the service. Check its status before trying again. Do not create another request.',
    check: 'Check request status',
    unknownChecked:
      'Status is unavailable in this visual preview. No request has been resent, and no successful submission is claimed.',
    loading: 'Sending request…',
    loadingNote: 'Example in-progress state. Sending again is disabled.',
    validationTitle: 'Check your reply address',
    validation:
      'Enter an email already verified for your account. Your report has been kept.',
    save: 'Save changes',
    cancelEdit: 'Cancel',
    close: 'Close editor',
    editTitle: 'Edit',
    validationForm: 'Check this field before saving.',
    saved:
      'Sample preview updated. Review the details again; no server request was made.',
    cancelTitle: 'Leave this review?',
    cancelBody:
      'Nothing has been submitted by this prototype. Cancel closes this sample review.',
    keep: 'Keep reviewing',
    cancelled: 'Review cancelled',
    cancelledNote: 'Nothing was sent or cancelled in an external service.',
    returnReview: 'Return to sample review',
    emailError: 'Enter a valid email address.',
    codeError: 'Enter all 8 digits of the code.',
    previewOnly:
      'Visual preview only. No email, account change, or support case is created.',
  },
  id: {
    back: 'Kembali ke percakapan',
    preview: 'Pratinjau desain · Data contoh · Tidak ada yang dikirim',
    state: 'Kondisi contoh',
    journey: 'Pengajuan bantuan Anda',
    note: 'Cerita Anda tetap disertakan. Anda tidak perlu menjelaskan dari awal.',
    steps: ['Situasi Anda', 'Verifikasi email', 'Tinjau pengajuan'],
    verify: 'Verifikasi email Anda',
    verifyIntro:
      'Verifikasi email untuk mengakses informasi booking milik Anda dan menerima balasan petugas.',
    emailTitle: 'Apa alamat email Anda?',
    emailIntro:
      'Gunakan alamat yang dapat Anda akses. Pada layanan terhubung, kode verifikasi akan dikirim ke alamat ini.',
    email: 'Alamat email',
    emailHint:
      'Email digunakan untuk memverifikasi akses dan menerima balasan terkait pengajuan Anda.',
    send: 'Minta kode',
    codeTitle: 'Masukkan kode verifikasi',
    codeIntro:
      'Gunakan kode 8 digit pada browser yang sama dengan saat Anda meminta kode.',
    demo: 'Contoh verifikasi untuk',
    code: 'Kode 8 digit',
    codeHint:
      'Pratinjau desain: masukkan 8 digit apa saja untuk melihat layar berikutnya. Tidak ada kode yang dikirim.',
    verifyButton: 'Verifikasi dan lanjutkan',
    change: 'Ganti email',
    resend: 'Minta kode baru',
    invalid:
      'Kode belum dapat diverifikasi. Periksa kode atau minta kode baru.',
    rateTitle: 'Tunggu sebelum meminta kode lagi',
    rate: 'Contoh ini memakai Retry-After 60 detik. Permintaan kode dijeda dalam pratinjau; masa berlaku kode tidak ditebak.',
    unavailableTitle: 'Verifikasi email belum tersedia',
    tryAgain: 'Coba lagi',
    unavailable:
      'Kode belum dapat diminta. Cerita Anda tetap tersimpan di halaman ini. Coba lagi saat layanan tersedia.',
    verifying: 'Memeriksa kode…',
    privacy:
      'Verifikasi memastikan akses email Anda. Proses ini tidak membuat booking, menagih pembayaran, atau menyetujui refund.',
    review: 'Tinjau pengajuan bantuan',
    reviewIntro:
      'Periksa informasi di bawah. Petugas akan menggunakannya untuk memahami situasi Anda.',
    reportLabel: 'Laporan Anda',
    edit: 'Ubah',
    request: 'Informasi pengajuan',
    category: 'Kategori',
    service: 'Layanan',
    summary: 'Ringkasan',
    booking: 'Kendala booking',
    parking: 'Parkir',
    lounge: 'Lounge',
    general: 'Layanan umum bandara',
    information: 'Informasi',
    transaction: 'Kendala transaksi',
    story: 'Apa yang terjadi',
    refs: 'Referensi yang Anda berikan',
    bookingRef: 'Referensi booking',
    transactionRef: 'Referensi transaksi',
    notProvided: 'Belum diisi',
    refNote:
      'Referensi ini bagian dari laporan Anda. Belum diperiksa melalui sistem booking atau pembayaran.',
    contact: 'Informasi balasan',
    contactNote:
      'Balasan dikirim langsung ke alamat ini. Anda tidak perlu membuka portal untuk membalas setelah layanan email terhubung.',
    contactVerify:
      'Gunakan email yang sudah terverifikasi pada akun Anda. Alamat baru perlu diverifikasi terlebih dahulu.',
    previewLanguage: 'Bahasa pengajuan',
    english: 'Inggris',
    indonesian: 'Indonesia',
    original: 'Laporan asli tetap seperti yang Anda tulis.',
    destination: 'Tujuan pengajuan Anda',
    team: 'Petugas bantuan parkir',
    loungeTeam: 'Petugas bantuan lounge',
    org: 'Westbridge Airport Services',
    routing:
      'Ditentukan oleh layanan. Anda tidak dapat mengubah tujuan ini secara langsung.',
    deadline:
      'Contoh batas tinjauan: 4 Nov 2026, 14.15 UTC. Batas sebenarnya berasal dari pratinjau server terbaru.',
    sendRequest: 'Kirim permintaan bantuan',
    cancel: 'Batalkan pengajuan',
    disclaimer:
      'Pada layanan terhubung, hanya laporan ini yang dikirim. Booking parkir dan pembayaran tidak berubah.',
    expiredTitle: 'Tinjauan ini kedaluwarsa',
    expired:
      'Ambil pratinjau baru dan tinjau ulang sebelum mengirim. Klik Kirim tidak memperpanjang tinjauan kedaluwarsa.',
    refresh: 'Ambil pratinjau contoh baru',
    changedTitle: 'Tujuan layanan berubah',
    changed:
      'Contoh ini sekarang ditujukan ke petugas layanan pelanggan bandara. Periksa tujuan terbaru sebelum mengirim.',
    accept: 'Tinjau tujuan terbaru',
    newTeam: 'Petugas layanan pelanggan bandara',
    unknownTitle: 'Hasil belum dapat dipastikan',
    unknown:
      'Permintaan Anda mungkin telah diterima layanan. Periksa status sebelum mencoba kembali. Jangan membuat pengajuan lain.',
    check: 'Periksa status pengajuan',
    unknownChecked:
      'Status belum tersedia dalam pratinjau visual. Tidak ada pengiriman ulang atau klaim pengajuan berhasil.',
    loading: 'Mengirim pengajuan…',
    loadingNote: 'Contoh proses pengiriman. Pengiriman ulang dinonaktifkan.',
    validationTitle: 'Periksa alamat balasan',
    validation:
      'Isi email yang sudah terverifikasi pada akun Anda. Laporan Anda tetap dipertahankan.',
    save: 'Simpan perubahan',
    cancelEdit: 'Batal',
    close: 'Tutup editor',
    editTitle: 'Ubah',
    validationForm: 'Periksa isian ini sebelum menyimpan.',
    saved:
      'Pratinjau contoh diperbarui. Tinjau kembali informasinya; tidak ada permintaan ke server.',
    cancelTitle: 'Tinggalkan tinjauan ini?',
    cancelBody:
      'Prototipe ini belum mengirim apa pun. Pembatalan menutup tinjauan contoh ini.',
    keep: 'Tetap meninjau',
    cancelled: 'Tinjauan dibatalkan',
    cancelledNote:
      'Tidak ada pengiriman atau pembatalan pada layanan eksternal.',
    returnReview: 'Kembali ke tinjauan contoh',
    emailError: 'Isi alamat email yang valid.',
    codeError: 'Masukkan kode lengkap 8 digit.',
    previewOnly:
      'Hanya pratinjau visual. Tidak membuat email, perubahan akun, atau kasus bantuan.',
  },
};
const sceneNames = {
  en: [
    'C-03 · Email address',
    'C-03 · Enter code',
    'C-03 · Invalid / expired code',
    'C-03 · Rate limited',
    'C-03 · Sender unavailable',
    'C-03 · Verifying',
    'C-04 · Review request',
    'C-04 · Validation error',
    'C-04 · Sending',
    'C-04 · Review expired',
    'C-04 · Destination changed',
    'C-04 · Result unknown',
  ],
  id: [
    'C-03 · Alamat email',
    'C-03 · Masukkan kode',
    'C-03 · Kode salah / kedaluwarsa',
    'C-03 · Batas permintaan',
    'C-03 · Pengiriman belum tersedia',
    'C-03 · Memverifikasi',
    'C-04 · Tinjau pengajuan',
    'C-04 · Validasi gagal',
    'C-04 · Mengirim',
    'C-04 · Tinjauan kedaluwarsa',
    'C-04 · Tujuan berubah',
    'C-04 · Hasil belum pasti',
  ],
};
const scenes = [
  'email',
  'code',
  'invalid',
  'rate',
  'unavailable',
  'verifying',
  'review',
  'validation',
  'sending',
  'expired',
  'changed',
  'unknown',
];
let changedDestination = false;
const esc = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[
        c
      ],
  );
const t = () => copy[lang];
const isReview = () =>
  [
    'review',
    'validation',
    'sending',
    'expired',
    'changed',
    'unknown',
    'cancelled',
  ].includes(scene);
function notice(title, body, kind = 'warning') {
  return `<div class="notice ${kind}" role="alert"><h2>${title}</h2><p>${body}</p></div>`;
}
function move(next) {
  capture();
  if (['email', 'code', 'review'].includes(next)) code = '';
  scene = next;
  render();
  $('main').focus();
}
function capture() {
  if ($('email-input')) email = $('email-input').value;
  if ($('code-input')) code = $('code-input').value;
}
function button(label, action, style = 'primary', disabled = false) {
  return `<button type="button" class="${style}" data-action="${action}" ${disabled ? 'disabled' : ''}>${label}</button>`;
}
function render() {
  const c = t();
  document.documentElement.lang = lang;
  $('back-text').textContent = c.back;
  $('back-link').setAttribute('aria-label', c.back);
  $('language').textContent = lang === 'en' ? 'ID' : 'EN';
  $('language').setAttribute(
    'aria-label',
    lang === 'en' ? 'Switch to Indonesian' : 'Ganti ke Inggris',
  );
  $('preview-label').textContent = c.preview;
  $('scene-label').textContent = c.state;
  $('journey-title').textContent = c.journey;
  $('journey-note').textContent = c.note;
  $('steps').innerHTML = c.steps
    .map(
      (s, i) =>
        `<li ${i === (isReview() ? 2 : 1) ? 'aria-current="step"' : ''}><span class="step-number">${i + 1}</span>${s}</li>`,
    )
    .join('');
  $('scene').innerHTML = scenes
    .map(
      (s, i) =>
        `<option value="${s}" ${s === scene ? 'selected' : ''}>${sceneNames[lang][i]}</option>`,
    )
    .join('');
  $('screen').innerHTML = isReview() ? review(c) : verify(c);
  document.title =
    (isReview() ? c.review : c.emailTitle) + ' — Westbridge Airport';
  bind();
}
function verify(c) {
  const emailStage = ['email', 'unavailable'].includes(scene);
  let message = '';
  if (scene === 'invalid') message = notice(c.invalid, c.codeHint, 'error');
  if (scene === 'rate') message = notice(c.rateTitle, c.rate);
  if (scene === 'unavailable')
    message =
      notice(c.unavailableTitle, c.unavailable, 'error') +
      button(c.tryAgain, 'email', 'secondary');
  return `<div class="page-heading"><h1>${c.verify}</h1><p>${c.verifyIntro}</p></div>${message}<section class="paper verify-paper"><h2>${emailStage ? c.emailTitle : c.codeTitle}</h2><p class="intro">${emailStage ? c.emailIntro : c.codeIntro}</p><form id="verify-form" novalidate>${emailStage ? `<label for="email-input">${c.email}</label><input id="email-input" name="email" type="email" autocomplete="email" value="${esc(email)}" maxlength="320" aria-describedby="input-hint input-error"><p class="hint" id="input-hint">${c.emailHint}</p>` : `<p class="hint">${c.demo}<br><span class="email-line">${esc(email)}</span></p><label for="code-input">${c.code}</label><input id="code-input" name="code" class="code-input" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{8}" maxlength="8" value="${esc(code)}" aria-describedby="input-hint input-error" ${scene === 'verifying' ? 'disabled' : ''}><p class="hint" id="input-hint">${c.codeHint}</p>`}<p class="field-error" id="input-error" role="alert"></p><div class="actions"><button class="primary" type="submit" ${['unavailable', 'verifying', 'rate'].includes(scene) ? 'disabled' : ''}>${scene === 'verifying' ? c.verifying : emailStage ? c.send : c.verifyButton}</button>${emailStage ? '' : button(c.change, 'email', 'quiet')}</div>${!emailStage ? button(c.resend, 'resend', 'quiet', scene === 'rate' || scene === 'verifying') : ''}</form><p class="privacy-note">${c.privacy}</p></section><p class="privacy-note">${c.previewOnly}</p>`;
}
function section(title, body, group) {
  const c = t();
  const locked = ['unknown', 'sending', 'expired'].includes(scene);
  return `<section class="review-section"><div class="section-heading"><h2>${title}</h2>${group ? `<button class="edit-link" data-edit="${group}" aria-label="${c.edit} — ${title}" ${locked ? 'disabled' : ''}>${c.edit}</button>` : ''}</div>${body}</section>`;
}
function review(c) {
  if (scene === 'cancelled')
    return `<div class="page-heading"><h1>${c.cancelled}</h1><p>${c.cancelledNote}</p></div>${button(c.returnReview, 'review')}`;
  const locked = [
    'unknown',
    'sending',
    'expired',
    'changed',
    'validation',
  ].includes(scene);
  let n = '';
  if (scene === 'expired')
    n =
      notice(c.expiredTitle, c.expired) +
      button(c.refresh, 'refresh', 'secondary');
  if (scene === 'changed')
    n =
      notice(c.changedTitle, c.changed) +
      button(c.accept, 'destination', 'secondary');
  if (scene === 'validation')
    n = notice(
      c.validationTitle,
      `${c.validation} <a href="#contact-section" data-contact-error>${c.edit} — ${c.contact}</a>`,
      'error',
    );
  if (scene === 'unknown')
    n =
      notice(c.unknownTitle, c.unknown) +
      button(c.check, 'status', 'secondary');
  if (scene === 'sending') n = notice(c.loading, c.loadingNote, '');
  return `<div class="page-heading"><h1>${c.review}</h1><p>${c.reviewIntro}</p></div>${n ? `<div style="margin-bottom:20px">${n}<p id="recovery-status" role="status"></p></div>` : ''}<div class="paper">${section(c.request, `<dl><dt>${c.category}</dt><dd>${c[report.category === 'booking_issue' ? 'booking' : report.category === 'transaction_issue' ? 'transaction' : 'information']}</dd><dt>${c.service}</dt><dd>${c[report.service]}</dd><dt>${c.summary}</dt><dd>${esc(report.summary)}</dd></dl>`, 'request')}${section(c.reportLabel, `<p class="report-text">${esc(report.story)}</p><p class="section-note">${c.original}</p>`, 'story')}${section(c.refs, `<dl><dt>${c.bookingRef}</dt><dd>${esc(report.booking) || c.notProvided}</dd><dt>${c.transactionRef}</dt><dd>${esc(report.transaction) || c.notProvided}</dd></dl><p class="section-note">${c.refNote}</p>`, 'refs')}<div id="contact-section">${section(c.contact, `<dl><dt>${c.email}</dt><dd>${scene === 'validation' ? 'alex.morgan@' : esc(report.contact)}</dd><dt>${c.previewLanguage}</dt><dd>${report.language === 'en' ? c.english : c.indonesian}</dd></dl><p class="section-note">${c.contactNote}</p>`, 'contact')}</div>${section(c.destination, `<p class="destination-name">${changedDestination || scene === 'changed' ? c.newTeam : report.service === 'parking' ? c.team : report.service === 'lounge' ? c.loungeTeam : c.newTeam}</p><p>${c.org}</p><p class="section-note">${c.routing}</p>`)}</div><p class="deadline">${c.deadline}</p><div class="review-actions">${button(c.cancel, 'cancel', 'secondary', scene === 'sending' || scene === 'unknown')}${button(scene === 'sending' ? c.loading : c.sendRequest, 'send', 'primary', locked)}</div><p class="privacy-note">${c.disclaimer}</p><p id="sample-status" class="screen-status" role="status"></p>`;
}
function validEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}
function bind() {
  document.querySelectorAll('[data-action]').forEach(
    (b) =>
      (b.onclick = () => {
        const a = b.dataset.action;
        if (a === 'send') {
          move('unknown');
          return;
        }
        if (a === 'cancel') {
          openLeave(null);
          return;
        }
        if (a === 'status') {
          $('recovery-status').textContent = t().unknownChecked;
          return;
        }
        if (a === 'refresh' || a === 'destination') {
          if (a === 'destination') changedDestination = true;
          move('review');
          $('sample-status').textContent = t().saved;
          return;
        }
        if (a === 'resend') {
          code = '';
          move('code');
          return;
        }
        move(a);
      }),
  );
  document
    .querySelectorAll('[data-edit]')
    .forEach((b) => (b.onclick = () => openEditor(b.dataset.edit)));
  const err = document.querySelector('[data-contact-error]');
  if (err)
    err.onclick = (e) => {
      e.preventDefault();
      openEditor('contact');
    };
  if ($('verify-form'))
    $('verify-form').onsubmit = (e) => {
      e.preventDefault();
      capture();
      const em = $('email-input');
      const field = em || $('code-input');
      if (em ? !validEmail(email) : !/^\d{8}$/.test(code)) {
        $('input-error').textContent = em ? t().emailError : t().codeError;
        field.setAttribute('aria-invalid', 'true');
        field.focus();
        return;
      }
      field.removeAttribute('aria-invalid');
      if (em) {
        report.contact = email;
        code = '';
        move('code');
      } else {
        code = '';
        move('review');
      }
    };
}
function input(key, label, value, max = 200) {
  return `<label for="edit-${key}">${label}</label><input id="edit-${key}" name="${key}" value="${esc(value)}" maxlength="${max}" aria-describedby="edit-error">`;
}
function openEditor(group) {
  editing = group;
  const c = t();
  $('editor-title').textContent =
    c.editTitle +
    ' — ' +
    c[
      group === 'story'
        ? 'reportLabel'
        : group === 'refs'
          ? 'refs'
          : group === 'contact'
            ? 'contact'
            : 'request'
    ];
  $('close-editor').setAttribute('aria-label', c.close);
  $('cancel-edit').textContent = c.cancelEdit;
  $('save-edit').textContent = c.save;
  $('edit-error').textContent = '';
  let fields = '';
  if (group === 'request')
    fields = `<label for="edit-category">${c.category}</label><select class="field" id="edit-category" name="category">${['information', 'booking_issue', 'transaction_issue'].map((v, i) => `<option value="${v}" ${v === report.category ? 'selected' : ''}>${[c.information, c.booking, c.transaction][i]}</option>`).join('')}</select><label for="edit-service">${c.service}</label><select class="field" id="edit-service" name="service">${['general', 'parking', 'lounge'].map((v) => `<option value="${v}" ${v === report.service ? 'selected' : ''}>${c[v]}</option>`).join('')}</select>${input('summary', c.summary, report.summary)}`;
  if (group === 'story')
    fields = `<label for="edit-story">${c.story}</label><textarea id="edit-story" name="story" maxlength="4000" aria-describedby="edit-error">${esc(report.story)}</textarea>`;
  if (group === 'refs')
    fields =
      input('booking', c.bookingRef, report.booking, 80) +
      input('transaction', c.transactionRef, report.transaction, 80);
  if (group === 'contact')
    fields =
      input(
        'contact',
        c.email,
        scene === 'validation' ? 'alex.morgan@' : report.contact,
        320,
      ) +
      `<p class="hint">${c.contactVerify}</p><label for="edit-language">${c.previewLanguage}</label><select class="field" id="edit-language" name="language"><option value="en" ${report.language === 'en' ? 'selected' : ''}>${c.english}</option><option value="id" ${report.language === 'id' ? 'selected' : ''}>${c.indonesian}</option></select>`;
  $('edit-fields').innerHTML = fields;
  $('editor').showModal();
}
$('edit-form').onsubmit = (e) => {
  e.preventDefault();
  const values = Object.fromEntries(new FormData(e.target));
  const invalid = Object.keys(values).find((k) =>
    k === 'contact'
      ? !validEmail(values[k]) || values[k] !== email
      : k === 'booking' || k === 'transaction'
        ? values[k] && !/^[A-Za-z0-9_-]{1,80}$/.test(values[k])
        : !values[k].trim(),
  );
  if (invalid) {
    const f = $('edit-' + invalid);
    f.setAttribute('aria-invalid', 'true');
    $('edit-error').textContent =
      invalid === 'contact' ? t().contactVerify : t().validationForm;
    f.focus();
    return;
  }
  Object.assign(report, values);
  edited = true;
  $('editor').close();
  if (scene === 'validation') scene = 'review';
  render();
  document.querySelector(`[data-edit="${editing}"]`)?.focus();
  $('sample-status').textContent = t().saved;
};
$('close-editor').onclick = $('cancel-edit').onclick = () =>
  $('editor').close();
function openLeave(href) {
  leaving = href;
  const c = t();
  $('leave-title').textContent = c.cancelTitle;
  $('leave-body').textContent = c.cancelBody;
  $('keep-review').textContent = c.keep;
  $('leave-review').textContent = c.cancel;
  $('leave').showModal();
}
$('keep-review').onclick = () => $('leave').close();
$('leave-review').onclick = () => {
  $('leave').close();
  edited = false;
  if (leaving) location.href = leaving;
  else move('cancelled');
};
$('language').onclick = () => {
  capture();
  lang = lang === 'en' ? 'id' : 'en';
  const url = new URL(location.href);
  url.search = '?step=' + (isReview() ? 'review' : 'verify') + '&lang=' + lang;
  history.replaceState(null, '', url);
  render();
};
$('scene').onchange = (e) => {
  capture();
  scene = e.target.value;
  code = '';
  render();
};
document.querySelectorAll('.request-header a').forEach(
  (a) =>
    (a.onclick = (e) => {
      if (edited) {
        e.preventDefault();
        openLeave(a.getAttribute('href'));
      }
    }),
);
window.addEventListener('beforeunload', (e) => {
  if (edited) {
    e.preventDefault();
    e.returnValue = '';
  }
});
render();

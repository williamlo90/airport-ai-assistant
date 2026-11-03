'use strict';
// FE-VIS-04: local design fixtures only. No API, authentication, storage or email.
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[
        c
      ],
  );
let lang =
  new URLSearchParams(location.search).get('lang') === 'id' ? 'id' : 'en';
let profile = 'cs',
  view = 'unassigned',
  statusFilter = 'all',
  scene = 'normal',
  selected = '1048',
  mobileDetail = false,
  queueScroll = 0;
let queuePageScroll = 0;
let draft = null,
  pendingNavigation = null,
  action = '',
  actionReview = null,
  feedback = '';
const profiles = {
  cs: { name: 'Jordan Miller', initials: 'JM', role: 'cs', org: 'airport' },
  supervisor: {
    name: 'Samira Patel',
    initials: 'SP',
    role: 'supervisor',
    org: 'airport',
  },
  partner: { name: 'Avery Chen', initials: 'AC', role: 'cs', org: 'lounge' },
  admin: { name: 'Morgan Lee', initials: 'ML', role: 'admin', org: 'airport' },
};
const cases = [
  {
    id: '1047',
    org: 'airport',
    service: 'general',
    owner: 'noah',
    customer: 'Noah Williams',
    language: 'en',
    title: 'Step-free route to the Terminal 2 meeting point',
    report:
      'I am meeting my father at Terminal 2. He uses a wheelchair. Could you help us find a step-free route to the meeting point?',
    reference: null,
    status: 'new',
    assignee: null,
    next: 'Check the accessible route and provide directions.',
    updated: '14:24',
    history: [['14:24', 'Customer support request recorded.']],
    resolution: null,
  },
  {
    id: '1048',
    org: 'airport',
    service: 'parking',
    owner: 'alex',
    customer: 'Alex Morgan',
    language: 'en',
    title: 'Flight delayed; parking booking ends tonight',
    report:
      'My flight has been delayed until tomorrow. My parking booking ends at 11 pm tonight. Could you explain my options and help me contact the parking team?',
    reference: 'WB-PK-20481',
    status: 'new',
    assignee: null,
    next: 'Check the parking policy and explain the available options.',
    updated: '14:32',
    history: [['14:32', 'Customer support request recorded.']],
    resolution: null,
  },
  {
    id: '1046',
    org: 'airport',
    service: 'parking',
    owner: 'emily',
    customer: 'Emily Rivera',
    language: 'en',
    title: 'Payment error when extending parking',
    report:
      'The payment page showed an error when I tried to extend parking. Could you check what I should do next? I do not know whether the payment went through.',
    reference: 'WB-PK-20390',
    status: 'in_progress',
    assignee: 'Jordan Miller',
    next: 'Request the transaction reference and check with parking support.',
    updated: '14:18',
    history: [
      ['14:18', 'Jordan Miller accepted the case.'],
      ['14:05', 'Customer support request recorded.'],
    ],
    resolution: null,
  },
  {
    id: '1044',
    org: 'airport',
    service: 'parking',
    owner: 'alex',
    customer: 'Alex Morgan',
    language: 'en',
    title: 'Help locating my parking confirmation',
    report:
      'I cannot find the confirmation for my parking reservation. Where should I look?',
    reference: 'WB-PK-20481',
    status: 'awaiting_customer',
    assignee: 'Taylor Brooks',
    next: 'Wait for the customer to confirm the email used for the booking.',
    updated: '13:50',
    history: [
      ['13:50', 'Waiting for customer information.'],
      ['13:35', 'Taylor Brooks accepted the case.'],
    ],
    resolution: null,
  },
  {
    id: '1042',
    org: 'lounge',
    service: 'lounge',
    owner: 'mei',
    customer: 'Mei Tan',
    language: 'en',
    title: 'Lounge confirmation not found at reception',
    report:
      'I booked a lounge visit for my mother. The reception desk cannot find the confirmation. Could you explain what information we should provide?',
    reference: 'WB-LG-8172',
    status: 'new',
    assignee: null,
    next: 'Review the reported reference and contact lounge reception.',
    updated: '13:42',
    history: [['13:42', 'Support request recorded for lounge partner.']],
    resolution: null,
  },
  {
    id: '1039',
    org: 'lounge',
    service: 'lounge',
    owner: 'raka',
    customer: 'Raka Pratama',
    language: 'id',
    title: 'Perubahan jadwal kunjungan lounge',
    report:
      'Penerbangan saya berubah jadwal. Saya ingin mengetahui prosedur mengubah waktu kunjungan lounge.',
    reference: 'WB-LG-8105',
    status: 'awaiting_external',
    assignee: 'Avery Chen',
    next: 'Wait for the lounge team to clarify the change policy.',
    updated: '13:25',
    history: [['13:25', 'Waiting for a response from the service team.']],
    resolution: null,
  },
  {
    id: '1036',
    org: 'airport',
    service: 'parking',
    owner: 'daniel',
    customer: 'Daniel Kim',
    language: 'en',
    title: 'Where to find the parking exit',
    report:
      'I need directions to the parking exit after collecting my vehicle.',
    reference: null,
    status: 'closed',
    assignee: 'Jordan Miller',
    next: 'No further action. Customer can request follow-up on the same issue.',
    updated: '12:40',
    history: [
      ['12:40', 'Case closed: resolved.'],
      ['12:28', 'Jordan Miller accepted the case.'],
    ],
    resolution:
      'Explained the exit route and provided the relevant parking guidance.',
    closeReason: 'resolved',
  },
];
const copy = {
  en: {
    workspace: 'Staff workspace',
    cases: 'Support cases',
    fixture: 'Design preview · Sample staff and cases · No requests are sent',
    sampleProfile: 'Sample profile',
    previewState: 'Preview state',
    customerService: 'Customer service',
    supervisor: 'Supervisor',
    admin: 'Administrator only',
    airport: 'Westbridge Airport Services',
    lounge: 'Westbridge Lounge Partner',
    scopeNote:
      'Only cases within this sample organisation are shown. Names, roles and permissions are fixtures, not a signed-in identity.',
    unassigned: 'Unassigned',
    mine: 'My cases',
    all: 'All in scope',
    status: 'Status',
    allStatus: 'All statuses',
    new: 'New',
    in_progress: 'In progress',
    awaiting_customer: 'Waiting for customer',
    awaiting_external: 'Waiting for service team',
    closed: 'Closed',
    queueFoot:
      'Sample list only. Counts and filters apply to these fixtures, not a live queue.',
    updated: 'Updated',
    utc: 'All times: 4 Nov 2026 · UTC',
    back: 'Back to cases',
    report: 'Customer report',
    refs: 'Reference provided',
    reported:
      'Customer-reported reference; not verified against a booking or payment system.',
    handling: 'Handling this case',
    assignee: 'Assigned to',
    nextActor: 'Next actor',
    nextAction: 'Next step',
    staff: 'Support team',
    customer: 'Customer',
    external: 'Service team',
    none: 'No action pending',
    claim: 'Take case',
    assign: 'Assign case',
    reassign: 'Reassign',
    close: 'Close case',
    update: 'Update case',
    updateStatus: 'Update status',
    actionHint:
      'Describe what happens next and who needs to act. This does not send an email.',
    actionLabel: 'Next action',
    submit: 'Save update',
    latest: 'Latest update',
    history: 'View case history',
    language: 'Report language',
    english: 'English',
    indonesian: 'Indonesian',
    original:
      'Original report. Interface language changes do not translate it.',
    unassignedHint: 'Take this case before updating its status.',
    assignHint: 'Assign a staff member before updating this case.',
    otherHint:
      'This case is assigned to another member of your team. It is read-only for this sample CS profile.',
    closedTitle: 'This case is closed',
    closedNote:
      'The recorded result remains below. Staff cannot edit or reopen a closed case.',
    outcome: 'Recorded resolution',
    reason: 'Closure reason',
    empty: 'No cases in this view',
    emptyText: 'Try another view or remove the status filter.',
    clear: 'Clear filter',
    loading: 'Loading cases…',
    loadingText: 'Sample loading state. Previous case details are hidden.',
    unavailable: 'Cases are unavailable',
    unavailableText:
      'The queue could not be loaded. Try again without changing your case update.',
    retry: 'Try again',
    noAccess: 'Cases are not available for this profile',
    noAccessText:
      'An administrator role alone does not grant access to customer cases.',
    revoked: 'Case access is unavailable',
    revokedText:
      'Previous case content and unsaved text have been cleared. Recheck access before continuing.',
    recheck: 'Recheck sample access',
    choose: 'Choose a case',
    chooseText:
      'Select a case from the queue to read the report and decide the next step.',
    dirtyTitle: 'Leave unsaved changes?',
    dirtyText:
      'Your unsaved case update will be discarded. Keep editing to retain it.',
    keep: 'Keep editing',
    discard: 'Discard changes and leave',
    cancel: 'Cancel',
    closeDialog: 'Close dialog',
    review: 'Review change',
    confirm: 'Confirm change',
    required: 'Describe the next action before saving.',
    saved: 'Sample case updated locally. No API request or email was sent.',
    claimed: 'Sample case accepted. No live assignment was made.',
    conflict: 'This case changed while you were editing',
    conflictText:
      'Load the latest case before continuing. Your update stays here while this sample still permits access.',
    reload: 'Load latest sample case',
    reviewed:
      'Latest sample loaded. Review the current status and your update before saving. Nothing was retried.',
    unknown: 'The change is not confirmed',
    unknownText:
      'Read the latest case and history before deciding what to do. Do not submit the change again.',
    check: 'Check latest case',
    checked:
      'Status is unavailable in this visual preview. No change was retried or marked successful.',
    selectAssignee: 'Choose an eligible staff member',
    assignmentNote:
      'Sample eligible list for this organisation. Live eligibility must come from the server.',
    from: 'From',
    to: 'To',
    confirmAssignment: 'Review this assignment',
    confirmClose: 'Review case closure',
    resolution: 'Resolution',
    resolved: 'Resolved',
    customer_cancelled: 'Customer cancelled',
    duplicate: 'Duplicate',
    no_response: 'No response',
    related: 'Related case for the same customer',
    evidence: 'Follow-up evidence',
    closeNote:
      'Closing a case does not confirm customer satisfaction or email delivery.',
    closeValidation:
      'Complete the resolution and any required related-case or follow-up evidence.',
    noEligibleDuplicate:
      'No eligible related case is available in this sample.',
    fixtureOnly: 'Sample change only. No external service will be updated.',
  },
  id: {
    workspace: 'Ruang kerja petugas',
    cases: 'Kasus bantuan',
    fixture:
      'Pratinjau desain · Petugas dan kasus contoh · Tidak ada pengiriman',
    sampleProfile: 'Profil contoh',
    previewState: 'Kondisi contoh',
    customerService: 'Layanan pelanggan',
    supervisor: 'Supervisor',
    admin: 'Administrator saja',
    airport: 'Westbridge Airport Services',
    lounge: 'Westbridge Lounge Partner',
    scopeNote:
      'Hanya kasus dalam organisasi contoh ini yang ditampilkan. Nama, peran, dan izin adalah fixture, bukan identitas login.',
    unassigned: 'Belum ditugaskan',
    mine: 'Kasus saya',
    all: 'Semua dalam cakupan',
    status: 'Status',
    allStatus: 'Semua status',
    new: 'Baru',
    in_progress: 'Ditangani',
    awaiting_customer: 'Menunggu pelanggan',
    awaiting_external: 'Menunggu tim layanan',
    closed: 'Ditutup',
    queueFoot:
      'Hanya daftar contoh. Jumlah dan filter berasal dari fixture ini, bukan antrean nyata.',
    updated: 'Diperbarui',
    utc: 'Semua waktu: 4 Nov 2026 · UTC',
    back: 'Kembali ke antrean',
    report: 'Laporan pelanggan',
    refs: 'Referensi yang diberikan',
    reported:
      'Referensi dari laporan pelanggan; belum diperiksa melalui sistem booking atau pembayaran.',
    handling: 'Penanganan kasus',
    assignee: 'Penanggung jawab',
    nextActor: 'Pihak berikutnya',
    nextAction: 'Langkah berikutnya',
    staff: 'Petugas bantuan',
    customer: 'Pelanggan',
    external: 'Tim layanan',
    none: 'Tidak ada tindakan tertunda',
    claim: 'Ambil kasus',
    assign: 'Tugaskan kasus',
    reassign: 'Ganti petugas',
    close: 'Tutup kasus',
    update: 'Perbarui kasus',
    updateStatus: 'Ubah status',
    actionHint:
      'Jelaskan langkah berikutnya dan pihak yang perlu bertindak. Ini tidak mengirim email.',
    actionLabel: 'Tindakan berikutnya',
    submit: 'Simpan pembaruan',
    latest: 'Pembaruan terbaru',
    history: 'Lihat riwayat kasus',
    language: 'Bahasa laporan',
    english: 'Inggris',
    indonesian: 'Indonesia',
    original:
      'Laporan asli. Pergantian bahasa antarmuka tidak menerjemahkannya.',
    unassignedHint: 'Ambil kasus ini sebelum mengubah statusnya.',
    assignHint: 'Tugaskan petugas sebelum memperbarui kasus ini.',
    otherHint:
      'Kasus ini ditugaskan ke rekan tim lain. Profil CS contoh ini hanya dapat membacanya.',
    closedTitle: 'Kasus ini sudah ditutup',
    closedNote:
      'Hasil tercatat tetap ditampilkan di bawah. Petugas tidak dapat mengubah atau membuka kembali kasus tertutup.',
    outcome: 'Hasil penanganan tercatat',
    reason: 'Alasan penutupan',
    empty: 'Tidak ada kasus pada tampilan ini',
    emptyText: 'Coba tampilan lain atau hapus filter status.',
    clear: 'Hapus filter',
    loading: 'Memuat kasus…',
    loadingText:
      'Contoh kondisi loading. Detail kasus sebelumnya disembunyikan.',
    unavailable: 'Kasus belum tersedia',
    unavailableText:
      'Antrean belum dapat dimuat. Coba lagi tanpa mengubah tulisan pembaruan kasus.',
    retry: 'Coba lagi',
    noAccess: 'Kasus tidak tersedia untuk profil ini',
    noAccessText:
      'Peran administrator saja tidak memberikan akses ke kasus pelanggan.',
    revoked: 'Akses kasus belum tersedia',
    revokedText:
      'Isi kasus sebelumnya dan tulisan yang belum disimpan sudah dihapus. Periksa akses sebelum melanjutkan.',
    recheck: 'Periksa akses contoh',
    choose: 'Pilih kasus',
    chooseText:
      'Pilih kasus dari antrean untuk membaca laporan dan menentukan langkah berikutnya.',
    dirtyTitle: 'Tinggalkan perubahan?',
    dirtyText:
      'Tulisan pembaruan kasus yang belum disimpan akan dibuang. Tetap mengedit untuk mempertahankannya.',
    keep: 'Tetap mengedit',
    discard: 'Buang perubahan dan pindah',
    cancel: 'Batal',
    closeDialog: 'Tutup dialog',
    review: 'Tinjau perubahan',
    confirm: 'Konfirmasi perubahan',
    required: 'Isi tindakan berikutnya sebelum menyimpan.',
    saved:
      'Kasus contoh diperbarui secara lokal. Tidak ada permintaan API atau email.',
    claimed: 'Kasus contoh diambil. Tidak ada penugasan nyata.',
    conflict: 'Kasus berubah saat Anda mengedit',
    conflictText:
      'Muat kasus terbaru sebelum melanjutkan. Tulisan Anda tetap ada selama akses contoh masih diizinkan.',
    reload: 'Muat kasus contoh terbaru',
    reviewed:
      'Kasus contoh terbaru dimuat. Tinjau status dan tulisan Anda sebelum menyimpan. Tidak ada retry otomatis.',
    unknown: 'Perubahan belum dapat dipastikan',
    unknownText:
      'Baca kasus dan riwayat terbaru sebelum menentukan tindakan. Jangan kirim perubahan itu lagi.',
    check: 'Periksa kasus terbaru',
    checked:
      'Status belum tersedia pada pratinjau visual. Tidak ada retry atau klaim perubahan berhasil.',
    selectAssignee: 'Pilih petugas yang berhak',
    assignmentNote:
      'Daftar petugas contoh untuk organisasi ini. Kelayakan nyata harus berasal dari server.',
    from: 'Dari',
    to: 'Ke',
    confirmAssignment: 'Tinjau penugasan ini',
    confirmClose: 'Tinjau penutupan kasus',
    resolution: 'Hasil penanganan',
    resolved: 'Terselesaikan',
    customer_cancelled: 'Dibatalkan pelanggan',
    duplicate: 'Duplikat',
    no_response: 'Tidak ada respons',
    related: 'Kasus terkait milik pelanggan yang sama',
    evidence: 'Bukti tindak lanjut',
    closeNote:
      'Kasus ditutup tidak berarti pelanggan puas atau email terkirim.',
    closeValidation:
      'Lengkapi hasil penanganan serta kasus terkait atau bukti tindak lanjut yang diwajibkan.',
    noEligibleDuplicate: 'Tidak ada kasus terkait yang berhak pada contoh ini.',
    fixtureOnly: 'Hanya perubahan contoh. Tidak memperbarui layanan eksternal.',
  },
};
const c = () => copy[lang];
const user = () => profiles[profile];
const allowed = () =>
  user().role === 'admin' ? [] : cases.filter((x) => x.org === user().org);
const current = () => allowed().find((x) => x.id === selected);
const dirty = () => !!draft?.touched;
const actor = (status) =>
  status === 'awaiting_customer'
    ? 'customer'
    : status === 'awaiting_external'
      ? 'external'
      : status === 'closed'
        ? 'none'
        : 'staff';
const badge = (status) =>
  `<span class="status ${status}">${c()[status]}</span>`;
const btn = (text, act, kind = 'secondary', disabled = false) =>
  `<button type="button" class="${kind}" data-action="${act}" ${disabled ? 'disabled' : ''}>${text}</button>`;
function deferNavigation(fn) {
  if (dirty()) {
    pendingNavigation = fn;
    $('dirty-title').textContent = c().dirtyTitle;
    $('dirty-copy').textContent = c().dirtyText;
    $('keep-editing').textContent = c().keep;
    $('discard').textContent = c().discard;
    $('dirty-dialog').showModal();
    return;
  }
  fn();
}
function choose(id) {
  queuePageScroll = window.scrollY;
  if (id === selected) {
    mobileDetail = true;
    render();
    window.scrollTo({ top: 0, behavior: 'instant' });
    $('detail').focus({ preventScroll: true });
    return;
  }
  deferNavigation(() => {
    queueScroll = $('queue-scroll').scrollTop;
    selected = id;
    draft = null;
    feedback = '';
    mobileDetail = true;
    render();
    window.scrollTo({ top: 0, behavior: 'instant' });
    $('detail').focus({ preventScroll: true });
  });
}
function render() {
  const t = c(),
    u = user(),
    mobile = matchMedia('(max-width:900px)').matches;
  document.documentElement.lang = lang;
  $('fixture-note').textContent = t.fixture;
  $('profile-label').textContent = t.sampleProfile;
  $('scene-label').textContent = t.previewState;
  $('language').textContent = lang === 'en' ? 'ID' : 'EN';
  $('language').setAttribute(
    'aria-label',
    lang === 'en' ? 'Switch to Indonesian' : 'Ganti ke Inggris',
  );
  $('initials').textContent = u.initials;
  $('identity').innerHTML =
    esc(u.name) +
    `<small>${u.role === 'supervisor' ? t.supervisor : u.role === 'admin' ? t.admin : t.customerService}</small>`;
  $('profile').innerHTML = Object.entries(profiles)
    .map(
      ([key, p]) =>
        `<option value="${key}" ${key === profile ? 'selected' : ''}>${esc(p.name)} · ${p.role === 'supervisor' ? t.supervisor : p.role === 'admin' ? t.admin : key === 'partner' ? t.lounge : t.customerService}</option>`,
    )
    .join('');
  $('scene').innerHTML = [
    'normal',
    'empty',
    'loading',
    'unavailable',
    'conflict',
    'unknown',
    'revoked',
  ]
    .map(
      (key, i) =>
        `<option value="${key}" ${key === scene ? 'selected' : ''}>${(lang === 'en' ? ['Normal queue', 'Empty queue', 'Loading', 'Unavailable', 'Version conflict', 'Result unknown', 'Access revoked'] : ['Antrean normal', 'Antrean kosong', 'Memuat', 'Belum tersedia', 'Konflik versi', 'Hasil belum pasti', 'Akses dicabut'])[i]}</option>`,
    )
    .join('');
  $('scope-title').textContent = t.workspace;
  $('scope-org').textContent = t[u.org];
  $('scope-current').textContent = t.cases;
  $('scope-note').textContent = t.scopeNote;
  $('queue-title').textContent = t.cases;
  $('queue-scope').textContent = t[u.org];
  $('status-label').textContent = t.status;
  $('queue-foot').textContent = t.queueFoot;
  $('queue-tabs').innerHTML = ['unassigned', 'mine', 'all']
    .map(
      (v) =>
        `<button aria-pressed="${view === v}" data-view="${v}">${t[v]}</button>`,
    )
    .join('');
  $('status-filter').innerHTML = [
    'all',
    'new',
    'in_progress',
    'awaiting_customer',
    'awaiting_external',
    'closed',
  ]
    .map(
      (s) =>
        `<option value="${s}" ${s === statusFilter ? 'selected' : ''}>${s === 'all' ? t.allStatus : t[s]}</option>`,
    )
    .join('');
  const denied = u.role === 'admin' || scene === 'revoked';
  let filtered = allowed().filter(
    (x) =>
      (view === 'all' ||
        (view === 'unassigned' && !x.assignee) ||
        (view === 'mine' && x.assignee === u.name)) &&
      (statusFilter === 'all' || x.status === statusFilter),
  );
  if (scene === 'empty') filtered = [];
  $('queue-scroll').innerHTML = denied
    ? empty(
        scene === 'revoked' ? t.revoked : t.noAccess,
        scene === 'revoked' ? t.revokedText : t.noAccessText,
        scene === 'revoked' ? btn(t.recheck, 'retry') : '',
      )
    : scene === 'loading'
      ? empty(t.loading, t.loadingText)
      : scene === 'unavailable'
        ? empty(t.unavailable, t.unavailableText, btn(t.retry, 'retry'))
        : filtered.length
          ? filtered
              .map(
                (x) =>
                  `<button class="case-row" data-case="${x.id}" aria-current="${selected === x.id}" aria-label="${esc(x.id + ' — ' + x.title)}"><span class="row-top"><span>WB-${x.id}</span><span>${x.updated} UTC</span></span><h2>${esc(x.title)}</h2><span class="row-bottom">${badge(x.status)}<span>${esc(x.assignee) || t.unassigned}</span></span></button>`,
              )
              .join('')
          : empty(t.empty, t.emptyText, btn(t.clear, 'clear'));
  $('queue-scroll').scrollTop = queueScroll;
  const x = current();
  $('detail').innerHTML = denied
    ? empty(
        scene === 'revoked' ? t.revoked : t.noAccess,
        scene === 'revoked' ? t.revokedText : t.noAccessText,
        scene === 'revoked' ? btn(t.recheck, 'retry') : '',
      )
    : scene === 'loading'
      ? empty(t.loading, t.loadingText)
      : scene === 'unavailable'
        ? empty(t.unavailable, t.unavailableText, btn(t.retry, 'retry'))
        : x
          ? detail(x)
          : empty(t.choose, t.chooseText);
  $('detail').setAttribute('tabindex', '-1');
  $('queue').hidden = mobile && mobileDetail;
  $('detail').hidden = mobile && !mobileDetail;
  bind();
}
function empty(title, text, actions = '') {
  return `<div class="empty-state"><h2>${title}</h2><p>${text}</p>${actions}</div>`;
}
function detail(x) {
  const t = c(),
    u = user(),
    canEdit =
      x.status !== 'closed' &&
      (u.role === 'supervisor' || x.assignee === u.name),
    locked = ['unknown', 'conflict'].includes(scene);
  let banner = '';
  if (scene === 'conflict')
    banner = notice(
      t.conflict,
      t.conflictText,
      btn(t.reload, 'reload'),
      'warning',
    );
  if (scene === 'unknown')
    banner = notice(t.unknown, t.unknownText, btn(t.check, 'check'), 'warning');
  if (x.status === 'closed') banner = notice(t.closedTitle, t.closedNote, '');
  if (feedback)
    banner += `<div class="notice" role="status">${esc(t[feedback])}</div>`;
  return `<div class="detail-top">${btn('← ' + t.back, 'back', 'back-queue')}<div class="case-meta"><span>WB-${x.id}</span>${badge(x.status)}<span>${t.updated} ${x.updated} UTC</span></div><h2>${esc(x.title)}</h2><p class="customer-line">${esc(x.customer)} · ${t.language}: ${x.language === 'en' ? t.english : t.indonesian}</p><div class="detail-actions">${!x.assignee && u.role === 'cs' && x.status !== 'closed' ? btn(t.claim, 'claim', 'primary', locked) : ''}${u.role === 'supervisor' && x.status !== 'closed' ? btn(x.assignee ? t.reassign : t.assign, 'assign', 'secondary', locked) : ''}${canEdit ? btn(t.close, 'close', 'secondary', locked) : ''}</div></div>${banner}<section class="detail-section"><h3>${t.report}</h3><p class="report">${esc(x.report)}</p><p class="subtle">${t.original}</p><dl style="margin-top:16px"><dt>${t.refs}</dt><dd>${esc(x.reference) || '—'}</dd></dl><p class="subtle">${t.reported}</p></section><section class="detail-section"><h3>${t.handling}</h3><dl><dt>${t.assignee}</dt><dd>${esc(x.assignee) || t.unassigned}</dd><dt>${t.nextActor}</dt><dd>${t[actor(x.status)]}</dd><dt>${t.nextAction}</dt><dd>${esc(x.next)}</dd></dl></section>${x.status === 'closed' ? `<section class="detail-section"><h3>${t.outcome}</h3><p>${esc(x.resolution)}</p><p class="subtle">${t.reason}: ${t[x.closeReason] || esc(x.closeReason)}</p><p class="subtle">${t.closeNote}</p></section>` : canEdit && !!x.assignee ? statusForm(x, locked) : `<section class="detail-section"><p class="subtle">${!x.assignee ? (u.role === 'supervisor' ? t.assignHint : t.unassignedHint) : t.otherHint}</p></section>`}<section class="detail-section"><h3>${t.latest}</h3><p>${esc(x.history[0]?.[1] || '—')}</p><p class="subtle">${t.utc}</p><details class="history"><summary>${t.history}</summary><ol>${x.history.map(([time, text]) => `<li><time>4 Nov 2026 · ${time} UTC</time>${esc(text)}</li>`).join('')}</ol></details></section>`;
}
function notice(title, text, actions = '', kind = '') {
  return `<div class="notice ${kind}" role="alert"><h3>${title}</h3><p>${text}</p>${actions}</div>`;
}
function allowedStatuses(x) {
  return x.status === 'new'
    ? ['in_progress']
    : x.status === 'awaiting_customer'
      ? ['in_progress', 'awaiting_customer']
      : x.status === 'awaiting_external'
        ? ['in_progress', 'awaiting_external']
        : ['in_progress', 'awaiting_customer', 'awaiting_external'];
}
function statusForm(x, locked) {
  const t = c();
  return `<section class="detail-section"><h3>${t.update}</h3><form id="update-form" novalidate><div class="field"><label for="new-status">${t.updateStatus}</label><select id="new-status" ${locked ? 'disabled' : ''}>${allowedStatuses(
    x,
  )
    .map(
      (s) =>
        `<option value="${s}" ${s === (draft?.status || x.status) ? 'selected' : ''}>${t[s]}</option>`,
    )
    .join(
      '',
    )}</select></div><div class="field"><label for="next-action">${t.actionLabel}</label><textarea id="next-action" maxlength="2000" aria-describedby="action-hint update-error" ${scene === 'unknown' ? 'disabled' : ''}>${esc(draft?.next || '')}</textarea><p class="subtle" id="action-hint">${t.actionHint}</p><p class="field-error" id="update-error" role="alert"></p></div><div class="detail-actions"><button class="primary" type="submit" ${locked ? 'disabled' : ''}>${t.submit}</button></div></form></section>`;
}
function keepDraft(touched = false) {
  if ($('next-action'))
    draft = {
      id: selected,
      next: $('next-action').value,
      status: $('new-status').value,
      touched: touched || draft?.touched || false,
    };
}
function bind() {
  document
    .querySelectorAll('[data-case]')
    .forEach((b) => (b.onclick = () => choose(b.dataset.case)));
  document.querySelectorAll('[data-view]').forEach(
    (b) =>
      (b.onclick = () => {
        keepDraft();
        view = b.dataset.view;
        queueScroll = 0;
        render();
      }),
  );
  document
    .querySelectorAll('[data-action]')
    .forEach((b) => (b.onclick = () => act(b.dataset.action)));
  $('status-filter').onchange = (e) => {
    keepDraft();
    statusFilter = e.target.value;
    queueScroll = 0;
    render();
  };
  if ($('next-action')) {
    $('next-action').oninput = () => keepDraft(true);
    $('new-status').onchange = () => keepDraft(true);
    $('update-form').onsubmit = (e) => {
      e.preventDefault();
      keepDraft();
      if (!draft.next.trim()) {
        $('update-error').textContent = c().required;
        $('next-action').setAttribute('aria-invalid', 'true');
        $('next-action').focus();
        return;
      }
      const x = current();
      x.status = draft.status;
      x.next = draft.next;
      x.history.unshift([
        '14:45',
        'Sample status update: ' + c()[x.status] + '.',
      ]);
      x.updated = '14:45';
      draft = null;
      feedback = 'saved';
      render();
    };
  }
}
function act(kind) {
  const x = current(),
    t = c();
  if (kind === 'back') {
    keepDraft();
    mobileDetail = false;
    render();
    window.scrollTo({ top: queuePageScroll, behavior: 'instant' });
    document
      .querySelector(`[data-case="${selected}"]`)
      ?.focus({ preventScroll: true });
    return;
  }
  if (kind === 'clear') {
    statusFilter = 'all';
    scene = 'normal';
    render();
    return;
  }
  if (kind === 'retry') {
    scene = 'normal';
    if (!selected) selected = allowed()[0]?.id || null;
    render();
    return;
  }
  if (kind === 'claim') {
    x.assignee = user().name;
    x.status = 'in_progress';
    x.history.unshift([
      '14:44',
      'Sample case accepted by ' + user().name + '.',
    ]);
    x.updated = '14:44';
    draft = null;
    feedback = 'claimed';
    render();
    return;
  }
  if (kind === 'reload') {
    scene = 'normal';
    feedback = 'reviewed';
    render();
    return;
  }
  if (kind === 'check') {
    feedback = 'checked';
    render();
    return;
  }
  if (kind === 'assign' || kind === 'close') {
    keepDraft();
    action = kind;
    actionReview = null;
    openAction();
  }
}
function openAction() {
  const t = c(),
    x = current();
  $('action-title').textContent =
    action === 'assign' ? (x.assignee ? t.reassign : t.assign) : t.close;
  $('close-dialog').setAttribute('aria-label', t.closeDialog);
  $('cancel-action').textContent = t.cancel;
  $('confirm-action').textContent = t.review;
  $('action-error').textContent = '';
  if (action === 'assign') {
    const names =
      x.org === 'lounge'
        ? ['Avery Chen', 'Nora Ellis']
        : ['Jordan Miller', 'Taylor Brooks', 'Samira Patel'];
    $('action-fields').innerHTML =
      `<p>WB-${x.id} · ${esc(x.title)}</p><p>${t.from}: <strong>${esc(x.assignee) || t.unassigned}</strong></p><label for="target-assignee">${t.selectAssignee}</label><select name="assignee" id="target-assignee">${names
        .filter((n) => n !== x.assignee)
        .map((n) => `<option>${esc(n)}</option>`)
        .join('')}</select><p class="subtle">${t.assignmentNote}</p>`;
  } else {
    $('action-fields').innerHTML =
      `<p>WB-${x.id} · ${esc(x.title)}</p><label for="close-reason">${t.reason}</label><select name="reason" id="close-reason">${['resolved', 'customer_cancelled', 'duplicate', 'no_response'].map((r) => `<option value="${r}">${t[r]}</option>`).join('')}</select><label for="resolution">${t.resolution}</label><textarea name="resolution" id="resolution" maxlength="4000" aria-describedby="action-error"></textarea><div id="close-extra"></div><p class="subtle">${t.closeNote}</p>`;
    $('close-reason').onchange = closeExtra;
  }
  $('action-dialog').showModal();
}
function closeExtra() {
  const x = current(),
    t = c(),
    reason = $('close-reason').value;
  if (reason === 'no_response')
    $('close-extra').innerHTML =
      `<label for="evidence">${t.evidence}</label><textarea name="evidence" id="evidence" maxlength="2000" aria-describedby="action-error"></textarea>`;
  else if (reason === 'duplicate') {
    const eligible = allowed().filter(
      (k) => k.id !== x.id && k.owner === x.owner,
    );
    $('close-extra').innerHTML = eligible.length
      ? `<label for="related">${t.related}</label><select id="related" name="related">${eligible.map((k) => `<option value="${k.id}">WB-${k.id} · ${esc(k.title)}</option>`).join('')}</select>`
      : `<p class="field-error">${t.noEligibleDuplicate}</p>`;
  } else $('close-extra').innerHTML = '';
}
$('action-form').onsubmit = (e) => {
  e.preventDefault();
  const x = current(),
    t = c();
  if (!actionReview) {
    const values = Object.fromEntries(new FormData(e.target));
    if (
      action === 'close' &&
      (!values.resolution?.trim() ||
        (values.reason === 'no_response' && !values.evidence?.trim()) ||
        (values.reason === 'duplicate' && !values.related))
    ) {
      $('action-error').textContent = t.closeValidation;
      const invalid = values.resolution?.trim()
        ? $('evidence')
        : $('resolution');
      invalid?.setAttribute('aria-invalid', 'true');
      (invalid || $('close-reason')).focus();
      return;
    }
    actionReview = values;
    $('action-title').textContent =
      action === 'assign' ? t.confirmAssignment : t.confirmClose;
    $('action-fields').innerHTML =
      `<div class="confirmation"><strong>WB-${x.id} · ${esc(x.title)}</strong>${action === 'assign' ? `<p>${t.from}: ${esc(x.assignee) || t.unassigned}<br>${t.to}: ${esc(values.assignee)}</p>` : `<p>${t.reason}: ${t[values.reason]}</p><p>${t.resolution}: ${esc(values.resolution)}</p>${values.evidence ? `<p>${t.evidence}: ${esc(values.evidence)}</p>` : ''}${values.related ? `<p>${t.related}: WB-${esc(values.related)}</p>` : ''}`}</div><p class="subtle">${t.fixtureOnly}</p>`;
    $('action-error').textContent = '';
    $('confirm-action').textContent = t.confirm;
    $('confirm-action').focus();
    return;
  }
  if (action === 'assign') {
    x.assignee = actionReview.assignee;
    x.history.unshift([
      '14:45',
      'Sample assignment changed to ' + x.assignee + '.',
    ]);
  } else {
    x.status = 'closed';
    x.closeReason = actionReview.reason;
    x.resolution = actionReview.resolution;
    x.next =
      'No further action pending. Customer follow-up is a separate flow.';
    x.history.unshift([
      '14:45',
      'Sample case closed: ' + t[x.closeReason] + '.',
    ]);
  }
  x.updated = '14:45';
  if (action === 'close') draft = null;
  feedback = 'saved';
  $('action-dialog').close();
  render();
  $('detail').focus();
};
$('close-dialog').onclick = $('cancel-action').onclick = () =>
  $('action-dialog').close();
function cancelNavigation() {
  $('dirty-dialog').close();
  pendingNavigation = null;
  if (draft && current()) {
    mobileDetail = true;
    render();
    $('next-action')?.focus();
  }
}
$('keep-editing').onclick = cancelNavigation;
$('dirty-dialog').addEventListener('cancel', (event) => {
  event.preventDefault();
  cancelNavigation();
});
$('discard').onclick = () => {
  const next = pendingNavigation;
  pendingNavigation = null;
  draft = null;
  $('dirty-dialog').close();
  next?.();
};
$('profile').onchange = (e) => {
  const next = e.target.value;
  e.target.value = profile;
  deferNavigation(() => {
    profile = next;
    selected = allowed()[0]?.id || null;
    draft = null;
    view = 'unassigned';
    scene = 'normal';
    feedback = '';
    queueScroll = 0;
    mobileDetail = false;
    render();
  });
};
$('scene').onchange = (e) => {
  keepDraft();
  scene = e.target.value;
  if (scene === 'revoked') {
    draft = null;
    selected = null;
    feedback = '';
  }
  if (
    ['conflict', 'unknown'].includes(scene) &&
    (!current()?.assignee || current()?.status === 'closed')
  ) {
    selected =
      allowed().find((x) => x.assignee === user().name && x.status !== 'closed')
        ?.id ||
      allowed().find((x) => x.status !== 'closed')?.id ||
      null;
    draft = null;
    view = 'all';
    mobileDetail = true;
  }
  if (['conflict', 'unknown'].includes(scene)) mobileDetail = true;
  render();
};
$('language').onclick = () => {
  keepDraft();
  lang = lang === 'en' ? 'id' : 'en';
  history.replaceState(null, '', '?lang=' + lang);
  render();
};
$('home').onclick = (e) => {
  e.preventDefault();
  deferNavigation(() => {
    draft = null;
    location.href = 'index.html?lang=' + lang;
  });
};
window.addEventListener('beforeunload', (e) => {
  if (dirty()) {
    e.preventDefault();
    e.returnValue = '';
  }
});
let priorMobile = matchMedia('(max-width:900px)').matches;
window.addEventListener('resize', () => {
  const mobile = matchMedia('(max-width:900px)').matches;
  if (mobile !== priorMobile) {
    keepDraft();
    priorMobile = mobile;
    render();
  }
});
render();

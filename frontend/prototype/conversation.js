// Deterministic design fixtures. No model, network, authentication, or business mutation.
const $ = (selector) => document.querySelector(selector);
const words = {
  id: {
    skip: "Langsung ke pesan",
    home: "Pusat bantuan",
    helpFor: "Bantuan untuk Anda",
    intro: "Mulai dari kebutuhan Anda. Kita cari langkah berikutnya.",
    preview: "Pratinjau desain",
    previewNote: "Percakapan contoh. Tidak ada data yang dikirim.",
    support: "Petugas",
    contact: "Hubungi petugas",
    chooseTopic: "Pilih topik",
    sessionNote: "Ruang bantuan Anda",
    thinking: "Menyiapkan jawaban contoh…",
    errorTitle: "Jawaban belum dapat ditampilkan.",
    errorBody:
      "Pesan Anda tetap ada. Coba lagi atau lanjutkan dengan bantuan petugas.",
    retry: "Coba lagi",
    readyTitle: "Cerita Anda tetap dibawa.",
    readyBody:
      "Lihat konteks yang sudah terkumpul sebelum melanjutkan ke verifikasi dan review pengajuan.",
    viewContext: "Lihat konteks permintaan",
    messageLabel: "Ceritakan kebutuhan Anda",
    composerNote:
      "Jangan kirim PIN, OTP, atau nomor kartu lengkap. Enter untuk kirim; Shift+Enter untuk baris baru.",
    placeholder: "Tulis pesan Anda…",
    send: "Kirim pesan",
    you: "Anda",
    assistant: "AI Assistant",
    sample: "Contoh jawaban",
    source: "Panduan contoh",
    close: "Tutup",
    done: "Mengerti",
    empty: "Tulis pesan terlebih dahulu.",
    original: "Pesan asli · ID",
    originalEn: "Pesan asli · EN",
    serviceQuestion: "Layanan apa yang terkait dengan kebutuhan Anda?",
    parking: "Parkir",
    lounge: "Lounge",
    delayed: "Penerbangan saya terlambat",
    confirmation: "Konfirmasi booking tidak ditemukan",
    paymentIssue: "Pembayaran saya bermasalah",
    pickup: "Saya ingin menjemput keluarga",
    facilities: "Saya mencari fasilitas bandara",
    prepare: "Apa yang perlu saya siapkan?",
    human: "Saya perlu bantuan petugas",
    still: "Tetap di percakapan",
    startNew: "Mulai percakapan baru",
    leave: "Kembali ke beranda",
    switchTitle: "Mulai percakapan lain?",
    switchBody:
      "Pesan dan tulisan di percakapan ini akan dihapus. Anda bisa tetap di sini untuk melanjutkan kebutuhan yang sama.",
    leaveTitle: "Kembali ke pusat bantuan?",
    leaveBody:
      "Percakapan contoh ini belum disimpan. Pesan dan tulisan di halaman ini akan dihapus jika Anda kembali.",
    contextTitle: "Konteks untuk bantuan petugas",
    contextBody:
      "Ini konteks yang terkumpul, bukan pengajuan yang sudah dikirim. Langkah berikutnya adalah verifikasi email bila diperlukan, lalu review sebelum konfirmasi pengiriman.",
    topicLabel: "Kebutuhan",
    serviceLabel: "Layanan",
    storyLabel: "Cerita Anda (teks asli)",
    noStory:
      "Belum ada cerita. Tutup dan jelaskan kebutuhan Anda terlebih dahulu.",
    notChosen: "Belum disebutkan",
    previewTitle: "Coba alur percakapan",
    previewBody:
      "Bandara Nusantara adalah nama contoh. Jawaban di sini mengikuti skenario lokal, bukan model AI atau informasi operasional nyata. Data hanya ada selama halaman ini terbuka; muat ulang akan menghapus percakapan.",
    simulateError: "Simulasikan jawaban gagal berikutnya",
    errorArmed:
      "Simulasi aktif: pesan berikutnya akan menampilkan error, lalu bisa dicoba lagi.",
    sourceTitle: "Panduan contoh — bantuan layanan",
    sourceBody:
      "Panduan ilustratif untuk prototipe: asisten membantu memahami informasi dan menyiapkan permintaan terkait booking atau transaksi. Perubahan booking, status pembayaran, dan keputusan refund mengikuti sistem serta penyedia layanan. Ini bukan ketentuan operasional bandara nyata.",
    sourcePrivacy:
      "Data booking pribadi memerlukan verifikasi email dan kewenangan dari sumber. Mengetahui referensi pesanan saja tidak memberikan akses.",
    replyBooking:
      "Untuk booking yang sudah ada, langkah berikutnya bergantung pada ketentuan penyedia. Siapkan referensi booking dan jelaskan perubahan atau kendalanya. Anda bisa memeriksa pilihan di halaman pengelolaan booking penyedia, atau menyiapkan permintaan bantuan jika pilihannya belum jelas.",
    replyPayment:
      "Siapkan referensi pesanan, waktu transaksi, jumlah, dan penjelasan kendala. Jika dana terasa sudah terpotong tetapi status belum sesuai, mintalah pengecekan melalui petugas. Laporan Anda tidak otomatis mengubah status pembayaran atau menyetujui refund.",
    replyInfo:
      "Agar arahannya tepat, sebutkan terminal dan apakah Anda ingin menunggu di mobil atau masuk ke terminal. Lokasi serta ketentuan harus mengikuti informasi bandara yang berlaku. Pratinjau ini belum memuat peta atau lokasi fasilitas bandara nyata.",
    replyPrepare:
      "Untuk bantuan petugas, siapkan cerita singkat, layanan terkait, dan referensi booking bila ada. Untuk kendala transaksi, tambahkan waktu serta jumlah transaksi. Verifikasi email dilakukan saat diperlukan; jangan mengirim PIN, OTP, atau nomor kartu lengkap.",
    handoffIntro:
      "Kita bisa langsung menyiapkan bantuan petugas. Ceritakan kebutuhan Anda; tidak perlu melewati jawaban AI terlebih dahulu.",
    handoffReply:
      "Terima kasih. Cerita Anda sudah ada di percakapan ini dan akan dibawa sebagai konteks. Anda masih bisa menambahkan detail. Buka konteks permintaan untuk melihat apa yang sudah terkumpul; belum ada permintaan yang dikirim.",
    handoffExisting:
      "Saya bawa cerita yang sudah Anda tulis ke konteks bantuan petugas, jadi Anda tidak perlu mengulangnya. Anda bisa menambah detail sebelum langkah verifikasi dan review.",
    bookingIntro:
      "Saya bantu memahami kendala pada booking parkir atau lounge yang sudah ada. Layanan mana yang ingin Anda tanyakan?",
    paymentIntro:
      "Saya bantu menyiapkan informasi untuk pengecekan kendala pembayaran. Layanan apa yang terkait?",
    informationIntro:
      "Apa yang ingin Anda ketahui tentang fasilitas atau layanan bandara? Ceritakan situasi Anda agar arahannya relevan.",
    replyFacilities:
      "Sebutkan fasilitas yang Anda cari, terminal, serta apakah Anda berada di area publik atau setelah pemeriksaan keamanan. Informasi tersebut membantu menentukan arahan yang relevan. Pratinjau ini belum memuat lokasi fasilitas bandara nyata.",
    askIssue:
      "Baik. Ceritakan kendala Anda pada layanan ini, termasuk perubahan rencana bila ada.",
    privateReply:
      "Pengecekan booking atau status transaksi pribadi memerlukan verifikasi email dan akses yang sah. Prototipe ini belum terhubung ke data pesanan, jadi saya belum dapat memastikan statusnya. Kita tetap bisa menyiapkan cerita untuk bantuan petugas.",
  },
  en: {
    skip: "Skip to message",
    home: "Help centre",
    helpFor: "Help for your journey",
    intro: "Start with what you need. Find your next step.",
    preview: "Design preview",
    previewNote: "Sample conversation. No data is sent.",
    support: "Support",
    contact: "Contact support",
    chooseTopic: "Choose a topic",
    sessionNote: "Your space for support",
    thinking: "Preparing a sample reply…",
    errorTitle: "The reply could not be displayed.",
    errorBody:
      "Your message is still here. Try again or continue with support.",
    retry: "Try again",
    readyTitle: "Your story comes with you.",
    readyBody:
      "See the context collected so far before moving to verification and request review.",
    viewContext: "View request context",
    messageLabel: "Tell us what you need",
    composerNote:
      "Do not share PINs, OTPs, or full card numbers. Enter to send; Shift+Enter for a new line.",
    placeholder: "Write your message…",
    send: "Send message",
    you: "You",
    assistant: "AI Assistant",
    sample: "Sample reply",
    source: "Sample guide",
    close: "Close",
    done: "Got it",
    empty: "Write a message first.",
    original: "Original message · ID",
    originalEn: "Original message · EN",
    serviceQuestion: "Which service is your question about?",
    parking: "Parking",
    lounge: "Lounge",
    delayed: "My flight is delayed",
    confirmation: "I cannot find my booking confirmation",
    paymentIssue: "There is an issue with my payment",
    pickup: "I am meeting a passenger",
    facilities: "I am looking for airport facilities",
    prepare: "What should I prepare?",
    human: "I need a support agent",
    still: "Stay in this conversation",
    startNew: "Start a new conversation",
    leave: "Back to help centre",
    switchTitle: "Start another conversation?",
    switchBody:
      "Messages and unsent text in this conversation will be cleared. Stay here to continue with the same issue.",
    leaveTitle: "Back to the help centre?",
    leaveBody:
      "This sample conversation has not been saved. Messages and unsent text on this page will be cleared if you leave.",
    contextTitle: "Context for our support team",
    contextBody:
      "This is the context collected so far, not a submitted request. Next comes email verification if needed, then review before you confirm submission.",
    topicLabel: "Topic",
    serviceLabel: "Service",
    storyLabel: "Your story (original text)",
    noStory:
      "No story yet. Close this window and describe what you need first.",
    notChosen: "Not specified yet",
    previewTitle: "Try the conversation flow",
    previewBody:
      "Bandara Nusantara is an example name. Replies follow local scenarios, not an AI model or real operational information. Data stays only while this page is open; reloading clears the conversation.",
    simulateError: "Make the next sample reply fail",
    errorArmed:
      "Simulation enabled: the next message will show an error and can then be retried.",
    sourceTitle: "Sample guide — service support",
    sourceBody:
      "Illustrative prototype guidance: the assistant explains information and prepares requests about bookings or transactions. Booking changes, payment statuses, and refund decisions belong to the service provider and its systems. This is not a real airport policy.",
    sourcePrivacy:
      "Private booking information requires email verification and authorisation from the source. Knowing an order reference alone does not grant access.",
    replyBooking:
      "For an existing booking, the next step depends on the provider’s terms. Have your booking reference ready and explain what changed or went wrong. Check the provider’s booking management page for options, or prepare a support request if the options are unclear.",
    replyPayment:
      "Have the order reference, transaction time, amount, and a description of the issue ready. If money appears to have been deducted but the status does not match, ask support to request a review. Your report does not automatically change a payment status or approve a refund.",
    replyInfo:
      "For relevant directions, specify your terminal and whether you want to wait in a car or enter the terminal. Locations and rules must follow current airport information. This preview does not contain a real airport map or facility locations.",
    replyPrepare:
      "Prepare a short description, the related service, and a booking reference if available. For a payment issue, add the transaction time and amount. Email verification comes when needed; do not share PINs, OTPs, or full card numbers.",
    handoffIntro:
      "We can prepare a support request directly. Describe what you need; you do not have to go through AI answers first.",
    handoffReply:
      "Thank you. Your story is in this conversation and will be carried as context. You can still add details. Open the request context to see what has been collected; no request has been sent.",
    handoffExisting:
      "I will carry what you have already written into the support context, so you do not have to repeat it. You can add details before verification and review.",
    bookingIntro:
      "I can help you understand an issue with an existing parking or lounge booking. Which service is it about?",
    paymentIntro:
      "I can help you prepare information for a payment review. Which service is it about?",
    informationIntro:
      "What would you like to know about airport facilities or services? Tell me your situation so the guidance is relevant.",
    replyFacilities:
      "Specify the facility you need, your terminal, and whether you are in a public area or past security. These details help identify relevant directions. This preview does not contain real airport facility locations.",
    askIssue:
      "Thank you. Describe your issue with this service, including any changes to your travel plans.",
    privateReply:
      "Checking private bookings or payment statuses requires email verification and valid access. This prototype is not connected to order data, so I cannot confirm a status. We can still prepare your story for support.",
  },
};
const topics = [
  {
    id: "information",
    idTitle: "Informasi bandara",
    enTitle: "Airport information",
    idSubtitle: "Informasi yang sesuai dengan situasi Anda.",
    enSubtitle: "Information relevant to your situation.",
    icon: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-11v1"/>',
  },
  {
    id: "booking",
    idTitle: "Bantuan booking",
    enTitle: "Booking support",
    idSubtitle: "Untuk booking parkir dan lounge yang sudah ada.",
    enSubtitle: "For existing parking and lounge bookings.",
    icon: '<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M8 3v4m8-4v4M4 10h16m-11 5 2 2 4-4"/>',
  },
  {
    id: "payment",
    idTitle: "Kendala pembayaran",
    enTitle: "Payment issues",
    idSubtitle: "Pahami langkah untuk meminta pengecekan.",
    enSubtitle: "Understand how to request a payment review.",
    icon: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h4"/>',
  },
  {
    id: "contact",
    idTitle: "Hubungi petugas",
    enTitle: "Contact support",
    idSubtitle: "Siapkan cerita Anda untuk bantuan lebih lanjut.",
    enSubtitle: "Prepare your story for further support.",
    icon: '<path d="M5 13v-2a7 7 0 0 1 14 0v6c0 3-2 4-5 4"/><rect x="3" y="11" width="4" height="7" rx="2"/><rect x="17" y="11" width="4" height="7" rx="2"/>',
  },
];
const params = new URLSearchParams(location.search);
let language = params.get("lang") === "en" ? "en" : "id";
let topic = topics.some((item) => item.id === params.get("topic"))
  ? params.get("topic")
  : "booking";
let service = null,
  issueBeforeService = false,
  handoff = topic === "contact",
  messages = [],
  nextActions = [],
  pending = null,
  timer = null,
  failNext = false,
  allowUnload = false,
  dialogKind = null;
const text = (key) => words[language][key];
const title = (id) => {
  const item = topics.find((item) => item.id === id);
  return item[`${language}Title`];
};
const make = (tag, content, className) => {
  const node = document.createElement(tag);
  if (content !== undefined) node.textContent = content;
  if (className) node.className = className;
  return node;
};
function addMessage(role, body, source = false) {
  const message = { role, body, language, source };
  messages.push(message);
  const article = make("article", undefined, `message ${role}`);
  const author = make("div", undefined, "message-author");
  if (role === "assistant") {
    const icon = make("img");
    icon.src = "assets/mark.svg";
    icon.alt = "";
    author.append(icon);
  }
  const authorLabel = make("span");
  authorLabel.dataset.author = role;
  author.append(authorLabel);
  const original = make("span", undefined, "message-language");
  original.dataset.original = message.language;
  author.append(original);
  const bodyNode = make("p", body, "message-text");
  bodyNode.lang = message.language;
  article.append(author, bodyNode);
  if (source) {
    const button = make("button", undefined, "source-button");
    button.dataset.source = "true";
    button.addEventListener("click", () => showSource(message.language));
    article.append(button);
  }
  $("#messages").append(article);
  updateMessageLabels();
}
function updateMessageLabels() {
  document
    .querySelectorAll("[data-author]")
    .forEach(
      (node) =>
        (node.textContent =
          node.dataset.author === "user"
            ? text("you")
            : `${text("assistant")} · ${text("sample")}`),
    );
  document.querySelectorAll("[data-original]").forEach((node) => {
    node.hidden = node.dataset.original === language;
    node.textContent =
      node.dataset.original === "id" ? text("original") : text("originalEn");
  });
  document
    .querySelectorAll("[data-source]")
    .forEach((node) => (node.textContent = `↗ ${text("source")}`));
}
function renderTopics() {
  if (!$("#topic-nav").children.length) {
    topics.forEach((item) => {
      const button = make("button");
      button.dataset.topic = item.id;
      const icon = make("span");
      icon.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${item.icon}</svg>`;
      button.append(icon, make("span"));
      button.addEventListener("click", () => changeTopic(item.id));
      $("#topic-nav").append(button);
      const option = make("option");
      option.value = item.id;
      $("#topic-select").append(option);
    });
  }
  $("#topic-nav")
    .querySelectorAll("button")
    .forEach((button) => {
      button.lastChild.textContent = title(button.dataset.topic);
      button.setAttribute(
        "aria-current",
        String(button.dataset.topic === topic),
      );
    });
  $("#topic-select")
    .querySelectorAll("option")
    .forEach((option) => (option.textContent = title(option.value)));
  $("#topic-select").value = topic;
}
function renderLabels() {
  document.documentElement.lang = language;
  document.title = `${title(topic)} — Bandara Nusantara AI Assistant`;
  document
    .querySelectorAll("[data-copy]")
    .forEach((node) => (node.textContent = text(node.dataset.copy)));
  $("#conversation-title").textContent = title(topic);
  $("#conversation-subtitle").textContent = topics.find(
    (item) => item.id === topic,
  )[`${language}Subtitle`];
  $("#language").firstChild.textContent = language === "id" ? "EN " : "ID ";
  $("#language").setAttribute(
    "aria-label",
    language === "id" ? "Switch to English" : "Ganti ke Bahasa Indonesia",
  );
  $("#message").placeholder = text("placeholder");
  $(".home-link").setAttribute("aria-label", text("home"));
  $("#send").setAttribute("aria-label", text("send"));
  $("#dialog-close").setAttribute("aria-label", text("close"));
  $("#chat-scroll").setAttribute(
    "aria-label",
    language === "id" ? "Percakapan" : "Conversation",
  );
  $("#suggestions").setAttribute(
    "aria-label",
    language === "id" ? "Pilihan jawaban" : "Suggested replies",
  );
  $("#topic-nav").setAttribute("aria-label", text("chooseTopic"));
  renderTopics();
  updateMessageLabels();
  renderSuggestions();
  syncState();
}
function renderSuggestions() {
  const hadFocus = $("#suggestions").contains(document.activeElement);
  $("#suggestions").replaceChildren(
    ...nextActions.map((key) => {
      const button = make("button", text(key));
      button.addEventListener("click", () => {
        if (key === "human") {
          beginHandoff();
          return;
        }
        send(text(key), key);
      });
      return button;
    }),
  );
  if (hadFocus) $("#message").focus({ preventScroll: true });
}
function scrollChat() {
  const area = $("#chat-scroll");
  area.scrollTop = area.scrollHeight;
}
function syncState() {
  $("#pending").hidden = !pending || pending.failed;
  $("#reply-error").hidden = !pending?.failed;
  $("#send").disabled = !!pending && !pending.failed;
  $("#message").readOnly = !!pending && !pending.failed;
  $("#handoff-ready").hidden =
    !handoff || !messages.some((message) => message.role === "user");
  $("#suggestions").hidden = !!pending;
}
function initialize() {
  if (timer) clearTimeout(timer);
  timer = null;
  pending = null;
  service = null;
  issueBeforeService = false;
  handoff = topic === "contact";
  messages = [];
  failNext = false;
  $("#messages").replaceChildren();
  $("#message").value = "";
  $("#message-error").hidden = true;
  nextActions =
    topic === "information"
      ? ["pickup", "facilities"]
      : topic === "contact"
        ? []
        : ["parking", "lounge"];
  renderLabels();
  addMessage("assistant", text(handoff ? "handoffIntro" : `${topic}Intro`));
  syncState();
  scrollChat();
}
function send(body, key = null) {
  if (pending && !pending.failed) return;
  const value = body.trim();
  if (!value) {
    $("#message-error").textContent = text("empty");
    $("#message-error").hidden = false;
    $("#message").focus();
    return;
  }
  if (pending?.failed) {
    $("#message-error").textContent =
      language === "id"
        ? "Coba ulang pesan yang belum dijawab atau lanjutkan ke petugas terlebih dahulu."
        : "Retry the unanswered message or continue with support first.";
    $("#message-error").hidden = false;
    $("#message").focus();
    return;
  }
  $("#message-error").hidden = true;
  addMessage("user", value);
  if (key === null) $("#message").value = "";
  pending = { body: value, key, failed: false };
  syncState();
  renderSuggestions();
  scrollChat();
  queueReply();
}
function queueReply() {
  timer = setTimeout(() => {
    timer = null;
    if (failNext) {
      failNext = false;
      pending.failed = true;
      syncState();
      scrollChat();
      return;
    }
    const request = pending;
    pending = null;
    respond(request);
    syncState();
    scrollChat();
  }, 650);
}
function respond(request) {
  const value = request.body.toLowerCase();
  if (request.key === "prepare") {
    addMessage("assistant", text("replyPrepare"), true);
    nextActions = ["human"];
  } else if (handoff) {
    addMessage("assistant", text("handoffReply"));
    nextActions = [];
  } else if (request.key === "parking" || request.key === "lounge") {
    service = request.key;
    if (issueBeforeService) {
      addMessage(
        "assistant",
        text(topic === "payment" ? "replyPayment" : "replyBooking"),
        true,
      );
      issueBeforeService = false;
      nextActions = ["prepare", "human"];
    } else {
      addMessage("assistant", text("askIssue"));
      nextActions =
        topic === "payment" ? ["paymentIssue"] : ["delayed", "confirmation"];
    }
  } else if (
    /\b(status|cek|check|reference|referensi)\b/.test(value) &&
    /(booking|pesanan|transaksi|payment|pembayaran|order)/.test(value)
  ) {
    addMessage("assistant", text("privateReply"));
    nextActions = ["human", "prepare"];
  } else if ((topic === "booking" || topic === "payment") && !service) {
    if (/parkir|parking/.test(value)) service = "parking";
    if (/lounge/.test(value)) service = "lounge";
    if (!service) {
      issueBeforeService = true;
      addMessage("assistant", text("serviceQuestion"));
      nextActions = ["parking", "lounge"];
    } else {
      addMessage(
        "assistant",
        text(topic === "payment" ? "replyPayment" : "replyBooking"),
        true,
      );
      nextActions = ["prepare", "human"];
    }
  } else {
    addMessage(
      "assistant",
      text(
        topic === "information"
          ? request.key === "facilities" ||
            /fasilitas|facilit|toilet|mushola|nursing/.test(value)
            ? "replyFacilities"
            : "replyInfo"
          : topic === "payment"
            ? "replyPayment"
            : "replyBooking",
      ),
      true,
    );
    nextActions = ["prepare", "human"];
  }
  renderSuggestions();
}
function beginHandoff() {
  if (timer) clearTimeout(timer);
  timer = null;
  pending = null;
  if (!handoff) {
    handoff = true;
    addMessage(
      "assistant",
      text(
        messages.some((message) => message.role === "user")
          ? "handoffExisting"
          : "handoffIntro",
      ),
    );
  }
  nextActions = [];
  renderSuggestions();
  syncState();
  scrollChat();
  $("#message").focus({ preventScroll: true });
}
function hasWork() {
  return (
    messages.some((message) => message.role === "user") ||
    !!$("#message").value.trim()
  );
}
function changeTopic(next) {
  if (next === "contact") {
    $("#topic-select").value = topic;
    beginHandoff();
    return;
  }
  if (next === topic) return;
  $("#topic-select").value = topic;
  const change = () => {
    topic = next;
    initialize();
    history.replaceState(
      null,
      "",
      `conversation.html?topic=${topic}&lang=${language}`,
    );
    $("#conversation-title").tabIndex = -1;
    $("#conversation-title").focus({ preventScroll: true });
  };
  if (hasWork()) confirmNavigation("switch", change);
  else change();
}
function openDialog(kind, titleText) {
  dialogKind = kind;
  $("#dialog-title").textContent = titleText;
  $("#dialog-kicker").textContent = text("preview");
  $("#dialog-content").replaceChildren();
  $("#dialog-actions").replaceChildren();
}
function dialogButton(label, callback, primary = false) {
  const button = make("button", label, primary ? "primary" : "secondary");
  button.addEventListener("click", callback);
  $("#dialog-actions").append(button);
  return button;
}
function presentDialog(focus) {
  $("#dialog").showModal();
  (focus || $("#dialog-close")).focus();
}
function closeDialog() {
  $("#dialog").close();
}
function confirmNavigation(kind, callback) {
  openDialog("confirm", text(kind === "switch" ? "switchTitle" : "leaveTitle"));
  $("#dialog-content").append(
    make("p", text(kind === "switch" ? "switchBody" : "leaveBody")),
  );
  const stay = dialogButton(text("still"), closeDialog, true);
  dialogButton(text(kind === "switch" ? "startNew" : "leave"), () => {
    closeDialog();
    callback();
  });
  presentDialog(stay);
}
function showSource(sourceLanguage) {
  openDialog("source", words[sourceLanguage].sourceTitle);
  $("#dialog-content").append(
    make("p", words[sourceLanguage].sourceBody),
    make("p", words[sourceLanguage].sourcePrivacy),
  );
  presentDialog(dialogButton(text("done"), closeDialog, true));
}
function showContext() {
  openDialog("context", text("contextTitle"));
  $("#dialog-content").append(make("p", text("contextBody")));
  const data = make("dl");
  const entries = [
    ["topicLabel", title(topic)],
    ["serviceLabel", service ? text(service) : text("notChosen")],
    [
      "storyLabel",
      messages
        .filter((message) => message.role === "user")
        .map((message) => message.body)
        .join("\n\n") || text("noStory"),
    ],
  ];
  entries.forEach(([key, value]) =>
    data.append(make("dt", text(key)), make("dd", value)),
  );
  $("#dialog-content").append(data);
  presentDialog(dialogButton(text("done"), closeDialog, true));
}
function showPreview() {
  openDialog("preview", text("previewTitle"));
  $("#dialog-content").append(make("p", text("previewBody")));
  const controls = make("div", undefined, "preview-controls");
  const failure = make("button", text("simulateError"));
  failure.addEventListener("click", () => {
    failNext = true;
    const note = make("p", text("errorArmed"));
    controls.replaceChildren(note);
    $("#dialog-actions button").focus();
  });
  controls.append(failure);
  $("#dialog-content").append(controls);
  presentDialog(dialogButton(text("done"), closeDialog, true));
}
$("#composer").addEventListener("submit", (event) => {
  event.preventDefault();
  send($("#message").value);
});
$("#message").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send($("#message").value);
  }
});
$("#message").addEventListener(
  "input",
  () => ($("#message-error").hidden = true),
);
$("#language").addEventListener("click", () => {
  language = language === "id" ? "en" : "id";
  renderLabels();
  history.replaceState(
    null,
    "",
    `conversation.html?topic=${topic}&lang=${language}`,
  );
});
$("#topic-select").addEventListener("change", (event) =>
  changeTopic(event.target.value),
);
$("#handoff").addEventListener("click", beginHandoff);
$("#error-handoff").addEventListener("click", beginHandoff);
$("#retry").addEventListener("click", () => {
  if (!pending?.failed) return;
  pending.failed = false;
  syncState();
  $("#message").focus({ preventScroll: true });
  scrollChat();
  queueReply();
});
$("#view-context").addEventListener("click", showContext);
$("#preview-open").addEventListener("click", showPreview);
$("#mobile-preview").addEventListener("click", showPreview);
$("#dialog-close").addEventListener("click", closeDialog);
$("#dialog").addEventListener("close", () => (dialogKind = null));
document.querySelectorAll("[data-home]").forEach((link) =>
  link.addEventListener("click", (event) => {
    event.preventDefault();
    const leave = () => {
      allowUnload = true;
      location.assign(`index.html?lang=${language}`);
    };
    if (hasWork()) confirmNavigation("leave", leave);
    else leave();
  }),
);
window.addEventListener("beforeunload", (event) => {
  if (!allowUnload && hasWork()) {
    event.preventDefault();
    event.returnValue = "";
  }
});
initialize();

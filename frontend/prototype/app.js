// Local, illustrative content only. This prototype does not call the application API.
const copy = {
  id: {
    skip: "Langsung ke bantuan",
    explore: "Pusat bantuan",
    contact: "Hubungi petugas",
    staff: "Petugas",
    login: "Masuk",
    welcome: "Ada yang bisa kami bantu?",
    searchLabel: "Cari topik bantuan",
    search: "Cari bantuan",
    placeholder: "Cari informasi atau bantuan",
    heroNote: "Informasi yang Anda perlukan. Bantuan untuk langkah berikutnya.",
    eyebrow: "Panduan bantuan",
    topicsTitle: "Temukan jawaban yang Anda butuhkan.",
    topicsDescription:
      "Pilih topik untuk memahami layanan dan langkah yang bisa Anda ambil.",
    common: "Pertanyaan umum",
    reset: "Lihat semua",
    emptyTitle: "Belum ada jawaban yang cocok.",
    emptyBody:
      "Coba kata lain, pilih topik, atau lihat cara menghubungi petugas.",
    moreHelp: "Masih perlu bantuan?",
    moreHelpBody: "Ada kebutuhan yang lebih baik dibicarakan dengan petugas.",
    preview: "Pratinjau desain",
    understood: "Mengerti",
    all: "Semua topik",
    browse: "Mulai percakapan",
    close: "Tutup",
    menu: "Buka menu",
    navigation: "Navigasi utama",
    filter: "Filter topik",
    choose: "Pilih kebutuhan bantuan",
    dialog: {
      contact: [
        "Bantuan petugas, dengan konteks yang jelas.",
        "Pada alur layanan yang direncanakan, Anda menjelaskan kendala, memverifikasi email bila diperlukan, lalu memeriksa ringkasan sebelum mengirim permintaan. Petugas melanjutkan melalui email. Pratinjau ini belum menerima atau mengirim permintaan.",
      ],
      login: [
        "Akses informasi pribadi Anda.",
        "Verifikasi email akan digunakan saat Anda perlu mengakses booking atau kasus pribadi. Fitur masuk belum tersedia dalam pratinjau desain ini.",
      ],
      about: [
        "Beranda bantuan, versi pertama.",
        "Ini adalah pratinjau visual Airport AI Assistant. Westbridge Airport merupakan nama contoh. Pencarian menelusuri panduan contoh di halaman ini; AI, akses booking, verifikasi email, dan pengiriman kasus belum dihubungkan.",
      ],
    },
  },
  en: {
    skip: "Skip to help",
    explore: "Help centre",
    contact: "Contact support",
    staff: "Support",
    login: "Sign in",
    welcome: "How can we help you?",
    searchLabel: "Search help topics",
    search: "Search help",
    placeholder: "Search information or help",
    heroNote: "The information you need. Help with your next step.",
    eyebrow: "Help guides",
    topicsTitle: "Find the answers you need.",
    topicsDescription:
      "Choose a topic to understand our services and the steps you can take.",
    common: "Common questions",
    reset: "View all",
    emptyTitle: "No matching answers yet.",
    emptyBody:
      "Try another phrase, choose a topic, or find out how to contact support.",
    moreHelp: "Need more help?",
    moreHelpBody:
      "Some questions are better discussed with a member of our team.",
    preview: "Design preview",
    understood: "Got it",
    all: "All topics",
    browse: "Start a conversation",
    close: "Close",
    menu: "Open menu",
    navigation: "Main navigation",
    filter: "Filter topics",
    choose: "Choose a help topic",
    dialog: {
      contact: [
        "Support, with the context that matters.",
        "In the planned service flow, you describe your issue, verify your email when needed, and review a summary before sending a request. Our team follows up by email. This preview does not receive or send requests.",
      ],
      login: [
        "Access your private information.",
        "Email verification will be used when you need to access a private booking or case. Sign-in is not available in this design preview.",
      ],
      about: [
        "Your help centre, first look.",
        "This is a visual preview of Airport AI Assistant. Westbridge Airport is an example name. Search explores sample guides on this page; AI, booking access, email verification, and case submission are not connected yet.",
      ],
    },
  },
};
const topics = [
  {
    key: "information",
    icon: '<circle cx="24" cy="24" r="17"/><path d="M24 22v12m0-21v2"/>',
    id: [
      "Informasi bandara",
      "Pahami fasilitas, layanan, dan informasi sebelum bepergian.",
      "Informasi",
    ],
    en: [
      "Airport information",
      "Understand facilities, services, and the essentials before you travel.",
      "Information",
    ],
  },
  {
    key: "booking",
    icon: '<rect x="9" y="12" width="30" height="28" rx="3"/><path d="M16 8v8m16-8v8M9 22h30m-22 9 5 5 10-10"/>',
    id: [
      "Bantuan booking",
      "Cari tahu langkah berikutnya saat rencana Anda berubah.",
      "Booking",
    ],
    en: [
      "Booking support",
      "Know what to do when your travel plans change.",
      "Bookings",
    ],
  },
  {
    key: "payment",
    icon: '<rect x="6" y="12" width="36" height="26" rx="4"/><path d="M6 21h36M12 30h8m9 0h6"/>',
    id: [
      "Kendala pembayaran",
      "Pahami cara meminta pengecekan transaksi layanan.",
      "Pembayaran",
    ],
    en: [
      "Payment issues",
      "Find out how to request a review of a service transaction.",
      "Payments",
    ],
  },
  {
    key: "contact",
    icon: '<path d="M10 27v-5a14 14 0 0 1 28 0v11c0 5-4 8-10 8h-4"/><rect x="7" y="23" width="7" height="12" rx="3"/><rect x="34" y="23" width="7" height="12" rx="3"/><path d="M21 41h5"/>',
    id: [
      "Hubungi petugas",
      "Dapatkan arahan untuk kebutuhan yang perlu bantuan lebih lanjut.",
      "Petugas",
    ],
    en: [
      "Contact support",
      "Find the next step for questions that need a helping hand.",
      "Support",
    ],
  },
];
const faqs = [
  {
    key: "booking",
    id: [
      "Penerbangan terlambat. Bagaimana dengan booking parkir saya?",
      "Siapkan referensi booking dan waktu perubahan perjalanan. Ketentuan perpanjangan mengikuti penyedia parkir. Gunakan halaman pengelolaan booking penyedia untuk melihat pilihan yang tersedia; minta bantuan petugas jika pilihan belum jelas.",
    ],
    en: [
      "My flight is delayed. What about my parking booking?",
      "Have your booking reference and updated travel times ready. Extension terms depend on the parking provider. Check the provider’s booking management page for available options, and contact support if you need help understanding them.",
    ],
  },
  {
    key: "payment",
    id: [
      "Pembayaran bermasalah. Apa yang perlu saya siapkan?",
      "Siapkan referensi pesanan, waktu transaksi, jumlah, dan penjelasan kendala. Jangan membagikan PIN, OTP, atau nomor kartu lengkap. Laporan Anda dapat membantu petugas meminta pengecekan; laporan tersebut tidak otomatis mengubah status pembayaran.",
    ],
    en: [
      "There is a payment issue. What should I prepare?",
      "Have your order reference, transaction time, amount, and a description of the issue ready. Do not share a PIN, OTP, or full card number. Your report helps support request a review; it does not automatically change the payment status.",
    ],
  },
  {
    key: "booking",
    id: [
      "Saya memesan lounge untuk orang lain. Bisa minta bantuan?",
      "Bisa, jika Anda tercatat sebagai pemesan yang berhak mengakses pesanan tersebut. Verifikasi email dan hubungan pesanan diperlukan sebelum informasi pribadi ditampilkan. Mengetahui nomor booking saja tidak cukup untuk memperoleh akses.",
    ],
    en: [
      "I booked a lounge for someone else. Can I get help?",
      "Yes, if you are recorded as an authorised booker for that order. Email verification and a valid booking relationship are required before private information is shown. Knowing the booking number alone does not grant access.",
    ],
  },
  {
    key: "information",
    id: [
      "Bagaimana cara mencari fasilitas yang sesuai kebutuhan saya?",
      "Jelaskan fasilitas yang Anda cari serta konteksnya, seperti terminal, area publik atau setelah pemeriksaan keamanan, dan apakah Anda penumpang atau penjemput. Informasi tersebut membantu menentukan arahan yang relevan. Pratinjau ini belum memuat lokasi fasilitas bandara nyata.",
    ],
    en: [
      "How do I find facilities that suit my needs?",
      "Describe what you need and your context, such as terminal, public or airside area, and whether you are travelling or meeting someone. These details help identify relevant directions. This preview does not contain real airport facility locations.",
    ],
  },
  {
    key: "booking",
    id: [
      "Apakah saya bisa membuat booking baru di sini?",
      "Pemesanan baru dilakukan melalui aplikasi atau situs penyedia layanan. Asisten ini berfokus pada penjelasan informasi dan bantuan untuk booking yang sudah ada.",
    ],
    en: [
      "Can I make a new booking here?",
      "New bookings are made through the service provider’s app or website. This assistant focuses on explaining information and helping with existing bookings.",
    ],
  },
  {
    key: "payment",
    id: [
      "Apakah mengajukan refund berarti dana langsung dikembalikan?",
      "Tidak. Petugas dapat membantu meneruskan permintaan sesuai prosedur. Keputusan, status, dan waktu pengembalian dana mengikuti hasil pemeriksaan penyedia layanan.",
    ],
    en: [
      "Does a refund request mean my money is returned immediately?",
      "No. Support can help forward a request through the appropriate procedure. The decision, status, and timing depend on the service provider’s review.",
    ],
  },
];
let language =
    new URLSearchParams(location.search).get("lang") === "id" ? "id" : "en",
  selected = "all",
  searchTerm = "",
  currentDialog = null;
const $ = (selector) => document.querySelector(selector);
const arrow =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h15m-6-6 6 6-6 6"/></svg>';
function renderTopics() {
  $("#helpers").replaceChildren(
    ...topics.map((topic) => {
      const button = document.createElement("a");
      button.className = "helper";
      button.href = `conversation.html?topic=${topic.key}&lang=${language}`;
      // All markup here is authored static content; user input is rendered only with textContent.
      button.innerHTML = `<span class="icon"><svg viewBox="0 0 48 48" aria-hidden="true">${topic.icon}</svg></span><h2>${topic[language][0]}</h2><p>${topic[language][1]}</p><span class="helper-link">${topic.key === "contact" ? copy[language].contact : copy[language].browse}${arrow}</span>`;
      return button;
    }),
  );
  $("#filters").replaceChildren(
    ...[
      { key: "all" },
      ...topics.filter((topic) => topic.key !== "contact"),
    ].map((topic) => {
      const button = document.createElement("button");
      button.textContent =
        topic.key === "all" ? copy[language].all : topic[language][2];
      button.setAttribute("aria-pressed", String(selected === topic.key));
      button.dataset.topic = topic.key;
      button.addEventListener("click", () => selectTopic(topic.key));
      return button;
    }),
  );
}
function selectTopic(key, scroll = false) {
  selected = key;
  searchTerm = "";
  $("#query").value = "";
  // Keep the existing buttons so keyboard focus stays on the selected filter.
  document.querySelectorAll("#filters button").forEach((button) => {
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.topic === selected),
    );
  });
  renderQuestions();
  if (scroll) scrollResults();
}
function scrollResults() {
  $("#topics").scrollIntoView({
    behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "instant"
      : "smooth",
  });
  $("#results-title").tabIndex = -1;
  $("#results-title").focus({ preventScroll: true });
}
function renderQuestions() {
  const words = searchTerm
    .toLocaleLowerCase(language)
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  const matches = faqs.filter(
    (faq) =>
      (selected === "all" || faq.key === selected) &&
      words.every((word) =>
        [...faq.id, ...faq.en]
          .join(" ")
          .toLocaleLowerCase(language)
          .includes(word),
      ),
  );
  $("#questions").replaceChildren(
    ...matches.map((faq) => {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = faq[language][0];
      const body = document.createElement("p");
      body.textContent = faq[language][1];
      details.append(summary, body);
      return details;
    }),
  );
  $("#empty").hidden = matches.length > 0;
  $("#reset").hidden = selected === "all" && !searchTerm;
  $("#results-title").textContent = searchTerm
    ? language === "id"
      ? "Hasil pencarian"
      : "Search results"
    : copy[language].common;
  $("#result-status").textContent =
    language === "id"
      ? `${matches.length} panduan ditemukan.`
      : `${matches.length} guides found.`;
}
function render() {
  document.documentElement.lang = language;
  document
    .querySelectorAll("[data-i18n]")
    .forEach((node) => (node.textContent = copy[language][node.dataset.i18n]));
  $("#query").placeholder = copy[language].placeholder;
  $("#language").firstChild.textContent = language === "id" ? "EN " : "ID ";
  $("#language").setAttribute(
    "aria-label",
    language === "id" ? "Switch to English" : "Ganti ke Bahasa Indonesia",
  );
  $(".menu-toggle").setAttribute("aria-label", copy[language].menu);
  $("#close").setAttribute("aria-label", copy[language].close);
  $("#navigation").setAttribute("aria-label", copy[language].navigation);
  $("#filters").setAttribute("aria-label", copy[language].filter);
  $(".helpers").setAttribute("aria-label", copy[language].choose);
  renderTopics();
  renderQuestions();
  if (currentDialog) fillDialog(currentDialog);
}
function fillDialog(kind) {
  $("#dialog-title").textContent = copy[language].dialog[kind][0];
  $("#dialog-body").textContent = copy[language].dialog[kind][1];
}
function showDialog(kind) {
  currentDialog = kind;
  fillDialog(kind);
  $("#dialog").showModal();
  $("#dialog-done").focus();
}
document.querySelectorAll("[data-dialog]").forEach((button) =>
  button.addEventListener("click", () => {
    if (button.dataset.dialog === "contact") {
      location.assign(`conversation.html?topic=contact&lang=${language}`);
      return;
    }
    closeMenu(true);
    showDialog(button.dataset.dialog);
  }),
);
$("#close").addEventListener("click", () => $("#dialog").close());
$("#dialog-done").addEventListener("click", () => $("#dialog").close());
$("#dialog").addEventListener("close", () => (currentDialog = null));
$("#language").addEventListener("click", () => {
  language = language === "id" ? "en" : "id";
  render();
});
$("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  searchTerm = $("#query").value.trim();
  selected = "all";
  renderTopics();
  renderQuestions();
  scrollResults();
});
$("#reset").addEventListener("click", () => selectTopic("all", true));
function closeMenu(restoreFocus = false) {
  const wasOpen = $("#navigation").classList.contains("open");
  $("#navigation").classList.remove("open");
  $(".menu-toggle").setAttribute("aria-expanded", "false");
  if (wasOpen && restoreFocus) $(".menu-toggle").focus();
}
$(".menu-toggle").addEventListener("click", () => {
  const open = $("#navigation").classList.toggle("open");
  $(".menu-toggle").setAttribute("aria-expanded", String(open));
});
$("#navigation a").addEventListener("click", (event) => {
  event.preventDefault();
  closeMenu();
  scrollResults();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMenu(true);
});
document.addEventListener("click", (event) => {
  if (!$("#header").contains(event.target)) closeMenu();
});
const updateHeader = () =>
  $("#header").classList.toggle("scrolled", window.scrollY > 72);
window.addEventListener("scroll", updateHeader, { passive: true });
updateHeader();
render();

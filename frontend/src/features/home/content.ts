/** Reviewed product information from frontend_spec / PRODUCT_CONTRACT.
 * Not airport operational knowledge; no provider facts or booking policies. */
export const homeCopy = {
  en: {
    welcome: 'How can we help you?',
    searchLabel: 'Search help topics',
    search: 'Search help',
    placeholder: 'Search information or help',
    note: 'The information you need. Help with your next step.',
    helpCentre: 'Help centre',
    contact: 'Contact support',
    skip: 'Skip to help',
    choose: 'Choose a help topic',
    browse: 'Start a conversation',
    eyebrow: 'Help guides',
    title: 'Find the answers you need.',
    description:
      'Learn how this assistant can help, or choose a topic to continue.',
    scope: 'About this assistant',
    unavailable:
      'Airport service information is currently unavailable. These guides explain how to use this assistant; they are not airport policies.',
    all: 'All topics',
    reset: 'View all',
    empty: 'No matching guides.',
    emptyBody:
      'Try another phrase or choose a help topic. You can also contact support directly.',
    results: (n: number) =>
      `${n} ${n === 1 ? 'guide' : 'guides'} found about this assistant.`,
    more: 'Need more help?',
    moreBody: 'Some questions are better discussed with a member of our team.',
    footer: 'Information and support for your next step.',
    topics: {
      information: [
        'Airport information',
        'Understand facilities, services, and the essentials before you travel.',
        'Information',
      ],
      booking: [
        'Booking support',
        'Know what to do when your travel plans change.',
        'Bookings',
      ],
      payment: [
        'Payment issues',
        'Find out how to request a review of a service transaction.',
        'Payments',
      ],
      contact: [
        'Contact support',
        'Find the next step for questions that need a helping hand.',
        'Support',
      ],
    },
    guides: [
      [
        'information',
        'What can this assistant help me with?',
        'You can ask for information, describe a booking or payment issue, or choose support from a member of the team. Airport service information may be unavailable; unavailable information is not a confirmed answer.',
      ],
      [
        'booking',
        'Can I make or change a booking here?',
        'This assistant does not make or change bookings. Manage your booking with the service provider. For a problem with an existing booking, choose Booking support to continue.',
      ],
      [
        'payment',
        'Can I pay or request a transaction check here?',
        'Payments stay with the service provider. This assistant does not take payments or change transaction status. Choose Payment issues to describe a problem with an existing transaction.',
      ],
      [
        'contact',
        'Can I contact support without asking the AI?',
        'Yes. Choose Contact support directly. The support process collects your issue and relevant details for review; staff follow-up is by email. Opening the support page does not send a request.',
      ],
    ],
  },
  id: {
    welcome: 'Ada yang bisa kami bantu?',
    searchLabel: 'Cari topik bantuan',
    search: 'Cari bantuan',
    placeholder: 'Cari informasi atau bantuan',
    note: 'Informasi yang Anda perlukan. Bantuan untuk langkah berikutnya.',
    helpCentre: 'Pusat bantuan',
    contact: 'Hubungi petugas',
    skip: 'Langsung ke bantuan',
    choose: 'Pilih kebutuhan bantuan',
    browse: 'Mulai percakapan',
    eyebrow: 'Panduan bantuan',
    title: 'Temukan jawaban yang Anda butuhkan.',
    description:
      'Kenali bantuan dari asisten ini, atau pilih topik untuk melanjutkan.',
    scope: 'Tentang asisten ini',
    unavailable:
      'Informasi layanan bandara sedang tidak tersedia. Panduan ini menjelaskan penggunaan asisten, bukan kebijakan bandara.',
    all: 'Semua topik',
    reset: 'Lihat semua',
    empty: 'Tidak ada panduan yang cocok.',
    emptyBody:
      'Coba kata lain atau pilih topik bantuan. Anda juga bisa langsung menghubungi petugas.',
    results: (n: number) => `${n} panduan tentang asisten ini ditemukan.`,
    more: 'Masih perlu bantuan?',
    moreBody: 'Ada kebutuhan yang lebih baik dibicarakan dengan petugas.',
    footer: 'Informasi dan bantuan untuk langkah berikutnya.',
    topics: {
      information: [
        'Informasi bandara',
        'Pahami fasilitas, layanan, dan informasi sebelum bepergian.',
        'Informasi',
      ],
      booking: [
        'Bantuan booking',
        'Cari tahu langkah berikutnya saat rencana Anda berubah.',
        'Booking',
      ],
      payment: [
        'Kendala pembayaran',
        'Pahami cara meminta pengecekan transaksi layanan.',
        'Pembayaran',
      ],
      contact: [
        'Hubungi petugas',
        'Dapatkan arahan untuk kebutuhan yang perlu bantuan lebih lanjut.',
        'Petugas',
      ],
    },
    guides: [
      [
        'information',
        'Apa yang bisa dibantu oleh asisten ini?',
        'Anda dapat mencari informasi, menjelaskan kendala booking atau pembayaran, atau memilih bantuan petugas. Informasi layanan bandara mungkin tidak tersedia; informasi yang tidak tersedia bukan jawaban yang sudah dikonfirmasi.',
      ],
      [
        'booking',
        'Bisakah saya membuat atau mengubah booking di sini?',
        'Asisten ini tidak membuat atau mengubah booking. Kelola booking melalui penyedia layanan. Untuk kendala booking yang sudah ada, pilih Bantuan booking untuk melanjutkan.',
      ],
      [
        'payment',
        'Bisakah saya membayar atau meminta pengecekan transaksi di sini?',
        'Pembayaran tetap dilakukan melalui penyedia layanan. Asisten ini tidak menerima pembayaran atau mengubah status transaksi. Pilih Kendala pembayaran untuk menjelaskan masalah transaksi yang sudah ada.',
      ],
      [
        'contact',
        'Bisakah saya menghubungi petugas tanpa bertanya kepada AI?',
        'Bisa. Langsung pilih Hubungi petugas. Alur bantuan mengumpulkan kendala dan informasi terkait untuk ditinjau; tindak lanjut petugas melalui email. Membuka halaman bantuan tidak mengirim permintaan.',
      ],
    ],
  },
} as const;

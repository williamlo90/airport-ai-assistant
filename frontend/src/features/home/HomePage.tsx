import { useRef, useState } from 'react';
import { Link } from 'react-router';
import { useRouting } from '../../app/routing/context';
import { localizedTarget, topics } from '../../app/routing/policy';
import { CustomerShell } from '../../app/shells/Shells';
import { homeCopy } from './content';
import styles from './home.module.css';

const icons = {
  information: (
    <>
      <circle cx="24" cy="24" r="17" />
      <path d="M24 22v12m0-21v2" />
    </>
  ),
  booking: (
    <>
      <rect x="9" y="12" width="30" height="28" rx="3" />
      <path d="M16 8v8m16-8v8M9 22h30m-22 9 5 5 10-10" />
    </>
  ),
  payment: (
    <>
      <rect x="6" y="12" width="36" height="26" rx="4" />
      <path d="M6 21h36M12 30h8m9 0h6" />
    </>
  ),
  contact: (
    <>
      <path d="M10 27v-5a14 14 0 0 1 28 0v11c0 5-4 8-10 8h-4" />
      <rect x="7" y="23" width="7" height="12" rx="3" />
      <rect x="34" y="23" width="7" height="12" rx="3" />
      <path d="M21 41h5" />
    </>
  ),
};
export function HomePage() {
  const { site, locale, switchLanguage } = useRouting();
  const c = homeCopy[locale];
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [topic, setTopic] = useState<string>('all');
  const [searched, setSearched] = useState(false);
  const resultHeading = useRef<HTMLHeadingElement>(null);
  const destination = (value: string) =>
    localizedTarget(`/help/new?topic=${value}`, locale);
  // Search only the reviewed product guides in both languages, never operational facts.
  const needle = submitted.trim().toLocaleLowerCase();
  const matches = c.guides.filter(
    (guide, i) =>
      (topic === 'all' || guide[0] === topic) &&
      (!needle ||
        [homeCopy.en.guides[i], homeCopy.id.guides[i]]
          .flat()
          .join(' ')
          .toLocaleLowerCase()
          .includes(needle)),
  );
  return (
    <CustomerShell
      site={site}
      variant="home"
      skipLabel={c.skip}
      languageLabel={locale === 'en' ? 'Bahasa Indonesia' : 'English'}
      onLanguageChange={switchLanguage}
      homeHref={localizedTarget('/', locale)}
      navigation={
        <nav
          className={styles.nav}
          aria-label={locale === 'en' ? 'Main navigation' : 'Navigasi utama'}
        >
          <a href="#topics">{c.helpCentre}</a>
          <Link to={destination('contact')}>{c.contact}</Link>
          <Link to={localizedTarget('/help/cases', locale)}>
            {locale === 'en' ? 'My cases' : 'Kasus saya'}
          </Link>
        </nav>
      }
    >
      <section className={styles.hero} aria-labelledby="welcome">
        <div className={styles.heroContent}>
          <h1 id="welcome">{c.welcome}</h1>
          <form
            className={styles.search}
            role="search"
            onSubmit={(e) => {
              e.preventDefault();
              setSubmitted(query);
              setSearched(true);
              resultHeading.current?.focus();
            }}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="10.5" cy="10.5" r="6.5" />
              <path d="m16 16 4 4" />
            </svg>
            <label className="visually-hidden" htmlFor="help-search">
              {c.searchLabel}
            </label>
            <input
              id="help-search"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              maxLength={160}
              placeholder={c.placeholder}
              autoComplete="off"
            />
            <button type="submit">
              {c.search}
              <span aria-hidden="true"> →</span>
            </button>
          </form>
          <p className={styles.heroNote}>{c.note}</p>
        </div>
      </section>
      <section className={styles.helpers} aria-label={c.choose}>
        <div className={styles.helperGrid}>
          {topics.map((key) => (
            <Link key={key} className={styles.helper} to={destination(key)}>
              <span className={styles.icon}>
                <svg viewBox="0 0 48 48" aria-hidden="true">
                  {icons[key]}
                </svg>
              </span>
              <h2>{c.topics[key][0]}</h2>
              <p>{c.topics[key][1]}</p>
              <span className={styles.helperLink}>
                {c.browse}
                <span aria-hidden="true"> →</span>
              </span>
            </Link>
          ))}
        </div>
      </section>
      <section
        className={styles.knowledge}
        id="topics"
        aria-labelledby="topics-title"
      >
        <div>
          <p className={styles.eyebrow}>{c.eyebrow}</p>
          <h2 id="topics-title">{c.title}</h2>
          <p className={styles.description}>{c.description}</p>
          <div
            className={styles.filters}
            role="group"
            aria-label={locale === 'en' ? 'Filter guides' : 'Filter panduan'}
          >
            {['all', ...topics].map((key) => (
              <button
                key={key}
                type="button"
                aria-pressed={topic === key}
                onClick={() => setTopic(key)}
              >
                {key === 'all'
                  ? c.all
                  : c.topics[key as (typeof topics)[number]][2]}
              </button>
            ))}
          </div>
        </div>
        <div className={styles.questions}>
          <div className={styles.resultsHeading}>
            <h3 ref={resultHeading} tabIndex={-1}>
              {c.scope}
            </h3>
            {(submitted || topic !== 'all') && (
              <button
                type="button"
                onClick={() => {
                  setQuery('');
                  setSubmitted('');
                  setTopic('all');
                  setSearched(false);
                }}
              >
                {c.reset}
              </button>
            )}
          </div>
          <p className={styles.availability}>{c.unavailable}</p>
          <p className="visually-hidden" role="status">
            {searched || topic !== 'all' ? c.results(matches.length) : ''}
          </p>
          {matches.map(([key, question, answer]) => (
            <details key={key}>
              <summary>{question}</summary>
              <p>{answer}</p>
            </details>
          ))}
          {!matches.length && (
            <div className={styles.empty}>
              <h3>{c.empty}</h3>
              <p>{c.emptyBody}</p>
              <Link className={styles.primary} to={destination('contact')}>
                {c.contact}
              </Link>
            </div>
          )}
        </div>
      </section>
      <section className={styles.contactSection}>
        <div className={styles.contactInner}>
          <div>
            <h2>{c.more}</h2>
            <p>{c.moreBody}</p>
          </div>
          <Link className={styles.primary} to={destination('contact')}>
            {c.contact}
          </Link>
        </div>
      </section>
      <footer className={styles.footer}>
        <span>
          {site.airportName} · {site.productName}
        </span>
        <span>{c.footer}</span>
      </footer>
    </CustomerShell>
  );
}

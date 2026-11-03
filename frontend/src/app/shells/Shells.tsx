import type { ReactNode } from 'react';
import type { PublicSite } from '../config/publicSite';
import { Button } from '../../components/Button';
import styles from './shells.module.css';

interface Props {
  site: PublicSite;
  languageLabel: string;
  onLanguageChange: () => void;
  skipLabel: string;
  children: ReactNode;
}
function Header({
  site,
  languageLabel,
  onLanguageChange,
}: Omit<Props, 'children' | 'skipLabel'>) {
  return (
    <header className={styles.header}>
      <a
        className={styles.brand}
        href="/"
        aria-label={`${site.airportName} — ${site.productName}`}
      >
        <img src="/assets/mark.svg" alt="" width="36" height="36" />
        <span>
          {site.airportName}
          <small>{site.productName}</small>
        </span>
      </a>
      <Button
        variant="secondary"
        className={styles.language}
        onClick={onLanguageChange}
      >
        {languageLabel}
      </Button>
    </header>
  );
}
export function CustomerShell({ skipLabel, children, ...header }: Props) {
  return (
    <>
      <a className={styles.skip} href="#main-content">
        {skipLabel}
      </a>
      <Header {...header} />
      <main id="main-content" className={styles.customer}>
        {children}
      </main>
    </>
  );
}
export function StaffShell({
  skipLabel,
  children,
  navigation,
  navigationLabel,
  ...header
}: Props & { navigation: ReactNode; navigationLabel: string }) {
  return (
    <>
      <a className={styles.skip} href="#main-content">
        {skipLabel}
      </a>
      <Header {...header} />
      <div className={styles.workspace}>
        <nav className={styles.navigation} aria-label={navigationLabel}>
          {navigation}
        </nav>
        <main id="main-content" className={styles.staff}>
          {children}
        </main>
      </div>
    </>
  );
}

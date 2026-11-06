import type { MouseEventHandler, ReactNode } from 'react';
import type { PublicSite } from '../config/publicSite';
import { Button } from '../../components/Button';
import styles from './shells.module.css';

interface Props {
  site: PublicSite;
  languageLabel: string;
  onLanguageChange: () => void;
  skipLabel: string;
  children: ReactNode;
  variant?: 'standard' | 'home';
  navigation?: ReactNode;
  homeHref?: string;
  onHomeNavigate?: MouseEventHandler<HTMLAnchorElement>;
}
function Header({
  site,
  languageLabel,
  onLanguageChange,
  homeHref = '/',
  onHomeNavigate,
  variant = 'standard',
  navigation,
}: Omit<Props, 'children' | 'skipLabel'>) {
  return (
    <header
      className={`${styles.header} ${variant === 'home' ? styles.homeHeader : ''}`}
    >
      {navigation}
      <a
        className={styles.brand}
        href={homeHref}
        onClick={onHomeNavigate}
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
export function CustomerShell({
  skipLabel,
  children,
  variant = 'standard',
  ...header
}: Props) {
  return (
    <>
      <a className={styles.skip} href="#main-content">
        {skipLabel}
      </a>
      <Header {...header} variant={variant} />
      <main
        id="main-content"
        tabIndex={-1}
        className={variant === 'home' ? styles.home : styles.customer}
      >
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
        <main id="main-content" tabIndex={-1} className={styles.staff}>
          {children}
        </main>
      </div>
    </>
  );
}

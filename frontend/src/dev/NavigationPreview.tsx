import { useState } from 'react';
import { Link, useLocation } from 'react-router';
import { PageFrame } from '../app/routing/pages';
import { useRouting } from '../app/routing/context';
import { useUnsavedChanges } from '../app/routing/draftState';
import { localizedTarget } from '../app/routing/policy';
import { TextArea } from '../components/Fields';

// Development-only navigation exercise; no API or simulated submission.
export function Component() {
  const [text, setText] = useState('');
  const { locale } = useRouting();
  const location = useLocation();
  useUnsavedChanges(text.length > 0, () => setText(''));
  return (
    <PageFrame>
      <h1>{locale === 'en' ? 'Navigation preview' : 'Contoh navigasi'}</h1>
      <p>Development only. Text stays in memory; no request is sent.</p>
      <TextArea
        label={locale === 'en' ? 'Your situation' : 'Situasi Anda'}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <p role="status">
        Topic:{' '}
        {new URLSearchParams(location.search).get('topic') ?? 'information'}
      </p>
      <nav
        aria-label="Navigation exercises"
        style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem' }}
      >
        <Link to={localizedTarget('/', locale)}>Help centre</Link>
        <Link to={localizedTarget('/__dev/navigation?topic=booking', locale)}>
          Booking topic
        </Link>
        <Link to={localizedTarget('/__dev/navigation?topic=contact', locale)}>
          Ask a staff member
        </Link>
        <Link
          to={localizedTarget(
            '/help/submissions/11111111-1111-4111-8111-111111111111',
            locale,
          )}
        >
          Submission link
        </Link>
      </nav>
    </PageFrame>
  );
}

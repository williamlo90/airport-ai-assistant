import { useCallback, useState, useSyncExternalStore } from 'react';
import type { ReactNode } from 'react';
import { useBeforeUnload, useBlocker } from 'react-router';
import { Dialog } from '../../components/Dialog';
import { Button } from '../../components/Button';
import type { Locale } from '../../locales/messages';
import { shouldBlockDirtyNavigation } from './policy';

import { DraftContext, DraftRegistry } from './draftState';
export function DraftNavigation({
  locale,
  children,
}: {
  locale: Locale;
  children: ReactNode;
}) {
  const [registry] = useState(() => new DraftRegistry());
  return (
    <DraftContext value={registry}>
      <NavigationBlocker registry={registry} locale={locale} />
      {children}
    </DraftContext>
  );
}
function NavigationBlocker({
  registry,
  locale,
}: {
  registry: DraftRegistry;
  locale: Locale;
}) {
  const count = useSyncExternalStore(
    registry.subscribe,
    registry.snapshot,
    () => 0,
  );
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      count > 0 && shouldBlockDirtyNavigation(currentLocation, nextLocation),
  );
  useBeforeUnload(
    useCallback(
      (event: BeforeUnloadEvent) => {
        if (registry.snapshot() > 0) {
          event.preventDefault();
          event.returnValue = '';
        }
      },
      [registry],
    ),
    { capture: true },
  );
  const c =
    locale === 'id'
      ? {
          title: 'Tinggalkan perubahan?',
          description:
            'Informasi yang disimpan di halaman ini akan dibuang. Permintaan yang sudah dikirim mungkin tetap diproses.',
          stay: 'Tetap mengedit',
          discard: 'Buang perubahan dan pindah',
          close: 'Tutup konfirmasi',
        }
      : {
          title: 'Leave unsaved changes?',
          description:
            'Leaving discards information kept on this page. Requests already sent may still complete.',
          stay: 'Keep editing',
          discard: 'Discard changes and leave',
          close: 'Close confirmation',
        };
  const cancel = () => {
    if (blocker.state === 'blocked') blocker.reset();
  };
  return (
    <Dialog
      open={blocker.state === 'blocked'}
      title={c.title}
      description={c.description}
      closeLabel={c.close}
      onClose={cancel}
      actions={
        <>
          <Button onClick={cancel}>{c.stay}</Button>
          <Button
            variant="secondary"
            onClick={() => {
              if (blocker.state === 'blocked') {
                registry.discardAll();
                blocker.proceed();
              }
            }}
          >
            {c.discard}
          </Button>
        </>
      }
    />
  );
}

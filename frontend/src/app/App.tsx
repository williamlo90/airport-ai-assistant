import { useEffect } from 'react';
import type { ComponentProps } from 'react';
import { RouterProvider } from 'react-router';
import type { BootstrapState } from './config/publicSite';

export function App({
  bootstrap,
  router,
}: {
  readonly bootstrap: BootstrapState;
  readonly router?: ComponentProps<typeof RouterProvider>['router'] | undefined;
}) {
  useEffect(() => {
    if (bootstrap.status === 'unconfigured') {
      document.title = 'Site unavailable — AI Assistant';
      document.documentElement.lang = 'en';
    }
  }, [bootstrap.status]);
  if (bootstrap.status === 'unconfigured')
    return (
      <main className="bootstrap">
        <h1>Site unavailable</h1>
        <p role="alert">
          This address has not been configured for this application.
        </p>
      </main>
    );
  if (!router) throw new Error('Configured application requires a router');
  return <RouterProvider router={router} />;
}

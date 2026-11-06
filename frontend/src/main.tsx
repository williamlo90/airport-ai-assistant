import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { createBootstrap } from './app/config/publicSite';
import { createBrowserRouter } from 'react-router';
import { createAppRoutes } from './app/routing/routes';
import { createBrowserAuth } from './app/auth/browser';
import './styles/tokens.css';
import './styles/global.css';

const container = document.getElementById('root');

if (!container) {
  throw new Error('The frontend entry requires a #root element.');
}

const bootstrap = createBootstrap(
  window.location.hostname,
  window.location.search,
);
const auth = bootstrap.status === 'configured' ? createBrowserAuth() : null;
const router =
  bootstrap.status === 'configured'
    ? createBrowserRouter(
        createAppRoutes(bootstrap.site, undefined, [], auth?.coordinator),
      )
    : undefined;

createRoot(container).render(
  <StrictMode>
    <App bootstrap={bootstrap} router={router} />
  </StrictMode>,
);

void auth?.start();
if (import.meta.hot)
  import.meta.hot.dispose(() => {
    auth?.dispose();
    router?.dispose();
  });

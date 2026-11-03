import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { createBootstrap } from './app/config/publicSite';
import { createBrowserRouter } from 'react-router';
import { createAppRoutes } from './app/routing/routes';
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
const router =
  bootstrap.status === 'configured'
    ? createBrowserRouter(createAppRoutes(bootstrap.site))
    : undefined;

createRoot(container).render(
  <StrictMode>
    <App bootstrap={bootstrap} router={router} />
  </StrictMode>,
);

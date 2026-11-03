import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { createBootstrap } from './app/config/publicSite';
import './app/bootstrap.css';

const container = document.getElementById('root');

if (!container) {
  throw new Error('The frontend entry requires a #root element.');
}

createRoot(container).render(
  <StrictMode>
    <App
      bootstrap={createBootstrap(
        window.location.hostname,
        window.location.search,
      )}
    />
  </StrictMode>,
);

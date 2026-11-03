import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import './app/bootstrap.css';

const container = document.getElementById('root');

if (!container) {
  throw new Error('The frontend entry requires a #root element.');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

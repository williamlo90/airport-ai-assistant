import http from 'node:http';
import https from 'node:https';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const hopHeaders = [
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
];
function end(response, status) {
  response.writeHead(status, {
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  response.end(
    JSON.stringify({
      error: {
        code:
          status === 404 ? 'RESOURCE_NOT_FOUND' : 'LOCAL_GATEWAY_UNAVAILABLE',
      },
    }),
  );
}

/** Loopback-only development gateway. Never use this as a production server. */
export function gatewayHandler({
  port = 8443,
  frontendPort = 5175,
  backendPort = 8001,
} = {}) {
  return (request, response) => {
    const hosts = request.rawHeaders
      .filter((_, i) => i % 2 === 0)
      .filter((name) => name.toLowerCase() === 'host');
    if (hosts.length !== 1 || request.headers.host !== `localhost:${port}`) {
      end(response, 421);
      return;
    }
    let pathname;
    try {
      if (
        !request.url?.startsWith('/') ||
        request.url.startsWith('//') ||
        request.url.includes('\\')
      )
        throw new Error();
      pathname = decodeURIComponent(request.url.split('?')[0]);
      if (
        pathname.startsWith('//') ||
        pathname.includes('\\') ||
        /(?:^|\/)\.{1,2}(?:\/|$)/.test(pathname) ||
        [...pathname].some(
          (char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127,
        )
      )
        throw new Error();
    } catch {
      end(response, 400);
      return;
    }
    if (
      /(?:^|\/)(?:\.env(?:\.[^/]*)?|\.git|\.secrets)(?:\/|$)/i.test(pathname) ||
      pathname.startsWith('/@fs/')
    ) {
      end(response, 404);
      return;
    }
    const api = /^\/(?:api|health)(?:\/|$)/.test(pathname);
    const headers = { ...request.headers };
    const connectionHeaders = String(headers.connection ?? '')
      .split(',')
      .map((value) => value.trim().toLowerCase());
    for (const name of Object.keys(headers)) {
      if (
        name === 'forwarded' ||
        name.startsWith('x-forwarded-') ||
        hopHeaders.includes(name) ||
        connectionHeaders.includes(name)
      )
        delete headers[name];
    }
    // Never append caller-supplied forwarding chains. Backend trusts only this loopback hop.
    headers.host = `localhost:${port}`;
    headers['x-forwarded-proto'] = 'https';
    headers['x-forwarded-for'] = '127.0.0.1';
    // Missing static resources must never become a successful SPA document.
    if (
      !api &&
      (pathname.startsWith('/assets/') || /\.[a-z0-9]{1,12}$/i.test(pathname))
    )
      headers.accept = 'application/octet-stream';
    const upstream = http.request(
      {
        hostname: '127.0.0.1',
        port: api ? backendPort : frontendPort,
        method: request.method,
        path: request.url,
        headers,
      },
      (incoming) => {
        const outgoing = {
          ...incoming.headers,
          'cache-control': 'no-store',
          'x-content-type-options': 'nosniff',
        };
        for (const name of hopHeaders) delete outgoing[name];
        response.writeHead(incoming.statusCode ?? 502, outgoing);
        incoming.on('error', () => response.destroy());
        incoming.pipe(response);
      },
    );
    upstream.setTimeout(30000, () => upstream.destroy());
    upstream.on('error', () => {
      if (!response.headersSent) end(response, 502);
      else response.destroy();
    });
    request.on('aborted', () => upstream.destroy());
    response.on('close', () => {
      if (!response.writableFinished) upstream.destroy();
    });
    request.pipe(upstream);
  };
}

export function startGateway({ preview = false } = {}) {
  // This reads only generated local TLS material, never a project .env file.
  const server = https.createServer(
    {
      cert: readFileSync(resolve(root, 'local/tls/localhost.pem')),
      key: readFileSync(resolve(root, 'local/tls/localhost-key.pem')),
      minVersion: 'TLSv1.2',
    },
    gatewayHandler({ frontendPort: preview ? 5174 : 5175 }),
  );
  server.requestTimeout = 30000;
  server.headersTimeout = 10000;
  server.on('upgrade', (_request, socket) => socket.destroy());
  server.on('error', () => {
    console.error(
      'Local HTTPS gateway failed. Check certificate files and port 8443.',
    );
    process.exitCode = 1;
  });
  server.listen(8443, '127.0.0.1', () =>
    console.log(
      'Local HTTPS gateway: https://localhost:8443 (loopback only; restart/refresh after source edits).',
    ),
  );
  return server;
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    if (process.argv.slice(2).some((arg) => arg !== '--preview'))
      throw new Error();
    startGateway({ preview: process.argv.includes('--preview') });
  } catch {
    console.error(
      'Local HTTPS gateway not started. Prepare local/tls certificates; see README. No HTTP fallback.',
    );
    process.exitCode = 1;
  }
}

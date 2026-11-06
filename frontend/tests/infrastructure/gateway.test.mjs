import http from 'node:http';
import { once } from 'node:events';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { gatewayHandler } from '../../scripts/local-https.mjs';

async function listening(handler) {
  const server = http.createServer(handler);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return server;
}
function call(port, path, headers = {}, method = 'GET', body) {
  return new Promise((resolve, reject) => {
    const request = http.request(
      {
        hostname: '127.0.0.1',
        port,
        path,
        method,
        headers: { host: 'localhost:8443', ...headers },
      },
      (response) => {
        let text = '';
        response.setEncoding('utf8');
        response.on('data', (chunk) => {
          text += chunk;
        });
        response.on('end', () =>
          resolve({
            status: response.statusCode,
            headers: response.headers,
            text,
          }),
        );
      },
    );
    request.on('error', reject);
    request.end(body);
  });
}
test('gateway keeps API/status/cookies separate from SPA fallback and overwrites forwarding', async () => {
  const seen = [];
  const api = await listening((req, res) => {
    seen.push({ path: req.url, headers: req.headers, method: req.method });
    res.setHeader('Content-Type', 'application/json');
    if (req.url === '/api/v1/auth/sessions') {
      res.setHeader('Set-Cookie', [
        '__Host-airport_session=synthetic; Secure; HttpOnly; Path=/; SameSite=Lax',
        '__Host-airport_csrf=synthetic; Secure; Path=/; SameSite=Lax',
      ]);
    }
    res.statusCode = req.url === '/api/v1/missing' ? 404 : 200;
    req.resume();
    req.on('end', () => res.end('{"synthetic":true}'));
  });
  const spa = await listening((req, res) => {
    if (
      req.url.startsWith('/assets/') &&
      req.headers.accept === 'application/octet-stream'
    ) {
      res.statusCode = 404;
      res.end('Not found');
      return;
    }
    res.setHeader('Content-Type', 'text/html');
    res.end('<main>synthetic SPA</main>');
  });
  const gateway = await listening(
    gatewayHandler({
      frontendPort: spa.address().port,
      backendPort: api.address().port,
    }),
  );
  try {
    const port = gateway.address().port;
    const reply = await call(
      port,
      '/api/v1/auth/sessions',
      {
        origin: 'https://localhost:8443',
        'x-forwarded-proto': 'http',
        'x-forwarded-for': '203.0.113.8',
        'x-forwarded-host': 'other.invalid',
        forwarded: 'proto=http',
        'x-csrf-token': 'synthetic-proof',
        cookie: '__Host-airport_session=synthetic',
      },
      'POST',
      '{}',
    );
    assert.equal(reply.status, 200);
    assert.equal(reply.headers['set-cookie'].length, 2);
    assert.match(reply.headers['set-cookie'][0], /Secure; HttpOnly; Path=\//);
    assert.equal(seen[0].headers.host, 'localhost:8443');
    assert.equal(seen[0].headers.origin, 'https://localhost:8443');
    assert.equal(seen[0].headers['x-csrf-token'], 'synthetic-proof');
    assert.equal(seen[0].headers['x-forwarded-proto'], 'https');
    assert.equal(seen[0].headers['x-forwarded-for'], '127.0.0.1');
    assert.equal(seen[0].headers['x-forwarded-host'], undefined);
    assert.equal(seen[0].headers.forwarded, undefined);
    assert.equal(
      (await call(port, '/api/v1/missing', { accept: 'text/html' })).status,
      404,
    );
    assert.equal(
      (await call(port, '/assets/missing.js', { accept: 'text/html' })).status,
      404,
    );
    const deep = await call(
      port,
      '/help/cases/11111111-1111-4111-8111-111111111111',
      { accept: 'text/html' },
    );
    assert.equal(deep.status, 200);
    assert.match(deep.text, /synthetic SPA/);
    assert.equal((await call(port, '/health/live')).status, 200);
    assert.equal(seen.at(-1).path, '/health/live');
    assert.equal(
      (await call(port, '/', { host: 'other.invalid:8443' })).status,
      421,
    );
    for (const path of [
      '/.env',
      '/%2eenv',
      '/.git/config',
      '/@fs/C:/private',
      '//other.invalid/',
      '/api/../help/new',
      '/assets/%2e%2e/help/new',
      '/%2fother.invalid/',
    ]) {
      assert.ok([400, 404].includes((await call(port, path)).status));
    }
    await new Promise((resolve) => api.close(resolve));
    assert.equal((await call(port, '/api/v1/missing')).status, 502);
  } finally {
    for (const server of [gateway, spa, api]) {
      server.closeAllConnections();
      server.close();
    }
  }
});

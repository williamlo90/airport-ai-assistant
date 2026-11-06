import https from 'node:https';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Explicitly trust only this project's public CA certificate; never disable TLS validation.
const ca = readFileSync(
  new URL('../../local/tls/ca/rootCA.pem', import.meta.url),
);
const origin = 'https://localhost:8443';
const jar = new Map();
const results = [];
let ownsSession = false;
function call(
  path,
  { method = 'GET', body, csrf = false, requestOrigin = origin } = {},
) {
  return new Promise((resolve, reject) => {
    const request = https.request(
      origin + path,
      {
        ca,
        rejectUnauthorized: true,
        family: 4,
        method,
        timeout: 20000,
        headers: {
          Accept: path.startsWith('/api/') ? 'application/json' : 'text/html',
          ...(method === 'GET' ? {} : { Origin: requestOrigin }),
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
          ...(jar.size
            ? { Cookie: [...jar].map(([k, v]) => `${k}=${v}`).join('; ') }
            : {}),
          ...(csrf ? { 'X-CSRF-Token': jar.get('__Host-airport_csrf') } : {}),
        },
      },
      (response) => {
        for (const item of response.headers['set-cookie'] ?? []) {
          const [name, ...value] = item.split(';')[0].split('=');
          if (/max-age=0/i.test(item)) jar.delete(name);
          else jar.set(name, value.join('=').replace(/^"|"$/g, ''));
        }
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
    request.on('timeout', () => request.destroy());
    request.on('error', reject);
    request.end(body === undefined ? undefined : JSON.stringify(body));
  });
}
function passed(name) {
  results.push(name);
}
try {
  for (const path of [
    '/',
    '/help/submissions/11111111-1111-4111-8111-111111111111',
  ]) {
    const response = await call(path);
    assert.equal(response.status, 200);
    assert.match(response.headers['content-type'], /text\/html/);
  }
  passed('SPA entry and deep-link HTML over verified TLS');
  const missingApi = await call('/api/v1/missing');
  assert.equal(missingApi.status, 404);
  assert.equal(JSON.parse(missingApi.text).error.code, 'RESOURCE_NOT_FOUND');
  const missingAsset = await call('/assets/missing.js');
  assert.equal(missingAsset.status, 404);
  assert.doesNotMatch(missingAsset.text, /<html/i);
  passed('API404 and asset404 do not become SPA documents');
  assert.equal((await call('/api/v1/me')).status, 401);
  const bootstrap = await call('/api/v1/auth/sessions', {
    method: 'POST',
    body: {},
  });
  assert.equal(bootstrap.status, 200);
  ownsSession = true;
  assert.equal(JSON.parse(bootstrap.text).session_kind, 'anonymous');
  const cookies = bootstrap.headers['set-cookie'];
  assert.equal(cookies.length, 2);
  for (const name of ['__Host-airport_session', '__Host-airport_csrf']) {
    const cookie = cookies.find((value) => value.startsWith(name + '='));
    assert.ok(cookie);
    assert.match(cookie, /; Secure/i);
    assert.match(cookie, /; Path=\//i);
    assert.doesNotMatch(cookie, /; Domain=/i);
    assert.equal(/; HttpOnly/i.test(cookie), name.endsWith('_session'));
  }
  passed(
    'real anonymous bootstrap and host-only Secure/HttpOnly cookie attributes',
  );
  assert.equal((await call('/api/v1/me')).status, 200);
  assert.equal(
    (await call('/api/v1/auth/logout', { method: 'POST', body: {} })).status,
    403,
  );
  assert.equal(
    (
      await call('/api/v1/auth/logout', {
        method: 'POST',
        body: {},
        csrf: true,
        requestOrigin: 'https://other.invalid',
      })
    ).status,
    403,
  );
  passed('same-origin session read; missing CSRF and wrong Origin rejected');
  assert.equal(
    (
      await call('/api/v1/auth/logout', {
        method: 'POST',
        body: {},
        csrf: true,
      })
    ).status,
    204,
  );
  ownsSession = false;
  assert.equal((await call('/api/v1/me')).status, 401);
  passed('valid-CSRF logout204 and subsequent401');
  console.log(
    JSON.stringify({
      status: 'PASS',
      scope: 'Node TLS/HTTP plus actual backend; not browser cookie proof',
      checks: results,
    }),
  );
} catch {
  console.error(
    JSON.stringify({
      status: 'FAIL',
      completedChecks: results,
      detail:
        'Redacted. Verify local services, registry and certificate setup.',
    }),
  );
  process.exitCode = 1;
} finally {
  if (ownsSession && jar.has('__Host-airport_csrf')) {
    try {
      await call('/api/v1/auth/logout', {
        method: 'POST',
        body: {},
        csrf: true,
      });
    } catch {
      console.error(
        'Anonymous smoke session cleanup unconfirmed; it will expire normally.',
      );
    }
  }
}

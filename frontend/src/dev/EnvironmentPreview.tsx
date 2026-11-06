import { useState } from 'react';
import { PageFrame } from '../app/routing/pages';
import { Button } from '../components/Button';
import { createApiClient } from '../api/client';
import { decodeEmpty, decodeSession } from '../api/contracts';

function csrfProof() {
  const values = document.cookie
    .split(';')
    .map((value) => value.trim())
    .filter((value) => value.startsWith('__Host-airport_csrf='));
  return values.length === 1
    ? values[0]!.slice('__Host-airport_csrf='.length)
    : null;
}

/** Explicit development diagnostic, not the application auth coordinator. */
export function Component() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(
    'Not checked. This check creates and logs out one anonymous test session. No email or customer submission is sent.',
  );
  const allowed =
    location.origin === 'https://localhost:8443' && window.isSecureContext;
  async function check() {
    setBusy(true);
    let ownedProof: string | null = null;
    let canCleanUp = false;
    let stage = 'secure-origin check';
    const client = createApiClient({
      boundary: {
        isCurrent: () => false,
        readCsrfProof: csrfProof,
        invalidate: () => {},
      },
    });
    try {
      if (!allowed) throw new Error();
      stage = 'existing readable session check';
      if (csrfProof()) throw new Error();
      // Preserve any existing HttpOnly session even when its readable proof is missing.
      stage = 'initial session inspection';
      const initial = await fetch('/api/v1/me', {
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
      });
      if (initial.status !== 401) throw new Error();
      stage = 'anonymous bootstrap';
      const value = await client.session({
        operation: 'bootstrap',
        permit: { csrfProof: null, isCurrent: () => true },
        body: {},
        successStatus: 200,
        decode: decodeSession,
      });
      ownedProof = csrfProof();
      stage = 'browser cookie validation';
      if (
        value.session_kind !== 'anonymous' ||
        !ownedProof ||
        document.cookie.includes('__Host-airport_session=')
      )
        throw new Error();
      const permit = {
        csrfProof: ownedProof,
        // This explicit check remains the same attempt after logout removes cookies.
        // The transport itself checks the captured proof before dispatch and for reads.
        isCurrent: () => true,
      };
      stage = 'authenticated session inspection';
      const inspected = await client.session({
        operation: 'inspect',
        permit,
        successStatus: 200,
        decode: decodeSession,
      });
      if (inspected.session_kind !== 'anonymous') throw new Error();
      canCleanUp = true;
      stage = 'missing-CSRF rejection';
      const denied = await fetch('/api/v1/auth/logout', {
        method: 'POST',
        credentials: 'same-origin',
        mode: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
      });
      if (denied.status !== 403) throw new Error();
      stage = 'valid-CSRF logout';
      await client.session({
        operation: 'logout',
        permit,
        successStatus: 204,
        decode: decodeEmpty,
      });
      ownedProof = null;
      stage = 'revoked session inspection';
      const after = await fetch('/api/v1/me', {
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
      });
      if (after.status !== 401 || csrfProof()) throw new Error();
      setResult(
        'PASS: secure browser origin; anonymous bootstrap; readable CSRF and hidden session cookie; authenticated read; missing-CSRF403; valid logout204; revoked session401. No OTP, email or case was created.',
      );
    } catch {
      setResult(
        `Check incomplete at ${stage}. Existing sessions are preserved. Use a separate browser profile with no local session, and verify the certificate, backend and exact host registration. Details and credentials are not displayed.`,
      );
    } finally {
      if (canCleanUp && ownedProof && csrfProof() === ownedProof) {
        try {
          await client.session({
            operation: 'logout',
            permit: {
              csrfProof: ownedProof,
              isCurrent: () => true,
            },
            successStatus: 204,
            decode: decodeEmpty,
          });
        } catch {
          /* Session expires normally if cleanup cannot be confirmed. */
        }
      }
      setBusy(false);
    }
  }
  return (
    <PageFrame>
      <h1>Local HTTPS connection check</h1>
      <p>
        Development diagnostic. It does not implement application sign-in or
        grant access to customer or staff pages.
      </p>
      <p role="status">{result}</p>
      <Button
        disabled={!allowed}
        loading={busy}
        loadingLabel="Checking connection…"
        onClick={() => void check()}
      >
        Run connection check
      </Button>
      {!allowed && (
        <p>
          Open this diagnostic at https://localhost:8443/__dev/environment after
          trusting the project certificate.
        </p>
      )}
    </PageFrame>
  );
}

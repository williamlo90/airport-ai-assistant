import { useState } from 'react';
import { useAuth, useAuthState } from '../app/auth/context';
import type { BoundarySnapshot } from '../api/contracts';
import { record } from '../api/contracts';
import { ApiFailure } from '../api/errors';
import { PageFrame } from '../app/routing/pages';
import { Button } from '../components/Button';
const samplePath = '/conversations/00000000-0000-4000-8000-000000000001';
/** Development-only diagnostic. Never exposes identities, cookies or proof values. */
export function Component() {
  const auth = useAuth(),
    state = useAuthState(auth);
  const [held, setHeld] = useState<BoundarySnapshot | null>(null);
  const [result, setResult] = useState(
    'No diagnostic action has run. Use two tabs at the same HTTPS origin.',
  );
  const [busy, setBusy] = useState(false);
  const allowed =
    location.origin === 'https://localhost:8443' && window.isSecureContext;
  const ownAnonymous = state.phase === 'ready' && state.kind === 'anonymous';
  async function change(kind: 'bootstrap' | 'logout') {
    setBusy(true);
    try {
      const response = await auth.change({ kind });
      setResult(
        `Session operation: ${response.outcome}. The current session was checked again; no business command was replayed.`,
      );
    } catch {
      setResult(
        'Session action was blocked or unavailable. No business request was retried.',
      );
    } finally {
      setBusy(false);
    }
  }
  function stage() {
    try {
      const owner = auth.capture();
      setHeld(owner);
      auth.keep(samplePath, 'Synthetic local draft', owner);
      auth.keep(
        'sample-command',
        'Synthetic pending command',
        owner,
        'command',
      );
      setResult(
        'Sample draft and command are owned by the captured boundary. No request was sent.',
      );
    } catch {
      setResult('A stable session is required before staging a sample draft.');
    }
  }
  async function attempt() {
    if (!held) return;
    setBusy(true);
    try {
      await auth.request({
        method: 'GET',
        path: samplePath,
        boundary: held,
        isRelevant: () => true,
        successStatus: 200,
        decode: record,
      });
      setResult(
        'The sample read completed. This does not grant permission to any other resource.',
      );
    } catch (error) {
      setResult(
        error instanceof ApiFailure && error.outcome === 'not-sent'
          ? 'PASS: the old boundary request was blocked before dispatch.'
          : 'The read did not complete. No mutation, case or email was created.',
      );
    } finally {
      setBusy(false);
    }
  }
  const retained = held ? auth.retained(samplePath, held) !== undefined : false;
  return (
    <PageFrame>
      <h1>Session boundary check</h1>
      <p>
        Development diagnostic, not a sign-in form. It uses real session
        inspection and explicit anonymous bootstrap/logout only. Customer/staff
        sessions are preserved by these controls.
      </p>
      <p role="status">
        Gate: {state.phase}
        {state.phase === 'ready' ? ` · ${state.kind}` : ''}. Sample draft{' '}
        {retained ? 'available' : 'hidden or cleared'}.
      </p>
      <div
        style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginTop: 20 }}
      >
        <Button
          disabled={!allowed || busy || state.phase !== 'guest'}
          onClick={() => void change('bootstrap')}
        >
          Start anonymous session
        </Button>
        <Button
          variant="secondary"
          disabled={!allowed || busy || !ownAnonymous}
          onClick={() => void change('logout')}
        >
          Log out anonymous session
        </Button>
        <Button
          variant="secondary"
          disabled={!allowed || busy || state.phase === 'transition'}
          onClick={() => void auth.recover()}
        >
          Recheck session
        </Button>
        <Button
          variant="secondary"
          disabled={!allowed || busy || !ownAnonymous}
          onClick={stage}
        >
          Stage sample draft
        </Button>
        <Button
          variant="secondary"
          disabled={!allowed || busy || !held}
          onClick={() => void attempt()}
        >
          Attempt captured read
        </Button>
      </div>
      <p role="status" style={{ marginTop: 20 }}>
        {result}
      </p>
      {!allowed && (
        <p>
          Use https://localhost:8443/__dev/auth in a browser that trusts the
          local certificate.
        </p>
      )}
    </PageFrame>
  );
}

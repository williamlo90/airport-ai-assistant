import { fixture, ID, OTHER, json, error } from '../chat/fixture';
import { VerificationStore } from '../../src/features/verification/store';
export { ID, OTHER, json, error };
export function verificationFixture(now?: () => number) {
  const f = fixture(),
    delegate = f.fetcher.getMockImplementation()!;
  const scenario = {
    challengeStatus: 202,
    verifyStatus: 200,
    unknownVerify: false,
    requests: 0,
    verifies: 0,
    delay: null as null | (() => Promise<Response>),
  };
  f.fetcher.mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === '/api/v1/auth/challenges') {
      scenario.requests++;
      if (scenario.challengeStatus === 429)
        return new Response(
          JSON.stringify({
            error: {
              code: 'RATE_LIMITED',
              message: 'PRIVATE',
              request_id: ID,
              retryable: false,
            },
          }),
          {
            status: 429,
            headers: { 'Content-Type': 'application/json', 'Retry-After': '3' },
          },
        );
      return scenario.challengeStatus === 202
        ? json(
            {
              challenge_id: scenario.requests === 1 ? ID : OTHER,
              status: 'verification_requested',
            },
            202,
          )
        : error(scenario.challengeStatus, 'VERIFICATION_UNAVAILABLE');
    }
    if (path.endsWith('/verify')) {
      scenario.verifies++;
      if (scenario.delay) return scenario.delay();
      if (scenario.verifyStatus !== 200)
        return error(
          scenario.verifyStatus,
          scenario.verifyStatus === 422
            ? 'VERIFICATION_FAILED'
            : 'RATE_LIMITED',
        );
      f.server.kind = 'customer';
      f.server.principal = OTHER;
      f.server.proof = 'r'.repeat(43);
      if (scenario.unknownVerify) throw Error('synthetic response lost');
      return json({ session_kind: 'customer', principal_id: OTHER });
    }
    return delegate(input, init);
  });
  const verify = new VerificationStore(f.auth, f.store, now),
    off = verify.connect();
  return {
    ...f,
    scenario,
    verify,
    dispose() {
      off();
      f.dispose();
    },
  };
}

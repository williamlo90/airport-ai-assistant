import { caseFixture, DRAFT, NEXT, OTHER, ID, json } from './fixture';
import { SubmissionStore } from '../../src/features/cases/submission';
export { DRAFT, NEXT, OTHER, ID, json };
export const result = () => ({
  state: 'committed',
  case_id: OTHER,
  message_id: ID,
  result_ref: 'CASE-' + OTHER.replaceAll('-', ''),
});
export function submissionFixture() {
  const f = caseFixture(),
    base = f.fetcher.getMockImplementation()!;
  const submits = {
    requests: [] as { path: string; body: unknown; key: string | null }[],
    gets: 0,
    commits: 0,
    loseResponse: false,
    loseBeforeCommit: false,
    handler: null as
      null | ((path: string, init?: RequestInit) => Promise<Response> | null),
  };
  f.fetcher.mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.startsWith('/api/v1/actions/') || path.endsWith('/confirm')) {
      const custom = submits.handler?.(path, init);
      if (custom) return custom;
      const d = f.previewServer.draft,
        id = path.includes(NEXT) ? NEXT : DRAFT;
      if (path.endsWith('/confirm')) {
        submits.requests.push({
          path,
          body: JSON.parse(String(init?.body)),
          key: new Headers(init?.headers).get('Idempotency-Key'),
        });
        if (submits.loseBeforeCommit) throw Error('lost before outcome known');
        if (d.state !== 'committed') submits.commits++;
        d.state = 'committed';
        d.result_ref = result().result_ref;
        if (submits.loseResponse) throw Error('lost response after commit');
        return json(result(), 201);
      }
      submits.gets++;
      return json({
        draft_id: id,
        state: d.state,
        version: d.version,
        expires_at: d.state === 'committed' ? null : d.expires_at,
        case_id: d.state === 'committed' ? OTHER : null,
        result_ref: d.state === 'committed' ? result().result_ref : null,
      });
    }
    return base(input, init);
  });
  const submission = new SubmissionStore(f.auth, f.cases),
    off = submission.connect();
  return {
    ...f,
    submission,
    submits,
    async ready() {
      await f.ready();
      await submission.open(DRAFT);
    },
    intent() {
      const d = f.cases.getState().draft!;
      return {
        draftId: d.draft_id,
        version: d.version,
        hash: d.payload_hash,
        owner: f.auth.capture(),
      };
    },
    dispose() {
      off();
      submission.dispose();
      f.dispose();
    },
  };
}

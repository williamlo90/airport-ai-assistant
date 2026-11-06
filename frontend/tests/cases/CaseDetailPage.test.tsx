import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { StrictMode } from 'react';
import { createMemoryRouter } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import { detailFixture, CASE, error } from './detail-fixture';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { CaseDetailContext } from '../../src/features/cases/detail-store';
import { CaseDetailPage } from '../../src/features/cases/CaseDetailPage';
const owned: ReturnType<typeof detailFixture>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
async function mount(setup?: (f: ReturnType<typeof detailFixture>) => void) {
  const f = detailFixture();
  owned.push(f);
  await f.ready();
  setup?.(f);
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const element = (
    <CaseDetailContext value={f.details}>
      <CaseDetailPage />
    </CaseDetailContext>
  );
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      undefined,
      [{ path: 'test-case/:caseId', element }],
      f.auth,
    ),
    { initialEntries: ['/test-case/' + CASE + '?lang=en'] },
  );
  render(
    <StrictMode>
      <App bootstrap={bootstrap} router={router} />
    </StrictMode>,
  );
  await screen.findByRole('heading', { name: 'Your case, at a glance' });
  await waitFor(() => expect(f.details.getState().phase).not.toBe('loading'));
  return { ...f, router };
}
it('shows real case status/next actor/action and closed result with public history, no mount POST', async () => {
  const f = await mount();
  expect(
    screen.getByRole('heading', { name: 'Handling result' }),
  ).toBeInTheDocument();
  expect(
    screen.getByText('No further action is pending.', { selector: 'dd' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('heading', { name: 'Case history' }),
  ).toBeInTheDocument();
  expect(f.detailServer.posts).toHaveLength(0);
});
it('source outage from another resource clears the visible case and offers a working fresh reload', async () => {
  const f = await mount();
  f.server.override = async () => error(503, 'SOURCE_UNAVAILABLE');
  await act(async () => {
    await f.auth
      .request({
        method: 'GET',
        path: '/source/check',
        boundary: f.auth.capture(),
        isRelevant: () => true,
        successStatus: 200,
        decode: (v) => v,
      })
      .catch(() => {});
  });
  expect(
    screen.queryByText('Existing parking booking after a delay'),
  ).toBeNull();
  f.server.override = null;
  const before = f.detailServer.gets;
  fireEvent.click(
    screen.getByRole('button', { name: 'Try reading case again' }),
  );
  await waitFor(() => expect(f.detailServer.gets).toBeGreaterThan(before));
  await waitFor(() => expect(f.details.getState().detail?.id).toBe(CASE));
});
it('requires explanation and an initially-unchecked same issue confirmation, focusing error summary', async () => {
  const f = await mount();
  fireEvent.click(
    screen.getByRole('button', { name: 'My issue is not resolved' }),
  );
  const checkbox = screen.getByRole('checkbox');
  expect(checkbox).not.toBeChecked();
  expect(screen.getByLabelText('Your explanation')).toHaveFocus();
  fireEvent.click(
    screen.getByRole('button', { name: 'Request further handling' }),
  );
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveFocus();
  expect(
    within(alert).getByText('Confirm that this is the same unresolved issue.'),
  ).toBeInTheDocument();
  expect(f.detailServer.posts).toHaveLength(0);
});
it('sends explicit follow-up once and preserves previous result in reopened case/history', async () => {
  const f = await mount();
  fireEvent.click(
    screen.getByRole('button', { name: 'My issue is not resolved' }),
  );
  fireEvent.change(screen.getByLabelText('Your explanation'), {
    target: { value: 'The same issue still needs help.' },
  });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Request further handling' }),
  );
  await screen.findByText(
    'Your follow-up request was recorded. The current case status is shown above.',
  );
  expect(
    screen.getByRole('heading', { name: 'Previous handling results' }),
  ).toBeInTheDocument();
  expect(
    screen.getByText('Awaiting assignment', { selector: 'strong' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Request further handling' }),
  ).not.toBeInTheDocument();
  expect(f.detailServer.applied).toBe(1);
});
it('active case offers reviewed additional information, has no rating composer and renders original report as text', async () => {
  const f = await mount((f) => {
    f.detailServer.detail.status = 'awaiting_customer';
    f.detailServer.detail.next_actor = 'customer';
    f.detailServer.detail.next_action =
      'Explain which parking booking needs help.';
    f.detailServer.detail.customer_report =
      '<img src=x onerror=alert(1)> Original report.';
  });
  expect(
    screen.getByText('<img src=x onerror=alert(1)> Original report.'),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('textbox', { name: 'Your additional information' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.queryByText(/rate your/i)).not.toBeInTheDocument();
  expect(f.detailServer.posts).toHaveLength(0);
});
it('no_response explains closure without assuming satisfaction', async () => {
  await mount((f) => {
    f.detailServer.detail.close_reason = 'no_response';
    f.detailServer.detail.resolution = 'Closed after documented follow-up.';
  });
  expect(
    screen.getByText(/No response does not mean you were satisfied/),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'My issue is not resolved' }),
  ).toBeEnabled();
});
it('lost committed response shows observed status but no own-success claim and no resend button', async () => {
  const f = await mount((f) => {
    f.detailServer.loseAfter = true;
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'My issue is not resolved' }),
  );
  fireEvent.change(screen.getByLabelText('Your explanation'), {
    target: { value: 'The same issue still needs help.' },
  });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Request further handling' }),
  );
  await screen.findByRole('heading', {
    name: 'Your follow-up result is not confirmed',
  });
  expect(
    screen.getByText('Awaiting assignment', { selector: 'strong' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(
      'Your follow-up request was recorded. The current case status is shown above.',
    ),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Retry the same request' }),
  ).not.toBeInTheDocument();
  expect(f.detailServer.posts).toHaveLength(1);
});
it('pending unchanged closed result permits manual same-command retry without duplicate history', async () => {
  const f = await mount((f) => {
    f.detailServer.loseBefore = true;
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'My issue is not resolved' }),
  );
  fireEvent.change(screen.getByLabelText('Your explanation'), {
    target: { value: 'Please continue this same unresolved issue.' },
  });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Request further handling' }),
  );
  await screen.findByRole('button', { name: 'Retry the same request' });
  f.detailServer.loseBefore = false;
  fireEvent.click(
    screen.getByRole('button', { name: 'Retry the same request' }),
  );
  await screen.findByText(
    'Your follow-up request was recorded. The current case status is shown above.',
  );
  expect(f.detailServer.posts[0]).toEqual(f.detailServer.posts[1]);
  expect(f.detailServer.applied).toBe(1);
});
it('fresh404 hides original report, reference, history and local explanation', async () => {
  const f = await mount();
  f.detailServer.handler = () =>
    Promise.resolve(error(404, 'RESOURCE_NOT_FOUND'));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh case' }));
  await screen.findByText(
    'Case information is not available. Check your access and try reading it again.',
  );
  expect(screen.queryByText('PARK-DEMO')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('heading', { name: 'Case history' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'My issue is not resolved' }),
  ).not.toBeInTheDocument();
});
it('locale changes preserve plain explanation and do not send or restore checked consent', async () => {
  const f = await mount();
  fireEvent.click(
    screen.getByRole('button', { name: 'My issue is not resolved' }),
  );
  fireEvent.change(screen.getByLabelText('Your explanation'), {
    target: { value: 'Keep this explanation.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Bahasa Indonesia' }));
  expect(await screen.findByLabelText('Penjelasan Anda')).toHaveValue(
    'Keep this explanation.',
  );
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  expect(f.detailServer.posts).toHaveLength(0);
});
it('refreshing same-proof owned case restores explanation with a new unchecked confirmation', async () => {
  const f = await mount();
  fireEvent.click(
    screen.getByRole('button', { name: 'My issue is not resolved' }),
  );
  fireEvent.change(screen.getByLabelText('Your explanation'), {
    target: { value: 'Keep only my explanation.' },
  });
  fireEvent.click(screen.getByRole('checkbox'));
  await act(async () => {
    await f.auth.recover();
  });
  await screen.findByLabelText('Your explanation');
  expect(screen.getByLabelText('Your explanation')).toHaveValue(
    'Keep only my explanation.',
  );
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  expect(f.detailServer.posts).toHaveLength(0);
});

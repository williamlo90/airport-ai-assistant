import { afterEach, expect, it, vi } from 'vitest';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { AgentAnswer } from '../../src/features/chat/AgentAnswer';
import { fixture, ID, OTHER } from './fixture';

const owned: (() => void)[] = [];
afterEach(() => {
  cleanup();
  owned.splice(0).forEach((fn) => fn());
});
async function setup(customer = true) {
  const f = fixture();
  if (customer) {
    f.server.kind = 'customer';
    f.server.principal = OTHER;
  }
  await f.auth.start();
  vi.spyOn(f.store, 'getState').mockReturnValue({
    ...f.store.getState(),
    phase: 'ready',
    meta: { ...f.server.meta, language: 'en' },
  });
  vi.spyOn(f.store.timeline, 'getState').mockReturnValue({
    ...f.store.timeline.getState(),
    id: ID,
    phase: 'ready',
    snapshot: { mode: 'ai', version: 1, generation_epoch: 0 },
  });
  vi.spyOn(f.store.agent, 'canAsk').mockReturnValue(true);
  vi.spyOn(f.store.agent, 'getState').mockReturnValue({
    ...f.store.agent.getState(),
    privateAvailable: customer,
  });
  const ask = vi.spyOn(f.store.agent, 'ask').mockResolvedValue();
  const view = (routeKey = 'a') => (
    <MemoryRouter>
      <AgentAnswer
        store={f.store}
        conversationId={ID}
        routeKey={routeKey}
        locale="en"
      />
    </MemoryRouter>
  );
  const mounted = render(view());
  owned.push(f.dispose);
  return { ...f, ask, view, mounted };
}
it('offers private references only to verified customers, with an explicit read-only explanation', async () => {
  await setup(false);
  expect(screen.queryByLabelText('Booking reference')).toBeNull();
  cleanup();
  await setup();
  expect(screen.getByLabelText('Booking reference')).toBeVisible();
  expect(
    screen.getByText(/bookings and payments are not changed/),
  ).toBeVisible();
});
it('invalid references are announced; valid explicit click supplies references once and clears the inputs', async () => {
  const f = await setup();
  const booking = screen.getByLabelText('Booking reference');
  fireEvent.change(booking, { target: { value: 'bad space' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask AI' }));
  expect(screen.getByRole('alert')).toHaveTextContent('Use letters');
  expect(f.ask).not.toHaveBeenCalled();
  fireEvent.change(booking, { target: { value: 'BOOK-1' } });
  fireEvent.change(screen.getByLabelText('Payment reference'), {
    target: { value: 'PAY-1' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Ask AI' }));
  expect(f.ask).toHaveBeenCalledExactlyOnceWith('general', {
    booking_references: ['BOOK-1'],
    transaction_references: ['PAY-1'],
  });
  expect(booking).toHaveValue('');
});
it('reference input is destroyed on route change and auth recheck rather than restoring old values', async () => {
  const f = await setup();
  fireEvent.change(screen.getByLabelText('Booking reference'), {
    target: { value: 'PRIVATE-REF' },
  });
  f.mounted.rerender(f.view('b'));
  expect(screen.getByLabelText('Booking reference')).toHaveValue('');
  fireEvent.change(screen.getByLabelText('Booking reference'), {
    target: { value: 'PRIVATE-REF' },
  });
  await act(async () => {
    await f.auth.recheck();
  });
  expect(screen.getByLabelText('Booking reference')).toHaveValue('');
  expect(f.ask).not.toHaveBeenCalled();
});

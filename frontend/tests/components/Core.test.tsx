import { act, fireEvent, render, screen, within } from '@testing-library/react';
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from 'vitest';
import { useState } from 'react';
import { Button } from '../../src/components/Button';
import { Field, TextArea, Select } from '../../src/components/Fields';
import { Dialog } from '../../src/components/Dialog';
import { createBootstrap } from '../../src/app/config/publicSite';
import CorePreview from '../../src/dev/CorePreview';

// jsdom has no native dialog focus/inert implementation. These shims test only
// controlled open/close wiring; focus trap, Escape and focus return need real browsers.
beforeAll(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute('open', '');
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    },
  });
});
afterAll(() => {
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'close');
});
afterEach(() => vi.useRealTimers());

describe('shared controls', () => {
  it('blocks repeated activation while loading and keeps its busy label accessible', () => {
    const onClick = vi.fn();
    render(
      <Button loading loadingLabel="Checking…" onClick={onClick}>
        Submit
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Checking…' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
  it('associates labels, hints and errors with controls without dropping existing descriptions', () => {
    render(
      <>
        <p id="custom">Additional context</p>
        <Field
          label="Reference"
          hint="Use the source reference"
          error="Reference is required"
          aria-describedby="custom"
        />
        <TextArea label="Message" />
        <Select label="Service">
          <option>Parking</option>
        </Select>
      </>,
    );
    const input = screen.getByRole('textbox', { name: 'Reference' });
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleDescription(
      'Use the source reference Reference is required Additional context',
    );
    expect(
      screen.getByRole('textbox', { name: 'Message' }),
    ).toHaveAccessibleName('Message');
    expect(
      screen.getByRole('combobox', { name: 'Service' }),
    ).toBeInTheDocument();
  });
  it('requests one controlled close on cancel and removes the modal open state', () => {
    const closed = vi.fn();
    function Example() {
      const [open, setOpen] = useState(true);
      return (
        <Dialog
          open={open}
          title="Review"
          closeLabel="Close review"
          onClose={() => {
            closed();
            setOpen(false);
          }}
        >
          Local content
        </Dialog>
      );
    }
    render(<Example />);
    const dialog = screen.getByRole('dialog', { name: 'Review' });
    expect(dialog).toHaveAttribute('open');
    fireEvent(dialog, new Event('cancel', { cancelable: true }));
    expect(closed).toHaveBeenCalledTimes(1);
    expect(dialog).not.toHaveAttribute('open');
  });
});

const state = createBootstrap('localhost', '');
if (state.status !== 'configured') throw new Error('Missing local fixture');
const site = state.site;
function Preview() {
  const [locale, setLocale] = useState<'en' | 'id'>('en');
  return (
    <CorePreview
      site={site}
      locale={locale}
      onLanguageChange={() => setLocale((v) => (v === 'en' ? 'id' : 'en'))}
    />
  );
}

describe('isolated component preview', () => {
  it('focuses validation summary and its links lead to the actual invalid field', () => {
    render(<Preview />);
    fireEvent.click(screen.getByRole('button', { name: 'Validate example' }));
    const summary = screen.getByRole('alert');
    expect(summary).toHaveFocus();
    fireEvent.click(
      within(summary).getByRole('link', {
        name: 'Example reference: Complete this field.',
      }),
    );
    expect(
      screen.getByRole('textbox', { name: 'Example reference' }),
    ).toHaveFocus();
  });
  it('preserves text and translates validation on language changes', () => {
    render(<Preview />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Example message' }), {
      target: { value: 'Keep this original text' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Validate example' }));
    fireEvent.click(screen.getByRole('button', { name: 'Bahasa Indonesia' }));
    expect(screen.getByRole('textbox', { name: 'Pesan contoh' })).toHaveValue(
      'Keep this original text',
    );
    expect(
      within(screen.getByRole('alert')).getByRole('link'),
    ).toHaveTextContent('Referensi contoh: Lengkapi field ini.');
  });
  it('keeps form text and a visible focus target across shell changes', () => {
    render(<Preview />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Example message' }), {
      target: { value: 'Original story' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Staff shell' }));
    expect(
      screen.getByRole('textbox', { name: 'Example message' }),
    ).toHaveValue('Original story');
    expect(screen.getByRole('button', { name: 'Staff shell' })).toHaveFocus();
    expect(
      screen.getByRole('navigation', { name: 'Sample staff navigation' }),
    ).toBeInTheDocument();
  });
  it('prevents edits and duplicate activation during example validation, with no submission claim', () => {
    vi.useFakeTimers();
    render(<Preview />);
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Example reference' }),
      { target: { value: 'DEMO-1' } },
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Example message' }), {
      target: { value: 'Sample story' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Validate example' }));
    expect(
      screen.getByRole('button', { name: 'Checking example…' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('textbox', { name: 'Example message' }),
    ).toBeDisabled();
    act(() => vi.advanceTimersByTime(600));
    expect(
      screen.getByText('Example inputs are valid. No request was sent.'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('textbox', { name: 'Example message' }),
    ).toBeEnabled();
  });
  it('distinguishes unknown results from failed or empty requests', () => {
    render(<Preview />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Preview state' }), {
      target: { value: 'unknown' },
    });
    expect(
      screen.getByRole('heading', { name: 'The result is not yet known' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Retry example' }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Preview state' }), {
      target: { value: 'error' },
    });
    expect(screen.getByRole('alert')).toHaveTextContent(
      'The example request failed',
    );
  });
});

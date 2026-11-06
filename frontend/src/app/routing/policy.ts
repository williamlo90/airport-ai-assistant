import type { Locale } from '../../locales/messages';

export const topics = ['information', 'booking', 'payment', 'contact'] as const;
export function readTopic(search: string): (typeof topics)[number] | null {
  const values = new URLSearchParams(search).getAll('topic');
  if (!values.length) return 'information';
  return values.length === 1 && topics.some((value) => value === values[0])
    ? (values[0] as (typeof topics)[number])
    : null;
}
export function isResourceId(value: string | undefined): boolean {
  return (
    !!value &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      value,
    )
  );
}
export function readHandoffCase(search: string): string | null | undefined {
  const values = new URLSearchParams(search).getAll('case');
  return !values.length
    ? null
    : values.length === 1 && isResourceId(values[0])
      ? values[0]!.toLowerCase()
      : undefined;
}
export function localizedTarget(path: string, locale: Locale): string {
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\'))
    throw new Error('Internal route required');
  const url = new URL(path, 'https://routing.invalid');
  if (url.origin !== 'https://routing.invalid')
    throw new Error('Internal route required');
  const output = new URLSearchParams();
  const topic = readTopic(url.search);
  const selectedCase = readHandoffCase(url.search);
  if (
    selectedCase &&
    /^\/help\/(new|verify|conversations\/[0-9a-f-]{36})$/i.test(url.pathname)
  )
    output.set('case', selectedCase);
  if (url.searchParams.has('topic') && topic) output.set('topic', topic);
  for (const [key, allowed] of [
    [
      'status',
      [
        'new',
        'in_progress',
        'awaiting_customer',
        'awaiting_external',
        'closed',
      ],
    ],
    ['view', ['unassigned', 'mine', 'all']],
    ['preview', ['components']],
  ] as const) {
    const values = url.searchParams.getAll(key);
    if (values.length === 1 && allowed.some((value) => value === values[0]))
      output.set(key, values[0]!);
  }
  output.set('lang', locale);
  const hash = /^#[a-zA-Z][\w-]{0,63}$/.test(url.hash) ? url.hash : '';
  return `${url.pathname}?${output}${hash}`;
}
export function shouldBlockDirtyNavigation(
  current: { pathname: string; search: string },
  next: { pathname: string; search: string },
): boolean {
  const conversationPath = /^\/help\/conversations\/[0-9a-f-]{36}$/i;
  const draftPath = /^\/help\/submissions\/[0-9a-f-]{36}$/i;
  if (
    (conversationPath.test(current.pathname) &&
      next.pathname === '/help/request') ||
    (current.pathname === '/help/request' && draftPath.test(next.pathname)) ||
    (draftPath.test(current.pathname) && next.pathname === '/help/request')
  )
    return false;
  if (
    (conversationPath.test(current.pathname) &&
      next.pathname === '/help/verify') ||
    (current.pathname === '/help/verify' &&
      conversationPath.test(next.pathname))
  )
    return false;
  if (current.pathname !== next.pathname) return true;
  if (readHandoffCase(current.search) !== readHandoffCase(next.search))
    return true;
  const currentTopic = readTopic(current.search),
    nextTopic = readTopic(next.search);
  // Switching to staff assistance continues this issue; it does not create a new story.
  if (nextTopic === 'contact') return false;
  return currentTopic !== nextTopic;
}

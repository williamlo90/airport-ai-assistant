import { VerificationStore } from '../verification/store';
import { VerificationContext } from '../verification/context';
import { useEffect, useMemo } from 'react';
import { ChatContext } from './context';
import type { ReactNode } from 'react';
import type { AuthCoordinator } from '../../app/auth/coordinator';
import { ChatStore } from './store';
import { CaseDraftStore } from '../cases/store';
import { CaseDraftContext } from '../cases/context';
import { SubmissionContext, SubmissionStore } from '../cases/submission';
import { CaseDetailContext, CaseDetailStore } from '../cases/detail-store';
import {
  CustomerInformationContext,
  CustomerInformationStore,
} from '../cases/information-store';

export function ChatProvider({
  auth,
  scope,
  children,
}: {
  auth: AuthCoordinator | null;
  scope: string;
  children: ReactNode;
}) {
  const store = useMemo(
    () => (auth ? new ChatStore(auth, scope) : null),
    [auth, scope],
  );
  const verification = useMemo(
    () => (store ? new VerificationStore(store.auth, store) : null),
    [store],
  );
  const cases = useMemo(
    () => (store ? new CaseDraftStore(store.auth, store, scope) : null),
    [store, scope],
  );
  const submission = useMemo(
    () => (cases ? new SubmissionStore(cases.auth, cases) : null),
    [cases],
  );
  const details = useMemo(
    () => (auth ? new CaseDetailStore(auth, scope) : null),
    [auth, scope],
  );
  const information = useMemo(
    () => (auth ? new CustomerInformationStore(auth) : null),
    [auth],
  );
  useEffect(() => information?.connect(), [information]);
  useEffect(() => {
    if (!details) return;
    const off = details.connect();
    return () => {
      off();
      details.dispose();
    };
  }, [details]);
  useEffect(() => {
    if (!submission) return;
    const off = submission.connect();
    return () => {
      off();
      submission.dispose();
    };
  }, [submission]);
  useEffect(() => {
    if (!cases) return;
    const off = cases.connect();
    return () => {
      off();
      cases.dispose();
    };
  }, [cases]);
  useEffect(() => verification?.connect(), [verification]);
  useEffect(() => {
    if (!store) return;
    const off = store.connect();
    return () => {
      off();
      store.dispose();
    };
  }, [store]);
  return (
    <ChatContext value={store}>
      <VerificationContext value={verification}>
        <CaseDraftContext value={cases}>
          <SubmissionContext value={submission}>
            <CaseDetailContext value={details}>
              <CustomerInformationContext value={information}>
                {children}
              </CustomerInformationContext>
            </CaseDetailContext>
          </SubmissionContext>
        </CaseDraftContext>
      </VerificationContext>
    </ChatContext>
  );
}

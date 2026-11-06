import type { AuthCoordinator } from '../../app/auth/coordinator';
import type { BoundarySnapshot } from '../../api/contracts';
import { z } from 'zod';
import { decodeConversation } from './contracts';
import {
  decodePrepared,
  decodeAgentOutcome,
  decodeAgentStatus,
  startAnswer,
  type StartAnswer,
} from './agent-contracts';

interface RequestScope {
  boundary: BoundarySnapshot;
  signal: AbortSignal;
  isRelevant: () => boolean;
}
/** Every call is explicit. This wrapper has no timer, polling or retry policy. */
export function agentApi(auth: AuthCoordinator) {
  return {
    capabilities(scope: RequestScope) {
      return auth.request({
        ...scope,
        path: '/agent-capabilities',
        method: 'GET',
        successStatus: 200,
        decode: (value) =>
          z.object({ private_assistance: z.boolean() }).strict().parse(value),
      });
    },
    receipt(id: string, scope: RequestScope) {
      return auth.readResource({
        ...scope,
        path: `/agent-runs/${id}/receipt`,
        method: 'GET',
        successStatus: 200,
        decode: (value) => decodeAgentStatus(value, id),
      });
    },
    conversation(id: string, scope: RequestScope) {
      return auth.readResource({
        ...scope,
        path: `/conversations/${id}`,
        method: 'GET',
        successStatus: 200,
        decode: decodeConversation,
      });
    },
    start(id: string, command: StartAnswer, scope: RequestScope) {
      return auth.request({
        ...scope,
        path: `/conversations/${id}/agent-runs`,
        method: 'POST',
        body: startAnswer.parse(command),
        timeoutMs: 15000,
        successStatus: 201,
        decode: decodePrepared,
      });
    },
    resume(id: string, scope: RequestScope) {
      // Workflow deadline is 180s; the client's existing 120s maximum is kept.
      // A timeout is unknown and must never cause an automatic second resume.
      return auth.request({
        ...scope,
        path: `/agent-runs/${id}/resume`,
        method: 'POST',
        timeoutMs: 120000,
        successStatus: 200,
        decode: (value) => decodeAgentOutcome(value, id),
      });
    },
  };
}

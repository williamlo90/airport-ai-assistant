"""Shared customer/session gate for bounded database units of work."""

from contextlib import asynccontextmanager
from inspect import iscoroutine
from airport_assistant.modules.identity.auth import AuthError


class CustomerSessionLost(Exception):
    """Repository confirmed session loss after a rolled-back RLS failure."""


async def require_customer_session(tx):
    if await tx.session_active() is not True:
        raise AuthError(401,'AUTH_REQUIRED')


def run_before_commit(check):
    """No awaits are permitted after the final deadline/operation checker."""
    if check is not None:
        result=check()
        if result is not None:
            if iscoroutine(result):result.close()
            raise TypeError('before_commit must be synchronous and return None.')


@asynccontextmanager
async def customer_transaction(repository,identity,*,before_commit=None):
    if before_commit is not None and not callable(before_commit):raise TypeError('before_commit must be callable.')
    if identity.session.kind not in {'anonymous','customer'}:
        raise AuthError(403,'ACTION_FORBIDDEN')
    try:
        async with repository.transaction(identity.database_context) as tx:
            await require_customer_session(tx)
            yield tx
            await require_customer_session(tx)
            run_before_commit(before_commit)
    except CustomerSessionLost:
        raise AuthError(401,'AUTH_REQUIRED') from None

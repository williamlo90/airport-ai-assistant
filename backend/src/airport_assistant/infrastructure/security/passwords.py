"""Fixed-cost Argon2id with bounded, cancellation-safe off-loop work."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import Argon2Error

from airport_assistant.modules.identity.auth import AuthError

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16, type=Type.ID)
# Public dummy verifier for unknown accounts, not an application credential.
DUMMY = HASHER.hash("non-account dummy verifier")


def hash_password(password: str) -> str:
    if not 15 <= len(password) <= 128:
        raise ValueError("Staff password must contain 15 to 128 characters.")
    if password.casefold() in {"passwordpassword", "password123456789", "123456789012345", "1234567890123456", "abcdefghijklmnop", "qwertyuiopasdfgh"} or len(set(password)) < 5:
        raise ValueError("Choose a less predictable staff password.")
    return HASHER.hash(password)


def verify_password(password: str, encoded: str | None) -> bool:
    candidate = encoded or DUMMY
    try:
        p = extract_parameters(candidate)
        if (p.type, p.version, p.memory_cost, p.time_cost, p.parallelism, p.hash_len, p.salt_len) != (Type.ID, 19, 19456, 2, 1, 32, 16):
            candidate = DUMMY
    except (Argon2Error, ValueError):
        candidate = DUMMY
    try:
        valid = HASHER.verify(candidate, password)
        return valid and encoded is not None and candidate == encoded
    except (Argon2Error, ValueError):
        return False


# Shared across all app/service instances in this Python process. No waiting queue.
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="staff-kdf")
_SLOTS = threading.BoundedSemaphore(2)


class BoundedPasswordVerifier:
    async def verify(self, password: str, encoded: str | None) -> bool:
        if not _SLOTS.acquire(blocking=False):
            raise AuthError(503, "AUTH_UNAVAILABLE")
        try:
            future = _EXECUTOR.submit(verify_password, password, encoded)
        except BaseException:
            _SLOTS.release()
            raise
        # concurrent.futures callback runs only when the actual worker completes.
        future.add_done_callback(lambda _: _SLOTS.release())
        return await asyncio.shield(asyncio.wrap_future(future))

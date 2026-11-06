"""Password budget, CLI secrecy and explicit membership policy."""

import asyncio
import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

from airport_assistant.api.staff import LoginRequest
from airport_assistant.infrastructure.security import passwords
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.staff import Membership, StaffCredential


def test_argon2id_hashes_are_salted_and_bad_unknown_or_corrupt_credentials_fail():
    secret = "A staff test password with Unicode 海 and spaces"
    first, second = passwords.hash_password(secret), passwords.hash_password(secret)
    assert first != second and first.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert passwords.verify_password(secret, first)
    for wrong, encoded in [("wrong", first),(secret,None),(secret,"invalid-encoded"),("non-account dummy verifier",None)]:
        assert not passwords.verify_password(wrong, encoded)
    assert secret not in repr(LoginRequest(contact="test@example.com",password=secret))
    assert first not in repr(StaffCredential(uuid4(),uuid4(),first))


@pytest.mark.parametrize("value",["short", "x"*129, "password123456789", "123456789012345", "a"*20])
def test_provisioned_password_policy_rejects_invalid_values(value):
    with pytest.raises(ValueError):passwords.hash_password(value)


def test_password_length_not_truncated_and_http_input_cannot_choose_roles():
    secret = "A long staff passphrase " + "海"*80
    encoded = passwords.hash_password(secret)
    assert passwords.verify_password(secret, encoded)
    assert not passwords.verify_password(secret[:-1], encoded)
    with pytest.raises(ValidationError):LoginRequest(contact="test@example.com",password="x"*129)
    with pytest.raises(ValidationError):LoginRequest(contact="test@example.com",password=secret,roles=["admin"])


def test_cancelled_password_work_keeps_slot_until_real_thread_completion(monkeypatch):
    release, started = threading.Event(), threading.Event()
    lock = threading.Lock()
    running = maximum = 0
    def slow(*args):
        nonlocal running, maximum
        with lock:
            running += 1
            maximum = max(maximum, running)
            if running == 2:started.set()
        try:
            release.wait(5)
            return False
        finally:
            with lock:running -= 1
    monkeypatch.setattr(passwords,"verify_password",slow)
    async def scenario():
        verifier=passwords.BoundedPasswordVerifier()
        tasks=[asyncio.create_task(verifier.verify("test",None)) for _ in range(2)]
        try:
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(.005)
            assert started.is_set()
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            # An unrelated eventloop task is responsive while both KDF workers run.
            assert await asyncio.wait_for(asyncio.sleep(.001,result="responsive"),.1)=="responsive"
            with pytest.raises(AuthError) as error:await passwords.BoundedPasswordVerifier().verify("third",None)
            assert error.value.status==503 and maximum==2
        finally:
            release.set()
            await asyncio.gather(*tasks,return_exceptions=True)
            for _ in range(100):
                if running==0:break
                await asyncio.sleep(.005)
            await asyncio.sleep(.01)
        assert await verifier.verify("after release",None) is False
    asyncio.run(scenario())


@pytest.mark.parametrize("roles,action,allowed",[
    ({"admin"},"read_case",False),({"admin"},"manage_memberships",True),
    ({"cs"},"read_case",True),({"cs"},"assign_case",False),
    ({"supervisor"},"assign_case",True),({"admin","cs"},"read_case",True),
    ({"admin"},"unknown",False),
])
def test_membership_scope_is_explicit_and_default_deny(roles,action,allowed):
    org, queue = uuid4(), uuid4()
    membership=Membership(uuid4(),org,1,frozenset(roles),frozenset({queue}))
    assert membership.allows(action,org,queue) is allowed
    assert not membership.allows(action,uuid4(),queue)
    if action!="manage_memberships":assert not membership.allows(action,org,uuid4())


def test_credential_cli_refuses_noninteractive_password_without_loading_env(monkeypatch,capsys):
    from airport_assistant import staff_credential as cli
    monkeypatch.setattr("sys.argv",["staff-credential","--tenant",str(uuid4()),"--principal",str(uuid4()),"--contact",str(uuid4())])
    monkeypatch.setattr("sys.stdin.isatty",lambda:False)
    monkeypatch.setattr(cli,"load_database_settings",lambda *a:pytest.fail("must not read dotenv"))
    with pytest.raises(SystemExit) as error:cli.main()
    assert error.value.code==1 and "redacted" in capsys.readouterr().out


@pytest.mark.parametrize("warning_at",[1,2])
def test_cli_aborts_before_echo_fallback_or_loading_credentials(monkeypatch,capsys,warning_at):
    import warnings
    from getpass import GetPassWarning
    from airport_assistant import staff_credential as cli
    monkeypatch.setattr("sys.argv",["staff-credential","--tenant",str(uuid4()),"--principal",str(uuid4()),"--contact",str(uuid4())])
    monkeypatch.setattr("sys.stdin.isatty",lambda:True)
    calls=[]
    def prompt(*args):
        calls.append("prompt")
        if len(calls)==warning_at:
            warnings.warn("echo fallback",GetPassWarning)
            calls.append("fallback_read")
        return "A test password must stay hidden!"
    monkeypatch.setattr(cli,"getpass",prompt)
    monkeypatch.setattr(cli,"hash_password",lambda *a:pytest.fail("must not hash after warning"))
    monkeypatch.setattr(cli,"load_database_settings",lambda *a:pytest.fail("must not load env after warning"))
    with pytest.raises(SystemExit):cli.main()
    assert calls==["prompt"]*warning_at
    output=capsys.readouterr().out
    assert "redacted" in output and "A test password" not in output

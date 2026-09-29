"""
OpenWEC — Admin authentication tests

Two things are asserted here, and they are independent:

1. `require_admin_key` keeps the exact semantics it had before the credential
   logging was removed. It is deliberately stricter than `require_api_key`:
   it denies even when API_KEYS is unset, and it accepts *only* static keys —
   a dynamic key that works on /laps gets 401 here.

2. No credential value ever reaches stdout, stderr or the logging module.
   Not the key that was received, not the configured key set, and not any
   derived form of either.

These run with no database, no server and no network: `require_admin_key`
depends only on the settings object and the header value, so the settings are
replaced with a stub.

    pytest tests/api/test_admin_auth.py -v
"""
import importlib
import io
import logging

import pytest
from fastapi import HTTPException

from api.routers import admin


# Obviously-synthetic values. They exist only to be searched for in captured
# output — if any of them shows up there, the test fails.
STATIC_KEY_A = "SYNTHETIC-STATIC-KEY-AAAA-0001"
STATIC_KEY_B = "SYNTHETIC-STATIC-KEY-BBBB-0002"
DYNAMIC_KEY = "SYNTHETIC-DYNAMIC-KEY-CCCC-0003"
WRONG_KEY = "SYNTHETIC-WRONG-KEY-DDDD-0004"

ALL_SECRETS = (STATIC_KEY_A, STATIC_KEY_B, DYNAMIC_KEY, WRONG_KEY)


class _StubSettings:
    """Minimal stand-in for api.config.settings, with only what admin.py uses."""

    def __init__(self, keys):
        self.valid_api_keys = set(keys)


@pytest.fixture
def com_chaves(monkeypatch):
    """API_KEYS configured with two static keys."""
    monkeypatch.setattr(admin, "settings", _StubSettings({STATIC_KEY_A, STATIC_KEY_B}))


@pytest.fixture
def sem_chaves(monkeypatch):
    """API_KEYS unset — the empty-set case."""
    monkeypatch.setattr(admin, "settings", _StubSettings(set()))


# ── Semantics: unchanged by the logging removal ───────────────────

def test_valid_static_key_is_accepted(com_chaves):
    assert admin.require_admin_key(api_key=STATIC_KEY_A) == STATIC_KEY_A
    assert admin.require_admin_key(api_key=STATIC_KEY_B) == STATIC_KEY_B


def test_missing_key_is_rejected(com_chaves):
    for ausente in (None, ""):
        with pytest.raises(HTTPException) as exc:
            admin.require_admin_key(api_key=ausente)
        assert exc.value.status_code == 401


def test_wrong_key_is_rejected(com_chaves):
    with pytest.raises(HTTPException) as exc:
        admin.require_admin_key(api_key=WRONG_KEY)
    assert exc.value.status_code == 401


def test_dynamic_key_is_rejected(com_chaves):
    """A dynamic key is valid on protected routes but never on admin ones."""
    with pytest.raises(HTTPException) as exc:
        admin.require_admin_key(api_key=DYNAMIC_KEY)
    assert exc.value.status_code == 401


def test_unset_api_keys_denies_everything(sem_chaves):
    """Stricter than require_api_key, which returns True when API_KEYS is empty."""
    for tentativa in (None, "", STATIC_KEY_A, DYNAMIC_KEY):
        with pytest.raises(HTTPException) as exc:
            admin.require_admin_key(api_key=tentativa)
        assert exc.value.status_code == 401


def test_rejection_detail_carries_no_credential(com_chaves):
    with pytest.raises(HTTPException) as exc:
        admin.require_admin_key(api_key=WRONG_KEY)
    for segredo in ALL_SECRETS:
        assert segredo not in str(exc.value.detail)


# ── No credential reaches stdout, stderr or logging ───────────────

def _exercitar_todos_os_caminhos():
    """Runs every branch of require_admin_key, swallowing the 401s."""
    for tentativa in (None, "", STATIC_KEY_A, STATIC_KEY_B, DYNAMIC_KEY, WRONG_KEY):
        try:
            admin.require_admin_key(api_key=tentativa)
        except HTTPException:
            pass


def test_no_credential_in_stdout_or_stderr(com_chaves, capsys):
    _exercitar_todos_os_caminhos()
    captured = capsys.readouterr()
    saida = captured.out + captured.err
    for segredo in ALL_SECRETS:
        assert segredo not in saida
    # The configured set must not be emitted either, in any form.
    assert "valid_api_keys" not in saida
    assert "DEBUG" not in saida
    assert saida == ""


def test_no_credential_in_stdout_when_api_keys_unset(sem_chaves, capsys):
    _exercitar_todos_os_caminhos()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_no_credential_reaches_the_logging_module(com_chaves):
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    nivel_anterior = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        _exercitar_todos_os_caminhos()
    finally:
        root.removeHandler(handler)
        root.setLevel(nivel_anterior)
    registrado = buffer.getvalue()
    for segredo in ALL_SECRETS:
        assert segredo not in registrado


# ── Source-level guard against the print coming back ──────────────

def test_admin_module_has_no_print_calls():
    """A regression guard: the leak was a bare print(), and it must not return."""
    import ast

    caminho = importlib.util.find_spec("api.routers.admin").origin
    with open(caminho, encoding="utf-8") as fh:
        arvore = ast.parse(fh.read())
    chamadas = [
        no for no in ast.walk(arvore)
        if isinstance(no, ast.Call)
        and isinstance(no.func, ast.Name)
        and no.func.id == "print"
    ]
    assert chamadas == [], f"print() found at lines {[n.lineno for n in chamadas]}"


def test_admin_module_never_passes_a_credential_to_an_output_call():
    """
    No print/log call anywhere in admin.py may take `api_key`, `valid_static_keys`
    or `settings.valid_api_keys` as an argument — in full or wrapped in anything.
    """
    import ast

    nomes_sensiveis = {"api_key", "valid_static_keys", "admin_key"}
    saidas = {"print", "debug", "info", "warning", "error", "exception", "critical", "log"}

    caminho = importlib.util.find_spec("api.routers.admin").origin
    with open(caminho, encoding="utf-8") as fh:
        arvore = ast.parse(fh.read())

    ofensas = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        alvo = no.func.id if isinstance(no.func, ast.Name) else (
            no.func.attr if isinstance(no.func, ast.Attribute) else None
        )
        if alvo not in saidas:
            continue
        for arg in ast.walk(no):
            if isinstance(arg, ast.Name) and arg.id in nomes_sensiveis:
                ofensas.append((no.lineno, alvo, arg.id))
    assert ofensas == [], f"credential passed to an output call: {ofensas}"

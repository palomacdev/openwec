"""
OpenWEC — Admin authentication tests

Three things are asserted here, and they are independent:

1. Administration is authorized by ADMIN_API_KEYS and by nothing else. An
   ordinary API key is refused, including when it is the *only* thing
   configured, and including when someone has copied it into ADMIN_API_KEYS.

2. The check is fail-closed. Absent, short or self-contradictory configuration
   leaves /admin/* shut rather than open.

3. No credential value ever reaches stdout, stderr or the logging module.
   Not the value received, not the configured set, not any derived form.

These run with no database, no server and no network: `require_admin_key`
depends only on the settings object and the header value.

    pytest tests/api/test_admin_auth.py -v
"""
import importlib
import io
import logging

import pytest
from fastapi import HTTPException

from api.config import ADMIN_KEY_MIN_LENGTH, Settings
from api.routers import admin


# Obviously-synthetic values. They exist to be searched for in captured output —
# if any of them shows up there, the test fails.
ADMIN_CRED   = "SYNTHETIC-ADMIN-CREDENTIAL-AAAA-0001"
ADMIN_CRED_2 = "SYNTHETIC-ADMIN-CREDENTIAL-BBBB-0002"
COMMON_KEY   = "SYNTHETIC-COMMON-API-KEY-CCCC-0003"
DYNAMIC_KEY  = "SYNTHETIC-DYNAMIC-KEY-DDDD-0004"
WRONG_KEY    = "SYNTHETIC-WRONG-KEY-EEEE-0005"
SHORT_CRED   = "SYNTHETIC-SHORT-0006"

ALL_SECRETS = (ADMIN_CRED, ADMIN_CRED_2, COMMON_KEY,
               DYNAMIC_KEY, WRONG_KEY, SHORT_CRED)


def montar(api_keys: str = "", admin_api_keys: str = "") -> Settings:
    """
    A real Settings object, so the parsing rules under test are the ones the
    application uses. Explicit arguments take precedence over the environment
    and over any .env on disk.
    """
    return Settings(api_keys=api_keys, admin_api_keys=admin_api_keys)


@pytest.fixture
def configurado(monkeypatch):
    """The intended configuration: common keys and admin credentials, disjoint."""
    monkeypatch.setattr(admin, "settings", montar(
        api_keys=COMMON_KEY,
        admin_api_keys=f"{ADMIN_CRED},{ADMIN_CRED_2}",
    ))


@pytest.fixture
def sem_administracao(monkeypatch):
    """API keys configured, ADMIN_API_KEYS empty."""
    monkeypatch.setattr(admin, "settings", montar(api_keys=COMMON_KEY))


def negado(api_key):
    """Asserts that this header value does not authorize administration."""
    with pytest.raises(HTTPException) as exc:
        admin.require_admin_key(api_key=api_key)
    assert exc.value.status_code == 401
    return exc.value


# ── The five cases the separation has to satisfy ──────────────────

def test_chave_comum_nao_autoriza(configurado):
    """A key from API_KEYS is an API key. It is not an admin credential."""
    negado(COMMON_KEY)


def test_chave_dinamica_nao_autoriza(configurado):
    """A dynamic key works on /laps; it never works here."""
    negado(DYNAMIC_KEY)


def test_sem_chave_nao_autoriza(configurado):
    for ausente in (None, ""):
        negado(ausente)


def test_credencial_administrativa_autoriza(configurado):
    assert admin.require_admin_key(api_key=ADMIN_CRED) == ADMIN_CRED
    assert admin.require_admin_key(api_key=ADMIN_CRED_2) == ADMIN_CRED_2


def test_configuracao_vazia_nao_autoriza(sem_administracao):
    """Fail-closed: nothing is accepted, not even a well-formed credential."""
    for tentativa in (None, "", ADMIN_CRED, COMMON_KEY, DYNAMIC_KEY):
        negado(tentativa)


# ── Fail-closed, in the ways configuration actually goes wrong ────

def test_credencial_curta_e_tratada_como_ausente(monkeypatch):
    """A leftover placeholder must not be what guards /admin/*."""
    assert len(SHORT_CRED) < ADMIN_KEY_MIN_LENGTH
    monkeypatch.setattr(admin, "settings", montar(admin_api_keys=SHORT_CRED))
    negado(SHORT_CRED)


def test_chave_comum_copiada_para_admin_fecha_a_administracao(monkeypatch):
    """
    The natural mistake — reusing an API key as the admin credential — must not
    work. Dropping it means the value authorizes nothing, in either role.
    """
    monkeypatch.setattr(admin, "settings", montar(
        api_keys=COMMON_KEY,
        admin_api_keys=COMMON_KEY,
    ))
    negado(COMMON_KEY)


def test_valor_em_ambos_os_conjuntos_nao_contamina_os_demais(monkeypatch):
    """One shared value is dropped; a genuinely separate credential still works."""
    monkeypatch.setattr(admin, "settings", montar(
        api_keys=COMMON_KEY,
        admin_api_keys=f"{COMMON_KEY},{ADMIN_CRED}",
    ))
    negado(COMMON_KEY)
    assert admin.require_admin_key(api_key=ADMIN_CRED) == ADMIN_CRED


def test_separador_sujo_nao_abre_nem_quebra(monkeypatch):
    """Stray commas and spaces are parsed, not turned into empty credentials."""
    monkeypatch.setattr(admin, "settings", montar(
        admin_api_keys=f" , {ADMIN_CRED} , , ",
    ))
    assert admin.require_admin_key(api_key=ADMIN_CRED) == ADMIN_CRED
    for ausente in (None, "", " "):
        negado(ausente)


def test_apenas_virgulas_nao_autoriza(monkeypatch):
    monkeypatch.setattr(admin, "settings", montar(admin_api_keys=" , , "))
    for tentativa in (None, "", " ", ",", ADMIN_CRED):
        negado(tentativa)


# ── The settings property itself ──────────────────────────────────

def test_conjuntos_sao_disjuntos_por_construcao():
    s = montar(api_keys=f"{COMMON_KEY},{ADMIN_CRED}", admin_api_keys=ADMIN_CRED)
    assert s.valid_admin_keys == frozenset()
    assert s.valid_api_keys & s.valid_admin_keys == set()


def test_admin_vazio_por_padrao():
    assert montar().valid_admin_keys == frozenset()
    assert montar(api_keys=COMMON_KEY).valid_admin_keys == frozenset()


def test_comprimento_minimo_e_aplicado():
    limite = "x" * ADMIN_KEY_MIN_LENGTH
    curto  = "x" * (ADMIN_KEY_MIN_LENGTH - 1)
    assert montar(admin_api_keys=limite).valid_admin_keys == frozenset({limite})
    assert montar(admin_api_keys=curto).valid_admin_keys == frozenset()


# ── No credential reaches stdout, stderr or logging ───────────────

def _exercitar_todos_os_caminhos():
    """Runs every branch of require_admin_key, swallowing the 401s."""
    for tentativa in (None, "", ADMIN_CRED, ADMIN_CRED_2, COMMON_KEY,
                      DYNAMIC_KEY, WRONG_KEY, SHORT_CRED):
        try:
            admin.require_admin_key(api_key=tentativa)
        except HTTPException:
            pass


def test_recusa_nao_carrega_credencial(configurado):
    erro = negado(WRONG_KEY)
    for segredo in ALL_SECRETS:
        assert segredo not in str(erro.detail)


def test_recusa_nao_revela_o_estado_da_configuracao(configurado, monkeypatch):
    """
    Wrong credential and unconfigured server must look identical from outside,
    or the error message becomes a probe for whether administration exists.
    """
    com = negado(WRONG_KEY)
    monkeypatch.setattr(admin, "settings", montar(api_keys=COMMON_KEY))
    sem = negado(WRONG_KEY)
    assert (com.status_code, com.detail) == (sem.status_code, sem.detail)


def test_nenhuma_credencial_em_stdout_ou_stderr(configurado, capsys):
    _exercitar_todos_os_caminhos()
    captured = capsys.readouterr()
    saida = captured.out + captured.err
    for segredo in ALL_SECRETS:
        assert segredo not in saida
    assert "valid_admin_keys" not in saida
    assert "valid_api_keys" not in saida
    assert saida == ""


def test_nenhuma_credencial_em_stdout_com_configuracao_vazia(sem_administracao, capsys):
    _exercitar_todos_os_caminhos()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_nenhuma_credencial_chega_ao_modulo_de_logging(configurado):
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


# ── Source-level guards ───────────────────────────────────────────

def _arvore_do_admin():
    import ast
    caminho = importlib.util.find_spec("api.routers.admin").origin
    with open(caminho, encoding="utf-8") as fh:
        return ast.parse(fh.read()), ast


def test_admin_nao_consulta_o_conjunto_comum():
    """
    The regression that matters. `require_admin_key` once read
    `settings.valid_api_keys`, which made every API key an admin credential.
    No attribute of that name may be read anywhere in the module.
    """
    arvore, ast = _arvore_do_admin()
    leituras = [
        no.lineno for no in ast.walk(arvore)
        if isinstance(no, ast.Attribute) and no.attr == "valid_api_keys"
    ]
    assert leituras == [], f"valid_api_keys read at lines {leituras}"


def test_admin_module_has_no_print_calls():
    """A regression guard: an earlier leak was a bare print(), and it must not return."""
    arvore, ast = _arvore_do_admin()
    chamadas = [
        no for no in ast.walk(arvore)
        if isinstance(no, ast.Call)
        and isinstance(no.func, ast.Name)
        and no.func.id == "print"
    ]
    assert chamadas == [], f"print() found at lines {[n.lineno for n in chamadas]}"


def test_admin_module_never_passes_a_credential_to_an_output_call():
    """
    No print/log call anywhere in admin.py may take `api_key`, `credentials`,
    `presented` or `admin_key` as an argument — in full or wrapped in anything.
    """
    nomes_sensiveis = {"api_key", "credentials", "presented", "expected", "admin_key"}
    saidas = {"print", "debug", "info", "warning", "error", "exception", "critical", "log"}

    arvore, ast = _arvore_do_admin()

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

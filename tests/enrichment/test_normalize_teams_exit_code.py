"""
Que uma fusão de times que falha deixe de sair com código 0.

O script sempre fez cada fusão na sua própria transação, e isso continua: o
que mudou é que a falha chega a quem chamou. Estes testes cobrem as duas
coisas — o código de saída e o que ficou gravado — porque um código certo
sobre um banco errado não valeria nada.

Nada aqui toca `public`: o schema é do módulo e cai inteiro no fim, pelas
mesmas comportas das demais suítes que escrevem. Ver conftest.py.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

psycopg2 = pytest.importorskip("psycopg2")

from database.enrichment import normalize_teams as nt  # noqa: E402


# ── Apoio ────────────────────────────────────────────────────

def conectar(schema: dict, autocommit: bool = True):
    conn = psycopg2.connect(connect_timeout=5, **schema["db"])
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema["schema"]}", public')
    conn.autocommit = autocommit
    return conn


def config_do_schema(schema: dict) -> dict:
    """DB_CONFIG apontando para o schema do módulo, pelo `options` da conexão."""
    cfg = dict(schema["db"])
    cfg["options"] = f'-c search_path="{schema["schema"]}",public'
    return cfg


@pytest.fixture
def banco(schema_times):
    conn = conectar(schema_times)
    try:
        with conn.cursor() as cur:
            for tabela in ("entries", "cars", "teams"):
                cur.execute(f"TRUNCATE {tabela} CASCADE")
        yield conn
    finally:
        conn.close()


def time(conn, nome: str) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", (nome,))
        return cur.fetchone()[0]


def carro(conn, numero: str, team_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO cars (number, team_id, vehicle) "
                    "VALUES (%s, %s, 'Oreca 07-Gibson') RETURNING id",
                    (numero, team_id))
        return cur.fetchone()[0]


def inscricao(conn, team_id: int, numero: str = "38") -> int:
    """Uma linha de `entries`, que também aponta para `teams` e que o script ignora."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO entries (event_id, team_id, number) "
                    "VALUES (1, %s, %s) RETURNING id", (team_id, numero))
        return cur.fetchone()[0]


def times(conn) -> set[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM teams")
        return {linha[0] for linha in cur.fetchall()}


def time_do_carro(conn, car_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT team_id FROM cars WHERE id = %s", (car_id,))
        return cur.fetchone()[0]


def executa(schema_times, monkeypatch, dry_run: bool = False) -> tuple[int, str]:
    """`run` contra o schema do módulo, devolvendo o código e a saída."""
    monkeypatch.setattr(nt, "DB_CONFIG", config_do_schema(schema_times))
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        codigo = nt.run(dry_run=dry_run)
    return codigo, buffer.getvalue()


# ── Sucesso ──────────────────────────────────────────────────

def test_sem_duplicatas_devolve_zero(banco, schema_times, monkeypatch):
    sozinho = time(banco, "AF Corse")
    meu_carro = carro(banco, "51", sozinho)

    codigo, saida = executa(schema_times, monkeypatch)

    assert codigo == 0
    assert "Duplicate groups: 0" in saida
    assert times(banco) == {sozinho}
    assert time_do_carro(banco, meu_carro) == sozinho


def test_sucesso_completo_devolve_zero_e_funde(banco, schema_times, monkeypatch):
    canonico = time(banco, "AF Corse")
    duplicado = time(banco, "AF CORSE")
    meu_carro = carro(banco, "51", duplicado)

    codigo, saida = executa(schema_times, monkeypatch)

    assert codigo == 0
    assert "[FAILURES]" not in saida
    assert "Errors:  0" in saida
    assert times(banco) == {canonico}, "o duplicado devia ter sido apagado"
    assert time_do_carro(banco, meu_carro) == canonico


def test_dry_run_devolve_zero_e_nao_escreve(banco, schema_times, monkeypatch):
    canonico = time(banco, "Jota")
    duplicado = time(banco, "JOTA")
    meu_carro = carro(banco, "38", duplicado)

    codigo, saida = executa(schema_times, monkeypatch, dry_run=True)

    assert codigo == 0
    assert "[DRY RUN]" in saida
    assert times(banco) == {canonico, duplicado}
    assert time_do_carro(banco, meu_carro) == duplicado


# ── Falha parcial ────────────────────────────────────────────

@pytest.fixture
def duas_fusoes(banco):
    """
    Duas fusões em ordem alfabética de canônico: 'AF Corse' passa, 'Jota' não.

    A segunda falha porque `entries` também referencia `teams` e o script só
    reaponta `cars` — a política que esta PR não muda.
    """
    dados = {
        "af_canonico": time(banco, "AF Corse"),
        "af_duplicado": time(banco, "AF CORSE"),
        "jota_canonico": time(banco, "Jota"),
        "jota_duplicado": time(banco, "JOTA"),
    }
    dados["carro_af"] = carro(banco, "51", dados["af_duplicado"])
    dados["carro_jota"] = carro(banco, "38", dados["jota_duplicado"])
    dados["inscricao"] = inscricao(banco, dados["jota_duplicado"])
    return dados


def test_falha_parcial_devolve_um(banco, schema_times, monkeypatch, duas_fusoes):
    codigo, saida = executa(schema_times, monkeypatch)

    assert codigo == 1, "uma fusão falhou e o processo saiu dizendo que deu certo"
    assert "Errors:  1" in saida
    assert "[FAILURES] 1 merge(s)" in saida


def test_a_falha_nomeia_origem_destino_e_motivo(banco, schema_times, monkeypatch,
                                                duas_fusoes):
    _, saida = executa(schema_times, monkeypatch)

    esperado = (f"  team {duas_fusoes['jota_duplicado']} -> "
                f"{duas_fusoes['jota_canonico']}: ")
    linha = next((l for l in saida.splitlines() if l.startswith(esperado)), None)
    assert linha is not None, f"nao encontrei '{esperado}' em:\n{saida}"
    assert "entries_team_id_fkey" in linha, "o motivo nao identifica a chave"
    assert "\n" not in linha


def test_a_fusao_anterior_continua_confirmada(banco, schema_times, monkeypatch,
                                              duas_fusoes):
    """A falha da segunda não pode alcançar a primeira, que já comitou."""
    executa(schema_times, monkeypatch)

    assert duas_fusoes["af_duplicado"] not in times(banco)
    assert time_do_carro(banco, duas_fusoes["carro_af"]) == duas_fusoes["af_canonico"]


def test_a_fusao_que_falhou_e_desfeita_inteira(banco, schema_times, monkeypatch,
                                               duas_fusoes):
    """O UPDATE de `cars` volta atrás junto com o DELETE que foi recusado."""
    executa(schema_times, monkeypatch)

    assert duas_fusoes["jota_duplicado"] in times(banco)
    assert time_do_carro(banco, duas_fusoes["carro_jota"]) == \
        duas_fusoes["jota_duplicado"], "ficou meio merge: o carro foi reapontado"


def test_a_inscricao_nao_e_tocada(banco, schema_times, monkeypatch, duas_fusoes):
    """A política fica como está: `entries` não é reapontada nem apagada."""
    executa(schema_times, monkeypatch)

    with banco.cursor() as cur:
        cur.execute("SELECT team_id FROM entries WHERE id = %s",
                    (duas_fusoes["inscricao"],))
        assert cur.fetchone()[0] == duas_fusoes["jota_duplicado"]


# ── Falha no commit e no rollback ────────────────────────────

class ConexaoQueFalha:
    """A conexão de verdade, com `commit` ou `rollback` quebrados."""

    def __init__(self, real, quebrar_commit=False, quebrar_rollback=False):
        self._real = real
        self.quebrar_commit = quebrar_commit
        self.quebrar_rollback = quebrar_rollback
        self.rollbacks = 0

    def commit(self):
        if self.quebrar_commit:
            raise psycopg2.OperationalError("conexao caiu no commit")
        self._real.commit()

    def rollback(self):
        self.rollbacks += 1
        if self.quebrar_rollback:
            raise psycopg2.InterfaceError("connection already closed")
        self._real.rollback()

    def __getattr__(self, nome):
        return getattr(self._real, nome)


def executa_com(schema_times, monkeypatch, conexao) -> tuple[int, str]:
    monkeypatch.setattr(nt, "DB_CONFIG", config_do_schema(schema_times))
    monkeypatch.setattr(nt.psycopg2, "connect", lambda **kw: conexao)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        codigo = nt.run(dry_run=False)
    return codigo, buffer.getvalue()


def test_falha_no_commit_vira_falha_relatada(banco, schema_times, monkeypatch):
    canonico = time(banco, "Jota")
    duplicado = time(banco, "JOTA")
    meu_carro = carro(banco, "38", duplicado)

    real = conectar(schema_times, autocommit=False)
    conexao = ConexaoQueFalha(real, quebrar_commit=True)
    try:
        codigo, saida = executa_com(schema_times, monkeypatch, conexao)
    finally:
        if not real.closed:
            real.rollback()
            real.close()

    assert codigo == 1
    assert "conexao caiu no commit" in saida
    assert f"team {duplicado} -> {canonico}" in saida
    assert conexao.rollbacks >= 1, "o commit falhou e nada foi desfeito"
    assert times(banco) == {canonico, duplicado}
    assert time_do_carro(banco, meu_carro) == duplicado


def test_falha_no_rollback_nao_esconde_o_erro_original(banco, schema_times,
                                                       monkeypatch):
    """
    Os dois quebram. O que não pode acontecer é o segundo apagar o primeiro.
    """
    af_canonico = time(banco, "AF Corse")
    af_duplicado = time(banco, "AF CORSE")
    time(banco, "Jota")
    time(banco, "JOTA")

    real = conectar(schema_times, autocommit=False)
    conexao = ConexaoQueFalha(real, quebrar_commit=True, quebrar_rollback=True)
    try:
        codigo, saida = executa_com(schema_times, monkeypatch, conexao)
    finally:
        if not real.closed:
            real.close()

    assert codigo == 1
    assert "conexao caiu no commit" in saida, "o erro original sumiu"
    assert "[ABORT]" in saida
    assert "connection already closed" in saida
    assert f"team {af_duplicado} -> {af_canonico}" in saida
    assert "o rollback tambem falhou" in saida
    assert "(a execucao parou antes de tentar as fusoes restantes)" in saida
    # A segunda fusão não foi tentada: uma falha, não duas.
    assert "[FAILURES] 1 merge(s)" in saida


# ── A saída, e o console que não fala Unicode ────────────────

def test_a_saida_atravessa_um_console_cp1252(banco, schema_times, monkeypatch,
                                             duas_fusoes):
    """
    O `<-` no lugar da seta: com a seta, isto levantava UnicodeEncodeError
    antes de qualquer fusão ser tentada.
    """
    monkeypatch.setattr(nt, "DB_CONFIG", config_do_schema(schema_times))
    # A conexão é desta suíte, não de `run`: se um caractere impossível voltar,
    # `run` levanta no meio e a conexão dele vazaria aberta, segurando `teams`
    # contra o TRUNCATE do próximo teste.
    ligacao = conectar(schema_times, autocommit=False)
    monkeypatch.setattr(nt.psycopg2, "connect", lambda **kw: ligacao)
    bruto = io.BytesIO()
    console = io.TextIOWrapper(bruto, encoding="cp1252", newline="")
    try:
        with redirect_stdout(console):
            codigo = nt.run(dry_run=False)
            console.flush()
    finally:
        if not ligacao.closed:
            ligacao.rollback()
            ligacao.close()

    assert codigo == 1
    assert b"merge <-" in bruto.getvalue()


# ── O processo ───────────────────────────────────────────────

def roda_como_processo(schema_times, *argumentos) -> subprocess.CompletedProcess:
    """
    `python -m database.enrichment.normalize_teams`, que é a invocação que a
    documentação passa a indicar. `PGOPTIONS` leva a sessão para o schema do
    módulo sem que o script precise saber disso.
    """
    ambiente = dict(os.environ)
    ambiente.update({
        "DB_HOST": schema_times["db"]["host"],
        "DB_PORT": str(schema_times["db"]["port"]),
        "DB_NAME": schema_times["db"]["dbname"],
        "DB_USER": schema_times["db"]["user"],
        "DB_PASSWORD": schema_times["db"]["password"],
        "PGOPTIONS": f'-c search_path="{schema_times["schema"]}",public',
    })
    return subprocess.run(
        [sys.executable, "-m", "database.enrichment.normalize_teams", *argumentos],
        cwd=str(RAIZ), env=ambiente, capture_output=True, text=True, timeout=120)


def test_o_modulo_roda_por_dash_m(banco, schema_times):
    """A invocação do arquivo falha em `from database.db import`; esta não."""
    saiu = roda_como_processo(schema_times, "--dry-run")
    assert saiu.returncode == 0, saiu.stderr
    assert "Phase 4" in saiu.stdout
    assert "ModuleNotFoundError" not in saiu.stderr


def test_o_processo_sai_zero_quando_tudo_passa(banco, schema_times):
    canonico = time(banco, "AF Corse")
    duplicado = time(banco, "AF CORSE")
    carro(banco, "51", duplicado)

    saiu = roda_como_processo(schema_times)
    assert saiu.returncode == 0, saiu.stdout + saiu.stderr
    assert times(banco) == {canonico}


def test_o_processo_sai_um_quando_uma_fusao_falha(banco, schema_times, duas_fusoes):
    saiu = roda_como_processo(schema_times)
    assert saiu.returncode == 1, saiu.stdout + saiu.stderr
    assert "[FAILURES]" in saiu.stdout
    assert duas_fusoes["af_duplicado"] not in times(banco)
    assert duas_fusoes["jota_duplicado"] in times(banco)

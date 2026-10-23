"""
That no path through merge_drivers.py can merge a driver.

These tests never touch a database. The connection is a fake that records
every statement, so the assertion is **that no write statement is issued** —
not that counts happened to stay the same afterwards. A test that only
compares counts would pass against a script that wrote and rolled back, and
rolling back is not what is being promised here.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from database.enrichment import merge_drivers as md  # noqa: E402

ESCRITA = re.compile(r"\b(UPDATE|DELETE|INSERT|TRUNCATE|ALTER|DROP|CREATE)\b",
                     re.IGNORECASE)

# Os dois pares reais que o arquivo versionado traz.
NICOLAS, NIC = 419822, 30285


class CursorFalso:
    """Registra tudo o que executa e devolve o que a descoberta espera."""

    def __init__(self, registro: list[str], linhas: list[dict] | None = None):
        self.registro = registro
        self.linhas = linhas if linhas is not None else []
        self.fechado = False

    def execute(self, sql, params=None):
        self.registro.append(sql)

    def fetchall(self):
        return self.linhas

    def fetchone(self):
        return self.linhas[0] if self.linhas else None

    def close(self):
        self.fechado = True


class ConexaoFalsa:
    def __init__(self, registro: list[str], linhas=None):
        self.registro = registro
        self.linhas = linhas
        self.commits = 0
        self.rollbacks = 0
        self.fechada = False
        self.cursores: list[CursorFalso] = []

    def cursor(self, *a, **k):
        c = CursorFalso(self.registro, self.linhas)
        self.cursores.append(c)
        return c

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.fechada = True


CANDIDATO = {
    "wec_id": 111, "wec_first": "Jonny", "wec_last": "Adam",
    "wec_country": None, "wec_imsa": None,
    "imsa_id": 222, "imsa_first": "Jonathan", "imsa_last": "Adam",
    "imsa_country": "GBR", "imsa_imsa": 42, "rating": "Platinum",
}


@pytest.fixture
def ligacao(monkeypatch, tmp_path):
    """Conexão falsa, e o arquivo de revisão redirecionado para o tmp."""
    registro: list[str] = []
    conexoes: list[ConexaoFalsa] = []

    def conectar(**_):
        c = ConexaoFalsa(registro, [dict(CANDIDATO)])
        conexoes.append(c)
        return c

    monkeypatch.setattr(md.psycopg2, "connect", conectar)
    monkeypatch.setattr(md, "REVIEW_FILE", tmp_path / "needs_review.json")

    class Ligacao:
        sql = registro
        conns = conexoes

        @property
        def escritas(self):
            return [s for s in registro if ESCRITA.search(s)]

    return Ligacao()


# ── Nenhum caminho escreve ────────────────────────────────────

def test_execucao_padrao_nao_altera_dados(ligacao):
    assert md.run() == 0
    assert ligacao.escritas == []
    assert all(c.commits == 0 for c in ligacao.conns)


def test_dry_run_nao_altera_dados(ligacao):
    assert md.run(dry_run=True) == 0
    assert ligacao.escritas == []
    assert not md.REVIEW_FILE.exists()


def test_execucao_padrao_nao_escreve_o_arquivo_de_revisao(ligacao):
    """
    Mudou nesta revisão: antes, rodar sem flag nenhuma reescrevia o arquivo
    versionado. Agora só `--refresh-review-file` escreve.
    """
    assert md.run() == 0
    assert not md.REVIEW_FILE.exists()


def test_execucao_padrao_preserva_arquivo_existente(ligacao):
    md.write_review_file([_decisao(111, 222, md.SKIP)], md.REVIEW_FILE)
    antes = md.REVIEW_FILE.read_bytes()
    assert md.run() == 0
    assert md.REVIEW_FILE.read_bytes() == antes


def test_review_nao_altera_nada(ligacao):
    md.write_review_file([_decisao(111, 222, md.SKIP)], md.REVIEW_FILE)
    antes = md.REVIEW_FILE.read_bytes()
    assert md.run(review=True) == 0
    assert md.REVIEW_FILE.read_bytes() == antes
    assert ligacao.sql == []            # nem consultou o banco


def test_refresh_altera_somente_o_json(ligacao):
    assert md.run(refresh_review_file=True) == 0
    assert md.REVIEW_FILE.exists()
    assert ligacao.escritas == []
    assert all(c.commits == 0 for c in ligacao.conns)


def test_refresh_normaliza_merge_legado(ligacao):
    md.write_review_file([_decisao(111, 222, md.LEGACY_MERGE,
                                   merge_from=111, merge_into=222)], md.REVIEW_FILE)
    assert md.run(refresh_review_file=True) == 0
    gravado = json.loads(md.REVIEW_FILE.read_text(encoding="utf-8"))
    assert gravado[0]["action"] == md.PENDING
    assert gravado[0]["merge_from"] is None and gravado[0]["merge_into"] is None


def test_refresh_nao_sobrescreve_arquivo_invalido(ligacao):
    md.REVIEW_FILE.write_text("{quebrado", encoding="utf-8")
    antes = md.REVIEW_FILE.read_bytes()
    assert md.run(refresh_review_file=True) == 2
    assert md.REVIEW_FILE.read_bytes() == antes


def test_escrita_falha_sem_destruir_o_anterior(ligacao, monkeypatch):
    """Escrita atômica: o arquivo anterior sobrevive a uma falha no meio."""
    md.write_review_file([_decisao(111, 222, md.SKIP)], md.REVIEW_FILE)
    antes = md.REVIEW_FILE.read_bytes()

    def explode(*a, **k):
        raise OSError("disco cheio")

    monkeypatch.setattr(md.os, "replace", explode)
    with pytest.raises(OSError):
        md.write_review_file([_decisao(1, 2, md.PENDING)], md.REVIEW_FILE)
    assert md.REVIEW_FILE.read_bytes() == antes
    assert not md.REVIEW_FILE.with_name(md.REVIEW_FILE.name + ".tmp").exists()


@pytest.mark.parametrize("kwargs,trecho", [
    ({"review": True, "refresh_review_file": True}, "pick one"),
    ({"review": True, "apply_reviewed": True}, "pick one"),
    ({"refresh_review_file": True, "apply_reviewed": True}, "pick one"),
    ({"dry_run": True, "refresh_review_file": True}, "contradict"),
    ({"dry_run": True, "apply_reviewed": True}, "contradict"),
])
def test_combinacoes_incompativeis_sao_recusadas(ligacao, capsys, kwargs, trecho):
    assert md.run(**kwargs) == 2
    assert trecho in capsys.readouterr().out
    assert ligacao.escritas == []


def test_apply_reviewed_nao_funde(ligacao, capsys):
    md.write_review_file([_decisao(NICOLAS, NIC, "pending")], md.REVIEW_FILE)
    codigo = md.run(apply_reviewed=True)
    assert codigo == 2
    assert ligacao.escritas == []
    assert "[BLOCKED]" in capsys.readouterr().out


def test_apply_merges_sempre_levanta():
    with pytest.raises(md.MergeBlocked) as exc:
        md.apply_merges([_decisao(NICOLAS, NIC, "pending")])
    assert "blocked" in str(exc.value).lower()
    assert "Nothing was written" in str(exc.value)


def test_nenhuma_heuristica_de_prefixo_funde(ligacao):
    """
    O candidato tem `LEFT(first,4)` coincidente — era o que a versão antiga
    chamava de fusão segura e aplicava sozinha.
    """
    assert md.run(refresh_review_file=True) == 0
    gravado = json.loads(md.REVIEW_FILE.read_text(encoding="utf-8"))
    assert [d["action"] for d in gravado] == [md.PENDING]
    assert ligacao.escritas == []


def test_o_modulo_nao_contem_mais_sql_destrutivo():
    """
    A garantia mais forte não é uma verificação de flag: as instruções não
    estão mais no arquivo.
    """
    fonte = Path(md.__file__).read_text(encoding="utf-8")
    codigo = "\n".join(l for l in fonte.splitlines()
                       if not l.lstrip().startswith("#"))
    corpo = codigo.split('"""', 2)[-1]          # fora da docstring do módulo
    for proibido in ("UPDATE result_drivers", "UPDATE laps", "DELETE FROM drivers"):
        assert proibido not in corpo, f"{proibido} voltou ao módulo"


# ── O arquivo de revisão ──────────────────────────────────────

def _decisao(a: int, b: int, acao: str, **extra) -> dict:
    d = {"wec_id": a, "wec_name": "A", "imsa_id": b, "imsa_name": "B",
         "country": None, "rating": None, "action": acao,
         "merge_from": None, "merge_into": None}
    d.update(extra)
    return d


def test_arquivo_legado_com_merge_nao_autoriza(ligacao):
    md.write_review_file([_decisao(NICOLAS, NIC, md.LEGACY_MERGE)], md.REVIEW_FILE)
    decisoes = md.read_review_file(md.REVIEW_FILE)
    assert decisoes[0]["action"] == md.LEGACY_MERGE
    assert md.is_approved(decisoes[0]) is False
    assert md.run(apply_reviewed=True) == 2
    assert ligacao.escritas == []


def test_merge_com_direcao_explicita_ainda_nao_autoriza(ligacao):
    """Nem nomear a direção basta: aplicar está bloqueado de todo modo."""
    d = _decisao(NICOLAS, NIC, md.LEGACY_MERGE,
                 merge_from=NICOLAS, merge_into=NIC)
    md.write_review_file([d], md.REVIEW_FILE)
    assert md.is_approved(md.read_review_file(md.REVIEW_FILE)[0]) is False
    assert md.run(apply_reviewed=True) == 2
    assert ligacao.escritas == []


def test_candidato_novo_entra_como_pending(ligacao):
    md.run(refresh_review_file=True)
    gravado = json.loads(md.REVIEW_FILE.read_text(encoding="utf-8"))
    assert gravado[0]["action"] == md.PENDING
    assert gravado[0]["merge_from"] is None and gravado[0]["merge_into"] is None


def test_decisao_existente_nao_e_sobrescrita_pela_descoberta(ligacao):
    md.write_review_file([_decisao(111, 222, md.SKIP)], md.REVIEW_FILE)
    md.run(refresh_review_file=True)
    gravado = json.loads(md.REVIEW_FILE.read_text(encoding="utf-8"))
    assert gravado[0]["action"] == md.SKIP


@pytest.mark.parametrize("acao", ["pending", "skip"])
def test_pending_e_skip_nunca_escrevem(ligacao, acao):
    md.write_review_file([_decisao(NICOLAS, NIC, acao)], md.REVIEW_FILE)
    assert md.run(apply_reviewed=True) == 2
    assert ligacao.escritas == []


# ── Validação ─────────────────────────────────────────────────

def test_json_invalido_falha_em_seguranca(ligacao):
    md.REVIEW_FILE.write_text("{nao e json", encoding="utf-8")
    with pytest.raises(md.ReviewFileInvalid):
        md.read_review_file(md.REVIEW_FILE)
    assert md.run(apply_reviewed=True) == 2
    assert ligacao.escritas == []


def test_arquivo_que_nao_e_utf8_falha_em_seguranca(ligacao):
    """O arquivo versionado antigo era cp1252."""
    md.REVIEW_FILE.write_bytes(b'[{"wec_name": "Ren\xe9 Binder"}]')
    with pytest.raises(md.ReviewFileInvalid) as exc:
        md.read_review_file(md.REVIEW_FILE)
    assert "UTF-8" in str(exc.value)
    assert md.run(apply_reviewed=True) == 2
    assert ligacao.escritas == []


def test_utf8_preserva_acentos(ligacao):
    d = _decisao(1, 2, md.PENDING, wec_name="René Binder", imsa_name="Timothé Buret")
    md.write_review_file([d], md.REVIEW_FILE)
    lido = md.read_review_file(md.REVIEW_FILE)[0]
    assert lido["wec_name"] == "René Binder"
    assert lido["imsa_name"] == "Timothé Buret"
    assert b"\xc3\xa9" in md.REVIEW_FILE.read_bytes()


def test_topo_precisa_ser_lista(ligacao):
    md.REVIEW_FILE.write_text('{"wec_id": 1}', encoding="utf-8")
    with pytest.raises(md.ReviewFileInvalid):
        md.read_review_file(md.REVIEW_FILE)


@pytest.mark.parametrize("ruim", [
    {"wec_id": 0, "imsa_id": 2, "action": "pending"},
    {"wec_id": -1, "imsa_id": 2, "action": "pending"},
    {"wec_id": "1", "imsa_id": 2, "action": "pending"},
    {"wec_id": True, "imsa_id": 2, "action": "pending"},
    {"wec_id": None, "imsa_id": 2, "action": "pending"},
    {"imsa_id": 2, "action": "pending"},
])
def test_ids_invalidos_sao_rejeitados(ligacao, ruim):
    md.REVIEW_FILE.write_text(json.dumps([ruim]), encoding="utf-8")
    with pytest.raises(md.ReviewFileInvalid):
        md.read_review_file(md.REVIEW_FILE)


def test_piloto_nao_pode_ser_origem_e_destino(ligacao):
    md.REVIEW_FILE.write_text(
        json.dumps([{"wec_id": 7, "imsa_id": 7, "action": "pending"}]), encoding="utf-8")
    with pytest.raises(md.ReviewFileInvalid) as exc:
        md.read_review_file(md.REVIEW_FILE)
    assert "both sides" in str(exc.value)


def test_par_repetido_e_rejeitado(ligacao):
    md.REVIEW_FILE.write_text(json.dumps([
        _decisao(1, 2, md.PENDING), _decisao(2, 1, md.PENDING)]), encoding="utf-8")
    with pytest.raises(md.ReviewFileInvalid) as exc:
        md.read_review_file(md.REVIEW_FILE)
    assert "more than once" in str(exc.value)


def test_acao_desconhecida_e_rejeitada(ligacao):
    md.REVIEW_FILE.write_text(json.dumps([_decisao(1, 2, "aprovado")]), encoding="utf-8")
    with pytest.raises(md.ReviewFileInvalid) as exc:
        md.read_review_file(md.REVIEW_FILE)
    assert "unknown action" in str(exc.value)


def test_ausencia_de_acao_nao_e_aprovacao(ligacao):
    sem = {"wec_id": 1, "imsa_id": 2}
    md.REVIEW_FILE.write_text(json.dumps([sem]), encoding="utf-8")
    with pytest.raises(md.ReviewFileInvalid) as exc:
        md.read_review_file(md.REVIEW_FILE)
    assert "not approval" in str(exc.value)


@pytest.mark.parametrize("desde,para", [
    (True, 2), (1, True), (False, 2), (1, False),
    ("1", 2), (1, "2"), (0, 2), (1, 0), (-1, 2), (1, -2), (1.0, 2), (1, 2.0),
])
def test_direcao_com_tipo_errado_e_rejeitada(ligacao, desde, para):
    """
    `True == 1` e `{True, 2} == {1, 2}`, entao comparar a direcao com o par
    sem checar o tipo aceitaria um booleano como id.
    """
    d = _decisao(1, 2, md.PENDING, merge_from=desde, merge_into=para)
    md.REVIEW_FILE.write_text(json.dumps([d]), encoding="utf-8")
    with pytest.raises(md.ReviewFileInvalid) as exc:
        md.read_review_file(md.REVIEW_FILE)
    assert "positive integer" in str(exc.value)


@pytest.mark.parametrize("desde,para", [(1, None), (None, 2), (1, 1), (1, 9), (9, 2)])
def test_direcao_inconsistente_e_rejeitada(ligacao, desde, para):
    d = _decisao(1, 2, md.PENDING, merge_from=desde, merge_into=para)
    md.REVIEW_FILE.write_text(json.dumps([d]), encoding="utf-8")
    with pytest.raises(md.ReviewFileInvalid):
        md.read_review_file(md.REVIEW_FILE)


def test_direcao_coerente_e_aceita_mas_nao_aprova(ligacao):
    d = _decisao(1, 2, md.PENDING, merge_from=2, merge_into=1)
    md.REVIEW_FILE.write_text(json.dumps([d]), encoding="utf-8")
    lido = md.read_review_file(md.REVIEW_FILE)[0]
    assert (lido["merge_from"], lido["merge_into"]) == (2, 1)
    assert md.is_approved(lido) is False


# ── Falha não deixa estado pendente ───────────────────────────

def test_falha_de_leitura_fecha_a_conexao(ligacao):
    md.REVIEW_FILE.write_text("{quebrado", encoding="utf-8")
    assert md.run(refresh_review_file=True) == 2
    assert all(c.fechada for c in ligacao.conns)
    assert all(c.commits == 0 for c in ligacao.conns)
    assert ligacao.escritas == []


def test_descoberta_fecha_a_conexao(ligacao):
    md.run()
    assert all(c.fechada for c in ligacao.conns)


# ── O arquivo versionado de verdade ───────────────────────────

ARQUIVO_REAL = RAIZ / "database" / "enrichment" / "needs_review.json"


def test_o_arquivo_versionado_e_utf8_valido():
    ARQUIVO_REAL.read_text(encoding="utf-8")       # levanta se não for


def test_os_quinze_pares_versionados_seguem_sem_autorizacao():
    decisoes = md.read_review_file(ARQUIVO_REAL)
    assert len(decisoes) == 15
    assert {d["action"] for d in decisoes} == {md.PENDING}
    assert not any(md.is_approved(d) for d in decisoes)
    assert all(d["merge_from"] is None and d["merge_into"] is None for d in decisoes)


def test_nicolas_minassian_nao_pode_ser_aplicado():
    decisoes = md.read_review_file(ARQUIVO_REAL)
    par = [d for d in decisoes if "Minassian" in d["wec_name"]]
    assert len(par) == 1
    assert (par[0]["wec_id"], par[0]["imsa_id"]) == (NICOLAS, NIC)
    assert par[0]["action"] == md.PENDING
    assert md.is_approved(par[0]) is False
    with pytest.raises(md.MergeBlocked):
        md.apply_merges(par)


def test_o_arquivo_versionado_nao_tem_nenhum_merge():
    bruto = json.loads(ARQUIVO_REAL.read_text(encoding="utf-8"))
    assert md.LEGACY_MERGE not in {d.get("action") for d in bruto}

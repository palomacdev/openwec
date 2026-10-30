"""
O manifesto de resolucoes, e quando ele nao se aplica.

Uma entrada vale para **uma publicacao, um carro e um slot** — nunca para uma
grafia. E deixa de valer sozinha quando o digest, a URL, o slot, o carro, a
grafia daquele slot ou o **conjunto de candidatos** mudam, porque qualquer um
deles muda a pergunta que foi respondida.

Sem banco: o validador e puro, e a integracao e exercitada com um cursor que
responde por roteiro.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from database.recovery import recover_classification as rc   # noqa: E402
from database.recovery.driver_manifest import (              # noqa: E402
    Manifest, ManifestRefused, SlotContext, load,
)

sys.path.insert(0, str(RAIZ / "database" / "loader"))
from row_identity import DriverOutcome  # noqa: E402

# Um identificador de cluster qualquer: o que importa e que a
# resolucao so vale para ele. O numero real de qualquer
# instalacao nao pertence a um teste.
CLUSTER = "1234567890123456789"
DIGEST = "2471776f52d6f1bcf741038e2d1c29690dc5162102588d5f0ed9ee304e4efa04"
URL = ("https://elms.alkamelsystems.com/Results/08_2013/02_IMOLA/"
       "07_EUROPEAN%20LE%20MANS%20SERIES/201305181600_Race/03_Race/"
       "05_Classification_Race_Race.CSV")

# Os cinco destinos determinados pela politica, com os ids reais do acervo.
DESTINOS = [
    ("Tom", "KIMBER-SMITH", [6179, 342905], 342905, "R1 serie (ELMS)"),
    ("Jean Baptiste", "LAHAYE", [1441, 332377], 332377, "R1 serie (ELMS)"),
    ("Horst", "FELBERMAYR JR", [5209, 330133], 330133, "R1 serie (ELMS)"),
    ("Hugo DE", "SADELEER", [17006, 353943], 17006, "R4 vinculos"),
    ("Alexander", "TALKA JR", [4953, 340995], 340995, "R4 vinculos"),
]


def entrada(first="Tom", last="KIMBER-SMITH", candidatos=(6179, 342905),
            escolhido=342905, **mudancas):
    base = {
        "session_id": 1003, "session_raw_id": "201305181600_Race",
        "series": "ELMS", "publication_url": URL,
        "publication_sha256": DIGEST, "car_number": "3", "driver_slot": 2,
        "published_first_name": first, "published_last_name": last,
        "candidate_driver_ids": list(candidatos),
        "equivalence_state": "SUPPORTED",
        "equivalence_ids": list(candidatos),
        "chosen_driver_id": escolhido, "choice_rule": "R1 serie (ELMS)",
        "evidence_ref": "docs/...", "decided_by": "revisor",
        "decision_ref": "PR #99", "approval_state": "APPROVED",
    }
    base.update(mudancas)
    return base


def manifesto(*entradas, cluster=CLUSTER):
    return Manifest({"generated_for_cluster": cluster,
                     "entries": list(entradas) or [entrada()]})


def ctx(**mudancas):
    base = dict(cluster=CLUSTER, session_id=1003, publication_url=URL,
                publication_sha256=DIGEST, car_number="3", driver_slot=2,
                first_name="Tom", last_name="KIMBER-SMITH",
                candidates=(6179, 342905))
    base.update(mudancas)
    return SlotContext(**base)


# ── os cinco destinos determinados ───────────────────────────

@pytest.mark.parametrize("first,last,cands,escolhido,regra", DESTINOS)
def test_os_cinco_destinos_resolvem(first, last, cands, escolhido, regra):
    m = manifesto(entrada(first=first, last=last, candidatos=cands,
                          escolhido=escolhido, choice_rule=regra))
    assert m.resolve(ctx(first_name=first, last_name=last,
                         candidates=tuple(cands))) == escolhido


def test_o_destino_vem_do_manifesto_e_nao_da_ordem():
    """
    Kimber-Smith e o caso que mostra: 342905 tem 117 vinculos e 6179 tem 284,
    e o manifesto escolhe 342905. Nenhuma heuristica de execucao chegaria a
    esse id.
    """
    m = manifesto()
    assert m.resolve(ctx()) == 342905
    assert 342905 != min(6179, 342905)


# ── Van Berlo e McMurry: sem destino ─────────────────────────

def test_van_berlo_sem_destino_recusa():
    """Equivalencia sustentada, destino pendente: nao resolve."""
    m = manifesto(entrada(first="Kay Van", last="BERLO",
                          candidatos=[4937, 40795, 337757], escolhido=None,
                          approval_state="PENDING"))
    with pytest.raises(ManifestRefused, match="estado"):
        m.resolve(ctx(first_name="Kay Van", last_name="BERLO",
                      candidates=(4937, 40795, 337757)))


def test_van_berlo_aprovado_sem_id_ainda_recusa():
    """Mesmo aprovada por engano, uma entrada sem destino nao resolve."""
    m = manifesto(entrada(first="Kay Van", last="BERLO",
                          candidatos=[4937, 337757], escolhido=None))
    with pytest.raises(ManifestRefused, match="sem id de destino"):
        m.resolve(ctx(first_name="Kay Van", last_name="BERLO",
                      candidates=(4937, 337757)))


def test_a_1189_tem_um_candidato_a_mais_e_recusa():
    """
    O par aprovado e [4937, 337757]; a 1189 apresenta [4937, 40795, 337757].
    A decisao foi tomada sobre outra pergunta.
    """
    m = manifesto(entrada(first="Kay Van", last="BERLO",
                          candidatos=[4937, 337757], escolhido=337757))
    with pytest.raises(ManifestRefused, match="candidatos mudou"):
        m.resolve(ctx(first_name="Kay Van", last_name="BERLO",
                      candidates=(4937, 40795, 337757)))


def test_mcmurry_com_415015_recusa():
    """O par sustentado e [6463, 343189]; o slot da 1243 traz tres."""
    m = manifesto(entrada(first="Matthew", last="McMURRY",
                          candidatos=[6463, 343189], escolhido=6463))
    with pytest.raises(ManifestRefused, match="candidatos mudou"):
        m.resolve(ctx(first_name="Matthew", last_name="McMURRY",
                      candidates=(6463, 343189, 415015)))


# ── contexto divergente ──────────────────────────────────────

def test_digest_divergente_recusa():
    with pytest.raises(ManifestRefused, match="publicacao mudou"):
        manifesto().resolve(ctx(publication_sha256="0" * 64))


def test_url_divergente_recusa():
    with pytest.raises(ManifestRefused, match="outra publicacao"):
        manifesto().resolve(ctx(publication_url="https://exemplo/outro.CSV"))


def test_slot_incorreto_recusa():
    with pytest.raises(ManifestRefused, match="sem entrada"):
        manifesto().resolve(ctx(driver_slot=1))


def test_numero_incorreto_recusa():
    with pytest.raises(ManifestRefused, match="sem entrada"):
        manifesto().resolve(ctx(car_number="4"))


def test_outra_sessao_nao_herda_a_decisao():
    with pytest.raises(ManifestRefused, match="sem entrada"):
        manifesto().resolve(ctx(session_id=1035))


def test_nome_literal_diferente_recusa():
    with pytest.raises(ManifestRefused, match="grafia publicada"):
        manifesto().resolve(ctx(last_name="KIMBER SMITH"))


def test_outro_cluster_recusa():
    with pytest.raises(ManifestRefused, match="cluster"):
        manifesto().resolve(ctx(cluster="999"))


def test_id_fora_dos_candidatos_recusa():
    m = manifesto(entrada(escolhido=999, candidatos=[6179, 342905]))
    with pytest.raises(ManifestRefused, match="nao esta entre os candidatos"):
        m.resolve(ctx())


@pytest.mark.parametrize("estado", ["PROPOSED", "PENDING", "REJECTED", ""])
def test_manifesto_nao_aprovado_recusa(estado):
    with pytest.raises(ManifestRefused, match="estado"):
        manifesto(entrada(approval_state=estado)).resolve(ctx())


def test_sem_responsavel_recusa():
    with pytest.raises(ManifestRefused, match="responsavel"):
        manifesto(entrada(decided_by="  ")).resolve(ctx())


def test_sem_decision_ref_recusa():
    with pytest.raises(ManifestRefused, match="decision_ref"):
        manifesto(entrada(decision_ref=None)).resolve(ctx())


# ── o arquivo ────────────────────────────────────────────────

def test_sem_caminho_nao_ha_manifesto():
    assert load(None) is None


def test_arquivo_ausente_recusa(tmp_path):
    with pytest.raises(ManifestRefused, match="nao encontrado"):
        load(tmp_path / "nao-existe.json")


def test_json_invalido_recusa(tmp_path):
    alvo = tmp_path / "m.json"
    alvo.write_text("{ nao e json", encoding="utf-8")
    with pytest.raises(ManifestRefused, match="JSON invalido"):
        load(alvo)


def test_entrada_incompleta_recusa(tmp_path):
    alvo = tmp_path / "m.json"
    incompleta = {k: v for k, v in entrada().items() if k != "decision_ref"}
    alvo.write_text(json.dumps({"generated_for_cluster": CLUSTER,
                                "entries": [incompleta]}), encoding="utf-8")
    with pytest.raises(ManifestRefused, match="sem os campos"):
        load(alvo)


def test_duas_entradas_para_o_mesmo_slot_recusam():
    with pytest.raises(ManifestRefused, match="mesmo slot"):
        manifesto(entrada(), entrada(escolhido=6179))


# ── integracao: o unico ponto de consulta ────────────────────

class Cursor:
    """Devolve sempre o mesmo conjunto de candidatos."""

    def __init__(self, candidatos=(6179, 342905)):
        self.candidatos = list(candidatos)
        self.sql: list[str] = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))

    def fetchall(self):
        return [(i,) for i in self.candidatos]


LINHA = {"NUMBER": "3", "TEAM": "T", "DRIVER_1": "A B", "DRIVER_2": "C D"}


def preparar(monkeypatch, desfecho, slot=2):
    def falso_resolve(_cur, _f, _l):
        return (1, DriverOutcome.EXACT_NAME) if desfecho is DriverOutcome.EXACT_NAME \
            else (None, desfecho)

    def falso_extract(_row, _serie):
        return [{"slot": slot, "first_name": "Tom", "last_name": "KIMBER-SMITH"}]

    monkeypatch.setattr(rc, "resolve_driver_id", falso_resolve)
    monkeypatch.setattr(rc, "extract_drivers", falso_extract)


def contexto():
    return {"cluster": CLUSTER, "session_id": 1003,
            "publication_url": URL, "publication_sha256": DIGEST}


def test_integracao_resolve_o_slot_ambiguo(monkeypatch):
    preparar(monkeypatch, DriverOutcome.MULTIPLE_CANDIDATES)
    res = rc.resolve_drivers(Cursor(), [LINHA], "ELMS", manifesto(), contexto())
    assert res.by_manifest == {("3", 2): 342905}
    assert res.ambiguous == []


def test_integracao_recusa_deixa_o_slot_ambiguo(monkeypatch):
    preparar(monkeypatch, DriverOutcome.MULTIPLE_CANDIDATES)
    res = rc.resolve_drivers(Cursor((6179, 342905, 999)), [LINHA], "ELMS",
                             manifesto(), contexto())
    assert res.by_manifest == {}
    assert len(res.ambiguous) == 1
    assert any("candidatos mudou" in m for m in res.manifest_refusals)
    assert rc.driver_refusals(res), "a comporta continua bloqueando"


def test_sem_manifesto_o_comportamento_e_o_de_antes(monkeypatch):
    preparar(monkeypatch, DriverOutcome.MULTIPLE_CANDIDATES)
    res = rc.resolve_drivers(Cursor(), [LINHA], "ELMS")
    assert res.by_manifest == {}
    assert len(res.ambiguous) == 1
    assert res.manifest_refusals == []


def test_sem_ambiguidade_o_manifesto_nao_e_consultado(monkeypatch):
    """Correspondencia unica: o comportamento anterior fica intacto."""
    preparar(monkeypatch, DriverOutcome.EXACT_NAME)
    cur = Cursor()
    res = rc.resolve_drivers(cur, [LINHA], "ELMS", manifesto(), contexto())
    assert (res.exact, res.ambiguous, res.by_manifest) == (1, [], {})
    assert cur.sql == [], "nenhuma consulta de candidatos foi feita"


def test_piloto_desconhecido_continua_exigindo_autorizacao(monkeypatch):
    """O manifesto nao substitui `--allow-new-drivers`."""
    preparar(monkeypatch, DriverOutcome.NOT_FOUND)
    res = rc.resolve_drivers(Cursor(), [LINHA], "ELMS", manifesto(), contexto())
    assert len(res.missing) == 1
    assert any("global" in m for m in rc.driver_refusals(res))
    assert rc.driver_refusals(res, allow_new_drivers=True) == []


def test_reexecucao_devolve_o_mesmo_id(monkeypatch):
    """Idempotente: duas passagens, o mesmo destino, sem efeito acumulado."""
    preparar(monkeypatch, DriverOutcome.MULTIPLE_CANDIDATES)
    m = manifesto()
    a = rc.resolve_drivers(Cursor(), [LINHA], "ELMS", m, contexto())
    b = rc.resolve_drivers(Cursor(), [LINHA], "ELMS", m, contexto())
    assert a.by_manifest == b.by_manifest == {("3", 2): 342905}


def test_a_recusa_acontece_antes_de_qualquer_escrita():
    """
    A comporta corre em `recover_session` antes do DELETE e antes de
    `insert_result`; a consulta ao manifesto esta dentro dela.
    """
    fonte = (RAIZ / "database" / "recovery" /
             "recover_classification.py").read_text(encoding="utf-8")
    corpo = fonte.split("def recover_session(", 1)[1].split("\ndef ", 1)[0]
    pos_comporta = corpo.index("assert_drivers_resolvable")
    for depois in ("drop_lap_shaped_results", "insert_result(",
                   "insert_result_drivers("):
        assert pos_comporta < corpo.index(depois), depois
    assert corpo.index("resolve_drivers(") < pos_comporta


def test_o_manifesto_e_consultado_num_lugar_so():
    fonte = (RAIZ / "database" / "recovery" /
             "recover_classification.py").read_text(encoding="utf-8")
    assert fonte.count("manifest.resolve(") == 1, "um unico ponto de consulta"
    assert fonte.count("_from_manifest(") == 2          # definicao e chamada
    corpo = fonte.split("def _from_manifest(", 1)[1].split("\ndef ", 1)[0]
    assert "manifest.resolve(ctx)" in corpo, "e ele esta em `_from_manifest`"


def test_resolve_driver_id_nao_foi_alterado_globalmente():
    """Outras rotinas de ingestao nao mudam de comportamento."""
    fonte = (RAIZ / "database" / "loader" /
             "row_identity.py").read_text(encoding="utf-8")
    assert "manifest" not in fonte.lower()

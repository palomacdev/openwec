"""
Quem pode escrever em `results`.

A tabela tem um escritor de INSERT em toda a historia do repositorio — o
loader de classificacao, desde 6e7f610 — e dois caminhos de exclusao, ambos
sob plano e comporta. Um terceiro caminho aparecendo sem decisao foi como a
multiplicacao da ELMS passou despercebida por meses.

Este arquivo nao toca banco nenhum e nao importa codigo de producao: le o
fonte como texto.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]

# Alcance desta varredura, declarado para que ninguém a leia como mais do
# que é: ela encontra SQL ESCRITO POR EXTENSO no fonte — `INSERT INTO
# results`, `UPDATE results`, `DELETE FROM results` — com os espaços
# normalizados, de modo que um statement quebrado em várias linhas também
# conta. Ela NÃO vê SQL montado em tempo de execução (nome de tabela vindo
# de variável ou de f-string), nem chamadas de ORM, nem SQL que chegue de
# fora do repositório. Não é prova de que `results` só tem estes escritores;
# é um alarme para o caso conhecido, que é alguém escrever o statement à mão
# num arquivo novo.
ESCRITA_EM_RESULTS = re.compile(
    r"\b(?:insert\s+into|update|delete\s+from)\s+\"?results\"?(?![a-z0-9_])",
    re.IGNORECASE,
)

# Onde é esperado e por quê. Cada entrada é uma decisão revisada, não uma
# isenção: mudar esta lista é mudar quem pode escrever na tabela.
ESCRITORES_CONHECIDOS = {
    # o único INSERT do projeto, desde 6e7f610
    "database/loader/load_classification.py",
    # a exclusão autorizada da deduplicação (#66), sob plano e backup
    "database/recovery/dedup_execute.py",
    # a remoção dos lap-shaped results na recuperação, em banco descartável
    "database/recovery/recover_classification.py",
}

RAIZES_VARRIDAS = ("database", "api", "analytics", "sdk")


def arquivos_de_producao():
    for raiz in RAIZES_VARRIDAS:
        base = RAIZ / raiz
        if not base.exists():
            continue
        for caminho in base.rglob("*"):
            if caminho.suffix.lower() not in (".py", ".sql"):
                continue
            if "__pycache__" in caminho.parts:
                continue
            yield caminho


def _rotulo(caminho: Path) -> str:
    try:
        return caminho.relative_to(RAIZ).as_posix()
    except ValueError:
        return caminho.as_posix()


def escritores_encontrados(arquivos=None) -> set[str]:
    achados = set()
    for caminho in (arquivos if arquivos is not None else arquivos_de_producao()):
        texto = " ".join(caminho.read_text(encoding="utf-8",
                                           errors="replace").split())
        if ESCRITA_EM_RESULTS.search(texto):
            achados.add(_rotulo(caminho))
    return achados


def test_results_so_tem_os_escritores_declarados():
    """
    Um caminho de escrita novo tem de passar por uma decisão, não por um
    arquivo novo. Veja o comentário acima para o que esta varredura alcança.
    """
    encontrados = escritores_encontrados()
    novos = encontrados - ESCRITORES_CONHECIDOS
    sumidos = ESCRITORES_CONHECIDOS - encontrados
    assert not novos, (
        f"escritor de `results` nao declarado: {sorted(novos)}. "
        f"Se for intencional, acrescente a ESCRITORES_CONHECIDOS com o motivo.")
    assert not sumidos, (
        f"estes deixaram de escrever em `results`: {sorted(sumidos)}. "
        f"Remova-os de ESCRITORES_CONHECIDOS.")


def test_a_varredura_enxerga_um_escritor_plantado(tmp_path):
    """
    Sem isto a varredura poderia estar sempre vazia e ninguém notaria.

    O statement é quebrado em várias linhas de propósito: é assim que ele
    aparece no loader, e um regex por linha não o enxergaria.

    O arquivo plantado vive em `tmp_path`; o código de produção não é tocado.
    """
    plantado = tmp_path / "escritor_novo.py"
    plantado.write_text(
        'def grava(cur):\n'
        '    cur.execute("""\n'
        '        INSERT INTO results (session_id, car_id)\n'
        '        VALUES (%s, %s)\n'
        '    """, (1, 2))\n', encoding="utf-8")
    inocente = tmp_path / "so_le.py"
    inocente.write_text(
        'def le(cur):\n'
        '    cur.execute("SELECT count(*) FROM results")\n', encoding="utf-8")

    assert escritores_encontrados([plantado, inocente]) == {_rotulo(plantado)}


@pytest.mark.parametrize("trecho, casa", [
    ("INSERT INTO results (session_id)", True),
    ("insert   into   results(session_id)", True),    # espacos e caixa
    ("UPDATE results SET position = 1", True),
    ("DELETE FROM results WHERE id = 1", True),
    ('DELETE FROM "results" WHERE id = 1', True),
    ("INSERT INTO result_drivers (result_id)", False),
    ("INSERT INTO resultsx (a)", False),
    ("UPDATE results_backup SET x = 1", False),
    ("SELECT * FROM results", False),
])
def test_o_padrao_distingue_escrita_de_leitura(trecho, casa):
    """Os limites do regex, escritos como casos em vez de prosa."""
    assert bool(ESCRITA_EM_RESULTS.search(trecho)) is casa

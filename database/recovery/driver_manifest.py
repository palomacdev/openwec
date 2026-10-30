"""
OpenWEC — o manifesto de resolucoes de piloto, e quando ele nao se aplica.

Um manifesto e um arquivo versionado que diz, para **um slot de uma
publicacao**, qual linha de `drivers` aquela grafia denota. Ele existe porque
`choose_driver` recusa quando varias linhas carregam um nome, e escolher ali
seria escolher por id — a duplicacao que o acervo ja tem, decidida por
acidente.

## O que o manifesto nao e

Nao e consolidacao: nenhuma linha de `drivers` e fundida, renomeada ou
removida, e os vinculos historicos ficam onde estao. Uma entrada diz a quem
ligar **deste slot**, e nada mais.

Nao e desempate: sem entrada aplicavel, este modulo **recusa**. Nao ha
caminho em que ele devolva o menor id, o primeiro da consulta ou o de mais
vinculos.

## A chave

`(publicacao, carro, slot)` — nunca a grafia sozinha. A mesma grafia noutra
sessao precisa da sua propria entrada, com a sua propria publicacao, porque a
decisao foi tomada olhando **aquele** texto.

## Quando uma entrada deixa de valer

Sozinha, sem ninguem a revogar: se a publicacao mudou de digest, se o slot ou
o carro sao outros, se a grafia daquele slot mudou, se o **conjunto de
candidatos** no banco nao e mais o que o decisor viu, ou se o manifesto foi
gerado contra outro cluster. O conjunto de candidatos e a condicao mais util
das seis: uma linha nova em `drivers` com a mesma chave muda a pergunta que
foi respondida, e a resposta antiga para de valer na hora.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

APPROVED = "APPROVED"

# Os campos que uma entrada tem de trazer para ser sequer considerada.
REQUIRED = (
    "session_id", "publication_url", "publication_sha256", "car_number",
    "driver_slot", "published_first_name", "published_last_name",
    "candidate_driver_ids", "chosen_driver_id", "decided_by", "decision_ref",
    "approval_state",
)


class ManifestRefused(Exception):
    """Levantada em vez de resolver. Nunca acompanha um id."""


@dataclass(frozen=True)
class SlotContext:
    """O slot que esta sendo resolvido, como o banco e a fonte o apresentam."""
    cluster: str
    session_id: int
    publication_url: str
    publication_sha256: str
    car_number: str
    driver_slot: int
    first_name: str
    last_name: str
    candidates: tuple


class Manifest:
    """Um manifesto carregado. Imutavel e sem banco."""

    def __init__(self, dados: dict, origem: str = "<memoria>"):
        if not isinstance(dados, dict):
            raise ManifestRefused(f"{origem}: manifesto nao e um objeto")
        self.origem = origem
        self.cluster = dados.get("generated_for_cluster")
        self.entries = dados.get("entries") or []
        # Indexado pela chave, que e o que torna "outra sessao nao herda a
        # decisao" uma propriedade da estrutura e nao de uma verificacao.
        self._por_slot: dict[tuple, dict] = {}
        for entrada in self.entries:
            faltando = [c for c in REQUIRED if c not in entrada]
            if faltando:
                raise ManifestRefused(
                    f"{origem}: entrada sem os campos {faltando}")
            chave = (entrada["session_id"], str(entrada["car_number"]),
                     int(entrada["driver_slot"]))
            if chave in self._por_slot:
                raise ManifestRefused(
                    f"{origem}: duas entradas para o mesmo slot {chave}")
            self._por_slot[chave] = entrada

    def __len__(self) -> int:
        return len(self._por_slot)

    def resolve(self, ctx: SlotContext) -> int:
        """
        O id que este manifesto autoriza para este slot, ou recusa.

        Toda divergencia e recusa, e a mensagem diz qual — para que quem a
        ler saiba se o manifesto esta desatualizado, se o arquivo mudou, ou
        se a decisao nunca existiu.
        """
        if self.cluster != ctx.cluster:
            raise ManifestRefused(
                f"manifesto gerado para o cluster {self.cluster}, "
                f"alvo e {ctx.cluster}")

        entrada = self._por_slot.get(
            (ctx.session_id, str(ctx.car_number), int(ctx.driver_slot)))
        if entrada is None:
            raise ManifestRefused(
                f"sem entrada para a sessao {ctx.session_id}, carro "
                f"{ctx.car_number}, slot {ctx.driver_slot}")

        if entrada["publication_sha256"] != ctx.publication_sha256:
            raise ManifestRefused(
                "a publicacao mudou desde a decisao: decidida sobre "
                f"{entrada['publication_sha256'][:12]}, carregada "
                f"{ctx.publication_sha256[:12]}")
        if entrada["publication_url"] != ctx.publication_url:
            raise ManifestRefused(
                f"outra publicacao: decidida sobre {entrada['publication_url']}")
        if (entrada["published_first_name"], entrada["published_last_name"]) != (
                ctx.first_name, ctx.last_name):
            raise ManifestRefused(
                "a grafia publicada naquele slot nao e a que foi decidida: "
                f"decidida {entrada['published_first_name']!r} "
                f"{entrada['published_last_name']!r}, lida "
                f"{ctx.first_name!r} {ctx.last_name!r}")

        decididos = sorted(entrada["candidate_driver_ids"])
        agora = sorted(ctx.candidates)
        if decididos != agora:
            raise ManifestRefused(
                f"o conjunto de candidatos mudou desde a decisao: decididos "
                f"{decididos}, agora {agora}")

        if entrada["approval_state"] != APPROVED:
            raise ManifestRefused(
                f"entrada em estado {entrada['approval_state']!r}, "
                f"nao {APPROVED}")
        if not (entrada.get("decided_by") or "").strip():
            raise ManifestRefused("entrada sem responsavel")
        if not (entrada.get("decision_ref") or "").strip():
            raise ManifestRefused("entrada sem decision_ref")

        escolhido = entrada["chosen_driver_id"]
        if escolhido is None:
            raise ManifestRefused(
                "entrada sem id de destino: a equivalencia pode estar "
                "sustentada e o destino, nao")
        if escolhido not in entrada["candidate_driver_ids"]:
            raise ManifestRefused(
                f"id de destino {escolhido} nao esta entre os candidatos "
                f"{entrada['candidate_driver_ids']}")
        return escolhido


def load(caminho: Path | str | None) -> Manifest | None:
    """Carrega um manifesto, ou devolve None quando nao ha caminho."""
    if caminho is None:
        return None
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ManifestRefused(f"manifesto nao encontrado: {caminho}")
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestRefused(f"{caminho}: JSON invalido — {exc}") from exc
    return Manifest(dados, origem=str(caminho))

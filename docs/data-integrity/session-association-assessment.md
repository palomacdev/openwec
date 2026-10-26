# Session association assessment

Avaliador **read-only** da associação entre resultados e sessões. Reporta o
que o acervo parece ser sob a história do carregador anterior à PR #41.
**Não move resultado nem volta, e não declara nenhuma sessão como a casa
correta de coisa alguma.**

**Ambiente medido:** `openwec@127.0.0.1:5433`, banco de desenvolvimento.
**Data:** 2026-10-26. **Base:** `3edb40d` (após a PR #62).
**Escrita no banco:** nenhuma.

---

## 1. O defeito, e por que o schema não podia impedi-lo

Um fim de semana publica vários campeonatos, cada um com a sua "Practice 2".
Antes da PR #41 (`35ab3db`), o carregador resolvia a sessão com:

```sql
WHERE sr.key = %s AND se.raw_id = %s AND e.raw_id = %s AND s.name = %s
LIMIT 1
```

Nome nu, `LIMIT 1`, sem `ORDER BY`. Onde quatro campeonatos partilham o
nome, devolve um arbitrário.

**Campeonato não é uma relação.** O schema é
`series → seasons → events → sessions → results`; não existe tabela
`championships` nem coluna de campeonato. Ele existe **apenas como texto no
sétimo segmento de `sessions.source_url`**. Nenhuma restrição podia impedir
um resultado de cair na sessão de outro campeonato, porque o banco não sabe
o que é um campeonato.

O que o schema garante é `UNIQUE (event_id, raw_id)` em `sessions`, e
`raw_id` carrega o horário (`202407120835_Practice 1`). **As sessões de
campeonatos distintos não colapsam.** O que erra é o que se prende a elas.

`MEASURED`: nenhum resultado foi criado depois de 2026-10-20, e a PR #41
entrou em 2026-10-22. **Comportamento histórico, não regressão** — e
nenhuma carga rodou desde a correção, então ela não pôde ser verificada no
acervo.

---

## 2. Três camadas de evidência, e a terceira está vazia

Cada sinal declara **como foi obtido**, porque não valem o mesmo:

| camada | significado |
|---|---|
| `OBSERVED` | lido do catálogo; nenhuma inferência |
| `CORPUS_DERIVED` | computado do próprio acervo, **que pode já carregar o defeito procurado**; corroboração **interna** e provavelmente **correlacionada** entre si |
| `INDEPENDENT` | evidência de fora do acervo — **nenhuma existe** |

A terceira está no contrato, nomeada e vazia, porque `ingest_log` tem **0
linhas**, `results.source_url` está preenchido em **0 de 668.273** e
`raw/imsa/` não contém os campeonatos em questão. Cada entrada carrega
`independent_evidence: []`: a ausência é um campo, não uma nota de rodapé.

**Nenhuma combinação de sinais `CORPUS_DERIVED` vira evidência
independente.** Todos nascem da mesma história de ingestão.

### 2.1 Por que isto não é formalidade

`MEASURED`: um perfil de classes construído a partir deste acervo encontra
**2** sessões IMSA suspeitas onde o sinal estrutural encontra **496** — o
perfil de referência da Michelin Pilot é idêntico ao da WeatherTech, porque
foi construído a partir de sessões já contaminadas. **O perfil herda o
defeito que deveria detectar.**

---

## 3. Camada de campeonato, por série

| série | camada | inferência | rótulos distintos medidos |
|---|---|---|---:|
| IMSA | `REAL` | **sim** | 48 |
| ELMS | `REAL` | **sim** | 49 |
| LEMANSCUP | `MIXED` | **não**, até investigação própria | 5 |
| WEC | `SERIES_LABEL` | **não** | 9, todos variantes de `FIA WEC` |
| ALMS | `SERIES_LABEL` | **não** | 1 |

Série desconhecida recebe `SERIES_LABEL` — a direção segura. Fora de `REAL`
nenhum sinal de campeonato é emitido, e a flag
`CHAMPIONSHIP_LAYER_NOT_APPLICABLE` entra na saída.

**ALMS foi investigado antes de concluir o alcance:** um único rótulo, grid
único (`GT`, `LMP2`, `LMP3`), **zero** sessões ambíguas por `(evento, nome)`.

### 3.1 O critério `CHAMPIONSHIP_NEVER_LOADED` é computado só no IMSA

Ali é mensurável: as classes presentes no acervo IMSA são as da WeatherTech.
No **ELMS não é**, porque campeonatos de apoio foram **parcialmente
carregados** — `Mitjet Italia` tem `FX1, FX2` **e** `LMP2, LMP3`.

**Nada medido no IMSA é transportado para o ELMS.** No ELMS os casos de
perfil estranho ficam em `ASSOCIATION_SUSPECT`, nunca em coorte.

---

## 4. Dois digests, deliberadamente separados

| digest | sobre o quê | o que sustenta |
|---|---|---|
| `car_set_digest` | o **conjunto de números de carro** | que os mesmos carros aparecem |
| `result_content_digest` | as **linhas de resultado** (número, classe, posição, status, voltas, tempo) | que os mesmos resultados estão lá |

**Igualdade de conjuntos de carros nunca é tratada como prova de resultados
duplicados.** O sinal `CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP` carrega a
advertência no próprio campo de evidência, e informa, por sessão comparada,
se o `result_content_digest` também coincide.

`CONTENT_MULTIPLE_OF_SIBLING` usa **só** o digest de conteúdo, exige
denominador positivo, e registra: id da sessão comparada, as duas contagens,
a razão inteira, qual digest foi usado e a consulta que reproduz o caso.

`MEASURED`, e vale registrar: **com o digest de conteúdo, esse sinal não
ocorre nenhuma vez** no acervo. A multiplicação real é **dentro** da sessão
— a 4986 tem 102 linhas para 34 carros distintos — e isso é a classe de
triplicação da PR #57, **outro defeito**. O módulo a mede e reporta como
`CARS_REPEATED_WITHIN_SESSION`, e **não a deixa classificar nada**.

---

## 5. Sinais

| sinal | camada | o que diz |
|---|---|---|
| `AMBIGUOUS_SESSION_NAME` | `OBSERVED` | outra sessão do evento tem o mesmo nome |
| `SIBLING_SAME_NAME_EMPTY` | `OBSERVED` | um irmão de mesmo nome está vazio |
| `SESSION_HAS_NO_RESULTS` | `OBSERVED` | descreve a sessão |
| `NO_PROVENANCE_URL` | `OBSERVED` | não registra de que arquivo veio |
| `CARS_REPEATED_WITHIN_SESSION` | `OBSERVED` | triplicação da PR #57 — **outro defeito** |
| `CHAMPIONSHIP_LAYER_NOT_APPLICABLE` | `OBSERVED` | série fora de `REAL` |
| `CHAMPIONSHIP_NEVER_LOADED` | `CORPUS_DERIVED` | nenhuma classe exclusiva do campeonato existe |
| `CLASS_PROFILE_FOREIGN` | `CORPUS_DERIVED` | classes fora do perfil do próprio campeonato |
| `CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP` | `CORPUS_DERIVED` | mesmo conjunto de carros, campeonato diferente |
| `CONTENT_MULTIPLE_OF_SIBLING` | `CORPUS_DERIVED` | contagem é múltiplo inteiro, mesmo conteúdo |
| `CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE` | `CORPUS_DERIVED` | **ressalva** — o perfil não discrimina ali |

### 5.1 A ressalva nunca corrobora

`CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE` é uma afirmação sobre a **fraqueza
do método**, não sobre a sessão. Deixá-la corroborar faria a limitação
sustentar a conclusão que ela limita. Está em `CAVEAT_SIGNALS`, é sempre
reportada e **nunca** classifica nem completa uma coorte. Dois testes fixam
isso.

Três sinais `OBSERVED` também ficam fora da decisão porque **descrevem a
sessão em vez de uma discordância sobre ela**: `SESSION_HAS_NO_RESULTS`,
`CHAMPIONSHIP_LAYER_NOT_APPLICABLE` e `CARS_REPEATED_WITHIN_SESSION`.

---

## 6. As seis classificações, com precedência

| ordem | classificação | critério | o que **recusa** afirmar |
|---|---|---|---|
| 1 | `UNDETERMINED_NO_PROVENANCE` | sem `source_url` | nada pode ser confirmado nem refutado |
| 2 | `STRUCTURAL_INCOMPATIBILITY_COHORT` | camada `REAL` + `CHAMPIONSHIP_NEVER_LOADED` + conteúdo + **≥1 sinal corroborante** | **não afirma que cada resultado foi comprovadamente associado à sessão errada** |
| 3 | `ASSOCIATION_SUSPECT` | ≥1 `CORPUS_DERIVED` **e** ≥1 `OBSERVED` de discordância | há o que investigar; nada está demonstrado |
| 4 | `PROVENANCE_METADATA_INCONSISTENT` | ≥1 `CORPUS_DERIVED` sem `OBSERVED` ao lado | **nada diz qual dos dois lados está errado** |
| 5 | `AMBIGUOUS_NAME_ONLY` | só o nome repete | **somente** isso; nomes iguais com conteúdos distintos são o caso normal |
| 6 | `NO_NAME_AMBIGUITY` | nome único no evento | **apenas ausência de ambiguidade nominal**; não comprova proveniência |

**Um sinal observacional sozinho nunca alcança `ASSOCIATION_SUSPECT`.** Nome
repetido mais irmão vazio são duas observações sobre nomes e contagens.

**`CHAMPIONSHIP_NEVER_LOADED` sozinho nunca alcança a coorte.** Uma única
medição `CORPUS_DERIVED` não é uma coorte.

---

## 7. Distribuição medida

`MEASURED`, `--all --evidence-limit 0`, 5.425 sessões:

| classificação | sessões | resultados |
|---|---:|---:|
| `NO_NAME_AMBIGUITY` | 2.754 | 429.801 |
| `AMBIGUOUS_NAME_ONLY` | 1.504 | 3.088 |
| **`STRUCTURAL_INCOMPATIBILITY_COHORT`** | **496** | **108.818** |
| `PROVENANCE_METADATA_INCONSISTENT` | 276 | 101.144 |
| `ASSOCIATION_SUSPECT` | 227 | 25.066 |
| `UNDETERMINED_NO_PROVENANCE` | 168 | 356 |
| total | **5.425** | |

A coorte é **inteiramente IMSA**. Os casos ELMS de perfil estranho — **84** —
estão todos em `ASSOCIATION_SUSPECT`, com 7.662 resultados, **nenhum**
classificado como incorreto.

Sinais: `AMBIGUOUS_SESSION_NAME` 2.244, `CARS_REPEATED_WITHIN_SESSION` 2.670,
`SESSION_HAS_NO_RESULTS` 2.523, `SIBLING_SAME_NAME_EMPTY` 1.833,
`CHAMPIONSHIP_LAYER_NOT_APPLICABLE` 1.456,
`CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP` 967,
`CHAMPIONSHIP_NEVER_LOADED` 497, `NO_PROVENANCE_URL` 168,
`CLASS_PROFILE_FOREIGN` 86 (84 ELMS, 2 IMSA), `CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE` 59,
`CONTENT_MULTIPLE_OF_SIBLING` **0**.

---

## 8. Divergências em relação às medições anteriores

**Não ajustei nada para coincidir.**

### 8.1 516 → 497 sinais de campeonato nunca carregado

`MEASURED`: as **19** que não levantam o sinal são de
`IMSA AirBNB Endurance Challenge`, cuja classe é **`P3`** — exclusiva dele.
Esse campeonato **foi** carregado, com o seu próprio grid.

**Isto corrige uma imprecisão da investigação anterior**, que afirmou que
"nenhuma classe não-WeatherTech existe no acervo". `P3` existe, com 571
resultados em 19 sessões.

A coorte continua em **496**, porque 496 das 516 tinham corroboração e a
regra do contrato revisado exige corroboração. `497 − 1` = a sessão que
levanta o sinal sem nenhum corroborante.

### 8.2 86 → 84 casos ELMS de perfil estranho

`MEASURED`: **6 sessões ELMS têm irmão de mesmo nome sem `source_url`.** A
consulta anterior filtrava a base por URL presente, então essas contavam
como não ambíguas e **entravam no perfil de referência**, deslocando quais
sessões caíam fora dele.

O módulo trata irmão sem URL **como irmão**, que é o correto: o `LIMIT 1`
podia devolvê-lo. Dois casos a menos, por uma diferença deliberada.

### 8.3 104 → 0 multiplicações entre irmãos

A medição anterior usava igualdade de **conjunto de carros**. Com o digest
de **conteúdo**, como o contrato exige, **nenhum caso sobrevive** — a
multiplicação real é dentro da sessão (§4).

---

## 9. Exemplo reproduzível — sessão 4986

```
session_id 4986   serie IMSA   temporada 2024   camada REAL
raw_id "202407120835_Practice 1"   session_at 2024-07-12 08:35
championship "IMSA VP Racing SportsCar Challenge"
results 102   distinct_cars 34   class_profile [GTD, GTDPRO, LMP2]

OBSERVED        CARS_REPEATED_WITHIN_SESSION
                  linhas 102, carros distintos 34, razao 3 (inteira)
                  SELECT count(*), count(DISTINCT c.number) FROM results r
                    JOIN cars c ON c.id=r.car_id WHERE r.session_id = 4986
OBSERVED        AMBIGUOUS_SESSION_NAME
                  irmaos 4987(0), 4988(0), 4991(0)
                  SELECT id, raw_id, name FROM sessions
                   WHERE event_id = <evento> AND name = 'Practice 1'
OBSERVED        SIBLING_SAME_NAME_EMPTY   vazios [4987, 4988, 4991]
CORPUS_DERIVED  CHAMPIONSHIP_NEVER_LOADED
                  classes_vistas [GTD, GTDPRO, LMP2], exclusivas [], baseline WeatherTech
CORPUS_DERIVED  CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP
                  digest_usado car_set_digest; por sessao comparada, se o
                  result_content_digest tambem coincide
INDEPENDENT     []

classification STRUCTURAL_INCOMPATIBILITY_COHORT
```

**Cada sinal carrega evidência reproduzível por consulta read-only.**

---

## 10. O que o relatório recusa

Texto fixo em cada entrada:

- **Os 108.818 resultados da coorte não são registros recuperáveis.** A
  origem individual não existe, e uma sessão pode conter dados de **várias
  cargas**.
- **Conjunto de carros igual não prova resultados duplicados.**
- **Múltiplo exato não prova erro.**
- **Perfis indistinguíveis não provam contaminação** — provam que ali o
  perfil não discrimina.
- **Exposição é propriedade da sessão, não da volta.** As 158.374 voltas em
  sessões expostas **não** são voltas incorretas.
- **Nenhum resultado ou volta é movido, e nenhuma linha canônica é
  escolhida.**

---

## 11. Limitações

1. **Nenhuma evidência independente existe.** Toda a análise é por conteúdo
   agregado, não por proveniência registrada.
2. **O perfil de referência pode estar contaminado** (§2.1), e o módulo o
   declara em cada sinal que o usa.
3. **`raw/imsa/` não contém os campeonatos não-WeatherTech**, então nenhum
   dos 496 pôde ser comparado contra arquivo.
4. **Os testes são unitários e com cursor falso.** `assess_sessions` é uma
   função pura sobre linhas, e `read_sessions` é exercitada contra um cursor
   que registra cada instrução. **Não há teste de integração contra banco
   real no CI**, porque o passo de recuperação não recebe variáveis de
   ambiente de banco e o passo de contagem falha em qualquer `skip`. A
   execução real está registrada em §7 e §9, reproduzível pelo comando de
   §12.
5. **LEMANSCUP não foi investigado** e fica em `MIXED`, sem inferência.
6. **Não medi `laps`** com o mesmo rigor que `results`.

---

## 12. Uso

```bash
python -m database.recovery.session_association --all --evidence-limit 0 \
  --json /caminho/fora/do/repo/sessoes.json

python -m database.recovery.session_association \
  --classification STRUCTURAL_INCOMPATIBILITY_COHORT

python -m database.recovery.session_association --session-id 4986
```

`--evidence-limit` limita **quantos sinais são listados**, nunca quantos são
contados nem como a sessão é classificada. Teste fixa isso.

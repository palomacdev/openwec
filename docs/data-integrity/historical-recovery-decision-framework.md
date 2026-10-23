# Historical recovery decision framework

Como decidir o destino dos registros contaminados sem destruir dado
legítimo. **Documentação e leitura apenas.** Nenhuma recuperação executada,
nenhuma autorizada, nenhuma escrita no banco histórico.

**Ambiente medido:** `openwec@127.0.0.1:5433`. **Data:** 2026-10-23.
**`main` em:** `314a373` (após a PR #47).

Cada afirmação carrega o seu estatuto:

| marca | significa |
|---|---|
| **`MEASURED`** | demonstrado por consulta ou leitura de código, nesta PR ou nas anteriores |
| **`DERIVED`** | calculado a partir de medições |
| **`PROPOSED`** | recomendação, ainda não aprovada |
| **`UNKNOWN`** | não estabelecido |

---

## 1. Resumo executivo

A pergunta desta PR era quais evidências precisamos para escolher entre
preservar, reconstruir ou remover as 324.720 linhas contaminadas. A
investigação encontrou uma evidência que **muda a pergunta**.

### A correção que reabre a estratégia B

A PR #43 classificou o conteúdo de classificação dessas sessões como
`UNRECOVERABLE`, com esta justificativa:

> ausência comprovada, não falta de solução: as 51 pastas não têm arquivo
> de classificação

**A ausência é em `raw/`, não na fonte.** `MEASURED`: o catálogo publica um
CSV de classificação para **todas as 51 sessões**:

| família publicada | sessões |
|---|---:|
| `05_Classification_*.CSV` | **50** |
| `03_Results.CSV` + `05_Results by Hour.CSV` | **1** |
| nenhuma | **0** |

Exemplos reais, lidos de `catalog/elms/sessions.json`:

```
ELMS 08_2013 / 01_SILVERSTONE / Race
    05_Classification_Race_Hour 1.CSV
    05_Classification_Race_Hour 2.CSV
    05_Classification_Race_Race.CSV
    23_Analysis_Race.CSV

ELMS 07_2012 / 01_PAUL RICARD / Race
    05_Classification_Race_1H.CSV  …  05_Classification_Race_6H.CSV
    23_Analysis_Race.CSV
```

`MEASURED`: `raw/` contém exatamente quatro famílias de CSV —
`03_Classification` (2.591), `03_Results` (948), `23_Analysis` (2.227 + 30)
e `26_Weather` (2.794). **Nenhum arquivo `05_*`.** `DERIVED`: o download
buscou as famílias `03_`, `23_` e `26_`, e esses eventos publicam a
classificação sob `05_`.

`MEASURED`: no catálogo inteiro, **78 sessões publicam
`05_Classification*.CSV`**, das quais 50 são contaminadas. As outras 28 não
foram investigadas — `UNKNOWN`.

**Consequência.** O estado correto das 324.720 linhas não é
`UNRECOVERABLE`. É **`NEEDS_SOURCE_VERIFICATION`**: o conteúdo existe no
publicador e não no nosso acervo. Recuperá-lo depende de baixar arquivos —
acesso de rede e alteração do corpus — que **não estão autorizados** e que
ninguém mediu quanto a disponibilidade atual das URLs (`UNKNOWN`: links de
2012 podem ter saído do ar).

### O que isso muda na decisão

Antes, a escolha parecia ser entre preservar lixo e apagar lixo. Com a
fonte existindo, a escolha passa a incluir **reconstruir**, e remover sem
antes verificar a fonte passa a ser a alternativa **menos** defensável:
apagaríamos registros cuja substituição está disponível.

---

## 2. Escopo e baselines

Conferidos nesta PR contra o banco, sem reinterpretação:

| indicador | referência | medido |
|---|---:|---:|
| `drivers` | 2.845 | **2.845** |
| `laps` | 2.034.223 | **2.034.223** |
| `result_drivers` | 469.457 | **469.457** |
| `results` | 671.353 | **671.353** |
| `results` contaminados | 324.720 | **324.720** |
| `cars` excedentes | 342.505 | **342.505** |
| grupos duplicados de pilotos | 1.079 | **1.079** |
| voltas sem piloto | 432.174 | **432.174** |
| colisões de voltas | 35.045 | **35.045** |
| duplicações em `result_drivers` | 14 | **14** |

Nenhuma divergência. As medições das PRs #43 a #46 são reutilizadas e não
foram refeitas, exceto onde esta PR precisou de evidência nova — §1 e §4.

---

## 3. Evidências já estabelecidas

| evidência | origem | estatuto |
|---|---|---|
| as 51 sessões foram servidas por arquivo de análise | #43 §3.1 | `MEASURED` |
| duas evidências independentes selecionam o mesmo conjunto, com intervalos disjuntos (1–7 contra 26–394) | #43 §3.1 | `MEASURED` |
| as 324.720 linhas têm **todos** os seis campos nulos e `status='Other'` | #43 §3.2 | `MEASURED` |
| zero coexistência com classificação legítima nessas sessões | #43 §3.3 | `MEASURED` |
| 42 tuplas aparecem nas populações contaminada e legítima | #43 §4 | `MEASURED` |
| 1.534 entradas com voltas, 131 sem | #44 §7 | `MEASURED` |
| colisões: 23.048 `IDENTICAL`, 695 `COMPLEMENTARY`, 11.302 `CONFLICTING` | #44 §3 | `MEASURED` |
| **todos** os conflitos são só em `driver_id`; zero em tempo, setor, velocidade, flag | #44 §5.1 | `MEASURED` |
| consolidar só identidades corroboradas resolve **zero** colisões | #46 §7.2 | `MEASURED` |
| `driver_id` não participa de nenhuma restrição única | #46 §7.5 | `MEASURED` |
| fusões não autorizadas estão bloqueadas | #47 | `MEASURED` |

### 3.1 O que ainda falta

| lacuna | estatuto |
|---|---|
| as URLs de `05_Classification*` ainda respondem? | `UNKNOWN` |
| o conteúdo desses CSV casa com o dialeto que o parser lê? | `UNKNOWN` |
| as 28 sessões não contaminadas que publicam `05_` têm `03_` também? | `UNKNOWN` |
| os 35.045 pares em colisão têm conteúdo idêntico campo a campo? | `UNKNOWN` — entregável da Fase 1 do plano |
| identidade real dos 651 grupos `POSSIBLE_HOMONYM` | `UNKNOWN` |
| por que 131 entradas contaminadas não têm volta | `UNKNOWN` |
| por que 14 linhas de `result_drivers` repetem `(result_id, driver_id)` | `UNKNOWN` |

---

## 4. Unidade de decisão

**`PROPOSED`: a sessão, subdividida pela família de arquivo publicada.**

### 4.1 Por que não uma unidade maior

| unidade | por que não serve |
|---|---|
| série | `MEASURED`: as 51 estão em 3 séries (ELMS 36, Le Mans Cup 14, IMSA 1), e cada série tem centenas de sessões legítimas |
| temporada | `MEASURED`: 12 temporadas distintas, todas com sessões legítimas |
| evento | `MEASURED`: 51 sessões em 50 eventos, e **em todos os 50 só algumas sessões estão contaminadas**. Decidir por evento atingiria sessões legítimas |

### 4.2 Por que não uma unidade menor

O registro individual não tem proveniência própria: uma linha de `results`
não carrega de que arquivo veio. `DERIVED`: a contaminação é uma
propriedade de **qual arquivo alimentou a sessão**, e o arquivo de origem é
aqui equivalente à sessão — há um arquivo de análise por sessão
contaminada.

Decidir por registro exigiria uma evidência por linha que não existe, e
abriria a porta para tratar diferentemente linhas da mesma origem, que é
precisamente o erro que se quer evitar.

### 4.3 Por que a sessão se subdivide

`MEASURED`: as 51 não são homogêneas quanto à fonte disponível.

| subgrupo | sessões | `results` | fonte publicada |
|---|---:|---:|---|
| **S1** | **1** | 13.488 | `03_Results.CSV` — família que o loader já lê |
| **S2** | **50** | 311.232 | `05_Classification_*.CSV` — família que o loader **não** lê |

`DERIVED`: 13.488 + 311.232 = 324.720. A sessão S1 é a IMSA 2016 Watkins
Glen Race.

A diferença é material: S1 precisa apenas do arquivo; S2 precisa do arquivo
**e** de estender o filtro de `is_classification_file`, hoje
`^03_(classification|results)`. São duas decisões, não uma.

`MEASURED`: todas as 51 são sessões de corrida — 49 `Race`, 1 `Race 1`,
1 `Race 2`.

---

## 5. Estratégias comparadas

### A. Preservação com isolamento lógico

Manter as linhas e excluí-las das consultas analíticas por predicado.

| | |
|---|---|
| **pré-condições** | nenhuma técnica; só a decisão de qual predicado usa |
| **evidências necessárias** | as duas de #43 §3.1, já estabelecidas |
| **dependências** | nenhuma |
| **impacto** | `results` 671.353 e `cars` 671.353 inalterados; `laps` e `result_drivers` inalterados |
| **perda de informação** | nenhuma |
| **reversibilidade** | total — nada muda |
| **validação em ambiente descartável** | desnecessária |
| **rejeitar quando** | a análise precisar de contagens globais corretas sem conhecer o predicado; e se a lista das 52 sessões se perder, a contaminação volta a ser invisível |

`PROPOSED`: é o estado vigente por omissão, e o único que não exige
autorização nenhuma. Marcar as linhas numa coluna seria mais robusto que um
predicado externo, mas exige migration — fora do escopo.

### B. Reconstrução a partir das fontes originais

Obter os CSV de classificação publicados e reingerir.

| | |
|---|---|
| **pré-condições** | Fase 0; autorização de rede; autorização para alterar `raw/`; para S2, estender o filtro do loader |
| **evidências necessárias** | que as URLs respondem (`UNKNOWN`); que o conteúdo casa com um dialeto conhecido (`UNKNOWN`); que o arquivo descreve a mesma sessão |
| **dependências** | política de colisões **não** é dependência: reconstruir `results` não toca em `laps` |
| **impacto** | `results` das 51 substituídos; `cars` reduzidos de 324.720 para ~1.665; `laps` **precisam ser re-apontados antes**; `result_drivers` ganharia vínculos que hoje são 0 |
| **perda de informação** | nenhuma, **se** a reconstrução for verificada antes da remoção |
| **reversibilidade** | depende inteiramente do backup |
| **validação em ambiente descartável** | sim, e é onde deve ocorrer |
| **rejeitar quando** | as URLs não responderem, ou o conteúdo não casar — aí S2 volta a `UNKNOWN` |

`PROPOSED`: é a única estratégia que **recupera** informação em vez de
apenas organizar a sua ausência.

### C. Remoção controlada e reingestão

Apagar as 324.720 linhas e os `cars` correspondentes, depois reingerir.

| | |
|---|---|
| **pré-condições** | Fase 0; re-apontamento prévio das 158.275 voltas; política de colisões aprovada |
| **evidências necessárias** | as de #43 §3.1 e §3.2 |
| **dependências** | **1.566 `cars` sustentam 158.275 voltas** — remover antes de re-apontar as órfã |
| **impacto** | `results` cai para 346.633; `cars` idem; `laps` preservadas se re-apontadas; `result_drivers` inalterado (hoje 0 nessas sessões) |
| **perda de informação** | **nenhuma do que a fonte tem, mas tudo o que a reconstrução traria, se feita antes dela** |
| **reversibilidade** | só por restauração |
| **validação em ambiente descartável** | obrigatória |
| **rejeitar quando** | executada **antes** de B. `DERIVED`: apagar primeiro não impede reconstruir depois, mas remove a chance de comparar o reconstruído com o que estava lá |

`PROPOSED`: **rejeitar como primeira ação.** Com a fonte existindo, remover
antes de tentar reconstruir é destruir a única cópia local de um dado cuja
substituição ainda não foi verificada.

### D. Híbrida

Reconstruir onde a fonte se comprovar, preservar o resto.

| | |
|---|---|
| **pré-condições** | as de B, por subgrupo |
| **evidências necessárias** | por sessão, não por bloco |
| **dependências** | as de B |
| **impacto** | proporcional ao que se comprovar |
| **perda de informação** | nenhuma |
| **reversibilidade** | por subgrupo |
| **validação em ambiente descartável** | sim |
| **rejeitar quando** | a fragmentação tornar o acervo incoerente — parte das corridas com classificação, parte sem, sem marca que distinga |

`PROPOSED`: **é a estratégia que a evidência sustenta hoje**, porque a
evidência já é heterogênea: S1 e S2 não estão no mesmo estado.

### 5.1 Comparação

| | A preservar | B reconstruir | C remover | D híbrida |
|---|---|---|---|---|
| autorização de rede | não | **sim** | não | **sim** |
| altera `raw/` | não | **sim** | não | **sim** |
| altera o loader | não | sim, para S2 | não | sim, para S2 |
| exige Fase 0 | não | **sim** | **sim** | **sim** |
| exige política de colisões | não | não | **sim** | só se remover |
| recupera informação | não | **sim** | não | **sim** |
| reversível sem backup | **sim** | não | não | não |
| executável hoje | **sim** | não | não | não |

---

## 6. Dependências e impacto referencial

As três contagens que o escopo pede para separar, e que **não são
intercambiáveis**:

| # | contagem | valor | o que é |
|---|---|---:|---|
| 1 | **registros contaminados** | **324.720** `results` + **324.720** `cars` | `MEASURED` (#43 §3.5) |
| 2 | **registros dependentes** | **158.275** `laps`, apontando para **1.566** `cars` | `MEASURED` — dependem, mas **não são contaminados**: vieram do arquivo de análise lido como análise |
| 3 | **candidatos a alteração** | **depende da estratégia** | A: 0 · B: 324.720 substituídos + 158.275 re-apontadas · C: 324.720 removidos + 158.275 re-apontadas · D: por subgrupo |

`DERIVED`: 323.154 dos 324.720 `cars` não são referenciados por nada além
da própria linha de `results` (#43 §3.5). Os outros 1.566 são a dependência
crítica.

### 6.1 Restrições de integridade contra convenções do loader

| relação | garantida por | consequência |
|---|---|---|
| `laps.car_id → cars.id` | **FK** | remover `cars` antes de re-apontar é recusado pelo banco |
| `laps.session_id → sessions.id` | **FK** | — |
| `results.car_id → cars.id` | **FK** | — |
| `result_drivers.driver_id → drivers.id` | **FK** | — |
| `results UNIQUE (session_id, car_id)` | **restrição** | reconstrução não colide: `car_id` novo |
| `laps UNIQUE (session_id, car_id, lap_number)` | **restrição** | **35.045 colisões prospectivas** numa consolidação por tupla |
| `result_drivers UNIQUE (result_id, slot)` | **restrição** | — |
| qual arquivo alimenta qual sessão | **convenção do loader** | não há nada no banco que registre a proveniência — é por isso que a unidade de decisão precisa ser a sessão |
| `status = 'Other'` como ausência | **convenção do loader** | 630.869 linhas; nenhuma restrição a protege |

`MEASURED` (#46 §4.1): hoje há **0** referências órfãs em `laps` e
`result_drivers`.

### 6.2 Operações que podem deixar órfãos ou atingir dado legítimo

| operação | risco | mitigação |
|---|---|---|
| remover `cars` contaminados antes de re-apontar | 158.275 voltas órfãs — a FK recusa, interrompendo no meio | re-apontar antes |
| identificar contaminados **pela tupla** | atinge `cars` legítimos: **42 tuplas** estão nas duas populações | identificar sempre pela linha de `results` nas 52 sessões |
| consolidar `cars` por tupla | 35.045 colisões em `laps` | política de colisões antes |
| reingerir sem consolidar antes | escreve numa linha arbitrária de cada grupo | consolidar antes |
| apagar por predicado de sessão sem conferir a lista | atingiria sessões legítimas se a lista estiver errada | a lista tem duas evidências independentes (#43 §3.1) |

---

## 7. Política necessária para colisões

Não implementada aqui. São os critérios que uma política futura precisa
satisfazer.

### 7.1 `IDENTICAL` — 23.048

`MEASURED`: as duas linhas coincidem nos 15 atributos semânticos, e cada
grupo tem exatamente duas linhas vindas de **duas janelas de ingestão
distintas**.

**A política não pode remover automaticamente.** O que está demonstrado é
igualdade de conteúdo, não identidade de evento. Precisa estabelecer:

1. Que as duas linhas descrevem a mesma volta real, e não duas voltas que
   o acaso igualou — `UNKNOWN`, e é o entregável da Fase 1 do plano.
2. Qual proveniência sobrevive, já que as duas vêm de execuções diferentes.
3. O que fazer se a Fase 1 mostrar que alguma coincidência é fortuita.

### 7.2 `COMPLEMENTARY` — 695

`MEASURED`: só `driver_id` difere, por lacuna; os seis atributos de
cronometragem coincidem nos 695. `MEASURED`: em **652** o piloto está na
linha de **maior** id.

A política precisa preservar a linha que tem o piloto — e **não** a de
menor id, que é a regra canônica usada para `cars`. `DERIVED`: aplicar
"menor id" aqui perderia o piloto em 94 % dos casos.

### 7.3 `CONFLICTING` — 11.302

`MEASURED`: o conflito é **sempre e apenas** em `driver_id`. 11.242 têm
nome normalizado equivalente; 60 são `Nic` contra `Nicolas Minassian`.

**Os 11.242 não representam, por isso, a mesma pessoa.** `MEASURED`
(#46 §5): dos 1.079 grupos de nome equivalente, 36 são integralmente
corroborados, 9 parcialmente, 376 exigem revisão e **651 não têm nenhuma
evidência além do nome**.

> **`MEASURED` (#46 §7.2), e esta é a conclusão que a política precisa
> absorver: consolidar somente identidades corroboradas resolve ZERO
> colisões.** Os 604 que uma consolidação alcançaria vêm de dois grupos
> parciais, e neles as duas linhas em conflito são justamente as **sem**
> corroboração.

A política precisa decidir se aceita resolver conflito de `driver_id` sem
resolver identidade — por exemplo preservando as duas referências — ou se
bloqueia até haver evidência externa.

### 7.4 Os 60 `Nic` / `Nicolas`

`MEASURED`: a fonte escreve `Nicolas MINASSIAN` nas 60 voltas; `Nic` não
aparece no corpus. `UNKNOWN`: se são a mesma pessoa. `MEASURED` (#47): o
par está `pending` e não pode ser aplicado.

---

## 8. Matriz de decisões

| # | situação | evidência disponível | evidência faltante | admissível | proibido | dependências | invariante | decisão humana |
|---|---|---|---|---|---|---|---|---|
| 1 | 13.488 `results` de **S1** (fonte `03_`) | duas independentes + fonte publicada | URL responde? conteúdo casa? | B, D, A | C antes de B | Fase 0, rede | `laps` da sessão preservadas | aprovar aquisição da fonte |
| 2 | 311.232 `results` de **S2** (fonte `05_`) | idem | idem + filtro do loader | B, D, A | C antes de B | Fase 0, rede, loader | idem | aprovar aquisição **e** extensão do filtro |
| 3 | 323.154 `cars` sem outra referência | `MEASURED` | — | C, D | identificação por tupla | Fase 0 | `cars` legítimos intocados | destino das linhas |
| 4 | 1.566 `cars` que sustentam voltas | `MEASURED` | — | re-apontar | remover antes de re-apontar | Fase 0 | 158.275 voltas preservadas | regra do `car_id` canônico |
| 5 | 131 entradas sem volta | `MEASURED` | por que não têm | A | tratar como as outras | — | — | investigar |
| 6 | 23.048 colisões `IDENTICAL` | conteúdo igual | identidade do evento | — | remoção automática | Fase 1 | `laps` sem perda não justificada | política de colisões |
| 7 | 695 `COMPLEMENTARY` | cronometragem igual, lacuna de piloto | — | preservar quem tem piloto | regra "menor id" | Fase 1 | piloto preservado em 695 | política de colisões |
| 8 | 11.242 `CONFLICTING` de nome equivalente | nome equivale | **identidade** | — | fundir por nome | #46, evidência externa | nenhuma identidade fundida sem evidência | consolidação de pilotos |
| 9 | 60 `Nic`/`Nicolas` | fonte diz `Nicolas` | identidade da pessoa | — | aplicar a fusão | — | par continua `pending` | identidade |
| 10 | 14 duplicações em `result_drivers` | `MEASURED` | causa | A | alterar | — | **continuar exatamente 14** | investigar |
| 11 | 342.505 `cars` excedentes legítimos | `MEASURED` | — | consolidação classe A | — | #6, Fase 1 | 94.438 entradas lógicas | regra de colisão |

### 8.1 Estados

| situação | estado |
|---|---|
| 1, 2 | **`NEEDS_SOURCE_VERIFICATION`** — a fonte existe; falta verificar que responde e casa |
| 3, 4, 11 | **`BLOCKED_BY_COLLISION_POLICY`** e **`BLOCKED_BY_BACKUP`** |
| 6, 7 | **`BLOCKED_BY_COLLISION_POLICY`** |
| 8, 9 | **`NEEDS_IDENTITY_REVIEW`** |
| 5, 10 | **`UNRESOLVED`** — nem investigadas |
| nenhuma | `EVIDENCE_SUFFICIENT` |

**Nenhuma situação está em `EVIDENCE_SUFFICIENT`.** `DERIVED`: nenhuma
operação de recuperação pode começar hoje, qualquer que seja a estratégia.

---

## 9. Pré-condições de segurança

### Fase 0 — backup verificado

1. Dump consistente do banco de desenvolvimento.
2. Restauração num ambiente descartável — **que não existe hoje**
   (`UNKNOWN`: onde seria criado; a porta 5440 está fora de escopo).
3. Contagens conferidas contra §2 deste documento, e hash do conjunto
   ordenado das tabelas grandes.
4. Procedimento de rollback escrito antes de começar: qual dump, qual
   comando, quanto tempo.
5. Evidência de que o backup restaura — a existência do arquivo não é
   evidência.

### Simulação

Somente no ambiente descartável. Comparar antes e depois:

| verificação | valor esperado |
|---|---|
| referências órfãs | 0, antes e depois |
| duplicações em `result_drivers` | **exatamente 14** |
| colisões de voltas | 35.045 antes; depois, o previsto pela política |
| entradas lógicas legítimas | 94.438, inalteradas |
| `laps` | 2.034.223 menos exatamente o que a política previu descartar |
| `cars` legítimos | nenhum tocado — conferir que os ids alterados ⊆ escopo |

### Autorização

- Decisão humana explícita, por escrito, por situação da matriz.
- Escopo por identificadores — a lista das 52 sessões, não "as
  contaminadas".
- Plano de execução revisado.
- Critérios de aceite mensuráveis, dos §9 e §10 do plano.

> Nada disso está satisfeito pela existência deste documento. Ele lista as
> condições; não as cumpre.

---

## 10. Sequência recomendada

`PROPOSED`. A ordem reflete dependências medidas, não a numeração do
escopo.

```
  [1] verificar a fonte          ──┐  independente, sem escrita
      URLs respondem?              │
      conteúdo casa o parser?      │
                                   │
  [2] Fase 0 — backup            ──┤  independente
      restauração conferida        │
                                   │
  [3] inventário das colisões    ──┘  independente, leitura
      conteúdo campo a campo dos 35.045
            │
            └──► [4] política de colisões      decisão humana
                      │
                      ├──► [5] re-apontar voltas (1.566 cars)
                      │          │
                      │          └──► [6] tratar os results contaminados
                      │
                      └──► [7] consolidar cars legítimos
                                 │
                                 └──► [8] reingestão
                                            │
                                            └──► [9] validação analítica
```

### 10.1 O que pode avançar já

| atividade | por quê |
|---|---|
| **[1] verificar a fonte** | só leitura de rede; nenhuma escrita. **É a de maior valor**: decide se B e D são viáveis |
| **[2] Fase 0** | não depende de nenhuma decisão |
| **[3] inventário das colisões** | leitura; é a Fase 1 do plano, ainda não feita |

### 10.2 O que permanece bloqueado

| atividade | bloqueio |
|---|---|
| [4] política de colisões | depende de [3] |
| [5]–[6] | `BLOCKED_BY_COLLISION_POLICY`, `BLOCKED_BY_BACKUP` |
| [7]–[8] | idem, mais a regra de sobrevivente |
| consolidação de pilotos | `NEEDS_IDENTITY_REVIEW`; #46 mostra que o corroborável resolve zero |
| [9] | depende de tudo |

`DERIVED`: as três atividades livres **não escrevem nada**. A primeira
escrita do projeto ainda está a quatro decisões de distância.

---

## 11. Decisões humanas pendentes

| # | decisão | bloqueia | evidência que falta |
|---|---|---|---|
| 1 | **Autorizar a verificação da fonte** (rede, leitura) | B e D inteiras | nenhuma — é a decisão de olhar |
| 2 | Autorizar aquisição dos arquivos e alteração de `raw/` | B e D | resultado de 1 |
| 3 | Estender o filtro do loader para a família `05_` | B e D em S2 | resultado de 1 |
| 4 | Estratégia: A, B, C ou D | tudo | resultados de 1 a 3 |
| 5 | Política de colisões, por categoria | toda alteração de referência | inventário da Fase 1 |
| 6 | Regra do `car_id` canônico | re-apontamento | — |
| 7 | Consolidação de pilotos | 11.302 colisões | evidência externa |
| 8 | Onde fica o ambiente descartável | Fase 0 | — |

**A decisão 1 é a que destrava mais com menos risco.** `DERIVED`: ela não
escreve em lugar nenhum, e o seu resultado determina se 324.720 linhas são
reconstruíveis ou se voltam ao estado `UNRECOVERABLE` que a PR #43
supunha.

---

## 12. Próxima etapa proposta

`PROPOSED`, e nenhuma está autorizada:

1. **Verificação da fonte**, em PR própria: tentar obter os
   `05_Classification*` e `03_Results` das 51 sessões, **sem gravá-los em
   `raw/`**, e relatar quantas URLs respondem, se o conteúdo casa com um
   dialeto conhecido, e quantas linhas por carro traz. Resolve a decisão 1
   e informa as decisões 2 a 4.
2. **Fase 1 — inventário das colisões**, em paralelo: comparar campo a
   campo os 35.045 pares, que o plano já define e que ninguém fez.
3. **Fase 0**, quando houver onde.

As três são leitura. Nenhuma depende das outras.

---

## 13. Correções aos documentos anteriores

| documento | afirmação | correção |
|---|---|---|
| `historical-recovery-assessment.md` §3.4 e matriz A2 | conteúdo de classificação `UNRECOVERABLE`, "não existe fonte da qual reconstruir" | **A ausência é em `raw/`.** O catálogo publica CSV de classificação para as 51 sessões. O estado correto é `NEEDS_SOURCE_VERIFICATION` |
| `recovery-plan.md` Fase 1 | "as 51 sessões ficariam sem classificação, que é o estado correto: a fonte não publica uma para elas" | a fonte publica; o download não a buscou |

`PROPOSED`: essas correções sejam aplicadas aos documentos originais quando
a decisão 1 der o seu resultado — se as URLs não responderem,
`UNRECOVERABLE` volta a ser o estado correto, por outro motivo. Até lá, o
registro desta divergência fica aqui, e **as medições originais não foram
reescritas**.

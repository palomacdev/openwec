# Evidence reconciliation

Reconcilia os documentos de integridade com o que as PRs #48, #49 e #50
mediram, **sem apagar o histórico das investigações**.

Cada afirmação corrigida continua legível na sua forma original, com a data
em que foi escrita, o defeito ou o limite que a investigação posterior
expôs, e a evidência que a substitui. A medição de origem é preservada:
onde ela estava certa e a conclusão errada, isso é dito; onde a medição
estava errada, o defeito é nomeado.

**Somente documentação.** Nenhuma escrita no banco histórico ou em `raw/`,
nenhum loader, migration, enrichment, fusão, normalização ou reingestão,
nenhuma alteração de runtime ou de parser.

**`main` em:** `3e1b225` (após a PR #50). **Data:** 2026-10-23.
Estatutos: **`MEASURED`** · **`DERIVED`** · **`PROPOSED`** · **`UNKNOWN`**.

---

## 1. Estado das fontes, hoje

`MEASURED`, consolidado das PRs #48, #49 e #50:

| | |
|---|---:|
| sessões contaminadas | **51** |
| entradas de catálogo que as representam | **52** |
| URLs de classificação investigadas (PR #49) | **182** |
| que responderam `HTTP 200` com CSV acessível | **182** |

As duas primeiras linhas não são a mesma grandeza e não devem ser somadas
nem trocadas: uma chave de sessão casa duas entradas de catálogo — a sessão
IMSA de Watkins Glen, que dois campeonatos publicam sob o mesmo nome.

`MEASURED` (PR #50), por **entrada de catálogo**:

| estado | entradas |
|---|---:|
| `FINALITY_CORROBORATED` | **49** |
| `PARTIAL_OR_DIVERGENT` | **1** |
| `INDETERMINATE` | **2** |

> **`FINALITY_CORROBORATED` significa evidência de classificação posterior
> à chegada — não prova de homologação definitiva.** A definição integral
> está em §9.1 de
> [classification-snapshot-finality-assessment.md](classification-snapshot-finality-assessment.md).
> O que está medido é que a corrida havia terminado quando o arquivo foi
> montado. `UNKNOWN`: se houve versão revista depois, se penalizações
> pós-corrida estavam incorporadas, e se o arquivo é a versão homologada.

**`MEASURED`: nenhuma reconstrução foi realizada.** Nada neste documento,
nem nos que ele corrige, autoriza operação alguma sobre o acervo.

---

## 2. Os dois erros de medição da PR #49

Corrigidos pela PR #50. Ambos vieram da minha rotina de verificação, não
dos dados.

### 2.1 Le Mans Cup 2018 — Paul Ricard e Monza

| | |
|---|---|
| **medição antiga** | "sem interseção" de números de carro entre a fonte e a análise local; estado `ACCESSIBLE_SCHEMA_UNKNOWN` |
| **documento** | `historical-classification-source-verification.md`, §3 e §8 nota 3 |
| **defeito** | a rotina descartava toda linha com menos campos que o cabeçalho (`if len(p) <= max(idx.values()): continue`). Esses arquivos têm **93 colunas no cabeçalho e 73 campos nas linhas**, então **nenhuma linha foi lida** e o conjunto de números saiu vazio — e um conjunto vazio não tem interseção com nada |
| **correção** | `MEASURED` (PR #50 §7.1 e §7.2): interseção real **28 de 28** (Paul Ricard) e **27 de 27** (Monza) |
| **interpretação atual** | Paul Ricard: `FINALITY_CORROBORATED`. Monza: `PARTIAL_OR_DIVERGENT` — a fonte casa a sessão, mas o líder termina **288 s antes** da duração nominal e 24 de 27 carros divergem em voltas |
| **pendência** | a validação do parser para esses arquivos, §4.1 |

`DERIVED`: o mesmo defeito que produziu o falso negativo é o que o §11.1 da
PR #50 registra como pré-condição. Não é coincidência: foi ele que revelou
o risco.

### 2.2 IMSA 2016 — Watkins Glen

| | |
|---|---|
| **medição antiga** | sessão corroborada contra `03_Results.CSV`; estado `VERIFIED_FINAL_CLASSIFICATION` na PR #49, e `ACCESSIBLE_SCHEMA_UNKNOWN` por "sem referência local" |
| **documento** | `historical-classification-source-verification.md`, §3 (subgrupo S1) e §9 |
| **defeito** | duas entradas de catálogo partilham a chave da sessão, uma por campeonato. O `03_Results.CSV` escolhido é do **`02_IMSA Continental Tire SportsCar Challenge`**; a sessão contaminada é do **`01_IMSA WeatherTech SportsCar Championship`**. `MEASURED`: interseção de **10 em 36** com a análise local |
| **por que faltou referência local** | `MEASURED`: `load_laps.RAW_DIRS` **não inclui IMSA**, então o índice de análises não a continha. O arquivo existe em `raw/.../Race/other/Hour 6/23_Analysis_Race.CSV` |
| **correção** | `MEASURED` (PR #50 §7.3): a fonte que corresponde é **`05_Results by Hour.CSV`** da entrada WeatherTech, com **39 de 39** carros da análise local, líder a **+22 s** da marca de 6 h e **zero** divergências de voltas |
| **interpretação atual** | `FINALITY_CORROBORATED`, por fonte diferente da que a PR #49 usou |
| **pendência** | incluir IMSA em `load_laps.RAW_DIRS` — escopo novo, §4.1 |

`DERIVED`: a descrição de **subgrupo S1** na PR #48 §4.3 e na PR #49 §3 —
"1 sessão, família `03_`" — **continua correta quanto à contagem** e
**incorreta quanto à família**: a fonte que lhe corresponde é `05_`, como
as outras 50.

---

## 3. O estado `UNRECOVERABLE`

Três leituras distintas, que não devem ser confundidas nem substituídas em
bloco.

### 3.1 Atribuído com a informação da época

`MEASURED` (PR #43, 2026-10-22): as 51 pastas de `raw/` não têm arquivo de
classificação. **Esta medição continua correta.**

A conclusão tirada dela — "não existe fonte da qual reconstruir" — tratava
`raw/` como equivalente à fonte. `DERIVED`: não é. O critério da PR #43
estava certo ao exigir ausência demonstrada; o que não estava delimitado
era o **alcance da palavra "ausência"**.

### 3.2 Estado atual, após verificação

`MEASURED` (PRs #48–#50): a fonte existe, responde, está no dialeto que o
parser lê e traz os 14 campos. A classe `UNRECOVERABLE` ficou **vazia**.

| dimensão | antes | hoje |
|---|---|---|
| A2 — reconstruir o conteúdo de classificação | `UNRECOVERABLE` | **`RECOVERABLE_CONDITIONAL`** |

`DERIVED`: "condicional" não é formalidade. As condições estão em §4.

### 3.3 O que ainda tem incerteza

| caso | estado | por quê |
|---|---|---|
| Le Mans Cup 2018, Monza | `PARTIAL_OR_DIVERGENT` | líder 288 s antes da nominal; 24 de 27 carros divergem |
| Le Mans Cup 2017, Red Bull Ring (2 entradas, 1 sessão) | `INDETERMINATE` | líder 89 s antes da nominal; `UNKNOWN` se a corrida foi encurtada ou se o arquivo antecede o fim |
| as 49 corroboradas | corroboradas **como pós-chegada** | `UNKNOWN` se alguma foi revista depois |

`PROPOSED`: as três primeiras linhas fiquem fora de qualquer primeira
reconstrução.

---

## 4. Pré-condições operacionais, todas pendentes

`MEASURED`: nenhuma está satisfeita.

| # | pré-condição | origem | estado |
|---|---|---|---|
| 1 | **Validar o parser para cabeçalho de 93 colunas com linhas de 73** | PR #50 §11.1 | pendente — já produziu um falso negativo (§2.1) |
| 2 | **Decidir entre classificação pós-chegada e homologada** | PR #50 §9.1 | pendente |
| 3 | **Autorizar download persistente** para `raw/` | PR #48 | pendente |
| 4 | **Estender o filtro de classificação** para `05_Classification*` e `05_Results by Hour*`, com as três grafias de hora | PR #49 §6.1, PR #50 §3.1 | pendente |
| 5 | **Ambiente descartável e backup verificado** (Fase 0) | `recovery-plan.md` | pendente — `UNKNOWN`: onde o ambiente seria criado |
| 6 | **Inventário campo a campo das 35.045 colisões de voltas** (Fase 1) | `recovery-plan.md` | pendente |
| 7 | **Investigar as 8 sessões adicionais** — 5 da Le Mans Cup sem dado algum, 3 da ELMS 2011 com arquivo e zero linhas | PR #49 §7 | pendente |

### 4.1 Duas delas não estavam em nenhuma lista

`DERIVED`: a pré-condição 1 nasceu de um defeito da própria investigação, e
a 7 de um efeito colateral de verificar sessões que não eram o alvo. Nenhuma
das duas aparecia no plano original.

Some-se a elas **incluir IMSA em `load_laps.RAW_DIRS`** (§2.2), que é
escopo novo e não bloqueia a reconstrução, mas explica por que uma sessão
ficou sem referência local.

> **A reconstrução não está pronta para execução.** Sete pré-condições
> pendentes, duas sessões com incerteza registrada, e a decisão 2 ainda em
> aberto quanto ao próprio significado do que seria reconstruído.

---

## 5. Matriz de rastreabilidade

| # | afirmação original | documento · seção | evidência posterior | interpretação atual | alteração realizada | pendência |
|---|---|---|---|---|---|---|
| 1 | "Não existe fonte da qual reconstruir posição, tempo total, status ou melhor volta" | `historical-recovery-assessment.md` §3.4 | #48: catálogo publica para as 51 · #49: 182/182 respondem | a ausência era de `raw/`; a fonte existe | nota de correção antes do parágrafo original, que **permanece íntegro**, e novo §3.4.1 com as medições | pré-condições §4 |
| 2 | A2 = `UNRECOVERABLE` | `historical-recovery-assessment.md` §7 | idem | `RECOVERABLE_CONDITIONAL` | célula da matriz marcada com o estado antigo tachado e o novo, com referência | idem |
| 3 | "A remoção dessas linhas não perde informação" | `historical-recovery-assessment.md` §7 | #48–#50 | **deixa de valer**: removê-las apagaria registros com substituição disponível | justificativa reescrita citando a original entre aspas | decisão sobre o destino das 324.720 |
| 4 | "Nada foi classificado como `UNRECOVERABLE` por falta de investigação" | `historical-recovery-assessment.md` §7 | #48–#50 | continua verdadeira; a classe ficou vazia | nota de que o critério estava certo e o alcance de "ausência" não estava delimitado | — |
| 5 | "as 51 sessões ficariam sem classificação, que é o estado correto: a fonte não publica uma para elas" | `recovery-plan.md` Fase 1, decisão 2 | #48 | a fonte publica; o download não a buscou | decisão 2 tachada e reformulada | decisão: obter ou não |
| 6 | "O conteúdo de classificação nunca existiu para essas sessões" | `recovery-plan.md` Fase 4 | #48–#50 | existe e responde | parágrafo reescrito citando a avaliação anterior | decisão sobre o destino |
| 7 | "essas correções sejam aplicadas quando a decisão 1 der o seu resultado" | `historical-recovery-decision-framework.md` §13 | #49: as URLs responderam | a condição foi satisfeita | nota de que esta PR as aplicou | — |
| 8 | subgrupo **S1**: "1 sessão, fonte `03_Results`" | `historical-recovery-decision-framework.md` §4.3 · `historical-classification-source-verification.md` §3 | #50 §7.3: campeonato errado | a contagem está certa; a família é `05_Results by Hour` | novo §13.1 no framework | incluir IMSA em `load_laps` |
| 9 | duas entradas LMC 2018 "sem interseção" | `historical-classification-source-verification.md` §9 | #50 §7.1–7.2 | 28/28 e 27/27 | §2.1 deste documento | validar o parser |
| 10 | "a classificação final está verificada em 16 entradas" | `historical-classification-source-verification.md` §1 | #50 | 49 corroboradas, 1 divergente, 2 indeterminadas | §1 deste documento | decisão 2 do §4 |

`DERIVED`: as linhas 1 a 7 foram **aplicadas nos documentos originais**; as
8 a 10 são registradas aqui e referenciadas de lá, porque alterar as tabelas
de resultado da PR #49 apagaria a medição que a PR #50 corrigiu — e é essa
sequência que se quer preservar.

### 5.1 O que não foi alterado, de propósito

- **Nenhuma contagem de PR anterior foi reescrita.** Os 16/32/4 da PR #49
  continuam lá como o que aquela investigação mediu; a evolução para
  49/1/2 está registrada, não substituída.
- **Os arquivos da PR #50** não foram tocados: eles já nascem com as
  correções.
- **Nenhum `UNRECOVERABLE` foi apagado.** Cada ocorrência ou ganhou
  contexto ou ficou como está, conforme §3.

---

## 6. Consistência entre os documentos

`MEASURED`, conferido nesta PR:

| grandeza | valor | onde aparece |
|---|---:|---|
| sessões contaminadas | **51** | #43, #48, #49, #50 |
| entradas de catálogo | **52** | #43, #48, #49, #50 |
| linhas de `results` contaminadas | **324.720** | todas |
| URLs verificadas | **182** | #49, #50 |
| entradas corroboradas | **49** | #50, este documento |
| carros comparados | **1.113** | #50 |
| colisões de voltas | **35.045** | #44, #46, #48 |

`DERIVED`: **sessão** e **entrada de catálogo** são grandezas diferentes em
todos os documentos — 51 e 52 — e a diferença tem causa nomeada (§1).

---

## 7. O que esta reconciliação não faz

1. **Não autoriza nada.** Nenhum estado aqui é permissão.
2. **Não declara a reconstrução pronta.** §4 lista sete pré-condições
   pendentes.
3. **Não apresenta classificação pós-chegada como homologada.** §1.
4. **Não altera parser, downloader, runtime, `raw/` ou banco.**
5. **Não refaz medição nenhuma.** Todos os números vêm das PRs citadas.

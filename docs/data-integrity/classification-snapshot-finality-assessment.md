# Classification snapshot finality assessment

Se o último snapshot horário de cada sessão representa a classificação
final. **Somente leitura.** Nenhuma escrita no banco, nada gravado em
`raw/`, nenhum CSV baixado persistido, nenhum loader, migration, enrichment
ou script executado, nenhum parser ou runtime alterado.

**Ambiente:** `openwec@127.0.0.1:5433` para leitura; requisições HTTP de
leitura ao publicador. **Data:** 2026-10-23. **`main` em:** `c5a8195`.

Estatutos: **`MEASURED`** · **`DERIVED`** · **`PROPOSED`** · **`UNKNOWN`**.

---

## 1. Resumo executivo

A PR #49 deixou 32 entradas em `VERIFIED_PARTIAL_CLASSIFICATION` porque
publicavam apenas snapshots horários, e a pergunta era se o último deles
vale como classificação final.

**Vale, em quase todas — e por um motivo que o nome do arquivo esconde.**

`MEASURED`: em **31 de 32** sessões, o `TOTAL_TIME` do primeiro colocado no
último snapshot **excede a duração nominal** da corrida — mediana +58 s,
máximo +105 s. Um arquivo chamado `Hour 4` numa corrida de 4 horas não é um
retrato tirado às 4:00:00; é a classificação **ao fim da quarta hora**, com
a volta final já incluída. A nomenclatura é por segmento de corrida, não
por instante.

| | entradas |
|---|---:|
| `FINALITY_CORROBORATED` | **33** |
| `PARTIAL_OR_DIVERGENT` | **1** |
| `INDETERMINATE` | **2** |

> **O que `FINALITY_CORROBORATED` afirma, e o que não afirma.** Ele
> significa: *evidência consistente de que o arquivo representa a
> classificação **após o encerramento da corrida**, sem comprovação
> independente de que seja a versão oficial definitiva depois de eventuais
> revisões.*
>
> O que as medições demonstram é que a corrida já havia terminado quando o
> arquivo foi montado, que ele cobre todos os carros que a análise conhece,
> e que voltas, equipe e classe coincidem. O que elas **não** demonstram:
>
> - que não houve versão publicada depois;
> - que penalizações aplicadas após a bandeirada já estavam incorporadas;
> - que o arquivo é a classificação oficial definitiva;
> - que posição e status foram validados contra uma segunda fonte final —
>   `MEASURED`: a análise local não traz nenhum dos dois (§2.1).
>
> `DERIVED`: "posterior ao fim da corrida" e "oficial definitiva" são
> afirmações diferentes, e só a primeira está medida.

`MEASURED`: **1.113 carros comparados**, 1.101 presentes nas duas fontes,
12 apenas na classificação, **zero apenas na análise**, 37 divergências de
voltas.

### 1.1 Duas correções à PR #49

**`MEASURED`, e as duas vêm de defeitos meus de medição, não dos dados.**

1. **Duas entradas da Le Mans Cup 2018 foram dadas como "sem interseção" de
   números de carro.** A interseção real é **28 de 28** e **27 de 27**. A
   rotina da #49 descartava toda linha com menos campos que o cabeçalho, e
   esses são exatamente os arquivos de 93 colunas com linhas de 73 que a
   própria #49 documentou no seu §6.2. O conjunto de números saía vazio, e
   um conjunto vazio não tem interseção com nada.
2. **A sessão IMSA de Watkins Glen foi corroborada contra o arquivo do
   campeonato errado.** O `03_Results.CSV` que casei pertence ao
   `02_IMSA Continental Tire SportsCar Challenge`; a sessão contaminada é do
   `01_IMSA WeatherTech SportsCar Championship`. `MEASURED`: interseção de
   **10 em 36** com a análise local. A fonte certa é
   `05_Results by Hour.CSV` da entrada WeatherTech — **39 de 39** carros.

---

## 2. Escopo e metodologia

**Entradas investigadas:** as 32 `VERIFIED_PARTIAL_CLASSIFICATION` e as 4
`ACCESSIBLE_SCHEMA_UNKNOWN` da PR #49 — 36 entradas de catálogo,
**35 sessões distintas** (duas entradas IMSA apontam para a mesma sessão).

**Requisições:** `GET` com `User-Agent` próprio, sem credenciais; timeout de
10 s para conexão e 25 s para leitura; 0,3 a 0,4 s entre chamadas; conteúdo
analisado em memória. `MEASURED`: **nenhum byte baixado foi gravado**.

**Fontes locais:** os `23_Analysis*` que já estão em `raw/`, lidos pelo
`read_csv` do próprio loader.

### 2.1 O que a análise local não tem

`MEASURED`: o arquivo de análise traz `NUMBER`, `LAP_NUMBER`, `LAP_TIME`,
`ELAPSED`, `HOUR`, `TEAM`, `CLASS` — e **não traz posição final nem
status**. Portanto:

- posição e status foram lidos **só** da classificação, sem equivalente
  local a comparar;
- a comparação carro a carro cobre **número, voltas, equipe e classe**;
- onde o campo não existe, nenhuma equivalência foi inventada.

---

## 3. Inventário dos snapshots

`MEASURED`, as 32 entradas parciais:

| | |
|---|---:|
| snapshots horários publicados | **117** |
| sessões distintas | 32 |
| arquivo explicitamente final despercebido | **0** |
| sequências horárias com lacuna | **0** |

Distribuição por número de snapshots: 2 em 5 sessões, 3 em 3, 4 em 23, 6 em 1.

### 3.1 Convenções de nomenclatura

`MEASURED`, três grafias para a mesma coisa:

| grafia | arquivos |
|---|---:|
| `_Hour N.CSV` | 102 |
| `_N Hour.CSV` / `_N Hours.CSV` | 9 |
| `_NH.CSV` | 6 |

A ordenação foi feita **pela hora extraída do nome**, não pela ordem
alfabética — `_10H` vem depois de `_9H`, o que a ordem textual inverteria.

---

## 4. Comparação carro a carro

`MEASURED`, último snapshot contra a análise local, nas 32 entradas
parciais:

| métrica | valor |
|---|---:|
| carros comparados | **1.017** |
| presentes nas duas fontes | **1.007** |
| apenas na classificação | **10** |
| apenas na análise | **0** |
| divergências de voltas | **12** |
| sessões sem nenhuma divergência nem carro faltando | **17** de 32 |

`MEASURED`: todos os 32 snapshots trazem `POSITION`, `STATUS`, `CLASS`,
`TEAM`, `TOTAL_TIME` e `LAPS`.

### 4.1 Os 10 carros presentes só na classificação

`MEASURED`: **todos têm `LAPS = 0`**, e o status é `Not started` (2) ou
`Not classified` (8).

```
08_2013/04_HUNGARORING   carro 60  laps=0 pos=25 status='Not started'
09_2014/04_PAUL RICARD   carro 41  laps=0 pos=37 status='Not classified'
11_2016/05_SPA           carro 33  laps=0 pos=39 status='Not classified'
```

`DERIVED`: **não é divergência.** Um carro que não completou volta
cronometrada não pode aparecer num arquivo de voltas. É informação que **só
a classificação tem**, e é um argumento a favor de obtê-la: sem ela, esses
carros não existem no acervo.

### 4.2 As 12 divergências de voltas

`MEASURED`: **todas com status `Classified`**, e em **todas** a análise
conta **mais** passagens do que a classificação declara voltas — de 1 a 5 a
mais.

```
08_2013/05_PAUL RICARD  carro 67  classificacao=87   analise=88
10_2015/03_RED BULL RING carro 5  classificacao=133  analise=136
12_2017/01_SILVERSTONE  carro 29  classificacao=117  analise=122
```

`DERIVED`: a direção é consistente — a análise registra **passagens pela
linha**, a classificação registra **voltas que contam**. `UNKNOWN`: a regra
exata que separa as duas. Não foram tratadas como erro, conforme o escopo.

### 4.3 Status encontrados

`MEASURED`, nas 32 classificações: `Classified` 857, `Not classified` 153,
`Excluded` 4, `Not started` 2, `Retired` 1.

---

## 5. Análise temporal

### 5.1 O teste que parecia contradizer

`MEASURED`: em **31 de 32** sessões, a análise local registra passagens
**depois** da marca horária do snapshot — mediana +166 s, máximo +614 s.

Isoladamente isso sugeriria que a corrida continuou após o snapshot. Mas
`MEASURED`: só **12 de 1.007** carros divergem em `LAPS`. Se o snapshot
tivesse sido tirado na marca exata, quase todos os carros teriam uma volta a
mais na análise.

### 5.2 O que resolve a contradição

`MEASURED`: o `TOTAL_TIME` do primeiro colocado no último snapshot:

| | sessões |
|---|---:|
| **maior** que a duração nominal | **31** |
| menor | 1 |

Delta mediano **+58 s**, máximo +105 s.

```
11_2016/01_SILVERSTONE  h4  nominal=14400s  lider=14505s  (+105)
12_2017/05_SPA          h4  nominal=14400s  lider=14501s  (+101)
10_2015/04_PAUL RICARD  h4  nominal=14400s  lider=14489s   (+89)
```

`DERIVED`: **o snapshot é posterior à bandeirada.** O líder cruza a linha
depois das 4 horas, e o arquivo o registra. As passagens ainda mais tardias
da análise (+166 s mediano) são **retardatários cruzando depois do
vencedor** — comportamento normal, não evidência de corrida em andamento.

`DERIVED`: as três grandezas que o escopo pede para separar ficam assim —
**duração nominal** é a marca do nome do arquivo; **duração efetiva** é o
`TOTAL_TIME` do líder, sempre maior; **horário de publicação** permanece
`UNKNOWN`, porque nenhum dos arquivos o carrega.

### 5.3 O que isto não prova

`UNKNOWN`: se houve classificação publicada **depois** do último snapshot e
revisada — por penalização aplicada após a corrida, por exemplo. Nenhum dos
arquivos carrega data de emissão, e não há como distinguir uma classificação
provisória de uma definitiva pelo conteúdo.

---

## 6. Comparação com classificações finais conhecidas

`MEASURED`: das 32 entradas parciais, **nenhuma** publica também um arquivo
não horário — por isso são parciais. A comparação direta foi possível apenas
nas entradas da Le Mans Cup 2018 (§7), que têm as duas coisas.

`MEASURED`, Le Mans Cup 2018 Paul Ricard, corrida de 2 horas com snapshots
`Hour 1` e `Hour 2` mais um `05_Classification_Race.CSV`:

| | líder |
|---|---|
| `05_Classification_Race.CSV` | 62 voltas, `2:01:25.448` → **+85 s** da nominal |

`DERIVED`: o arquivo não horário é posterior à bandeirada, exatamente como
os últimos snapshots das outras 31. `PROPOSED`: isto **apoia** a leitura de
que a família se comporta do mesmo modo — mas, conforme o escopo, **não
promove nenhum snapshot a final por analogia.** Cada sessão foi decidida
pela sua própria evidência temporal.

---

## 7. As quatro entradas desconhecidas

Investigadas individualmente. Nenhuma correspondência foi forçada.

### 7.1 Le Mans Cup 2018, Paul Ricard — era erro de medição meu

| | |
|---|---|
| diagnóstico da #49 | "sem interseção" de números de carro |
| **causa real** | `MEASURED`: a rotina da #49 descartava linhas mais estreitas que o cabeçalho; o arquivo tem 93 colunas e linhas de 73, então **nenhuma linha foi lida** |
| interseção real | **28 de 28** |
| líder | 62 voltas, `2:01:25.448`, **+85 s** da nominal de 2 h |
| carros | 28 na classificação, 28 na análise, 0 só num lado |
| divergências | 1 |
| **estado** | **`FINALITY_CORROBORATED`** |

### 7.2 Le Mans Cup 2018, Monza — fonte casa, finalidade não

| | |
|---|---|
| interseção real | **27 de 27** |
| líder | 59 voltas, `1:55:12.216`, **−288 s** da nominal de 2 h |
| divergências de voltas | **24 de 27 carros**, sempre com a análise à frente |
| **estado** | **`PARTIAL_OR_DIVERGENT`** |

`DERIVED`: o arquivo registra uma corrida mais curta do que a análise mostra
ter ocorrido. É o padrão oposto ao das 31 corroboradas.

### 7.3 e 7.4 IMSA 2016, Watkins Glen — campeonato errado

Duas entradas de catálogo para a mesma sessão, uma por campeonato.

| | |
|---|---|
| diagnóstico da #49 | "sem referência local", corroborada via `03_Results.CSV` |
| **causa real** | `MEASURED`: esse `03_Results.CSV` é do `02_IMSA Continental Tire SportsCar Challenge`; a sessão contaminada é do `01_IMSA WeatherTech`. Interseção **10 de 36** |
| por que faltou referência local | `MEASURED`: `load_laps.RAW_DIRS` **não inclui IMSA**, então o índice de análises não a continha. O arquivo existe em `raw/.../Race/other/Hour 6/23_Analysis_Race.CSV` |
| fonte correta | `05_Results by Hour.CSV` da entrada WeatherTech, `Hour 1` a `Hour 6` |
| interseção com a análise | **39 de 39** |
| líder | 197 voltas, `6:00'21.671`, **+22 s** da nominal de 6 h |
| carros | 41 na classificação, 39 na análise, 2 só na classificação, 0 só na análise |
| divergências de voltas | **0** |
| **estado** | **`FINALITY_CORROBORATED`**, por fonte diferente da que a #49 usou |

---

## 8. Matriz de divergências

`MEASURED`, agregando as 36 entradas:

| dimensão | valor |
|---|---:|
| carros comparados | **1.113** |
| presentes nas duas fontes | **1.101** |
| apenas na classificação | **12** — todos com `LAPS = 0` |
| apenas na análise | **0** |
| divergências de voltas | **37** |
| — nas 32 parciais | 12 |
| — Le Mans Cup 2018 Paul Ricard | 1 |
| — Le Mans Cup 2018 Monza | 24 |
| — IMSA Watkins Glen | 0 |
| divergências de posição | `UNKNOWN` — a análise não traz posição |
| divergências de status | `UNKNOWN` — a análise não traz status |

`DERIVED`: **31 das 37 divergências estão em duas sessões** — Monza 2018
concentra 24. Nas outras 34 entradas, a mediana de divergências por sessão
é 0.

---

## 9. Classificação de finalidade

| estado | entradas | sessões |
|---|---:|---:|
| `FINALITY_CORROBORATED` | **33** | 33 |
| `PARTIAL_OR_DIVERGENT` | **1** | 1 |
| `INDETERMINATE` | **2** | 1 |

### 9.1 O que `FINALITY_CORROBORATED` significa

**Definição.** Evidência consistente de que o arquivo representa a
classificação **após o encerramento da corrida**, sem comprovação
independente de que seja a versão oficial definitiva após eventuais
revisões.

| a evidência sustenta | a evidência **não** sustenta |
|---|---|
| a corrida terminou antes de o arquivo ser montado | que não houve revisão posterior |
| todos os carros da análise estão cobertos | que penalizações pós-corrida estão incorporadas |
| voltas, equipe e classe coincidem | que é a versão oficial definitiva |
| `STATUS` está preenchido | que posição e status foram validados contra outra fonte final |

`DERIVED`: a quarta coluna da direita é intransponível com o que existe
hoje — `MEASURED` (§2.1): o arquivo de análise **não traz posição nem
status**, então não há segunda fonte local contra a qual validá-los.
Confirmá-los exigiria uma terceira fonte, que não foi procurada.

### 9.2 Evidências que sustentam o estado

Quatro, independentes, exigidas em conjunto:

1. **`TOTAL_TIME` do líder maior que a duração nominal** — a corrida
   terminou antes de o arquivo ser montado.
2. **`LAPS` coincide** com o maior `LAP_NUMBER` da análise, com no máximo
   duas exceções por sessão.
3. **Nenhum carro aparece só na análise** — a classificação cobre todos os
   que correram.
4. **`STATUS` preenchido**, com estados terminais.

`DERIVED`: nenhuma sessão foi promovida só porque `LAPS` coincide. A
coincidência de voltas é a evidência 2 de 4, e sozinha não bastou em nenhum
caso — Monza 2018 tem fonte que casa perfeitamente em números de carro e
mesmo assim é `PARTIAL_OR_DIVERGENT`.

### 9.3 Os casos que não corroboraram

**`PARTIAL_OR_DIVERGENT` — Le Mans Cup 2018, Monza.** §7.2.

**`INDETERMINATE` — Le Mans Cup 2017, Red Bull Ring** (duas entradas de
catálogo, uma sessão). `MEASURED`: líder com 76 voltas e `1:58'30.790`,
**89 s antes** da nominal de 2 h; 21 carros nas duas fontes; 1 divergência.
`UNKNOWN`: por que a corrida terminou antes — bandeira vermelha, duração
reduzida, ou outra causa. O arquivo **pode** ser final de uma corrida
encurtada, e **pode** ser um snapshot anterior ao fim. Pela regra de
segurança do escopo, fica `INDETERMINATE`.

---

## 10. Limitações

1. **Verificação num instante.** `UNKNOWN` se as URLs responderão depois.
2. **Nada foi guardado**; reproduzir exige repetir as requisições.
3. **Posição e status não foram comparados** — a análise não os tem. A
   corroboração repousa em voltas, cobertura e tempo.
4. **`UNKNOWN`: classificação revisada depois da corrida.** Nenhum arquivo
   traz data de emissão.
5. **A regra que separa passagem de volta válida é `UNKNOWN`**, e explica
   as 37 divergências apenas por direção, não por mecanismo.
6. **Posição por classe não foi avaliada** — `UNKNOWN` se os arquivos a
   trazem de forma utilizável.
7. **Os 12 arquivos `05_Results by Hour` da IMSA** foram inspecionados em 3;
   os outros 9, não.
8. **A duração nominal foi inferida do nome do arquivo** (`Hour N` → N
   horas). `DERIVED`, não `MEASURED`: nenhum arquivo declara a duração
   programada.

---

## 11. Implicações para a reconstrução histórica

`DERIVED`, para as 324.720 linhas contaminadas:

| | antes da #50 | depois |
|---|---|---|
| entradas com classificação final verificada | 16 | **49** |
| entradas parciais | 32 | **1** |
| entradas indeterminadas | 4 | **2** |

`DERIVED`: **49 das 52 entradas** têm agora fonte acessível, correspondente
à sessão, e com evidência de finalidade **no sentido definido em §9.1** —
posterior ao encerramento da corrida. Isso cobre a quase totalidade das
324.720 linhas.

> **O que isso permite reconstruir, e sob que ressalva.** Uma reconstrução
> feita com essas fontes produziria a classificação **como estava ao fim da
> corrida**. `UNKNOWN`: se alguma delas foi revista depois — por
> penalização aplicada fora de pista, recurso, ou exclusão posterior. Nada
> nos arquivos permite distinguir uma classificação provisória de uma
> definitiva, e nenhuma segunda fonte final foi consultada.
>
> `PROPOSED`: se o acervo reconstruído for usado para análise competitiva,
> essa ressalva precisa viajar com o dado — a classificação registrada
> seria a do fim da corrida, não necessariamente a homologada.

`DERIVED`: a estratégia **B — reconstrução** passa a ter o seu pressuposto
central verificado. O que resta não é "a fonte serve?", e sim as decisões
operacionais: baixar, estender o filtro de nome, e tratar os 5 arquivos de
cabeçalho desalinhado — este último é **pré-condição**, não detalhe; §11.1.

### 11.1 Pré-condição: os arquivos de 93 colunas

`MEASURED`: 5 dos arquivos verificados têm **cabeçalho de 93 campos e
linhas de dados de 73**, com as informações relevantes presentes apesar da
diferença. `MEASURED`: esse comportamento **já produziu um resultado falso**
— a PR #49 registrou duas entradas como "sem interseção de números de
carro" porque a rotina descartava linhas mais estreitas que o cabeçalho;
a interseção real é 28 de 28 e 27 de 27 (§7.1).

Quatro requisitos, registrados como pré-condição de qualquer recuperação:

1. **A presença de um campo no cabeçalho não garante extração correta.**
   Os 93 nomes existem; os valores param no 73º.
2. **A leitura precisa tolerar linhas mais curtas sem deslocar valores.**
   Descartá-las produz ausência falsa; preenchê-las à direita é seguro
   apenas porque as 16 primeiras colunas alinham — o que também precisa
   ser verificado, não suposto.
3. **A compatibilidade precisa ser comprovada por testes específicos**
   sobre esses arquivos, antes de qualquer ingestão.
4. **Nenhuma reconstrução pode depender desses arquivos** enquanto essa
   validação não existir.

`PROPOSED`: nada disso foi implementado aqui, e o parser não foi alterado.
`DERIVED`: as entradas afetadas são identificáveis — basta comparar a
largura do cabeçalho com a das linhas ao ler.

`PROPOSED`: as correções do §1.1 **sejam aplicadas aos documentos da #49**
numa PR própria, porque alteram dois estados que aquele documento registrou
— e porque uma delas mostra que a corroboração de uma sessão foi feita
contra o campeonato errado.

---

## 12. Próximas decisões necessárias

| # | decisão | estado |
|---|---|---|
| 1 | **Autorizar o download** das 49 fontes corroboradas para `raw/` | pendente; nada mais a verificar |
| 2 | **Estender `is_classification_file`** para `05_Classification*` e `05_Results by Hour*`, com as três grafias de hora | pendente |
| 3 | **Tratar os 5 arquivos de 93 colunas com linhas de 73** — §11.1, com os quatro requisitos; é **pré-condição**, não ajuste | pendente |
| 3b | **Decidir se "posterior ao fim da corrida" basta**, ou se a reconstrução exige confirmar a versão homologada (§9.1) | pendente |
| 4 | **Corrigir os dois estados da #49** (§1.1) | pendente; é registro |
| 5 | Monza 2018 e Red Bull Ring 2017 | `PROPOSED`: excluí-las da primeira reconstrução |
| 6 | **Incluir IMSA em `load_laps.RAW_DIRS`** | escopo novo, descoberto em §7.3 |

**Nenhuma decisão foi tomada aqui. Nenhum estado autoriza ingestão.**

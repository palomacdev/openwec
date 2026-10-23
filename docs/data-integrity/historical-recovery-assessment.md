# Historical recovery assessment

Diagnóstico somente-leitura do acervo persistido antes das correções das
PRs #38–#42. Nenhuma recuperação foi executada, e nenhuma está autorizada
por este documento.

**Ambiente medido:** `openwec@127.0.0.1:5433`, banco de desenvolvimento.
**Data:** 2026-10-22. **`main` em:** `0ec4574` (após a PR #42).
**Fonte:** `raw/` e `catalog/` do repositório, lidos pelas próprias funções
dos carregadores.
**Escrita executada:** nenhuma. Sem `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`,
DDL, migration, carregador ou reingestão — nem dentro de transação revertida.

---

## 1. Resumo executivo

O acervo tem **671.353 linhas em `results`**. Delas, **324.720 (48,4 %) não
são classificação**: são linhas de volta gravadas na tabela errada, em 51
sessões cuja pasta de origem nunca teve arquivo de classificação. Essas
linhas não carregam dado nenhum — todos os seis campos lidos pelo parser
estão nulos em 100 % delas — e o conteúdo que elas deveriam ter **não existe
na fonte**. Em compensação, as voltas que as originaram já estão corretamente
em `laps`.

As 346.633 linhas restantes são classificação legítima, multiplicada por até
sete execuções do carregador. Dessas, **83.595 entradas lógicas são
consolidáveis sem perder nenhum valor**, 156 precisam de uma regra de
escolha, e 1.235 envolvem identidades competitivas que a fonte não resolve.

**Nenhuma exclusão está autorizada por este documento**, e nenhuma foi
executada. A remoção controlada das linhas comprovadamente contaminadas é
**uma das alternativas futuras**, não uma conclusão deste diagnóstico: marcá-
las, preservá-las intactas ou removê-las são caminhos abertos, e a escolha é
a decisão 1 do [plano](recovery-plan.md).

Duas afirmações distintas, que não devem ser lidas como uma só:

- **Ausência de perda de informação.** Para as 324.720 linhas contaminadas,
  medimos que elas não carregam valor nenhum lido da fonte (§3.2) e que o
  conteúdo de classificação que deveriam ter não existe em `raw/` (§3.4).
  Removê-las não perderia informação que a fonte ainda possua. Isso é uma
  constatação sobre o conteúdo.
- **Ausência de operação `DELETE`.** Nada foi removido, e nada está
  autorizado a ser. Isso é uma constatação sobre o que foi feito.

A primeira **não implica** a segunda nem a dispensa: que uma remoção não
perderia informação é argumento a favor de considerá-la, não permissão para
executá-la.

**Não estamos prontos para executar a recuperação.** Faltam o backup
verificado (Fase 0 do plano) e cinco decisões humanas listadas em
[recovery-plan.md](recovery-plan.md).

---

## 2. Metodologia das medições

Cada número abaixo segue o mesmo formato: população examinada, critério,
quantidade encontrada, o que ficou por verificar, e a limitação conhecida.

As medições são reprodutíveis. As consultas estão no corpo de cada seção e
rodam contra o banco de desenvolvimento sem nenhuma preparação. As que
percorrem `raw/` usam `find_classification_sessions`, `find_analysis_sessions`,
`get_final_classification`, `get_final_analysis`, `is_classification_file` e
`is_analysis_file` dos próprios carregadores, na versão de `0ec4574`.

**Distinção mantida em todo o documento.** Contagens de *linhas* (registros),
de *identidades* (tuplas distintas), de *sessões* (linhas de `sessions`) e de
*arquivos* (CSV em `raw/`) são grandezas diferentes e nunca somadas entre si.

**Consulta cara, substituída.** A primeira formulação do §4 usava
`NOT IN (SELECT car_id FROM ...)` contra 324.720 linhas, repetido em oito
subconsultas, e não terminou em 10 minutos. Foi trocada por uma varredura
única de `cars` com `EXISTS` e agregação por tupla numa passada só, que
responde em segundos. É a formulação publicada aqui.

### 2.1 Inventário de base

| tabela | linhas |
|---|---:|
| `cars` | 671.353 |
| `results` | 671.353 |
| `laps` | 2.034.223 |
| `result_drivers` | 469.457 |
| `sessions` | 5.425 (2.523 sem nenhuma linha em `results`) |
| `events` | 528 |
| `teams` | 1.111 |
| `drivers` | 2.845 |
| `analytics_car_session` | 8.243 |
| `analytics_stints` | 42.558 |

As tabelas do modelo competitivo (migrations 006–010) não existem neste banco.

O acervo foi escrito em **13 janelas horárias, em 8 dias distintos**, entre
2026-05-22 e 2026-10-20. Essa é a origem da multiplicação: cada execução
inseria um `cars` novo, e com ele um `results` novo.

```sql
SELECT date_trunc('hour', created_at) AS hora, count(*) AS results,
       count(DISTINCT session_id) AS sessoes
FROM results GROUP BY 1 ORDER BY 1;
```

---

## 3. Família A — resultados contaminados

### 3.1 Identificação, por duas evidências independentes

**Evidência 1 — a fonte.** Percorrendo `raw/` com as funções de `0ec4574`:
53 pastas de sessão têm CSV mas nenhum arquivo de classificação legítimo.
Destas, **51 têm arquivo de análise de voltas** e 2 só têm meteorologia.
Era nessas 51 que o carregador antigo caía, por falta de alternativa.
Elas casam **52 linhas de `sessions`**.

| | |
|---|---:|
| população examinada | todas as pastas de sessão de `raw/` |
| critério | `get_final_classification` devolve `None` **e** a pasta contém `23_Analysis*` |
| encontrado | 51 pastas → 52 sessões |
| não verificado | nada; a varredura é exaustiva |
| limitação | depende de `raw/` ser o mesmo corpus da ingestão; não há registro de versão dos arquivos que permita confirmá-lo |

**Evidência 2 — o banco, sem olhar a fonte.** O tamanho do maior grupo
(sessão, tupla de carro) separa as duas populações sem sobreposição:

```sql
WITH tup AS (SELECT c.id, (c.number,c.team_id,c.vehicle,c.car_class,
                 c.car_group,c.tires,c.imsa_car_id,c.imsa_team_id)::text t
             FROM cars c),
g AS (SELECT r.session_id, tup.t, count(*) n
      FROM results r JOIN tup ON tup.id=r.car_id GROUP BY 1,2)
SELECT session_id, max(n) FROM g GROUP BY 1;
```

| população | sessões | menor grupo máximo | maior grupo máximo | linhas |
|---|---:|---:|---:|---:|
| fora da lista da fonte | 2.851 | 1 | **7** | 346.633 |
| na lista da fonte | 51 | **26** | 394 | 324.720 |

Os intervalos são disjuntos: 1–7 contra 26–394. Numa sessão legítima o
tamanho do grupo é o número de execuções do carregador; numa contaminada é o
número de voltas daquele carro. **As duas evidências selecionam exatamente o
mesmo conjunto.**

### 3.2 Perfil das 324.720 linhas

```sql
SELECT count(*) AS linhas, count(position), count(laps_completed),
       count(total_time_s), count(gap_to_first_s), count(fl_time_s),
       count(fl_kph), count(*) FILTER (WHERE status <> 'Other')
FROM results WHERE session_id IN (<as 52>);
```

| campo | linhas com valor |
|---|---:|
| `position` | **0** |
| `laps_completed` | **0** |
| `total_time_s` | **0** |
| `gap_to_first_s` | **0** |
| `fl_time_s` | **0** |
| `fl_kph` | **0** |
| `status` diferente de `'Other'` | **0** |

As 324.720 linhas não carregam **nenhum** valor lido da fonte. São um
`session_id`, um `car_id` e nulos.

**Correção de uma afirmação anterior.** O diagnóstico de 2026-10-22 registrou
que `fl_kph` nessas sessões viria do `KPH` por volta, através do alias
introduzido na PR #38. A medição acima mostra `fl_kph` nulo em 100 % das
linhas: a última ingestão é de 2026-10-20, anterior à PR #38, então o alias
nunca as alcançou. O risco era **prospectivo** — aconteceria numa reingestão
— e as PRs #40 e #42 o eliminaram antes que ocorresse. Nenhuma linha do
acervo tem velocidade de volta gravada como velocidade de melhor volta.

### 3.3 Coexistência com resultados legítimos

**Nenhuma medida.** Uma linha de classificação legítima do dialeto B, mesmo
anterior à PR #38, traria `laps_completed` — a coluna `LAPS` existe nesse
dialeto e era lida. Nas 52 sessões, `laps_completed` é nulo em 324.720 de
324.720 linhas. Não há classificação legítima misturada.

| | |
|---|---:|
| população examinada | 324.720 linhas das 52 sessões |
| critério | existir qualquer campo lido da fonte com valor |
| encontrado | 0 linhas |
| não verificado | nada |
| limitação | o critério é necessário, não suficiente: uma classificação cujo arquivo de origem tivesse todas as colunas vazias seria indistinguível. Nenhum arquivo assim foi encontrado no corpus |

### 3.4 Campos reconstruíveis a partir das classificações originais

**Nenhum.** As 51 pastas não têm arquivo de classificação — foi exatamente
por isso que o carregador caiu no arquivo de análise. Não existe fonte da
qual reconstruir posição, tempo total, status ou melhor volta para elas.

O que existe é a volta: **158.275 linhas de `laps`** nessas sessões, carregadas
corretamente por `load_laps` a partir dos mesmos arquivos de análise. O
arquivo de origem tem 162.360 linhas com `NUMBER`; a diferença de 4.085 não
foi investigada nesta PR.

### 3.5 Dependências das 324.720 linhas

```sql
-- cars criados pelas 52 sessões e quem os referencia
```

| | |
|---|---:|
| linhas de `results` | 324.720 |
| linhas de `cars` criadas por elas | 324.720 (uma por linha de volta) |
| entradas competitivas reais representadas | **1.665** (sessão × tupla) |
| números de carro distintos | 104 |
| desses `cars`, referenciados por `laps` | **1.566** |
| desses `cars`, referenciados por `analytics_car_session` | 1.566 |
| `result_drivers` nessas sessões | **0** |

**323.154 dos 324.720 `cars` são referenciados apenas pela própria linha de
`results`.** Os 1.566 restantes sustentam as 158.275 voltas e precisam ser
re-apontados antes de qualquer remoção.

### 3.6 Casos que não podem ser classificados com segurança

Uma das 52 sessões não tem nenhuma linha em `results`. Ela entrou na lista
pela fonte, mas não há registro para classificar. **UNAFFECTED**.

---

## 4. Família B — carros duplicados

Varredura única de `cars`, marcando a contaminação por `EXISTS` e agregando
por tupla numa passada:

```sql
WITH alvo(id) AS (VALUES (<as 52 sessões>)),
marcado AS (
  SELECT c.id,
         (c.number,c.team_id,c.vehicle,c.car_class,c.car_group,
          c.tires,c.imsa_car_id,c.imsa_team_id)::text AS t,
         EXISTS (SELECT 1 FROM results r
                 WHERE r.car_id = c.id AND r.session_id IN (SELECT id FROM alvo))
           AS contaminado
  FROM cars c),
porTupla AS (SELECT t, contaminado, count(*) n FROM marcado GROUP BY 1,2)
SELECT contaminado, sum(n) AS cars, count(*) AS tuplas,
       count(*) FILTER (WHERE n>1) AS tuplas_duplicadas,
       sum(n) FILTER (WHERE n>1) - count(*) FILTER (WHERE n>1) AS excedentes,
       count(*) FILTER (WHERE n=1) AS tuplas_de_linha_unica, max(n) AS maior
FROM porTupla GROUP BY 1;
```

| | legítimos | de sessão contaminada |
|---|---:|---:|
| linhas de `cars` | 346.633 | 324.720 |
| tuplas distintas | 4.128 | 327 |
| tuplas com mais de uma linha | 4.078 | 327 |
| **linhas excedentes** | **342.505** | **324.393** |
| tuplas de linha única | 50 | 0 |
| maior grupo | 1.749 | 5.422 |

**4.128 + 327 = 4.455, contra 4.413 tuplas distintas no total: 42 tuplas
aparecem nas duas populações.** O mesmo carro corre numa sessão contaminada e
numa legítima. Consequência direta para o plano: **os `cars` contaminados não
podem ser identificados pela tupla**, só pela linha de `results` nas 52
sessões.

### 4.0 Quem referencia os `cars` legítimos

| | |
|---|---:|
| `cars` legítimos | 346.633 |
| referenciados por `results` | **346.633** (todos) |
| referenciados por `laps` | **56.793** |
| sem nenhuma referência | **0** |

Cada `cars` legítimo tem exatamente uma linha de `results` — é a relação que
a duplicação criou: uma execução do carregador, um carro novo, um resultado
novo. Nenhum carro órfão existe, o que significa que **a consolidação de
`cars` obriga a consolidar `results` junto**; não há linha solta a descartar
sem consequência.

Os 56.793 citados por `laps` são a interseção que a Fase 5 do plano precisa
reconciliar antes de qualquer merge — depois que a Fase 2 tiver aprovado a
regra para as colisões que esse re-apontamento provoca.

> **Nota de método — duas formulações, mesmo resultado.** Esta seção foi
> medida duas vezes por formulações diferentes: a varredura única com
> `EXISTS` publicada acima, e uma versão com `NOT IN` contra 324.720 linhas
> em oito subconsultas, que levou mais de 10 minutos. As duas devolvem os
> mesmos seis valores — 346.633, 4.128, 4.078, 342.505, 50 e 1.749 — o que
> é uma conferência independente do §4. A versão cara foi descartada por
> custo, não por discordância, e os dois números desta subseção vieram dela.

### 4.1 Duplicatas comprovadas pelos oito atributos

Chave lógica (sessão, tupla das oito colunas que `upsert_car` grava), apenas
nas sessões legítimas:

| | grupos |
|---|---:|
| grupos (sessão, tupla) | 94.438 |
| grupos de linha única | 10.687 |
| **classe A** — n>1 e uniformes em todas as oito colunas | **83.595** |
| **classe B** — n>1 e divergentes em alguma coluna | **156** |

A classe B diverge em `laps_completed` (152 grupos) e `gap_to_first_s` (154);
nenhum grupo diverge em `position`, `status`, `total_time_s` ou nos `fl_*`.

Critério de uniformidade, consciente de nulos — uma coluna é uniforme no
grupo quando `count(coluna) IN (0, count(*)) AND count(DISTINCT coluna) <= 1`.
A forma ingênua classificaria 8 grupos a menos como divergentes.

### 4.2 Mesmo número, identidades diferentes

| | |
|---|---:|
| pares (sessão, número) com mais de uma tupla | **1.235** |
| sessões envolvidas | 266 |
| máximo de tuplas por número | 2 |
| diferem em `team_id` | 1.227 |
| diferem em `car_class` | 138 |
| diferem em `vehicle` | 132 |
| diferem só em colunas menores | 0 |

Dos 1.227 com equipe diferente, **1.058 têm o mesmo nome depois de dobrar
caixa e pontuação** e **169 têm nomes de fato distintos** — incluindo pares
como `360° Racing` / `Comtoyou Racing` e ocorrências de `Unknown` como nome
de equipe.

| | |
|---|---:|
| população examinada | todos os pares (sessão, número) com results |
| critério | duas tuplas de oito atributos distintas sob o mesmo número na mesma sessão |
| encontrado | 1.235 pares |
| não verificado | qual dos dois registros corresponde a cada inscrição real |
| limitação | a divisão 1.058/169 depende da regra de normalização; com outra regra os números mudam. Nenhuma evidência externa foi consultada |

**Nada aqui prova identidade competitiva.** Igualdade de número, de equipe ou
de tupla não é prova, e a ADR 0001 é explícita quanto a isso.

### 4.3 Dependências que impedem consolidação segura

| dependência | medida |
|---|---:|
| `laps` que colidiriam em `UNIQUE (session_id, car_id, lap_number)` | **35.045** |
| — em cars legítimos | 31.310 |
| — em cars de sessão contaminada | 3.735 |
| `analytics_car_session` que colidiriam em `UNIQUE (session_id, car_id)` | 122 |
| `result_drivers` apontando para `results` que seriam consolidados | 469.457, sobre 172.006 `results` distintos |

Todos os 35.045 grupos de `laps` têm exatamente duas linhas. **Não são
colisões existentes** — a restrição única vale hoje. São colisões que uma
consolidação por tupla provocaria, e que precisam de regra antes dela.

> **Não está estabelecido que esses pares sejam duplicatas.** O medido é que
> as duas linhas compartilham sessão, identidade de carro e número da volta.
> **Se o conteúdo das duas coincide — tempo, setores, velocidade, hora — não
> foi verificado nesta PR.** Podem ser a mesma volta gravada duas vezes, duas
> leituras da mesma volta, ou um defeito de numeração na fonte. Essa
> comparação campo a campo é o entregável da Fase 1 do plano, e sem ela
> nenhuma das duas linhas pode ser descartada.

---

## 5. Família C — voltas e associações

Três defeitos distintos, medidos separadamente para não se somarem.

### 5.1 Duplicação: voltas espalhadas por `car_id` da mesma identidade

`get_car_id` usava `LIMIT 1` sem `ORDER BY` — não determinístico em
PostgreSQL. Corrigido na PR #41 para novas cargas; o acervo mantém o efeito.

| | pares (sessão, tupla) |
|---|---:|
| com voltas em mais de um `car_id` | **687** |
| — em cars legítimos | 655 |
| — em cars de sessão contaminada | 32 |

### 5.2 Associação incorreta: voltas nas 52 sessões contaminadas

| | |
|---|---:|
| `laps` nessas sessões | 158.275 |
| `laps` apontando para `cars` criados por elas | 158.275 |
| `cars` distintos que recebem essas voltas | 1.566 |
| entradas competitivas reais | 1.665 |

As voltas em si são corretas: vieram do arquivo de análise lido como análise.
O que está errado é o `car_id` a que se agarram, escolhido arbitrariamente
entre as centenas de linhas que a contaminação criou para o mesmo carro.

### 5.3 Ambiguidade: voltas de campeonato possivelmente incorreto

A PR #41 passou a recusar 138 seleções de sessão de voltas por ambiguidade.
Historicamente elas foram carregadas com `LIMIT 1`, numa das candidatas.

| | |
|---|---:|
| seleções de arquivo de voltas ambíguas | 138 |
| linhas de `sessions` candidatas dessas seleções | 302 |
| `laps` nessas candidatas | **158.374** |

| | |
|---|---:|
| população examinada | todas as sessões candidatas das 138 seleções |
| critério | pertencer a um grupo (evento, nome) com mais de uma sessão alcançável por um arquivo de voltas |
| encontrado | 158.374 linhas |
| não verificado | **quantas dessas voltas estão de fato na sessão errada** |
| limitação | é um **limite superior**, não uma contagem de erros. A árvore `raw/` dessas séries não guarda o campeonato, então nada no arquivo diz a qual candidata ele pertence — é por isso que a PR #41 as recusa |

### 5.4 Colisões em chaves relevantes

Hoje **não há** colisão: `laps` tem `UNIQUE (session_id, car_id, lap_number)`
e `results` tem `UNIQUE (session_id, car_id)`, ambas íntegras. As 35.045 de
`laps` e 122 de `analytics_car_session` são prospectivas, condicionadas a uma
consolidação por tupla.

`cars` não tem nenhuma restrição única além da chave primária. É a causa
estrutural, corrigida no código pela PR #39 e não no acervo.

---

## 6. Família D — sessões e metadados

| lacuna | sessões | registros afetados |
|---|---:|---:|
| sessões sem nenhuma linha em `results` | 2.523 | — |
| sessões compartilhando `(event_id, name)` | 2.244 | — |
| **8 sessões WeatherTech ausentes** (PR #41) | 8 contraditadas | 1.924 `results`, 0 `laps` |
| **145 seleções de classificação ambíguas** (ELMS/WEC) | 314 candidatas | 12.979 `results` |
| **138 seleções de voltas ambíguas** | 302 candidatas | 158.374 `laps` |

### 6.1 As 8 sessões WeatherTech

O catálogo tem a sessão WeatherTech; `load_metadata` não a criou no banco. A
única linha com aquele nome no evento pertence ao Pilot Challenge ou ao
Continental Tire Challenge. Antes da PR #41 o carregador aceitava a candidata
única e arquivava dado de um campeonato debaixo de outro.

As 8 linhas de sessão contraditadas contêm **1.924 `results`**. Não foi
verificado se esses 1.924 vieram do arquivo WeatherTech ou do arquivo do
próprio campeonato daquela sessão — distinguir exige comparar o conteúdo com
os dois arquivos de origem, o que não foi feito nesta PR.

`load_metadata` não foi tocado, conforme o escopo.

### 6.2 Lacuna adicional encontrada

2.523 das 5.425 sessões não têm `results`. Parte é estrutural: `sessions` é
única por `(event_id, raw_id)`, mas o carregador casava por `s.name` com
`LIMIT 1`, então entre as 2.244 que compartilham `(event_id, name)` apenas
uma recebia carga. Outra parte são sessões de campeonatos que nunca foram
baixados para `raw/`. As duas causas não foram separadas quantitativamente.

---

## 7. Matriz de recuperabilidade

Cada linha é uma **dimensão** de um defeito, não um registro. Um mesmo
registro aparece em mais de uma linha quando tem mais de um defeito, e as
colunas de quantidade **não devem ser somadas entre famílias**.

| # | família | dimensão | classe | registros | unidade |
|---|---|---|---|---:|---|
| A1 | resultados contaminados | identificar quais linhas são | `RECOVERABLE_EXACT` | 324.720 | linhas de `results` |
| A2 | resultados contaminados | reconstruir o conteúdo de classificação | `UNRECOVERABLE` | 324.720 | linhas de `results` |
| A3 | resultados contaminados | `cars` sem nenhuma outra referência | `RECOVERABLE_EXACT` | 323.154 | linhas de `cars` |
| A4 | resultados contaminados | `cars` que sustentam voltas, a re-apontar | `RECOVERABLE_CONDITIONAL` | 1.566 | linhas de `cars` |
| A5 | resultados contaminados | sessão da lista sem nenhum `results` | `UNAFFECTED` | 1 | sessão |
| B1 | carros duplicados | grupos uniformes nas oito colunas | `RECOVERABLE_EXACT` | 83.595 | grupos (sessão, tupla) |
| B2 | carros duplicados | grupos divergentes | `RECOVERABLE_CONDITIONAL` | 156 | grupos (sessão, tupla) |
| B3 | carros duplicados | mesmo número, equipes de grafia diferente | `AMBIGUOUS` | 1.058 | pares (sessão, número) |
| B4 | carros duplicados | mesmo número, equipes de nome distinto | `AMBIGUOUS` | 169 | pares (sessão, número) |
| B5 | carros duplicados | tuplas de linha única | `UNAFFECTED` | 50 | tuplas |
| C1 | voltas | espalhadas por `car_id` da mesma identidade | `RECOVERABLE_CONDITIONAL` | 687 | pares (sessão, tupla) |
| C2 | voltas | colisões provocadas por consolidação | `RECOVERABLE_CONDITIONAL` | 35.045 | grupos de 2 linhas |
| C3 | voltas | campeonato possivelmente incorreto | `AMBIGUOUS` | 158.374 | linhas de `laps` (limite superior) |
| D1 | metadados | 8 sessões WeatherTech ausentes | `RECOVERABLE_CONDITIONAL` | 1.924 | linhas de `results` |
| D2 | metadados | 145 seleções de classificação ambíguas | `AMBIGUOUS` | 12.979 | linhas de `results` |
| D3 | metadados | 138 seleções de voltas ambíguas | `AMBIGUOUS` | 158.374 | linhas de `laps` |
| E1 | campos não lidos | valores recuperáveis por reingestão | `RECOVERABLE_CONDITIONAL` | 312.144 | valores |

**Por que cada classe foi atribuída**

- **A1 `RECOVERABLE_EXACT`** — duas evidências independentes selecionam o
  mesmo conjunto, com intervalos disjuntos (§3.1).
- **A2 `UNRECOVERABLE`** — ausência comprovada, não falta de solução: as 51
  pastas não têm arquivo de classificação. A remoção dessas linhas não perde
  informação, porque as voltas que as originaram estão em `laps`.
- **A4, B2, C1, C2, D1 `RECOVERABLE_CONDITIONAL`** — o caminho existe e
  depende de pré-condição ou decisão listada no plano.
- **B3, B4, C3, D2, D3 `AMBIGUOUS`** — mais de uma interpretação e nenhuma
  evidência disponível que escolha entre elas.
- **E1** — medido em 92.853 entradas lógicas das sessões legítimas, uma linha
  por entrada, comparando a fonte com o banco pelas funções de parsing do
  próprio carregador: `position` 79.136, `fl_lap_number` 79.526, `fl_time_s`
  75.235, `fl_kph` 75.235, `laps_completed` 3.012. Condicional porque depende
  da consolidação anterior: sobre o acervo atual, a reingestão escreveria numa
  linha arbitrária de cada grupo.

**Nada foi classificado como `UNRECOVERABLE` por falta de investigação.** A
única linha nessa classe tem ausência demonstrada na fonte.

---

## 8. Riscos

| # | risco | severidade | mitigação |
|---|---|---|---|
| 1 | Consolidar `cars` por tupla faz 35.045 linhas de `laps` colidirem e interrompe a operação no meio | alta | inventariar os pares (Fase 1) e aprovar a regra de colisão (Fase 2) antes de qualquer re-apontamento |
| 2 | `cars` contaminados não são identificáveis pela tupla: 42 tuplas aparecem nas duas populações | alta | identificar sempre pela linha de `results` nas 52 sessões |
| 3 | Remover `cars` contaminados sem re-apontar as voltas órfã 158.275 linhas de `laps` | alta | re-apontar antes de remover |
| 4 | `status = 'Other'` em 630.869 linhas é default fabricado por `normalize_status("")`, não dado | média | reavaliar a semântica antes que qualquer análise dependa dela |
| 5 | Consolidar os 169 pares da classe C por heurística funde inscrições possivelmente distintas, de forma irreversível sem backup | alta | manter separados até haver critério externo |
| 6 | Normalizar `teams` para os 1.058 pares de grafia altera dado mestre com efeito em todo o acervo | média | decisão humana explícita, com inventário prévio |
| 7 | 309 grupos têm leituras conflitantes cuja causa não é reproduzível a partir do corpus atual | média | qualquer regra de escolha é convenção, não recuperação do valor verdadeiro — registrar como tal |

---

## 9. O que ainda precisa ser investigado

1. A diferença de 4.085 entre as 162.360 linhas com `NUMBER` dos 51 arquivos
   de análise e as 158.275 linhas de `laps` nessas sessões.
2. Se os 1.924 `results` das 8 sessões contraditadas vieram do arquivo
   WeatherTech ou do arquivo do campeonato da própria sessão.
3. Quantas das 2.523 sessões sem `results` são efeito do casamento por
   `s.name` e quantas são campeonatos nunca baixados.
4. A causa das leituras divergentes em 21 sessões / 309 grupos: nenhum
   arquivo do corpus atual as produz.
5. A identidade competitiva real dos 169 pares da classe C.
6. O conteúdo original das 367 linhas de `results` cujo número não aparece no
   arquivo de classificação atual da sessão.
7. Se `raw/` mudou entre as execuções de maio, julho e setembro: não há
   registro de versão dos arquivos que permita verificar.

---

## 10. Limitações gerais

- Tudo foi medido num único ambiente, o banco de desenvolvimento na porta
  5433. Nenhuma conclusão foi transportada de outro ambiente.
- Nenhum número foi extrapolado de amostra. Onde uma amostra foi inspecionada
  — exemplos de divergência, nomes de equipe — isso está dito no texto, e a
  contagem que a acompanha vem de varredura completa.
- O corpus `raw/` é tratado como a fonte da ingestão. Não há registro de
  versão que confirme que é o mesmo conteúdo de maio, julho e setembro.
- As classes de recuperabilidade descrevem o que a evidência disponível
  hoje sustenta. Uma evidência nova pode mover um registro de `AMBIGUOUS`
  para uma classe recuperável; o contrário também vale.

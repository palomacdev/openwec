# A classe de triplicação, e qual das três linhas pode ficar

`MEASURED` em 2026-10-24, **somente leitura** sobre o banco histórico, com
`default_transaction_read_only = on`. Nenhuma escrita, nenhuma recuperação,
nenhuma reingestão, nenhum export.

**1.315 sessões** guardam exatamente três linhas em `results` para cada
número de carro, cada uma sobre um `cars` próprio: **116.673 linhas** sobre
**38.891 entradas** (sessão, número).

`DERIVED`: não é a contaminação por log de voltas que a recuperação trata.
Os valores concordam. São três execuções do mesmo carregador numa época em
que o `upsert_car` não encontrava a linha que ele mesmo acabara de escrever,
e `results` é chaveado em `(session_id, car_id)`, de modo que nada entrou em
conflito.

---

## 1. As três levas concordam, menos numa coluna e numa sessão

Comparei todas as colunas relevantes de `results` e `cars`, separando
**contradição** (mais de um valor **não-nulo**) de **silêncio** (um valor, e
nem toda linha o carrega).

| | sessões |
|---|---:|
| três linhas **absolutamente idênticas** | **206** |
| divergem **só por silêncio** | **1.108** |
| **contradição** | **1** |

A coluna que fica em silêncio é **uma só**:

```
tyre_supplier ......................................... 1.109 sessões
position, status, laps_completed, total_time_s,
gap_to_first_s, fl_time_s, fl_kph, team_id, vehicle,
manufacturer, car_class, car_group, tires ............. 0
```

`DERIVED`: em 1.314 das 1.315 sessões as três levas concordam em **todo
valor de corrida**. A primeira preencheu `tyre_supplier`; as outras duas
não. A coluna nunca fez parte de `car_identity`, e por isso nunca entrou na
comparação que o `upsert_car` faz.

---

## 2. O critério, e o que ele custa

O canônico é **a linha que as voltas já apontam**. Preserva `laps`,
`analytics_car_session` e `analytics_stints` sem re-apontar nada.

| ordem | caso | entradas |
|---|---|---:|
| 1 | exatamente um `car_id` carrega voltas → **ele** | **34.422** |
| 2 | nenhum carrega → **o menor id** | **4.244** |
| 3 | mais de um carrega → **nada é escolhido** | **225** |

`MEASURED`, a distribuição que justifica a ordem:

| | entradas |
|---|---:|
| voltas no **menor** id | 34.355 |
| voltas num id que **não** é o menor | **67** |
| `tyre_supplier` presente, sempre no menor id | 33.990 · nunca em outro · nunca nos três |
| `result_drivers` presentes nas **três** linhas | 3.047 · em nenhuma: 35.844 · **em só uma ou duas: 0** |

`DERIVED`: pilotos e equipes não se perdem por construção — os vínculos
existem nas três linhas ou em nenhuma, e `team_id` é idêntico nas três em
100% das entradas. O custo do critério é outro, e é exatamente contável.

### 2.1 As 67 entradas, e por que não passam em silêncio

Nelas a linha que carrega as voltas **não** é a que carrega
`tyre_supplier`. Manter as voltas significaria perder o metadado.

O módulo não deixa isso passar: reporta o valor **e o `car_id` de onde veio**
como metadado complementar, e marca a entrada como `SPECIAL`. Exemplo real,
sessão 2066:

```
#53  SPECIAL  COMPLEMENTARY_METADATA_ON_DISCARDED
     canonico=173316  descartar=[40305, 176464]
     complementar={'tyre_supplier': {'valor': 'Michelin',
                                     'proveniencia_car_id': 40305}}
```

`PROPOSED`: transportar ou não esse valor é **decisão separada**. Um valor
sem proveniência não é evidência de nada, e por isso os dois viajam juntos.

### 2.2 As 225 entradas com dois portadores

**Nada é escolhido.** Escolher um dos `car_id` daria metade das voltas de
uma inscrição a uma linha que não as possui. São **92 sessões** e **10.430
linhas de voltas excedentes**; **32 entradas** também têm
`analytics_car_session` e stints duplicados.

---

## 3. Os impedimentos, contados separadamente

Cada um é independente: uma sessão pode ter qualquer deles sem os outros.

| impedimento | o que é | sessões |
|---|---|---:|
| `MULTIPLE_LAP_CARRIERS` | voltas em dois ou três `car_id` | **92** |
| `STATUS_DIVERGES_FROM_SOURCE` | o banco discorda do **arquivo** | **73** (321 linhas) |
| `SOURCE_REVISION_DIVERGES` | as três levas discordam **entre si** | **1** |
| `SOURCE_UNVERIFIABLE` | sem arquivo local para conferir | **120** |
| `DRIVER_IDENTITY_SPLIT` | a mesma pessoa em duas linhas de `drivers` | **22** |
| `COMPLEMENTARY_METADATA_ON_DISCARDED` | metadado na linha que sairia | **67 entradas** |

`DERIVED`: **status e revisão são coisas diferentes**, e por isso têm nomes
diferentes. O primeiro é o banco contra o arquivo; o segundo é uma ingestão
contra a outra.

### 3.1 Status: um único defeito, 321 vezes

Reprocessei o arquivo local de cada sessão com o parser **corrigido**. Das
1.195 sessões comparáveis, 73 divergem, e o detalhamento não deixa dúvida:

```
321x  'Not classified'  →  deveria ser 'Not Classified'
                           mas o banco tem 'Classified'
```

Nenhum outro par de valores diverge. É o defeito corrigido pela PR #52, em
dados ingeridos antes dela. **Só uma reingestão o corrige** — deduplicar não
toca nisso.

### 3.2 Revisão da fonte: a sessão 1670

ELMS 2023, `00_OFFICIAL TEST - BARCELONA-CATALUNYA`, Morning Session. 42
entradas com contradição em `laps_completed` e nos três campos de gap:

```
carro 10:  leva de 2026-05-22 → 77 voltas, gap +6.225
           leva de 2026-07-13 → 14 voltas, gap +8.188
           leva de 2026-09-22 → 14 voltas, gap +8.188
soma de voltas por leva: 3.109 / 2.763 / 2.763
```

`DERIVED`: o arquivo mudou entre maio e julho. Duas levas concordam e uma
discorda. `UNKNOWN`: qual revisão vale. Não é deduplicação — é escolha de
revisão, e fica **bloqueada**.

### 3.3 A premissa da classe, verificada e não suposta

Tudo o que este documento mede — qual linha carrega as voltas, onde o
metadado fica, que os vínculos de piloto existem nas três ou em nenhuma —
foi medido sobre entradas de **exatamente três linhas**.

Uma entrada com uma, duas ou quatro **não pertence à população**, e o
critério não se aplica a ela. O módulo verifica isso antes de qualquer outra
coisa e devolve `OUTSIDE_TRIPLICATION_CLASS` com `BLOCKED`, sem canônico e
sem avaliar divergência, portador ou metadado. `DERIVED`: **nenhuma entrada
fora da premissa pode sair `ELIGIBLE`**, e responder por ela seria responder
a uma pergunta que ninguém fez.

### 3.4 Cobertura da fonte, separada de divergência de status

A comparação com o arquivo responde a **duas** perguntas, e elas não se
confundem:

| achado | o que é |
|---|---|
| `STATUS_DIVERGES_FROM_SOURCE` | os dois lados descrevem o mesmo carro de modo diferente |
| `SOURCE_COVERAGE_INCOMPLETE` | os dois lados **não descrevem o mesmo conjunto** de carros |

A cobertura é incompleta quando a fonte publica um número que o banco não
tem, o banco tem um que a fonte não publica, a fonte publica o mesmo número
duas vezes, ou traz linha sem número. Nenhum desses casos é divergência de
status, e **nenhum pode ser pulado**.

`DERIVED`: uma comparação que descarta em silêncio o que não consegue parear
relata uma concordância que nunca estabeleceu. É a forma exata do defeito que
a PR #49 cometeu — `if len(p) <= max(idx.values()): continue` fez a rotina
ler zero linhas e reportar "sem interseção" — e que a PR #50 teve de
corrigir. Uma sessão com cobertura incompleta **não sai `ELIGIBLE`**.

### 3.5 Pilotos: candidatos, nunca fusões

A detecção usa `full_name_key`, a mesma dobra que `row_identity` já aplica
para dizer que duas grafias são o mesmo nome — emprestada aqui para
**reportar**, nunca para agir.

| | |
|---|---:|
| sessões da classe com `result_drivers` | **80** de 1.315 |
| sessões com **os dois lados** | **22** |
| pares candidatos nessas 22 | 1.312 (332 pares de ids distintos) |

```
329053 'Rui ANDRADE'  ←→  788 'Rui Andrade'
330940 'Sébastien BUEMI'  ←→  4 'Sébastien Buemi'
```

`PROPOSED`: cada par é candidato, não prova. As regras de `row_identity` já
recusam a fusão automática, e nada aqui a contorna.

---

## 4. A matriz

`MEASURED`: produzida **pelo próprio módulo**, rodando `assess_session` sobre
as 1.315 sessões — não por um roteiro à parte. É a razão de ela não coincidir
com a tabela da investigação anterior; o §4.2 reconcilia as duas.

| categoria | sessões | entradas |
|---|---:|---:|
| **ELIGIBLE** | **982** | **38.557** |
| **SPECIAL** | **240** | **67** |
| **BLOCKED** | **93** | **267** |
| total | 1.315 | 38.891 |

Motivos, por sessão, com sobreposição:

```
120  SOURCE_UNVERIFIABLE
 92  MULTIPLE_LAP_CARRIERS
 73  STATUS_DIVERGES_FROM_SOURCE
 56  COMPLEMENTARY_METADATA_ON_DISCARDED
 22  DRIVER_IDENTITY_SPLIT
  1  MATERIAL_DIVERGENCE
  1  SOURCE_REVISION_DIVERGES
  0  SOURCE_COVERAGE_INCOMPLETE
  0  OUTSIDE_TRIPLICATION_CLASS
```

Combinações reais:

```
982  (nenhum)
115  SOURCE_UNVERIFIABLE
 79  MULTIPLE_LAP_CARRIERS
 56  COMPLEMENTARY_METADATA_ON_DISCARDED
 55  STATUS_DIVERGES_FROM_SOURCE
 14  DRIVER_IDENTITY_SPLIT + STATUS_DIVERGES_FROM_SOURCE
  5  MULTIPLE_LAP_CARRIERS + SOURCE_UNVERIFIABLE
  4  DRIVER_IDENTITY_SPLIT + MULTIPLE_LAP_CARRIERS
  4  DRIVER_IDENTITY_SPLIT + MULTIPLE_LAP_CARRIERS + STATUS_DIVERGES_FROM_SOURCE
  1  MATERIAL_DIVERGENCE + SOURCE_REVISION_DIVERGES
```

`MEASURED`: as entradas conferem com as medições do §2 — as **67** `SPECIAL`
são exatamente as de metadado complementar, e as **267** `BLOCKED` são as
**225** com dois portadores mais as **42** da sessão 1670.

### 4.1 A regra de severidade

Vale para a entrada e para a sessão, e é a mesma nos dois níveis: **o pior
vence**.

| verdito | quando |
|---|---|
| `BLOCKED` | a entrada não pertence à classe, as três levas se contradizem, ou mais de um `car_id` carrega voltas — **nenhum canônico é devolvido** |
| `SPECIAL` | há canônico, mas alguma coisa precisa de decisão separada antes de qualquer consolidação |
| `ELIGIBLE` | há canônico e nada pendente |

A sessão recebe o pior verdito das suas entradas, e depois os impedimentos
de sessão só podem piorá-lo. `DERIVED`: uma sessão `BLOCKED` pode ter a
maioria das entradas `ELIGIBLE` — é o caso da 855, com 33 de 35.

### 4.2 Reconciliação com a matriz da investigação anterior

| | investigação | módulo | por quê |
|---|---:|---:|---|
| ELIGIBLE | 1.038 | **982** | −56: o metadado complementar não era contado como impedimento de sessão, só de entrada |
| SPECIAL | 276 | **240** | −92 para `BLOCKED`, +56 vindos de `ELIGIBLE` |
| BLOCKED | 1 | **93** | +92: a investigação tratava "voltas duplicadas" como especial; o módulo não devolve canônico nesse caso, e isso é bloqueio |

`DERIVED`: a divergência é de **regra**, não de medição — nenhuma contagem
bruta mudou. As 92 sessões com voltas duplicadas, as 56 com metadado
complementar e a 1 com contradição são as mesmas de antes. A classificação
do módulo é a que vale, porque é a que o código aplica.

**A sessão 855 é `BLOCKED`**, não `SPECIAL` como a investigação registrou.
Ela tem dois números — `007` e `009` — com voltas em dois `car_id`, e para
esses o módulo não escolhe nada.

### 4.3 Dois guardas que não encontraram nada

`SOURCE_COVERAGE_INCOMPLETE` e `OUTSIDE_TRIPLICATION_CLASS` aparecem **zero
vezes**. `DERIVED`: nas 1.195 sessões com arquivo local, a fonte e o banco
cobrem exatamente o mesmo conjunto de números; e toda entrada da classe tem
mesmo três linhas. São duas afirmações verificadas, não duas verificações
ausentes — e é essa a diferença que o §3.4 explica.

Perfil das 982 elegíveis (as 56 de metadado complementar saíram desta lista
e estão em `SPECIAL`):

```
ELMS      Qualifying 242 · Test 125 · Practice 106 · Other 57 · Race 30
LEMANSCUP Qualifying 117 · Practice 110 · Test 99 · Other 20
ALMS      Qualifying 14 · Race 8 · Practice 6 · Test 4 · Other 2
IMSA      Practice 9 · Other 9 · Qualifying 5 · Race 3 · WarmUp 1
WEC       Hyperpole 8 · Qualifying 7
```

`MEASURED`: **41 das 982 são corridas, e nenhuma é WEC.** Das 105 corridas
WEC do acervo, **8 estão nesta classe**, e **as 8 são `BLOCKED`** — todas têm
voltas em mais de um `car_id`, e com isso nenhum canônico é devolvido para os
números afetados:

```
772 · 789 · 799 · 809 · 819 · 829 · 845 · 855
```

---

## 5. A sessão 855, caso de referência

`MEASURED`, com o módulo:

```
sessao 855: BLOCKED
  entradas: 35  (33 elegiveis, 0 especiais, 2 bloqueadas)
  motivo: DRIVER_IDENTITY_SPLIT
  motivo: MULTIPLE_LAP_CARRIERS
  motivo: STATUS_DIVERGES_FROM_SOURCE
    #007  BLOCKED  MULTIPLE_LAP_CARRIERS  canonico=None
    #009  BLOCKED  MULTIPLE_LAP_CARRIERS  canonico=None
```

33 das 35 entradas têm canônico determinável. As duas que não têm são os
**únicos dois números com zero à esquerda** da sessão — correlação medida,
`HYPOTHESIS` quanto à causa.

### 5.1 Proveniência da Hora 6

`MEASURED`: o banco bate com a **Hora 6** (3 divergências em 420 campos) e
não com as Horas 1–5 (270 · 258 · 241 · 226 · 197).

```
03_Classification_Race_Hour 6.CSV   5881 b
sha256 03c8b239b0a4eb98c3be17bf7bd3f09b…   (calculado agora, sobre raw/ local)
```

Mas **o banco não registra isso**: `source_url` aponta para a **Hora 1** (em
5 de 5 corridas WEC 2026), `snapshot_hour` é **NULL**, e nenhum hash ou
versão da fonte é armazenado. `DERIVED`: um terceiro não tem como verificar
a revisão a partir do banco.

`DERIVED`: a Hora 6 tem o padrão de posterior à bandeirada — líder a
**+77,036 s** das 6 h nominais. `UNKNOWN`: se é a homologada.

### 5.2 O carro 77

`MEASURED`: **128 linhas em `laps`** contra `laps_completed = 127`, **sem
`lap_number` repetido**. `DERIVED`: não é duplicação — é a classificação
creditando uma volta a menos do que o log registra. `UNKNOWN`: qual das duas
está certa. Mesmo padrão do carro 99 na sessão 2226.

### 5.3 Fuso horário: investigação pendente

`MEASURED`, `session_at` contra a primeira `hour_raw` em corridas WEC de
fusos muito diferentes:

| evento | fuso real | `session_at` (UTC) | primeira `hour_raw` |
|---|---|---|---|
| Imola 2026 | UTC+2 | 13:00 | 13:03:17 |
| Spa 2026 | UTC+2 | 14:00 | 14:02:56 |
| São Paulo 2026 | UTC−3 | 11:30 | 11:32:43 |
| COTA 2026 | UTC−5 | 13:00 | 13:03:19 |
| Fuji 2025 | UTC+9 | 11:00 | 11:02:26 |
| Bahrein 2025 | UTC+3 | 14:00 | 14:02:24 |

`DERIVED`: o deslocamento é ~0 **em todos os fusos**. Se `hour_raw` e
`session_at` fossem ambos UTC, o deslocamento acompanharia o fuso de cada
pista.

`HYPOTHESIS`, **não verificada e deliberadamente não implementada**:
`hour_raw` é hora local da pista e `session_at` guarda a mesma hora local
rotulada como `+00`, de modo que `session_at` não seria um instante UTC
verdadeiro.

`MEASURED`: `lap_recorded_at`, a única coluna `timestamptz` de `laps`, está
**NULL em 100%** das 5.118 voltas da sessão.

> **Nenhuma atualização automática de `session_at` é proposta aqui.** A
> suspeita alcança **todas as 5.425 sessões** do acervo, não só esta classe,
> e corrigi-la exigiria decidir o fuso de cada pista e de cada data — uma
> investigação própria, com a sua própria autorização.

`UNKNOWN`: COTA 2025 mostra `session_at` 13:30 contra `hour_raw` 13:03, 27
minutos, único caso. Le Mans e Losail aparecem com `00:00:xx` porque cruzam
a meia-noite e o `min()` de texto pega o valor pós-meia-noite — não são
contraexemplo.

---

## 6. O contrato de somente leitura

O que a proteção cobre, e o que não precisa cobrir:

| | |
|---|---|
| **banco histórico** | lido e nunca escrito: todo comando é `SELECT`, a conexão abre com `default_transaction_read_only`, e um teste afirma isso sobre as consultas emitidas |
| **arquivos de origem** | abertos por `read_csv` para leitura e nada mais; `raw/` não é tocado |
| **`--json`** | **escreve um arquivo local**, escolhido por quem roda o comando |

`DERIVED`: o relatório é escrito **depois que a conexão já foi fechada**. Um
caminho inexistente ou sem permissão devolve saída 1, imprime o motivo e diz
em voz alta que o banco não foi tocado — e o banco fica como ficaria numa
execução sem `--json`.

`PROPOSED`: o destino recomendado é um **diretório temporário fora do
repositório e fora de `raw/`**. O relatório carrega a medição de um momento,
não algo que o projeto guarda.

---

## 7. O que esta avaliação não faz

1. **Não escreve no banco nem em `raw/`.** A única escrita é o relatório
   local do `--json`, descrita no §6.
2. **Não deduplica nada.** Diz qual linha *poderia* ficar.
3. **Não escolhe** quando dois `car_id` carregam voltas.
4. **Não funde pilotos**, por nome ou por qualquer outro critério.
5. **Não transporta** o metadado complementar — reporta com proveniência.
6. **Não corrige** os 321 status nem decide a revisão da sessão 1670.
7. **Não propõe** alteração de `session_at`.
8. **Não gera export** para ninguém.
9. **Não devolve canônico** para uma entrada fora da premissa de três
   linhas, nem quando a cobertura da fonte está incompleta.

---

## 8. Reproduzir

```bash
python -m database.recovery.triplication \
  --session-id 855 \
  --csv "raw/wec/.../06_Hour 6/03_Classification_Race_Hour 6.CSV" \
  --host 127.0.0.1 --port 5433 --dbname openwec --user openwec \
  --password "$DB_PASSWORD" --json /tmp/openwec/855.json
```

Sem `--csv` o status não é conferido, e a sessão recebe
`SOURCE_UNVERIFIABLE` — não conferir não é o mesmo que conferir e não achar
nada.

O `--json` é a única escrita, e vai para onde se mandar; o §6 recomenda um
diretório temporário fora do repositório. Não há modo de escrita no banco.

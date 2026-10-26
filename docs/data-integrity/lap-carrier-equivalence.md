# Dois portadores, uma inscrição: são as mesmas voltas?

`MEASURED` em 2026-10-26, **somente leitura** sobre o banco histórico, com
`default_transaction_read_only = on`. Nenhuma escrita, nenhuma consolidação,
nenhuma reingestão, nenhum export.

A PR #57 bloqueia uma inscrição cujas voltas são carregadas por mais de um
`car_id`, porque ali não há canônico a escolher. **Essa recusa continua de
pé e não foi tocada.** O que esta avaliação acrescenta é a evidência que
faltava: se os dois portadores guardam **as mesmas voltas**, e o que mais os
distingue.

> **Nada aqui autoriza apagar linha ou redirecionar referência.** O módulo
> devolve `candidate_car_ids` com **os dois** portadores e uma lista de
> impedimentos; nunca devolve um id só.

---

## 1. O que é comparado, e por quê

### 1.1 A identidade da volta

As voltas são pareadas por `(session_id, lap_number)` dentro da inscrição.
A chave **não é presumida**: `laps` carrega
`UNIQUE (session_id, car_id, lap_number)`, verificado no catálogo, e é isso
que torna o pareamento um-para-um.

`DERIVED`: o módulo **confere em tempo de execução** em vez de citar a
constraint. Um portador que de algum modo guarde o mesmo número duas vezes
devolve `UNDETERMINED`, e **nenhuma linha é escolhida para representar as
outras**.

`MEASURED`: nenhum `DISTINCT ON` e nenhum `LIMIT` no módulo. Toda linha dos
dois portadores é lida, e as contagens do relatório reconciliam com os pares
comparados.

### 1.2 As colunas

O conjunto de comparação é **descoberto do catálogo**, não escrito à mão.

`DERIVED`: uma lista no código envelhece — uma coluna acrescentada a `laps`
seria pulada, e duas voltas que diferissem nela seriam relatadas idênticas.
Com descoberta, o padrão é o contrário: **coluna nova entra na comparação
esportiva**, e um teste fixa isso.

Quatro colunas ficam de fora, cada uma com motivo registrado no código:

| coluna | por que não é comparada |
|---|---|
| `id` | chave substituta, diferente por construção entre duas linhas |
| `session_id` | chave de junção, igual nos dois por seleção |
| `car_id` | **é o que está sendo comparado**, não atributo da volta |
| `lap_number` | identidade da volta dentro da inscrição, não atributo |
| `created_at` | escrituração da ingestão, não fato sobre a volta |

`driver_id` é comparado, **mas à parte**.

---

## 2. As categorias de par

| categoria | significado |
|---|---|
| `EXACT_DUPLICATE` | todos os atributos concordam, inclusive `driver_id` |
| `DRIVER_ID_DIVERGENCE` | o esportivo concorda; os ids de piloto diferem |
| `DRIVER_ID_MISSING` | o esportivo concorda; um lado não tem `driver_id` |
| `SPORTING_CONFLICT` | divergência material num atributo esportivo |
| `UNMATCHED_LAP` | a volta não tem correspondente no outro portador |
| `UNDETERMINED` | a comparação não permite conclusão segura |

`DERIVED`, a ordem importa: o esportivo é pesado **primeiro**, porque
discordância ali é discordância sobre o que aconteceu na pista. `driver_id`
vem depois e sozinho — dois ids diferentes podem ser duas grafias de uma
pessoa, então a divergência é relatada como **questão de identidade**, não
como conflito de fato.

`UNDETERMINED` cobre também o caso de um atributo esportivo presente de um
lado e ausente do outro: é lacuna, não contradição, e o módulo não decide
lacunas.

---

### 2.1 A evidência é completa; o limite só exibe

`pair_evidence` lista os pares não exatos, e por padrão corta em 20. O corte
é **de exibição e nada mais**:

```
divergencias: 90   exibidas: 20   truncado: True
como_ver_o_resto: "--evidence-limit 0 lista todas; a contagem e a
                   classificacao nao dependem deste limite"
```

`MEASURED`: com `--evidence-limit 0` sobre as 225 entradas, as **2.363**
divergências aparecem inteiras (1.960 + 403), e **37 entradas** deixam de
ser truncadas. As contagens por categoria e os vereditos são **idênticos**
com e sem limite — um teste parametrizado em 0, 1, 5, 20 e 100 fixa isso.

`DERIVED`: nada é escondido e nada é exportado automaticamente. Para ver o
resto basta repetir a leitura com outro limite.

---

## 3. Os portadores como registros inteiros

**Voltas iguais não tornam dois `cars` intercambiáveis.** Para cada portador
o módulo lê os atributos de `cars`, a linha de `results`, os vínculos de
`result_drivers` e as referências analíticas.

`MEASURED`, as colunas `car_id` que existem no banco:

| tabela | FK |
|---|---|
| `results.car_id` | FK → cars |
| `laps.car_id` | FK → cars |
| `analytics_car_session.car_id` | **SEM FK** |
| `analytics_stints.car_id` | **SEM FK** |

`DERIVED`: as duas analíticas são criadas em tempo de execução por
`analytics/engine.py`, sem `REFERENCES`. Remover um `cars` **não levantaria
erro** nelas, e não existe chave estrangeira para redirecionar. Por isso a
presença de linha analítica em qualquer portador é registrada como
impedimento `ANALYTICS_REFERENCES_WITHOUT_FK`.

### 3.1 Os `results` de cada portador, comparados à parte

Cada portador tem a sua linha em `results`, e essa linha **não são as
voltas**: um portador cujas voltas batem com as do outro ainda pode guardar
posição, status ou proveniência diferentes.

As colunas vêm do catálogo, pelo mesmo motivo das de `laps`. Quatro ficam de
fora:

| coluna | por que não é comparada |
|---|---|
| `id` | chave substituta, diferente por construção |
| `session_id` | chave de junção, igual nos dois por seleção |
| `car_id` | **é o portador**, não atributo do resultado |
| `created_at` | escrituração da ingestão, não fato sobre o resultado |

`MEASURED`, as 14 comparadas: `position`, `status`, `laps_completed`,
`total_time_raw`, `total_time_s`, `gap_to_first_raw`, `gap_to_first_s`,
`gap_to_prev_raw`, `gap_to_prev_s`, `fl_lap_number`, `fl_time_raw`,
`fl_time_s`, `fl_kph` e **`source_url`** — a proveniência entra, porque uma
diferença ali seria material.

Dois achados, separados:

| | o que é | impedimento |
|---|---|---|
| **diferença material** | os dois lados afirmam, e afirmam coisas diferentes | `RESULT_MATERIAL_DIFFERENCE` |
| **informação exclusiva** | um lado guarda valor que o outro deixa NULL | `RESULT_EXCLUSIVE_INFORMATION` |

Qualquer um dos dois **impede `CANDIDATE`**: o primeiro porque só um pode
descrever a inscrição e o módulo não escolhe; o segundo porque descartar
aquele portador perderia o valor.

`DERIVED`: isto **não se mistura** com a equivalência das voltas. Voltas
iguais e `results` divergentes é combinação possível, e o relatório diz as
duas coisas separadamente.

---

## 4. Equivalência ≠ seleção canônica

Dois conceitos, e **só o primeiro é respondido aqui**.

**Equivalência** — os dois portadores representam o mesmo conjunto de
voltas? `EXACT_EQUIVALENCE` · `SPORTING_EQUIVALENCE_WITH_IDENTITY_AMBIGUITY`
· `NON_EQUIVALENT` · `UNDETERMINED`.

**Seleção canônica** — há evidência para preferir um portador? **Não
implementada.** `candidate_car_ids` traz sempre **os dois**.

`DERIVED`: id menor, `created_at` mais antigo, mais metadados ou mais
referências **não são evidência de correção**. Devolver um id só se leria
como decisão, e a decisão não foi tomada. Um teste afirma que o objeto nem
sequer tem um campo `canonical_car_id`.

### 4.1 O veredito operacional

| veredito | critério |
|---|---|
| `CANDIDATE` | equivalência integral demonstrada **e** nenhum impedimento conhecido |
| `REQUIRES_SPECIAL_HANDLING` | equivalência esportiva demonstrada, com ambiguidade de piloto, metadado exclusivo ou dependência a tratar |
| `BLOCKED` | conflito esportivo, volta sem par, população inválida, ou evidência insuficiente |

Precedência: a equivalência decide primeiro. `NON_EQUIVALENT` e
`UNDETERMINED` vão direto a `BLOCKED`. Só então os impedimentos rebaixam de
`CANDIDATE` para `REQUIRES_SPECIAL_HANDLING`.

> `CANDIDATE` diz que as voltas foram demonstradas iguais e que nada
> conhecido impede **estudar** uma consolidação. **Não diz que ela está
> autorizada.**

---

## 5. A população

| exigência | |
|---|---|
| três linhas de resultado para a inscrição | da classe de triplicação |
| exatamente dois `car_id` com voltas | a população das 225 investigadas |

Fora disso o módulo devolve `OUTSIDE_ASSESSMENT_CLASS` com `BLOCKED`, **sem
aplicar critério algum**. `DERIVED`: as conclusões vieram de 225 inscrições
de dois portadores; estendê-las a três portadores ou a resultados
incompletos seria responder com evidência que nunca foi reunida.

---

## 6. Reconciliação com a investigação

`MEASURED`, `--whole-class`, 225 entradas em 92 sessões:

### 6.1 Os pares reproduzem exatamente

| | investigação | avaliador | |
|---|---:|---:|---|
| pares comparados | 10.430 | **10.430** | ✓ |
| `EXACT_DUPLICATE` | 8.067 | **8.067** | ✓ |
| `DRIVER_ID_DIVERGENCE` | 1.960 | **1.960** | ✓ |
| `DRIVER_ID_MISSING` | 403 | **403** | ✓ |
| `SPORTING_CONFLICT` | 0 | **0** | ✓ |
| `UNMATCHED_LAP` | 0 | **0** | ✓ |
| entradas · sessões | 225 · 92 | **225 · 92** | ✓ |

Equivalência: **135** `EXACT_EQUIVALENCE` e **90**
`SPORTING_EQUIVALENCE_WITH_IDENTITY_AMBIGUITY`.

### 6.2 Os vereditos **não** reproduzem, e a causa é conceitual

| | investigação | avaliador |
|---|---:|---:|
| `CANDIDATE` | 119 entradas · 45 sessões | **1 entrada · 1 sessão** |
| `REQUIRES_SPECIAL_HANDLING` | 106 · 47 | **224 · 91** |
| `BLOCKED` | 0 · 0 | **0 · 0** |

**Não ajustei nada para coincidir.** A diferença vem do §4 deste escopo, que
a investigação não aplicava: **avaliar os portadores como registros
inteiros**.

`MEASURED`, impedimentos por entrada (reexecutado com a comparação de
`results` em vigor):

```
 110  EXCLUSIVE_METADATA_ON_A_CARRIER
  82  DRIVER_IDENTITY_UNRESOLVED + EXCLUSIVE_METADATA_ON_A_CARRIER
  14  ANALYTICS_REFERENCES_WITHOUT_FK + EXCLUSIVE_METADATA_ON_A_CARRIER
  10  ANALYTICS_REFERENCES_WITHOUT_FK
   8  ANALYTICS_REFERENCES_WITHOUT_FK + DRIVER_IDENTITY_UNRESOLVED
      + EXCLUSIVE_METADATA_ON_A_CARRIER + RESULT_DRIVERS_DIFFER
   1  (nenhum)
```

`DERIVED`: **214 das 225 entradas têm `tyre_supplier` em um portador só** —
sempre o da primeira leva. A investigação registrava isso como nota; aqui é
impedimento, porque manter o outro portador perderia o metadado e isso é
exatamente a perda silenciosa que o escopo proíbe.

`MEASURED`, aplicando o critério **da investigação** (só voltas e identidade
de piloto, ignorando metadado e analíticas) aos dados de hoje: **135
entradas em 81 sessões**, e 51 sessões com todas as entradas passando. A
diferença restante para os 119/45 originais são os filtros de sessão que a
investigação aplicava — fonte local ausente e identidade partida em
`results` — que aqui viram impedimento **por entrada**.

`DERIVED`: a avaliação nova é **mais estrita**, e deliberadamente.

### 6.2.1 A comparação de `results` não encontrou nada

`MEASURED`, nas 225 entradas: **0** com diferença material em `results` e
**0** com informação exclusiva. As 14 colunas comparadas concordam nos dois
portadores em todas elas, `source_url` incluído.

`DERIVED`: os vereditos **não mudaram** por causa deste acréscimo —
continuam 1 / 224 por entrada e 1 / 91 por sessão. É uma afirmação
verificada, não uma verificação ausente: a PR #57 já havia medido que todas
as colunas de `results` concordam entre as três levas, e a comparação por
portador confirma pelo outro lado.

### 6.3 A matriz da PR #57 não muda

`MEASURED`: o avaliador de triplicação devolve para a sessão 855 exatamente
o que devolvia antes — `BLOCKED`, 33 elegíveis, 2 bloqueadas, os mesmos três
motivos. Este módulo é independente, tem vereditos de nome próprio e **não
altera 982 / 240 / 93**.

### 6.4 A única entrada `CANDIDATE`

```
sessao 1594 #128: CANDIDATE  [EXACT_EQUIVALENCE]
  pares: EXACT_DUPLICATE=7  total=7
  candidate_car_ids: [32045, 351403]   (nenhuma preferencia demonstrada)
```

ELMS 2022, Monza, `Qualifying Practice 2`. 7 voltas, idênticas em tudo,
nenhum metadado exclusivo, nenhuma analítica. **Mesmo assim os dois ids
voltam.**

---

## 7. A sessão 855

```
sessao 855 #007: REQUIRES_SPECIAL_HANDLING  [EXACT_EQUIVALENCE]
  pares: EXACT_DUPLICATE=151  total=151
  impedimentos: EXCLUSIVE_METADATA_ON_A_CARRIER, ANALYTICS_REFERENCES_WITHOUT_FK
  candidate_car_ids: [18214, 197636]

sessao 855 #009: REQUIRES_SPECIAL_HANDLING  [SPORTING_EQUIVALENCE_WITH_IDENTITY_AMBIGUITY]
  pares: EXACT_DUPLICATE=46  DRIVER_ID_DIVERGENCE=90  total=136
  impedimentos: DRIVER_IDENTITY_UNRESOLVED, EXCLUSIVE_METADATA_ON_A_CARRIER,
                RESULT_DRIVERS_DIFFER, ANALYTICS_REFERENCES_WITHOUT_FK
  candidate_car_ids: [18240, 197662]
```

Bate com a investigação: 151/151 no 007; 46 exatas e 90 com `driver_id`
divergente no 009 — ids **43** (`Marco Sørensen`) e **330979**
(`Marco SØRENSEN`). Zero conflito esportivo e zero volta sem par nos dois.

`DERIVED`: nomes que dobram iguais **não são prova de uma pessoa**.
`full_name_key` é usado para dizer que o par **merece ser olhado**, e
nenhuma fusão é feita.

**O carro 77 não entra nesta população**: tem um único portador. As suas 128
linhas contra 127 voltas classificadas continuam um problema separado.

---

## 8. Contrato de somente leitura

| | |
|---|---|
| banco histórico | lido e nunca escrito; todo comando é `SELECT`, e um teste afirma isso sobre as consultas emitidas |
| conexão | `default_transaction_read_only=on` |
| `raw/` | não é aberto por este módulo |
| `--json` | **a única escrita**: um relatório local, no caminho que o operador indicar |

`DERIVED`: o relatório é escrito **depois que a conexão já foi fechada**. Um
caminho impossível devolve saída 1, imprime o motivo e diz que o banco não
foi tocado. `PROPOSED`: destino num diretório temporário, fora do
repositório e fora de `raw/`.

Uma falha de leitura levanta `CarrierAssessmentError` e **não produz
classificação nenhuma** — em particular, nunca `CANDIDATE`.

---

## 9. Reproduzir

```bash
# uma inscricao
python -m database.recovery.carriers --session-id 855 --number 009 \
  --password "$DB_PASSWORD"

# a mesma, com todas as divergencias listadas em vez das 20 primeiras
python -m database.recovery.carriers --session-id 855 --number 009 \
  --evidence-limit 0 --password "$DB_PASSWORD"

# uma sessao inteira
python -m database.recovery.carriers --session-id 855 --password "$DB_PASSWORD"

# a reconciliacao deste documento
python -m database.recovery.carriers --whole-class \
  --password "$DB_PASSWORD" --json /tmp/openwec/classe.json
```

---

## 10. O que esta avaliação não faz

1. **Não escreve no banco**, não consolida, não deduplica, não reingere.
2. **Não escolhe** um portador: `candidate_car_ids` traz sempre os dois.
3. **Não funde pilotos** — nem por nome dobrado, nem por nada.
4. **Não altera** a matriz 982 / 240 / 93 da PR #57.
5. **Não corrige** os carregadores históricos, ainda que a investigação
   tenha demonstrado os defeitos deles.
6. **Não toca** em `session_at`, `lap_recorded_at` nem na issue #31.
7. **Não responde** pela proveniência da Hora 6, pela homologação, nem pelo
   carro 77.

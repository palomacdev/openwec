# A exceção do carro sem voltas, e três ensaios

`MEASURED` em 2026-10-23, no mesmo ambiente descartável da PR #53. **Nada foi
escrito no banco histórico**, cujas 14 contagens terminam como começaram.

---

## 1. A regra

Um carro publicado pode ficar sem volta correspondente, e **só de um jeito**:
quando a fonte o credita com `LAPS = 0` sob um status dizendo que ele não
participou.

`DERIVED`: um carro que não completou volta nenhuma **não pode** aparecer num
log de voltas. A ausência dele ali não é desacordo entre as fontes — é o que
as duas fontes concordando parece. A regra de completude tratava as duas
situações como uma só.

As condições, todas exigidas:

| condição | como é verificada |
|---|---|
| `LAPS` é exatamente zero | `as_int` devolve `0` só para um zero escrito; campo vazio dá `None` e **não** passa |
| status normalizado é `Not Classified` ou `DNS` | `VALID_STATUSES` não tem "Not Started": a fonte escreve "Not started" e `normalize_status` mapeia para `DNS`, que é o mesmo fato sob o nome do enum |
| número único na classificação | recusa anterior, preservada: número repetido interrompe antes |
| nenhum `car_id` ambíguo para o número | recusa anterior, preservada: número em dois `car_id` interrompe antes. A exceção ainda exige explicitamente que o número não tenha volta alguma |
| identidade criável ou associável sem conflito | `upsert_car` resolve antes da escrita, e a recuperação recusa se a identidade colidir com a de um carro já pareado |

### 1.1 O que continua recusado

`MEASURED`, pelos testes:

- carro com voltas **ausente** da classificação — a exceção é de um lado só;
- carro com `LAPS > 0` sem correspondência;
- `LAPS = 0` com status `Classified`, `Retired`, `DNF`, `DSQ` ou vazio;
- `LAPS` vazio, em branco ou ilegível;
- número duplicado na fonte;
- número espalhado por vários `car_id`;
- identidade que resolve para um carro já pareado nesta sessão.

`DERIVED`: **a exceção não vira tolerância a pareamento parcial.** São 34
testes, e a maioria é negativa de propósito.

### 1.2 Por que a identidade é resolvida antes do `DELETE`

Um carro sem voltas não tem volta que o proteja. Se o `car_id` dele ficasse
fora do `keep`, a execução seguinte apagaria o resultado que a anterior
gravou e cunharia um carro novo — exatamente a repetição que o recuperador
existe para evitar. A resolução acontece antes, e o id entra no `keep` junto
com os pareados.

---

## 2. Elegibilidade, medida de novo

`MEASURED`, varredura completa das 51 sessões com a regra nova:

| | antes (PR #54) | agora |
|---|---:|---:|
| **`ELIGIBLE`** (fora o controle) | **30** | **40** |
| pareamento incompleto | 10 | **0** |
| número em mais de um `car_id` | 7 | 7 |
| sem voltas no banco | 3 | 3 |
| `SOURCE_UNAVAILABLE` | 0 | 0 |

`DERIVED`: as **dez** sessões que a PR #54 mediu como bloqueadas apenas por
um carro publicado sem par passaram, e **nenhuma outra mudou de estado**. O
número 40 é o que os dados mostraram, não um alvo perseguido: as 7 com
número em dois `car_id` e as 3 sem voltas continuam exatamente onde estavam.

`MEASURED`: a sessão 2721 (IMSA) teve um dos seus 36 números admitidos pela
exceção e **continua bloqueada** pelos outros 35. A regra não a salva, e não
deveria.

---

## 3. Os três ensaios

Ambiente confirmado antes de qualquer escrita:

```
container  openwec-recovery-rehearsal, 127.0.0.1:5455
cluster    7699915985261658146   (o histórico é 7642299396037144610)
identidade 64b625caa9b0403fb882a7e5e4cc94a5, conferida contra RECOVERY_REHEARSAL_ID
```

### 3.1 ELMS 2014, Imola — sessão 1035

| | antes | depois |
|---|---:|---:|
| `results` | 9.424 | **37** |
| `car_id` distintos em `results` | 9.424 | **37** |
| `result_drivers` | 0 | **102** |
| `laps` | 4.712 | **4.712** |
| `car_id` distintos nas voltas | 37 | **37** |

Fonte: `Hour 4/05_Classification_Race_Hour 4.CSV`, 5.959 bytes, 37 carros.
Planejamento: **37 pareados**, nenhum admitido pela exceção, nenhuma pendência
dos dois lados.

`MEASURED`: 9.387 `results` e 9.387 `cars` removidos; 111 colunas de `cars`
preenchidas; **555 campos comparados com o CSV, 0 divergências**; 103 nomes
na fonte, 102 vinculados, **1 recusado por ambiguidade**.

### 3.2 Le Mans Cup 2019, Paul Ricard — sessão 2279

| | antes | depois |
|---|---:|---:|
| `results` | 3.026 | **31** |
| `result_drivers` | 0 | **62** |
| `laps` | 1.513 | **1.513** |
| `car_id` distintos nas voltas | 31 | **31** |

Fonte: `Hour 2/05_Classification_Race.CSV`, 4.613 bytes, 31 carros.
Planejamento: **31 pareados**, nenhum admitido pela exceção.

`MEASURED`: 2.995 removidos de cada lado; 62 colunas preenchidas; **465
campos comparados, 0 divergências**; 62 nomes, **62 vinculados, 0 ambíguos**.

### 3.3 ELMS 2013, Silverstone — sessão 999, o caso da regra nova

| | antes | depois |
|---|---:|---:|
| `results` | 1.878 | **23** |
| `result_drivers` | 0 | **54** |
| `laps` | 939 | **939** |
| **`car_id` distintos nas voltas** | **22** | **22** |

Fonte: `03_Race/05_Classification_Race_Race.CSV`, 3.553 bytes, **23 carros**.
Planejamento: **22 pareados** e **1 admitido pela exceção — o carro 47**.

`MEASURED`, o que a fonte diz do 47 e o que ficou gravado:

| | na fonte | em `results` |
|---|---|---|
| posição | 23 | 23 |
| status | `Not classified` | **`Not Classified`** |
| `LAPS` | `0` | **0** |
| `TOTAL_TIME` | vazio | NULL |
| `GAP_FIRST` | `48 Laps` | `48 Laps`, sem segundos |
| equipe · veículo · classe | Team Endurance Challenge · Formula Le Mans Oreca 09 · LMPC | idem |
| pilotos vinculados | 2 na fonte | **2** |
| **voltas na sessão** | — | **0** |

`DERIVED`: **nenhuma volta foi inventada.** O total de `laps` não se moveu e
o número de carros com voltas continua 22, embora `results` tenha 23 linhas.

`MEASURED`: 345 campos comparados com o CSV, **0 divergências**; 55 nomes na
fonte, 54 vinculados, 1 recusado por ambiguidade.

#### A identidade do carro 47

`MEASURED`: `upsert_car` **reaproveitou** o `cars.id` 20022, criado em
2026-05-22 — `#47`, Team Endurance Challenge, Formula Le Mans Oreca 09, LMPC,
pneu M —, porque a tupla publicada bate em todas as colunas que o carregador
escreve. Nenhum carro novo foi criado para ele.

`MEASURED`: esse carro também é referenciado pela sessão **996**, o Free
Practice 1 do mesmo evento, onde tem 19 voltas. `DERIVED`: as voltas são de
996 e continuam de 996 — `laps` carrega o seu próprio `session_id`, e
`results` é chaveado em `(session_id, car_id)`, de modo que as duas linhas
convivem. É o mesmo compartilhamento que o carregador já pratica em todo o
acervo, e a ADR 0001 é explícita em que `cars` não tem escopo temporal.

`UNKNOWN`: se as duas linhas descrevem a mesma inscrição competitiva. A
pergunta é do modelo de `entries`, não desta recuperação.

---

## 4. Idempotência

`MEASURED`, segunda execução de cada ensaio:

| | 1035 | 2279 | 999 |
|---|---:|---:|---:|
| `results` removidas | 0 | 0 | 0 |
| `cars` removidos | 0 | 0 | 0 |
| colunas de `cars` preenchidas | 0 | 0 | 0 |
| `results` gravadas | 37 | 31 | 23 |
| identidade do carro sem voltas | — | — | **20022, a mesma** |
| digest do conteúdo | idêntico | idêntico | **idêntico** |
| contagens globais | idênticas | idênticas | idênticas |

Digests: `ff2dc4b4cd509691f0b862c72eec428a` (1035),
`3d9c8569c7915d47a519160d3fe4e4d7` (2279),
`3fe65b93a0fc943da2850119990d0180` (999).

---

## 5. Nenhuma outra sessão se moveu

`MEASURED`, digest de `(id, results, laps)` das **5.424** demais sessões,
tomado antes e depois de cada execução:

| ensaio | antes | depois |
|---|---|---|
| 1035 | `dc533132e598c8073b2b367fa947fcb7` | idêntico |
| 2279 | `cfd157e4b9a1a6b4256572c477788143` | idêntico |
| 999 | `05dbcb4f39f636ba96e8a6446665658b` | idêntico |

O digest muda entre ensaios porque o ensaio anterior alterou a sua própria
sessão, que entra no conjunto do seguinte. Dentro de cada ensaio ele não se
move.

`MEASURED`, o banco histórico ao fim de tudo: as 14 contagens de `openwec-db`
são as do início da sessão de trabalho. `raw/` sem alteração. Nenhuma
migration, nenhuma reingestão, nenhum `car_id` consolidado, nenhum piloto
fundido. IMSA e as duas Road to Le Mans 2017 não foram tocadas.

---

## 6. O que estes ensaios não fazem

1. **Não recuperam em lote.** Três sessões, uma de cada vez.
2. **Não resolvem** as 7 sessões com número em vários `car_id`, nem as 3 sem
   voltas, nem a reatribuição de 2721.
3. **Não vinculam** os 2 pilotos recusados por ambiguidade.
4. **Não decidem** se o carro 20022 é a mesma inscrição em 996 e 999.
5. **Não decidem sobre homologação.** A fonte continua sendo corroborada
   como posterior à chegada, e nada além disso.

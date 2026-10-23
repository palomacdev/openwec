# Lap collision assessment

Fase 1 do [plano de recuperação](recovery-plan.md): inventário dos 35.045
pares de voltas que colidiriam numa consolidação de `cars` por identidade.

**Esta PR não produz regra de descarte.** Ela produz a evidência para que a
Fase 2 possa produzi-la.

**Ambiente medido:** `openwec@127.0.0.1:5433`, banco de desenvolvimento.
**Data:** 2026-10-23. **`main` em:** `2bd422d` (após a PR #43).
**Escrita executada:** nenhuma. Sem `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`,
DDL, migration, carregador ou reingestão — nem em transação revertida.
Nenhum índice ou estrutura alterado.

---

## 1. Resumo executivo

Os 35.045 pares **não divergem em nada que descreva a volta**. Tempo, três
setores, velocidade, velocidade máxima, tempo decorrido, hora, pit, flag,
melhoria, slot do piloto: **zero conflitos em todas essas colunas, nos
35.045 pares.** A única coluna que diverge em algum par é `driver_id`.

| classe | pares | % |
|---|---:|---:|
| `IDENTICAL` | **23.048** | 65,8 % |
| `COMPLEMENTARY` | **695** | 2,0 % |
| `CONFLICTING` | **11.302** | 32,2 % |
| `INDETERMINATE` | **0** | — |
| total | **35.045** | 100 % |

E os 11.302 conflitos são de `driver_id`, que é identificador técnico. Comparando
a **identidade do piloto** em vez do id:

| | pares | |
|---|---:|---|
| mesmo nome completo, grafia ou divisão diferente | **11.242** | 99,5 % |
| nome diferente | **60** | 0,5 % — todos numa sessão, todos o mesmo par |

Os 60 são `Nic Minassian` contra `Nicolas Minassian`, nas voltas 1 a 60 da
sessão 1217. O arquivo de origem de hoje escreve `Nicolas MINASSIAN` nessas
exatas 60 voltas. **Que as duas linhas descrevem a mesma volta está
corroborado pela fonte; que `Nic` e `Nicolas` sejam a mesma pessoa é um fato
do mundo que o dado não estabelece**, e fica como tal.

**Causa-raiz, nova:** `drivers` tem **2.845 linhas para 1.705 nomes
normalizados** — 1.140 excedentes, 1.079 nomes com duplicata. É a mesma
doença de `cars`, numa tabela que ninguém tinha olhado.

**Achado que condiciona a Fase 2:** em **todos** os 11.242 pares de mesmo
piloto, o registro que segue a convenção da fonte está no **maior** `id`. E
em 652 dos 695 complementares, o piloto está no maior `id`. Uma regra
"preservar o menor id" descartaria sistematicamente o lado correto.

---

## 2. Metodologia

### 2.1 Estratégia de consulta

O banco tem 2.034.223 voltas. Para não varrê-las mais de uma vez, a medição
é feita em **uma única passagem** que emite uma linha por grupo em colisão,
exportada por `COPY ... TO STDOUT`, e todo o resto é calculado localmente.

```sql
COPY (
WITH tup AS (SELECT id, (number,team_id,vehicle,car_class,car_group,tires,
                         imsa_car_id,imsa_team_id)::text AS t FROM cars),
l AS (SELECT l.session_id, tup.t, l.lap_number, l.id, l.car_id, /* ...demais colunas... */
      FROM laps l JOIN tup ON tup.id = l.car_id)
SELECT session_id, lap_number, count(*) AS n, min(id), max(id),
       count(driver_id),  count(DISTINCT driver_id),
       count(lap_time_s), count(DISTINCT lap_time_s),
       /* ...um par (count, count distinct) por coluna semântica... */
FROM l GROUP BY session_id, t, lap_number HAVING count(*) > 1
) TO STDOUT WITH CSV;
```

Custo medido: **3 min 26 s**, uma passagem, 35.045 linhas exportadas. Nenhuma
subconsulta repetida, nenhuma tabela temporária — uma tabela temporária seria
escrita, e escrita não está autorizada.

As consultas seguintes são por chave primária, restritas aos 11.997 grupos
que precisavam de detalhe: 23.994 `id` de `laps`, resolvidos em 1,2 s.

> **Uma tentativa foi interrompida e substituída.** A busca do piloto passando
> os 23.994 ids como variável de psql estourou o limite de linha de comando
> (`Argument list too long`). Foi trocada por um `ARRAY[...]` embutido no
> próprio arquivo SQL, lido por stdin. Sem custo no banco.

### 2.2 Colunas comparadas

Todas as colunas semânticas de `laps`, excluídos os identificadores técnicos
e a metadata de ingestão:

| comparadas | excluídas, e por quê |
|---|---|
| `driver_id`, `driver_slot`, `lap_time_s`, `s1_s`, `s2_s`, `s3_s`, `kph`, `top_speed_kph`, `elapsed_raw`, `hour_raw`, `lap_improvement`, `crossing_finish_in_pit`, `flag_at_fl`, `pit_time_s`, `lap_recorded_at` | `id` — chave técnica; `session_id`, `car_id`, `lap_number` — **são a colisão**; `created_at` — metadata de ingestão, não da volta |

`created_at` não entra na comparação, mas foi contado: **os 35.045 grupos têm
exatamente duas linhas, cada uma de uma janela de ingestão distinta.** As
duas linhas são a mesma origem lida duas vezes.

### 2.3 Critério de classificação

Para um grupo de `n` linhas e uma coluna `c`, com `cnt = count(c)` e
`dis = count(DISTINCT c)`:

| situação | condição |
|---|---|
| ambos nulos | `cnt = 0` |
| concordam | `cnt = n` e `dis = 1` |
| **conflitam** | `cnt = n` e `dis > 1` |
| **lacuna** | `0 < cnt < n` |

Nulo igual a nulo é concordância, não lacuna: é a equivalência apropriada de
`NULL` que o escopo pede.

| classe | regra |
|---|---|
| `CONFLICTING` | ao menos um conflito |
| `IDENTICAL` | nenhum conflito, nenhuma lacuna, ao menos uma concordância não nula |
| `COMPLEMENTARY` | nenhum conflito, ao menos uma lacuna, **e evidência de mesmo evento** |
| `INDETERMINATE` | o resto — sem conflito, mas sem evidência de que seja a mesma volta |

**Evidência de mesmo evento** é concordância não nula em algum atributo de
cronometragem daquela volta: `lap_time_s`, `s1_s`, `s2_s`, `s3_s`,
`elapsed_raw`, `hour_raw`, `lap_recorded_at`. Ficam de fora o piloto — que
faz muitas voltas — e os booleanos `lap_improvement` e
`crossing_finish_in_pit`, cujo default é `false`: concordar neles não
distingue duas voltas quaisquer.

**Nenhuma tolerância numérica foi usada.** Igualdade é igualdade exata, no
tipo armazenado. Nenhum par foi declarado igual por aproximação.

---

## 3. Pares por classificação

| classe | total | legítimos | contaminados |
|---|---:|---:|---:|
| `IDENTICAL` | 23.048 | 19.944 | 3.104 |
| `COMPLEMENTARY` | 695 | 403 | 292 |
| `CONFLICTING` | 11.302 | 10.963 | 339 |
| `INDETERMINATE` | 0 | 0 | 0 |
| **total** | **35.045** | **31.310** | **3.735** |

A divisão 31.310 / 3.735 reproduz exatamente a do assessment da PR #43.
Nenhum par é contado duas vezes: a classe sai de um `GROUP BY` sobre a chave
(sessão, tupla, número da volta), e cada grupo recebe exatamente um rótulo.

**Nenhum par ficou `INDETERMINATE`**, porque em todos os 35.045 há
concordância não nula em pelo menos um atributo de cronometragem. Não é um
resultado assumido: é consequência de não haver um único conflito fora de
`driver_id`.

---

## 4. Distribuição

### 4.1 Por série

| série | pares | `IDENTICAL` | `COMPLEMENTARY` | `CONFLICTING` |
|---|---:|---:|---:|---:|
| FIA World Endurance Championship | 29.222 | 18.729 | 0 | 10.493 |
| European Le Mans Series | 5.773 | 4.288 | 676 | 809 |
| Michelin Le Mans Cup | 50 | 31 | 19 | 0 |

ALMS não aparece: nenhuma colisão.

### 4.2 Por temporada, as dez maiores

| série / ano | pares | `IDENTICAL` | `COMPLEMENTARY` | `CONFLICTING` |
|---|---:|---:|---:|---:|
| WEC 2025 | 9.018 | 7.734 | 0 | 1.284 |
| WEC 2021 | 4.912 | 2.648 | 0 | 2.264 |
| WEC 2023 | 4.733 | 3.013 | 0 | 1.720 |
| WEC 2024 | 3.618 | 1.088 | 0 | 2.530 |
| WEC 2022 | 3.102 | 1.480 | 0 | 1.622 |
| ELMS 2017 | 3.084 | 2.513 | 292 | 279 |
| ELMS 2018 | 1.470 | 791 | 285 | 394 |
| WEC 2026 | 1.349 | 950 | 0 | 399 |
| WEC 2015 | 847 | 847 | 0 | 0 |
| WEC 2019 | 775 | 250 | 0 | 525 |

### 4.3 Por sessão

343 sessões envolvidas. Mediana de 35 pares por sessão, máximo de 2.603.

---

## 5. Divergências por coluna

### 5.1 Nos 11.302 pares conflitantes

| coluna | pares em conflito |
|---|---:|
| `driver_id` | **11.302** (100 %) |
| `lap_time_s` | 0 |
| `s1_s`, `s2_s`, `s3_s` | 0 |
| `kph`, `top_speed_kph` | 0 |
| `elapsed_raw`, `hour_raw` | 0 |
| `lap_improvement`, `crossing_finish_in_pit` | 0 |
| `flag_at_fl`, `pit_time_s`, `lap_recorded_at` | 0 |
| `driver_slot` | 0 |

**Um padrão só**, em 11.302 de 11.302 pares: exatamente uma coluna em
conflito, e sempre a mesma.

### 5.2 Nos 695 pares complementares

A lacuna é sempre em `driver_id`: uma linha tem piloto, a outra não. A
evidência de mesmo evento vem de `lap_time_s`, `s1_s`, `s2_s`, `s3_s`,
`elapsed_raw` e `hour_raw` — **todos os seis concordam em todos os 695**.

| | pares |
|---|---:|
| só a linha de **maior** id tem piloto | 652 |
| só a linha de **menor** id tem piloto | 43 |

---

## 6. O conflito de `driver_id`, examinado

### 6.1 Identificador técnico não é identidade

O escopo é explícito: identificadores técnicos diferentes não tornam, por si
sós, duas voltas semanticamente diferentes. Comparando o **nome**:

| | pares | % |
|---|---:|---:|
| mesmo nome completo, grafia ou divisão diferente | 11.242 | 99,5 % |
| nome diferente | 60 | 0,5 % |

Exemplos reais dos 11.242:

```
sessão  100 volta  7:  id=32     'Mark'          'Patterson'
                       id=329356 'Mark'          'PATTERSON'

sessão  775 volta 21:  id=4188   'Antonio Felix' 'Da Costa'
                       id=335124 'Antonio FELIX DA' 'COSTA'
```

O segundo caso é divisão, não grafia. `split_wec_driver_name` corta no último
espaço, então `Antonio FELIX DA COSTA` vira `('Antonio FELIX DA', 'COSTA')`,
enquanto outro caminho de parsing produziu `('Antonio Felix', 'Da Costa')`.
**A fronteira entre nome e sobrenome é artefato de qual parser rodou**, não
do conteúdo — por isso a comparação é feita sobre o nome completo
concatenado e normalizado em caixa e pontuação, e não sobre os dois campos
separadamente.

### 6.2 Os 60 que o nome não reconcilia

Todos na sessão 1217 (ELMS 2018, Red Bull Ring, Race), todos o mesmo par:

```
id=30285  'Nic'     'Minassian'
id=419822 'Nicolas' 'Minassian'
```

**Confrontado com o arquivo original** `23_Analysis_Race.CSV` daquela sessão:

| | |
|---|---:|
| voltas em conflito no banco | 60 |
| voltas de `Nicolas MINASSIAN` na fonte | 60 |
| os conjuntos coincidem? | **sim**, voltas 1 a 60 |
| ocorrências de `Nic Minassian` na fonte | **nenhuma** |

A fonte de hoje escreve `Nicolas MINASSIAN` nas exatas 60 voltas. Isso
**corrobora que as duas linhas descrevem as mesmas voltas** e mostra que uma
delas aponta para uma grafia que o corpus atual não contém.

**O que isso não estabelece:** se `Nic Minassian` e `Nicolas Minassian` são a
mesma pessoa. Essa é uma abreviação de primeiro nome, e decidi-la é um fato
do mundo, não do dado. Fica registrado como tal, e a Fase 2 decide o que
fazer com ele — não este documento.

### 6.3 Causa-raiz: `drivers` tem duplicatas

| | |
|---|---:|
| linhas em `drivers` | 2.845 |
| nomes distintos após normalizar caixa e pontuação | **1.705** |
| **linhas excedentes** | **1.140** |
| nomes com mais de uma linha | 1.079 |

```sql
WITH d AS (SELECT id, regexp_replace(lower(first_name||last_name),
                                     '[^a-z0-9]','','g') AS n FROM drivers)
SELECT count(*), count(DISTINCT n), count(*) - count(DISTINCT n) FROM d;
```

`get_driver_id` em `load_laps.py` procura por igualdade exata de
`(first_name, last_name)` e devolve `LIMIT 1` **sem `ORDER BY`** — o mesmo
defeito que a PR #41 corrigiu em `get_car_id` e `get_session_id`, numa função
que não estava no escopo daquela PR e continua como está.

### 6.4 Efeito de versões diferentes do arquivo de origem

A fonte de hoje escreve `Mark PATTERSON` e `Nicolas MINASSIAN` — sobrenome em
caixa alta, convenção do publicador. As linhas que apontam para
`Mark Patterson` e `Nic Minassian` só podem ter vindo de um conteúdo que o
corpus atual não tem.

Medido sobre os 11.242 pares de mesmo piloto:

| | pares |
|---|---:|
| registro que segue a convenção da fonte está no **maior** id | **11.242** (100 %) |
| está no menor id | 0 |

Essa unanimidade sustenta a hipótese de que `raw/` mudou entre as ingestões —
a mesma questão em aberto nº 7 do assessment da PR #43. **Não a comprova:**
não há registro de versão dos arquivos, e uma mudança de caminho de parsing
entre execuções produziria o mesmo efeito. As duas explicações continuam
abertas.

---

## 7. Verificação das 1.665 entradas e dos 1.566 carros

O assessment da PR #43 registrou três medidas das 52 sessões contaminadas:
1.665 entradas competitivas, 1.566 `cars` referenciados por `laps`, 158.275
voltas. A relação entre elas não estava explicada.

| | |
|---|---:|
| entradas (sessão, tupla) derivadas de `results` | **1.665** |
| entradas com **pelo menos uma volta** | **1.534** |
| entradas **sem nenhuma volta** | **131** |
| entradas presentes em `laps` e ausentes de `results` | **0** |
| entradas com voltas em mais de um `car_id` | **32** |
| `car_id` distintos citados por `laps` | **1.566** |

A aritmética fecha nas duas direções:

```
1.534 com voltas  +    131 sem voltas  = 1.665 entradas
1.534 entradas    +     32 com dois car_id = 1.566 car_id
```

### 7.1 Correção necessária ao plano da PR #43

O plano fixava como invariante da Fase 3 que **1.665** entradas estariam
representadas em `laps`, e que `count(DISTINCT car_id)` nessas sessões
deveria valer 1.665 depois do re-apontamento. **A medição mostra que é
1.534.** As outras 131 entradas existem apenas em `results` e não têm volta
nenhuma para re-apontar.

A correção documental mínima foi aplicada em
[recovery-plan.md](recovery-plan.md): o invariante passa a ser 1.534, com a
observação de que as 131 entradas sem voltas são inalcançáveis pela Fase 3,
que só move referências de `laps`.

**O invariante não foi afrouxado para fazer os números coincidirem.** Ele
estava errado: afirmava uma cobertura que o dado nunca teve. A meta correta é
mais estrita, não menos — exige saber que 131 entradas ficam de fora, em vez
de supor que todas são alcançadas.

### 7.2 Identidades incompatíveis

Nenhuma. As 32 entradas com voltas em mais de um `car_id` têm, por
construção da chave, a mesma tupla de oito atributos em todos os seus
`car_id`. Não há volta associada a uma identidade incompatível com a sua
entrada: a chave do agrupamento é a própria identidade.

---

## 8. Recuperabilidade por classe

| classe | demonstrável pelo dado | corroborável pela fonte | ambíguo | perdido ao descartar uma linha | preservado numa consolidação |
|---|---|---|---|---|---|
| `IDENTICAL` (23.048) | todos os 15 atributos coincidem | sim, por amostragem | nada | **nada** | tudo |
| `COMPLEMENTARY` (695) | seis atributos de cronometragem coincidem; só `driver_id` tem lacuna | sim | nada sobre a volta | **o piloto**, se for descartada a linha que o tem — 652 vezes a de maior id | a volta, e o piloto se for preservada a linha certa |
| `CONFLICTING`, mesmo piloto (11.242) | toda a cronometragem coincide; divergem só os `driver_id` | sim: a fonte tem uma das duas grafias | qual registro de `drivers` é o canônico | a referência a um dos dois registros do **mesmo** piloto | a volta; a identidade do piloto sobrevive em qualquer escolha |
| `CONFLICTING`, nome diferente (60) | toda a cronometragem coincide | sim: a fonte diz `Nicolas MINASSIAN` nas 60 voltas | **se `Nic` e `Nicolas` são a mesma pessoa** | a referência a uma das duas grafias | a volta; a identidade do piloto depende da decisão |

---

## 9. Limitações

1. **A comparação com a fonte é por amostra dirigida**, não exaustiva. Três
   sessões foram abertas (100, 775, 1217) para explicar os padrões
   encontrados. As contagens do documento vêm todas de varredura completa; a
   corroboração pela fonte vale para os casos citados.
2. **Não há registro de versão de `raw/`.** A hipótese de que os arquivos
   mudaram entre as ingestões é sustentada por evidência circunstancial
   unânime (§6.4), não comprovada.
3. **A identidade real de `Nic` e `Nicolas Minassian` não foi decidida**, e
   não pode sê-lo a partir do banco nem do corpus.
4. **A normalização de nome usada na comparação dobra caixa e pontuação e
   concatena nome e sobrenome.** Ela não dobra acentos, não resolve
   abreviações e não consulta fonte externa. Com outra regra os 11.242 e os
   60 mudariam.
5. **O conteúdo de `drivers` não foi auditado além da contagem de
   duplicatas.** Quantos dos 1.079 nomes com duplicata são de fato a mesma
   pessoa não foi verificado — é o mesmo tipo de pergunta que a classe C de
   `cars` deixou em aberto.
6. As 131 entradas sem voltas não foram investigadas: por que a classificação
   contaminada registrou um carro que o carregador de voltas não carregou
   fica como questão aberta.

---

## 10. Recomendações para a Fase 2

Isto é insumo para a decisão, **não a decisão**.

1. **Separar duas perguntas que a Fase 2 trata como uma.** "Qual linha de
   `laps` sobrevive" e "qual registro de `drivers` é o canônico" são
   decisões distintas. A segunda não é sobre voltas: é a mesma consolidação
   por identidade que `cars` precisa, aplicada a `drivers`.
2. **Para os 23.048 `IDENTICAL`, a evidência é suficiente.** As duas linhas
   coincidem em todos os 15 atributos; preservar qualquer uma não perde nada
   mensurável.
3. **Para os 695 `COMPLEMENTARY`, preservar a linha que tem o piloto**, e não
   a de menor id — em 652 dos 695 são lados opostos. Preservar o menor id
   perderia o piloto em 94 % dos casos.
4. **Não adotar "menor id" como regra geral.** Em 100 % dos 11.242 pares de
   mesmo piloto, o lado que segue a convenção da fonte é o de maior id. A
   regra de representante canônico que `row_identity.choose_car` usa para
   `cars` é correta ali, onde as linhas são comprovadamente idênticas, e
   seria errada aqui.
5. **Tratar `drivers` antes, ou junto.** 1.140 linhas excedentes em
   `drivers` são a causa de 11.242 dos 11.302 conflitos. Consolidá-las
   dissolve o conflito em vez de arbitrá-lo — e `get_driver_id` ainda usa
   `LIMIT 1` sem `ORDER BY`, então o defeito continua produzindo divergência
   a cada carga.
6. **Manter os 60 em aberto.** A fonte corrobora que são as mesmas voltas e
   indica uma das grafias, mas não estabelece a identidade da pessoa. São
   0,17 % dos pares; retê-los não bloqueia o resto.
7. **Reavaliar o escopo da Fase 2 à luz do item 5.** Se `drivers` for
   consolidado primeiro, 11.242 conflitos deixam de existir e a Fase 2 passa
   a decidir sobre 23.048 idênticos, 695 complementares e 60 em aberto — um
   problema materialmente menor.

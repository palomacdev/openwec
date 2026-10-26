# Driver identity group assessment

Avaliador **read-only** dos grupos de nomes equivalentes em `drivers`:
descobre os grupos, classifica cada um com a evidência anexada, e **nunca
escolhe uma linha canônica**.

**Nenhuma fusão foi executada e nenhuma está autorizada.** Nenhuma linha de
`drivers`, `laps` ou `result_drivers` foi alterada. Nenhuma matriz das
PRs #57 e #58 mudou.

**Ambiente medido:** `openwec@127.0.0.1:5433`, banco de desenvolvimento.
**Data:** 2026-10-26. **Base:** `bdd5879` (após a PR #58).
**Escrita no banco:** nenhuma.

Documento irmão: [`driver-identity-assessment.md`](driver-identity-assessment.md)
(PR #46, a auditoria que encontrou a causa) e
[`driver-consolidation-assessment.md`](driver-consolidation-assessment.md)
(PR #46, a simulação de consolidação). **Este documento não os substitui**,
e a §7 reconcilia as contagens das três avaliações.

---

## 1. Resumo executivo

`drivers` tem **2.862 linhas** para no máximo 1.783 pessoas: **1.079 nomes
dobrados ocupam 2.219 linhas**. A causa está fechada desde a PR #45; o
acervo duplicado não.

O módulo classifica os 1.079 grupos e três resultados governam o que se
pode fazer com eles:

1. **1.004 dos 1.079 grupos (93%) não têm evidência independente que
   corrobore a fusão.** 622 não têm atributo algum em nenhuma linha; 382 têm
   evidência em uma linha só, que não corrobora nem contradiz.

2. **Dos 18 grupos implicados nas divergências de volta da PR #58, em
   nenhum a corroboração alcança as duas linhas que carregam as voltas.**
   16 não têm corroboração alguma, 1 alcança parcialmente, 1 tem
   corroboração que não atinge nenhum dos portadores. Resolver "o grupo"
   não resolve o par que colide.

3. **Um identificador IMSA concorda sobre um marcador que não é pessoa.**
   As duas linhas `TBD` compartilham `imsa_driver_id = 1240`, e nenhum
   piloto real carrega esse id. A corroboração externa mais forte que esta
   tabela oferece concorda sobre algo que não é ninguém.

O avaliador devolve sempre **todos** os `driver_id` do grupo. Não existe
campo `canonical_driver_id`, e um teste verifica que a string `canonical`
não aparece em nenhum ponto do relatório.

---

## 2. O que o módulo é, e o que ele não é

| é | não é |
|---|---|
| uma classificação com a evidência anexada | uma decisão sobre quem é quem |
| uma lista de impedimentos nomeados | uma autorização |
| `driver_ids` com o grupo inteiro | uma escolha de sobrevivente |
| consultas `SELECT` em conexão read-only | qualquer escrita no banco |

Uma lista de impedimentos vazia **não é permissão**. Significa que este
módulo não encontrou obstáculo no que mediu — afirmação sobre a medição,
não sobre o que é permitido.

---

## 3. A dobra, e o que ela não afirma

Os grupos vêm de `full_name_key` de
[`row_identity.py`](../../database/loader/row_identity.py) — **a mesma dobra
que o carregador usa** para decidir que um piloto já está armazenado.
Emprestá-la garante que um grupo aqui é um grupo lá.

A dobra junta primeiro e último nome **antes** de dobrar, porque a fronteira
entre eles é artefato do parser: `split_wec_driver_name` corta no último
espaço, então `Giedo VAN DER GARDE` vira `('Giedo VAN DER', 'GARDE')`
enquanto outro caminho produziu `('Giedo', 'Van Der Garde')`.

**Dobrar igual é uma afirmação sobre texto, nunca sobre pessoas.** Dois
homônimos produzem a mesma chave e nada nesta tabela os separa.

### 3.1 O ponto cego da própria dobra

`MEASURED`: `full_name_key` dobra caixa e pontuação e **não diacríticos**.

| | |
|---|---:|
| grupos com a dobra atual | **1.079** |
| grupos se os diacríticos também fossem dobrados | 1.056 |
| linhas em grupos, dobra atual | **2.219** |
| linhas em grupos, com acento dobrado | 2.236 |
| grupos que **só** existem dobrando acento | **40** |

`Jérôme De Sadeleer` tem cinco linhas em três variantes de acento
(`Jerome`, `Jérome`, `Jérôme`) e **este módulo não compara nenhuma delas
entre si**. O mesmo vale para `François Perrodo`, `Jörg Bergmeister`,
`André Lotterer` e mais 36.

`DERIVED`: **1.079 é um limite inferior.** Dizer "1.079 grupos" sem dizer
isso superestimaria a cobertura. A dobra **não foi alterada** — ela precisa
continuar sendo a do carregador — e o número é medido e reportado por
`fold_blind_spot`, que não muda chave, grupo nem classificação alguma.

---

## 4. Critérios e precedência

### 4.1 O que conta como evidência

As colunas de `drivers` que não são o nome nem escrituração, **descobertas
do catálogo** e não escritas à mão:

```
short_name  country  license  hometown
imsa_driver_id  imsa_driver_plug_id  imsa_driver_rating
```

Excluídas, cada uma com o motivo declarado em `EXCLUDED_COLUMNS`: `id`
(chave substituta), `first_name` e `last_name` (o nome dobrado não é
evidência sobre de quem ele é), `created_at` (escrituração da ingestão).

O padrão é **inclusão**: uma coluna nova em `drivers` vira evidência em vez
de ser ignorada em silêncio. Um teste fixa isso.

Cada campo cai em **exatamente um** dos três estados:

| estado | significa |
|---|---|
| `agreeing` | duas ou mais linhas declaram, e os valores são iguais |
| `contradicting` | duas ou mais linhas declaram, e os valores diferem |
| `exclusive` | **uma única** linha declara — não corrobora nem contradiz |

Um campo ausente de todas as linhas não é nenhum dos três. **Um `NULL`
armazenado é silêncio, não concordância.**

### 4.2 Precedência

| ordem | classificação | critério | o que recusa afirmar |
|---|---|---|---|
| 1 | `NOT_A_PERSON` | **todos** os tokens do nome são marcadores | nenhuma pergunta de identidade se aplica a `TBD` |
| 2 | `CONFLICTING_EVIDENCE` | algum campo contradiz | **não prova pessoas distintas** |
| 3 | `CORROBORATED` | ao menos um campo concorda, nada contradiz | **não significa identidade comprovada** |
| 4 | `REVIEW_REQUIRED` | evidência existe, só de um lado | não corrobora nem contradiz |
| 5 | `INSUFFICIENT_EVIDENCE` | nada além da dobra | — |

A contradição vence a corroboração porque **um grupo pode ter as duas**, e
reportar só a concordância esconderia a divergência. `DeFrancesco` é
exatamente isso.

`NOT_A_PERSON` vem primeiro porque um grupo de marcadores também não tem
evidência: sem a precedência, `TBD` sairia como `INSUFFICIENT_EVIDENCE`, e
com corroboração externa sairia como `CORROBORATED`.

### 4.3 As categorias não são mutuamente exclusivas

Por isso há **classificação principal + flags**. Uma flag nunca muda a
classificação; ela carrega o que o rótulo único não cabe.

```
CASE_ONLY_DIFFERENCE                   1023
EVIDENCE_ON_ONE_ROW_ONLY                382
NO_EVIDENCE_ON_ANY_ROW                  622
EXTERNAL_ID_ABSENT                      738
SAME_CREATION_DATE                      275
SEGMENTATION_DIFFERENCE                  56
EXTERNAL_ID_AGREES                       43
MULTI_ROW_GROUP                          42
COUNTRY_CONFLICT                          5
EXTERNAL_ID_CONFLICT                      5
CONTRADICTION_ONLY_IN_WEAK_ATTRIBUTES     3
EXTERNAL_ID_ON_A_MARKER                   1
MONONYM_OR_PSEUDONYM                      1
REPEATED_ID_IN_ONE_RESULT                 1
```

### 4.4 Marcador é token do nome, não formato da linha

`MARKER_TOKENS` é uma lista literal — `tbd`, `tba`, `n/a`, `unknown`,
`piloto`, `?`, `-` e outros — e **todos** os tokens declarados precisam ser
marcadores. `TBD Smith` não é marcador.

A alternativa óbvia seria uma regra estrutural como "`first_name` vazio".
**Ela apagaria pessoas.** `Bankcy` e `Hash` são pseudônimos mononímicos de
competidores reais, e a inscrição mononímica deixa `first_name` vazio. O
grupo `bankcy` sai como `INSUFFICIENT_EVIDENCE` com flag
`MONONYM_OR_PSEUDONYM`, **nunca** como `NOT_A_PERSON`. Dois testes fixam
isso, incluindo um mononímico que *é* corroborado.

---

## 5. As duas advertências que o módulo foi construído em torno de

### 5.1 `country` divergente não prova pessoas distintas

A semântica do campo **não está estabelecida** — nacionalidade, licença, ou
a escolha da própria fonte. A PR #46 mediu um caso em que ele diverge
*dentro* de uma pessoa, e este módulo o reproduz:

```
devlindefrancesco: CONFLICTING_EVIDENCE  [CASE_ONLY]
  driver_ids: [16984, 34676]   (nenhum canonico escolhido)
  grafias: ['Devlin|DeFrancesco', 'Devlin|Defrancesco']
  flags: CASE_ONLY_DIFFERENCE, COUNTRY_CONFLICT, EXTERNAL_ID_AGREES,
         SAME_CREATION_DATE
  externos: imsa_driver_id AGREEMENT 1625 em [16984, 34676]
  justificativa: contradicao em country; country divergente NAO prova
    pessoas distintas: a semantica do campo nao esta estabelecida; um
    identificador externo CONCORDA no mesmo grupo, o que torna a
    contradicao da fonte, nao da identidade
```

A justificativa diz as duas coisas no mesmo fôlego, e é isso que o teste
verifica — não o rótulo.

### 5.2 Identificadores IMSA não são infalíveis

Três medições independentes, todas `MEASURED`:

**(a) Dois identificadores para um nome.** `Job van Uitert` tem quatro
linhas e **dois** `imsa_driver_id` (2174 e 3013). É o mesmo par que a
recuperação da PR #53 recusou vincular.

**(b) Um identificador concordando sobre um não-pessoa.** As linhas 24121
`Tbd|Tbd` e 361597 `TBD|TBD` compartilham `imsa_driver_id = 1240` **e**
`imsa_driver_plug_id = 1`. Consulta: nenhuma outra linha de `drivers`
carrega o id 1240. A flag é `EXTERNAL_ID_ON_A_MARKER`, e a justificativa
diz que o identificador "não estabelece pessoa".

**(c) O identificador menos estável da tabela é um IMSA.** `MEASURED` por
`attribute_stability`, sobre as **71** identidades externas
(`imsa_driver_id` não nulo) carregadas por mais de uma linha — o conjunto
mais próximo de "mesma pessoa" que esta tabela oferece:

| coluna | diverge em | de 71 | declarada fraca |
|---|---:|---:|---|
| `imsa_driver_plug_id` | **15** | 71 | sim |
| `imsa_driver_rating` | **8** | 71 | sim |
| `license` | 5 | 71 | não |
| `country` | 2 | 71 | não |
| `short_name` | 0 | 71 | não |
| `hometown` | 0 | 71 | não |

`DERIVED`: `imsa_driver_plug_id` diverge dentro de uma mesma identidade
externa **sete vezes mais** que `country`. Parece indexar a inscrição, não a
pessoa. E `imsa_driver_rating` é uma classificação desportiva que o piloto
muda entre temporadas **por desenho**.

Por isso existe a flag `CONTRADICTION_ONLY_IN_WEAK_ATTRIBUTES`: quando a
contradição está **somente** nessas duas colunas, a classificação continua
`CONFLICTING_EVIDENCE` — algo de fato contradiz — e a flag diz de que tipo
é a contradição. **A medição é executada, não citada**: nenhuma
classificação lê esse número, e ele entra no relatório como contexto.

---

## 6. A matriz

`MEASURED`, `--all --evidence-limit 0`, 1.079 grupos:

| classificação | grupos | o que afirma |
|---|---:|---|
| `INSUFFICIENT_EVIDENCE` | **622** | só a equivalência textual |
| `REVIEW_REQUIRED` | **382** | evidência de um lado só |
| `CORROBORATED` | **64** | concordante, sem contradição — **não é identidade comprovada** |
| `CONFLICTING_EVIDENCE` | **10** | contraditório — **não prova pessoas distintas** |
| `NOT_A_PERSON` | **1** | marcador |
| total | **1.079** | |

`622 + 382 + 64 + 10 + 1 = 1.079` e as linhas somam `2.219`.

Tamanhos: **1.037** grupos de 2 linhas, 29 de 3, 9 de 4, 2 de 5, 2 de 6.

Impedimentos por grupo:

```
1079  NAME_BEARING_VIEW_DEPENDS_ON_SPELLING
 622  NO_INDEPENDENT_EVIDENCE
 411  EXCLUSIVE_METADATA_ON_A_ROW
  42  MULTI_ROW_GROUP
  10  CONTRADICTING_ATTRIBUTE
   5  EXTERNAL_ID_CONFLICT
   1  NOT_A_PERSON
```

`NAME_BEARING_VIEW_DEPENDS_ON_SPELLING` aparece em **todos** os grupos, e
não por formalidade: `v_driver_career` materializa
`first_name || ' ' || last_name` como coluna, **não é alcançável por chave
estrangeira nenhuma**, e portanto uma busca de dependências por restrição
não a encontra. Hoje ela exibe cada grupo sob tantos nomes quantas forem as
grafias, partindo a carreira de uma pessoa.

Referências em jogo: **1.525.878 voltas** de 2.034.223 (75,0%) e **292.312
`result_drivers`** de 469.511 (62,3%) apontam para linhas dentro desses
grupos. Se houvesse uma linha canônica por grupo, **1.140 linhas** seriam
absorvidas.

---

## 7. Reconciliação

### 7.1 Com a investigação read-only anterior

| alvo | medido | |
|---|---:|---|
| linhas de `drivers` | **2.862** | ✓ |
| grupos de nomes equivalentes | **1.079** | ✓ |
| linhas nesses grupos | **2.219** | ✓ |
| grupos só por caixa | **1.023** | ✓ |
| grupos com diferença de segmentação | **56** | ✓ |
| grupos criados na mesma data | **275** | ✓ |
| grupos com evidência corroborante | 68 → **64** | **diverge** |
| grupos com evidência contraditória | 7 → **10** | **diverge** |

**A divergência é inteiramente da regra de evidência, e não ajustei nada
para coincidir.** A investigação comparou **4** campos — `country`,
`license`, `hometown`, `imsa_driver_id`. O módulo descobre **7** do
catálogo, acrescentando `short_name`, `imsa_driver_plug_id` e
`imsa_driver_rating`.

Os quatro grupos que mudaram, nominalmente:

| grupo | investigação | módulo | o que decidiu |
|---|---|---|---|
| `dionvonmoltke` | `CORROBORATED` | `CONFLICTING_EVIDENCE` | `imsa_driver_plug_id` 3 vs 4; país, licença, rating e `imsa_driver_id` concordam |
| `mateollarena` | `CORROBORATED` | `CONFLICTING_EVIDENCE` | `imsa_driver_plug_id` 3 vs 1; idem |
| `romandeangelis` | `CORROBORATED` | `CONFLICTING_EVIDENCE` | `imsa_driver_plug_id` 4/2/4 **e** `imsa_driver_rating` Silver/Gold/Silver |
| `tbdtbd` | `CORROBORATED` | `NOT_A_PERSON` | é marcador, e o `imsa_driver_id` concordante é o que torna o caso notável |

Aritmética fechada: `68 = 64 + 3 + 1` e `7 = 10 − 3`. Os três que migraram
carregam a flag `CONTRADICTION_ONLY_IN_WEAK_ATTRIBUTES`, cuja contagem é
exatamente **3**.

`DERIVED`: as 622 `INSUFFICIENT_EVIDENCE` e as 382 `REVIEW_REQUIRED`
reproduzem **exatamente** sob as duas regras. A regra mais larga só
reclassifica dentro dos 75 grupos que tinham alguma evidência.

### 7.2 Com a PR #46 (`driver-consolidation-assessment.md`)

Aquele documento classifica **os mesmos 1.079 grupos** com uma regra mais
estrita ainda — **só** `imsa_driver_id`, e cobrindo o grupo inteiro:

| PR #46 | grupos | aqui |
|---|---:|---|
| `FULLY_CORROBORATED` | 36 | subconjunto de `CORROBORATED` |
| `PARTIALLY_CORROBORATED` | 9 | subconjunto de `CORROBORATED` |
| `REVIEW_REQUIRED` | 376 | nome igual, **definição diferente** |
| `CONFLICTING_EVIDENCE` | **7** | nome igual, definição diferente (aqui **10**) |
| `POSSIBLE_HOMONYM` | 651 | sem equivalente direto |

**Dois rótulos coincidem de nome e divergem de definição**, e isso é um
risco de leitura, não um erro de nenhum dos dois:

- `REVIEW_REQUIRED` na PR #46 = "atributos presentes e consistentes, sem
  corroboração externa". Aqui = "evidência presente **em uma linha só**".
- `CONFLICTING_EVIDENCE` na PR #46 considera `country` e `imsa_driver_id`.
  Aqui considera as sete colunas.

Ao ler os dois documentos juntos, **a definição vale por documento**.

Este módulo não altera as avaliações anteriores, e a garantia é
**estrutural**, não aritmética: dois testes verificam, por análise da
árvore sintática, que nada de `triplication`, `carriers`, `merge_drivers`,
`normalize_drivers`, `subprocess` ou `os` é importado, e que não há `exec`,
`eval` nem `__import__`. Nenhum caminho de código daqui alcança aqueles
vereditos. A suíte completa de `tests/recovery/` — 346 testes, incluindo os
das PRs #57 e #58 — continua passando.

### 7.3 Com a PR #58 (portadores de voltas)

`MEASURED`, `--reconcile-carriers`, sobre a população daquela PR —
inscrições com três linhas de resultado e exatamente dois portadores:

| alvo | medido | |
|---|---:|---|
| pares de volta com `driver_id` divergente | **1.960** | ✓ |
| ...no mesmo grupo de nome equivalente | **1.960** | ✓ |
| ...em grupos diferentes | **0** | ✓ |
| ...fora de qualquer grupo | **0** | ✓ |
| pares com `driver_id` ausente de um lado | **403** | ✓ |
| grupos implicados | **18** | ✓ |

Os 18 grupos, por classificação:

```
 7  INSUFFICIENT_EVIDENCE   1.039 voltas divergentes
 9  REVIEW_REQUIRED           510
 1  CORROBORATED              200
 1  CONFLICTING_EVIDENCE      211
```

**E o resultado que mais importa:**

```
a corroboracao alcanca os portadores?
  SEM_CORROBORACAO_NO_GRUPO: 16
  PARCIAL:                    1
  NENHUM:                     1
  TODOS:                      0
```

`DERIVED`: **em nenhum dos 18 grupos a corroboração alcança as duas linhas
que carregam as voltas divergentes.** A classificação vale para o grupo; o
par que colide é outra coisa. Os dois casos não triviais:

```
antoniofelixdacosta  [CORROBORATED]  alcance=PARCIAL
   linhas do grupo  : [25, 4188, 17000, 330961, 335124, 353937]
   carregam voltas  : [4188, 335124]
   corroboradas     : [4188]

romandeangelis  [CONFLICTING_EVIDENCE]  alcance=NENHUM
   linhas do grupo  : [3843, 23944, 30341, 334779, 361420]
   carregam voltas  : [3843, 334779]
   corroboradas     : []
```

É exatamente o que a PR #46 §6.1 havia medido para `Roman De Angelis`:
*"consolidar só o que está corroborado deixaria 3843 e 334779 intactas"*.
Aqui isso deixa de ser observação de um caso e passa a ser medida dos 18.

**Correção a uma afirmação minha anterior.** Na investigação eu disse que
nenhum dos 18 **pares** tem evidência que decida, e isso continua verdadeiro
para os pares. Mas **3 dos 18 grupos têm mais linhas que os dois
portadores** — `antoniofelixdacosta` tem 6, `romandeangelis` 5, `pjhyett` 3
— e por isso o grupo pode ser corroborado enquanto o par não é. A métrica
`corroboracao_alcanca_os_portadores` existe para que a diferença não some.

### 7.4 O que esses números **não** dizem

- **1.960 pares comparáveis por nome não são 1.960 identidades provadas.**
  Um grupo `INSUFFICIENT_EVIDENCE` continua sem evidência por mais voltas
  que carregue. A string está no relatório, em `o_que_isto_nao_diz`.
- **Os 403 pares com `driver_id` ausente de um lado não se resolvem
  normalizando nome algum.** Um id ausente é um fato ausente; nenhuma dobra
  o fornece. Eles são contados separadamente e nunca entram nas
  divergências — um teste fixa isso.

---

## 8. Incógnitas

Registradas como incógnitas. **Não inventei explicação para nenhuma.**

### 8.1 Os 275 grupos criados na mesma data

`MEASURED`:

| | |
|---|---|
| data | **2026-05-22 em todos os 275** |
| diferença de grafia | **`CASE_ONLY` em todos os 275** |
| tamanho | 274 de 2 linhas, 1 de 3 |
| `created_at` igual ao segundo | **1 de 275** |
| classificação | 252 `REVIEW_REQUIRED`, 16 `CORROBORATED`, 7 `CONFLICTING_EVIDENCE` |

Dois fatos que a tabela torna visíveis e não explica:

- **Nenhum dos 275 é `INSUFFICIENT_EVIDENCE`**, enquanto 622 dos 1.079 da
  população geral são. Esses grupos têm evidência em proporção muito maior
  que o resto, e não sei por quê.
- As linhas nasceram em **momentos diferentes do mesmo dia** (274 de 275
  diferem no `created_at` completo), então não é uma inserção simultânea.

O ciclo de cinco passos da PR #46 explica os 804 grupos de datas
diferentes: carga → `normalize_drivers.py` reescreve → nova carga reencontra
a grafia da fonte → não casa no `UNIQUE` → segunda linha. **Esse ciclo não
explica estes 275**, porque o normalizador entrou em `7380c08`, 2026-05-27,
cinco dias depois. Uma hipótese seria dois caminhos de ingestão distintos no
mesmo dia, com grafias distintas; **não a testei e não a afirmo.**

### 8.2 Os 56 grupos com diferença de segmentação

`MEASURED`: 25 `INSUFFICIENT_EVIDENCE`, 20 `REVIEW_REQUIRED`, 8
`CORROBORATED`, 3 `CONFLICTING_EVIDENCE`.

A forma é consistente com partícula de sobrenome composto movida entre os
campos:

```
adriendeleener        ['Adrien|De Leener',  'Adrien DE|LEENER']
ahmadalharthy         ['Ahmad|Al Harthy',   'Ahmad AL|HARTHY']
alessandropierguidi   ['Alessandro Pier|Guidi', 'Alessandro|Pier Guidi',
                       'Alessandro PIER|GUIDI']
giedovandergarde      ['Giedo|Van Der Garde', 'Giedo|van der Garde',
                       'Giedo VAN DER|GARDE']
```

`split_wec_driver_name` corta no último espaço e `split_compound_name` move
partículas da lista `PARTICLES`; as duas produzem cortes diferentes para o
mesmo nome. **Não medi qual caminho gerou qual linha**, e por isso registro
a forma, não a atribuição de causa.

### 8.3 A semântica de `country`

Não sei se é nacionalidade, licença esportiva ou escolha da fonte. Decide se
as 5 contradições de país são homonímia ou colisão de definição, e **nada no
esquema responde**. Enquanto não for decidido, `COUNTRY_CONFLICT` é uma flag
e a justificativa recusa tratá-la como prova.

---

## 9. Segurança

| exigência | como |
|---|---|
| conexão read-only explícita | `psycopg2.connect(options="-c default_transaction_read_only=on")` |
| somente `SELECT` | teste varre cada instrução emitida; verifica palavra inteira e exige que **toda** instrução comece com `SELECT` |
| nenhum `UPDATE`/`INSERT`/`DELETE`/`ALTER`/`DROP` | idem, por limite de palavra |
| nenhuma migration | nenhuma criada |
| nada em `raw/` | o módulo não abre `raw/` |
| `normalize_drivers.py` e `merge_drivers.py` não executados | não são importados |
| carregadores e pipelines intactos | só `row_identity.full_name_key` é importado, para leitura |
| relatório JSON só com caminho explícito | `--json`, **depois de a conexão fechar** |

Sobre o teste de somente-`SELECT`: a primeira versão procurava `"CREATE"`
como substring e falhava em `created_at`. O teste **estava errado**, não o
código. A versão atual usa limite de palavra e acrescenta a verificação de
que toda instrução começa com `SELECT` — que a busca por palavra sozinha
não estabeleceria.

Nota de ambiente: `default_transaction_read_only` está **`off`** no cluster
de desenvolvimento. O que garante leitura é a opção **por conexão** acima, e
não um default do servidor.

---

## 10. Riscos e limitações

1. **1.079 é limite inferior** (§3.1): 40 grupos existem só dobrando acento
   e não são avaliados.
2. **93% dos grupos não têm evidência a favor da fusão.** O risco não é
   errar um; é normalizar decidir por grafia.
3. **A escala é a do acervo:** 75% das voltas e 62% dos `result_drivers`
   apontam para linhas desses grupos.
4. **Irreversibilidade prática:** `laps.driver_id` e `result_drivers.driver_id`
   são `NO ACTION`, nenhuma das 2.219 linhas é órfã, e **não há coluna que
   registre procedência**. Sem gravar origem no mesmo ato, uma fusão não é
   reversível por consulta.
5. **`v_driver_career` muda sem aparecer em FK alguma.** Carreiras hoje
   partidas passariam a somar — efeito desejado, mas que altera números já
   publicados.
6. **Rótulos homônimos entre documentos** (§7.2): `REVIEW_REQUIRED` e
   `CONFLICTING_EVIDENCE` existem na PR #46 com outra definição.
7. **`tests/recovery/` não é executado no CI.** O workflow roda `schema`,
   `loader`, `enrichment`, `sdk` e `api`. Os 50 testes deste módulo, como os
   das PRs #53–#58, rodam apenas localmente. **Não alterei o pipeline**, por
   estar fora do escopo autorizado; o registro fica aqui.
8. **Nenhuma fonte externa foi consultada.** As contradições da §5 ficam em
   aberto, não resolvidas.

---

## 11. Uso

```bash
# toda a população, com a evidência inteira
python -m database.recovery.driver_identity --all --evidence-limit 0 \
  --json /caminho/fora/do/repo/identidade.json

# um grupo, pela chave dobrada ou por qualquer grafia
python -m database.recovery.driver_identity --key "Devlin DeFrancesco"

# a reconciliação com os portadores da PR #58
python -m database.recovery.driver_identity --reconcile-carriers
```

`--evidence-limit` limita **quantas entradas de evidência são listadas**,
nunca quantas são contadas nem como o grupo é classificado. Quando a lista é
cortada, o que sobrevive é a contradição — o que mais restringe uma
reconciliação. `0` lista todas, e os metadados sempre dizem quantas havia e
se a lista foi cortada. Dois testes fixam que classificação, flags e
impedimentos são idênticos com e sem limite.

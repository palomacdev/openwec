# Driver consolidation assessment

Avaliação da possibilidade de consolidar identidades de piloto no acervo
histórico. **Somente leitura.** Nenhuma consolidação foi executada e nenhuma
está autorizada.

**Ambiente medido:** `openwec@127.0.0.1:5433`, banco de desenvolvimento.
**Data:** 2026-10-23. **`main` em:** `24eb188` (após a PR #45).
**Scripts de `enrichment/` executados:** nenhum.
**Escrita no banco persistente:** nenhuma.

---

## 1. Resumo executivo

A resposta curta é que **o acervo não sustenta uma consolidação automática
em massa**, e a diferença entre o que parecia e o que se mede é grande.

| | |
|---|---:|
| grupos de nome normalizado duplicado | 1.079 |
| **com evidência independente do nome** | **45** (4,2 %) |
| que exigem revisão humana | 376 |
| com evidência conflitante | 7 |
| sem nada além do nome | **651** (60,3 %) |
| com evidência positiva de pessoas distintas | 0 |

**Correção material à PR #44.** Aquele documento recomendou consolidar
`drivers` porque isso "dissolve 11.242 dos 11.302 conflitos". Medido agora
contra a classificação por evidência: **604 conflitos (5,3 %) desapareceriam
com uma consolidação segura**, não 11.242. Os outros 8.665 dependem de
grupos cuja única evidência é o nome coincidir, que é exatamente o que não
pode bastar. A recomendação está corrigida em §8.

**Nenhum critério de sobrevivente é evidentemente certo.** Testados sete
critérios sobre os 1.072 grupos consolidáveis em tese: em **zero** deles os
sete concordam. A escolha muda o registro que sobrevive em todos os casos.

**`merge_drivers.py` tem um risco imediato.** O `needs_review.json` que
existe para capturar a revisão humana está commitado com **`"action":
"merge"` em todos os 15 pares**, incluindo `Nicolas Minassian` →
`Nic Minassian`. Executar `--apply-reviewed` hoje apagaria `Nicolas` e
redirecionaria as 60 voltas para `Nic` — decidindo, sem revisão, justamente
o caso que as PRs #44 e #45 deixaram em aberto.

---

## 2. Metodologia e limitações

Consultas somente leitura; o inventário de `drivers` (2.845 linhas) é
exportado por `COPY ... TO STDOUT` e processado em memória. As agregações de
`laps` e `result_drivers` por `driver_id` são de uma passagem, pelos índices
existentes. Nenhum índice ou estrutura foi criado.

A normalização é `full_name_key` de `row_identity.py`, a mesma da PR #45:
nome e sobrenome concatenados, caixa e pontuação dobradas, **acentos
preservados**, sem dicionário de apelidos.

**Limitações, declaradas de saída:**

1. Nenhuma fonte externa foi consultada. A identidade real das pessoas não é
   verificável a partir deste banco.
2. A classificação mede **evidência disponível**, não verdade. Um grupo
   `POSSIBLE_HOMONYM` pode ser uma pessoa só; o ponto é que o dado não o
   demonstra.
3. A simulação de §7 é aritmética sobre o estado atual. Não houve ambiente
   descartável com cópia do acervo — ele não existe, e criá-lo não estava no
   escopo.
4. Acentos não são dobrados, então `René` e `Rene` são grupos distintos e
   não aparecem entre os 1.079.

### 2.1 Baseline, verificado e não presumido

| | referência | medido |
|---|---:|---:|
| `drivers` | 2.845 | **2.845** |
| `laps` sem piloto | 432.174 | **432.174** |
| `results` | 671.353 | **671.353** |
| `laps` | — | 2.034.223 |
| `result_drivers` | — | 469.457 |
| grupos duplicados | 1.079 | **1.079** |
| colisões de voltas | 35.045 | **35.045** |
| conflitos só em `driver_id` | 11.302 | **11.302** |

Todos conferem.

---

## 3. Auditoria dos scripts existentes

### 3.1 `merge_drivers.py`

**Como decide que dois registros são a mesma pessoa**

```sql
FROM drivers d1 JOIN drivers d2
  ON d1.last_name = d2.last_name
 AND LEFT(d1.first_name, 3) = LEFT(d2.first_name, 3)
 AND d1.id != d2.id
WHERE d1.country IS NULL AND d2.country IS NOT NULL
```

Depois, `LEFT(first_name,4)` igual ⇒ fusão automática; diferente ⇒ revisão.

**Pressupostos perigosos encontrados**

| # | pressuposto | por que é perigoso |
|---|---|---|
| 1 | `d1.last_name = d2.last_name`, igualdade **exata** | é sensível a caixa, então `Patterson` ≠ `PATTERSON`. **O script não enxerga a duplicação que de fato existe**: encontra 15 pares hoje, contra 1.079 grupos medidos |
| 2 | país nulo ⇒ registro "WEC" descartável; país preenchido ⇒ "IMSA" canônico | a PR #45 mediu que o país **diverge entre linhas da mesma pessoa** (`Devlin DeFrancesco` CAN e ITA com o mesmo `imsa_driver_id`). Presença de país não é evidência de canonicidade |
| 3 | prefixo de 3 ou 4 letras do primeiro nome ⇒ mesma pessoa | é uma heurística de similaridade. `Christian` e `Christopher` compartilham quatro letras e seriam **fundidos automaticamente** se coexistissem com o mesmo sobrenome |
| 4 | o `needs_review.json` representa decisão humana | ele é gravado com `"action": "merge"` por padrão, e o arquivo commitado tem os 15 pares aprovados |

**Qual registro sobrevive:** sempre o que tem país. O outro é **apagado**
com `DELETE FROM drivers WHERE id = %s`. Os atributos do apagado —
`short_name`, `license`, `hometown` — são perdidos sem nenhuma tentativa de
transferência.

**Quais referências atualiza:** `result_drivers.driver_id` e
`laps.driver_id`. São as duas únicas chaves estrangeiras (§4), então a
cobertura está correta.

**Transações e integridade:** um `conn.commit()` único ao final de todas as
fusões seguras, outro ao final das revisadas. **Não há verificação de
integridade depois, nem rollback por item**: se uma fusão estiver errada,
tudo é confirmado junto. Não há conferência de que a soma das referências
antes e depois bate.

**Idempotência:** o registro apagado não é reencontrado, então repetir não
produz efeito adicional **sobre a mesma entrada**. Mas a entrada regride: o
carregador pode criar uma linha nova sem país para a mesma pessoa, e a
próxima execução a fundiria de novo.

**Fragilidade de codificação:** o `needs_review.json` é gravado com
`ensure_ascii=False` e `open(..., "w")` sem `encoding`, e lido com
`open(REVIEW_FILE)` — também sem. O arquivo commitado **não é UTF-8
válido** (`René` gravado em cp1252), então é ilegível num runner Linux.

**Os 15 pares que ele encontra hoje** são todos apelido ou acento:

```
Jonny/Jonathan Adam     Benjamin/Ben Barker    René/Rene Binder
Timothé/Tim Buret       Nicholas/Nick Cassidy  Mateo/Matteo Cressoni
Robert/Robby Foley      Benjamin/Ben Hanley    Daniel/Dan Harper
Gerald/Gerry Kraut      Nicolas/Nic Minassian  René/Rene Rast
Nicholas/Nick Tandy     Nicholas/Nick Yelloly  Donald/Don Yount
```

Nenhum é fusão automática (o prefixo de 4 difere em todos), e **todos os 15
estão pré-aprovados no arquivo de revisão**.

### 3.2 `normalize_drivers.py`

**Pode recriar o problema corrigido na PR #45?** Sim, e é exatamente o
mecanismo que o causou. Ele reescreve `(first_name, last_name)` para caixa
de título e move partículas. Depois disso, a grafia que a fonte ainda
carrega deixa de casar no `UNIQUE`, e a carga seguinte cria uma linha nova.
A PR #45 fez o carregador tolerar o descasamento quando há **um** candidato;
onde já houver vários, ele recusa. Então **uma nova execução do normalizador
pode aumentar o número de nomes ambíguos**, empurrando pilotos de
`BY_NORMALIZED_NAME` para `MULTIPLE_CANDIDATES`.

**Conflita com o `UNIQUE`?** Ele trata: `UPDATE` por linha, `commit` por
linha, e `except psycopg2.errors.UniqueViolation` → `rollback` e pula. Não
quebra — **mas pular deixa a duplicata viva**, com uma linha no nome
normalizado e outra no nome cru. É assim que os 1.079 grupos sobreviveram.

### 3.3 `enrich_wikidata.py`

Lê `drivers` sem país e consulta o Wikidata para preencher atributos. É a
única fonte **externa** disponível no projeto e, por isso, a candidata
natural a fornecer a evidência independente que §5 mostra faltar — mas
consultá-lo é rede e escrita, e está fora do escopo desta PR.

---

## 4. Inventário das referências

```sql
SELECT conrelid::regclass, conname FROM pg_constraint
WHERE confrelid = 'drivers'::regclass;
```

| referência | tipo | linhas |
|---|---|---:|
| `laps.driver_id` | FK | 1.602.049 apontando (432.174 nulas) |
| `result_drivers.driver_id` | FK | 469.457 |
| `v_driver_career` | view | derivada, recalculada — sem estado próprio |

**Não há outra referência direta ou indireta.** Não há tabela de analytics
com `driver_id` materializado.

### 4.1 Integridade atual

| | |
|---|---:|
| `laps` órfãs | **0** |
| `result_drivers` órfãos | **0** |
| `drivers` sem nenhuma referência | **0** |
| `result_drivers` com `(result_id, driver_id)` repetido | **14** |

As chaves estrangeiras estão íntegras. As 14 linhas com o mesmo piloto duas
vezes num resultado, em slots diferentes, são uma anomalia **preexistente**,
não um efeito de consolidação — e §7.4 mostra que uma consolidação não
acrescentaria nenhuma.

---

## 5. Classificação dos grupos

Critérios explícitos, aplicados aos 1.079 grupos:

| categoria | critério |
|---|---|
| `NOT_MERGEABLE` | dois ids do grupo aparecem no **mesmo `result_id`**, em slots diferentes — correram como pilotos distintos de um carro |
| `CONFLICTING_EVIDENCE` | `country` ou `imsa_driver_id` divergem entre linhas, ambos não nulos |
| `SAFE_CANDIDATE` | duas ou mais linhas compartilham o **mesmo `imsa_driver_id` não nulo** — identificador externo corroborando, além do nome |
| `POSSIBLE_HOMONYM` | nenhuma linha tem país nem identificador: **a única evidência é o nome** |
| `REVIEW_REQUIRED` | o resto: atributos presentes e consistentes, sem corroboração externa |

| categoria | grupos | linhas | `laps` | `result_drivers` |
|---|---:|---:|---:|---:|
| `SAFE_CANDIDATE` | **45** | 106 | 6.833 | 14.349 |
| `REVIEW_REQUIRED` | **376** | 784 | 861.441 | 255.515 |
| `CONFLICTING_EVIDENCE` | **7** | 18 | 17.782 | 6.763 |
| `POSSIBLE_HOMONYM` | **651** | 1.311 | 639.822 | 15.649 |
| `NOT_MERGEABLE` | **0** | 0 | 0 | 0 |
| total | **1.079** | **2.219** | | |

`45 + 376 + 7 + 651 = 1.079` e `106 + 784 + 18 + 1.311 = 2.219`.

### 5.1 Por que `NORMALIZED_EQUIVALENT` não vira `SAFE_CANDIDATE`

A PR #45 classificou 1.072 grupos como `NORMALIZED_EQUIVALENT`, que é uma
afirmação **sobre o texto**: os nomes coincidem depois de dobrar caixa e
pontuação. Não é uma afirmação sobre pessoas.

Aqui, 1.027 desses 1.072 **não têm nenhuma evidência além desse texto**: 651
não têm atributo algum, e 376 têm atributos consistentes mas nada que
corrobore de fora. Só 45 trazem um identificador externo compartilhado.

Duas pessoas com o mesmo nome produziriam exatamente o mesmo texto. A
classificação textual não as separa, e **nada na tabela separa** — a PR #45
mediu que país e `imsa_driver_id` divergem entre linhas da mesma pessoa, de
modo que nem servem como desempate negativo.

### 5.2 O teste que não encontrou nada

Se dois ids de um grupo aparecessem no mesmo `result_id` em slots
diferentes, seriam dois pilotos do mesmo carro — evidência positiva de
pessoas distintas.

```sql
SELECT count(*) FROM result_drivers rd1
JOIN result_drivers rd2 ON rd2.result_id = rd1.result_id
                       AND rd2.driver_id > rd1.driver_id
WHERE <rd1.driver_id e rd2.driver_id no mesmo grupo normalizado>;
-- 0
```

**Zero.** Nenhum grupo tem evidência positiva de distinção. Isso não
transforma os demais em seguros: ausência de prova de que são dois não é
prova de que são um.

---

## 6. Evidências de identidade, por categoria

### 6.1 `SAFE_CANDIDATE` — 45 grupos

A evidência é o `imsa_driver_id` repetido. Exemplo:

```
id   3843  'Roman DE'  'Angelis'     país —    imsa —      404 laps     3 vínculos
id  23944  'Roman'     'Deangelis'   país CAN  imsa 1379     0 laps    55 vínculos
id  30341  'Roman'     'De Angelis'  país CAN  imsa 1379   229 laps  2316 vínculos
id 334779  'Roman DE'  'ANGELIS'     país —    imsa —      622 laps     9 vínculos
id 361420  'Roman'     'DeAngelis'   país CAN  imsa 1379     0 laps    24 vínculos
```

Cinco linhas, três com o mesmo identificador externo. As outras duas são a
divisão de partícula que `split_wec_driver_name` produz. A evidência aqui é
boa — e §9 mostra que ela ainda não diz **qual das cinco sobrevive**.

### 6.2 `CONFLICTING_EVIDENCE` — 7 grupos

Medidos na PR #45 e reexaminados: nenhum é homônimo. `Devlin DeFrancesco`
tem CAN e ITA com o **mesmo** `imsa_driver_id`; `Job van Uitert` tem **dois**
identificadores IMSA para a mesma pessoa. A divergência é da fonte, não da
identidade — mas é divergência, e por isso não entram em `SAFE_CANDIDATE`.

### 6.3 `POSSIBLE_HOMONYM` — 651 grupos

Nenhuma linha tem país ou identificador. 1.311 linhas, 639.822 voltas. É a
maior categoria e a de menor evidência: só o nome.

---

## 7. Simulação de consolidação

Aritmética sobre o estado atual, sem escrever nada.

### 7.1 Referências que seriam redirecionadas — 45 grupos `SAFE_CANDIDATE`

Depende de quem sobrevive:

| critério do sobrevivente | referências redirecionadas |
|---|---:|
| mais referências no total | **5.841** |
| menor id | 8.622 |
| mais voltas | 8.630 |
| mais completo | 12.054 |
| grafia da fonte | 12.952 |

As 45 somam 21.182 referências. O critério escolhido muda o volume da
operação por um fator de **2,2**.

### 7.2 Colisões de voltas que desapareceriam

Das 11.302 colisões em que as duas linhas apontam para `driver_id`
diferentes:

| | conflitos | |
|---|---:|---|
| ambos os ids num grupo `SAFE_CANDIDATE` | **604** | desapareceriam com consolidação segura |
| num grupo `REVIEW_REQUIRED` | 1.973 | dependem de revisão humana |
| num grupo `POSSIBLE_HOMONYM` | **8.665** | equivalência textual não é evidência suficiente |
| num grupo `CONFLICTING_EVIDENCE` | 0 | — |
| ids de nomes que nem normalizados equivalem | **60** | permaneceriam de todo modo |

**604, não 11.242.** A diferença é toda a distância entre "os nomes
coincidem" e "há evidência de que é a mesma pessoa".

### 7.3 Colisões que permaneceriam

10.698 das 11.302, sendo 8.665 por falta de evidência, 1.973 por dependerem
de revisão, e 60 por serem outro problema (§8).

E as colisões de `driver_id` são **11.302 de 35.045**: as outras 23.743
(23.048 idênticas e 695 complementares) não têm conflito de piloto e não são
afetadas por consolidação nenhuma.

### 7.4 Conflitos novos em `result_drivers`

**Zero.** O teste de §5.2 mostra que dois ids de um mesmo grupo nunca
aparecem no mesmo `result_id`. Uma consolidação, portanto, não pode produzir
o mesmo piloto duas vezes num resultado.

### 7.5 Colisões com restrições únicas

Nenhuma. As restrições envolvidas:

| restrição | afetada? |
|---|---|
| `laps UNIQUE (session_id, car_id, lap_number)` | **não** — `driver_id` não participa |
| `result_drivers UNIQUE (result_id, slot)` | **não** — `driver_id` não participa |
| `drivers UNIQUE (first_name, last_name)` | não, se a consolidação apagar em vez de renomear |

**Esta é a diferença essencial em relação à consolidação de `cars`**, que
provocaria 35.045 colisões em `laps`. Consolidar `drivers` não colide com
restrição nenhuma. O obstáculo não é técnico: é a falta de evidência.

### 7.6 Linhas que exigiriam intervenção manual

| | grupos | linhas |
|---|---:|---:|
| revisão caso a caso | 376 + 7 = **383** | 802 |
| sem caminho automático concebível hoje | **651** | 1.311 |

---

## 8. Os 60 pares `Nic` / `Nicolas Minassian`

Tratados à parte, como pedido.

| | |
|---|---|
| `Nic Minassian` | id 30285, país FRA, rating Platinum, 2 vínculos |
| `Nicolas Minassian` | id 419822, sem país, **60 voltas**, 2 vínculos |
| o que a fonte escreve | `Nicolas MINASSIAN`, nas 60 voltas (PR #44, §6.2) |
| nomes normalizados | `nicminassian` ≠ `nicolasminassian` — **não formam grupo** |
| categoria | fora das cinco: não é duplicata de nome normalizado |

**O risco concreto:** o `needs_review.json` commitado traz este par com
`"action": "merge"`, e a direção da fusão apagaria **`Nicolas`** (419822,
sem país) preservando **`Nic`** (30285, com país). Isto é, o registro que
sobreviveria é o que **contradiz a grafia da fonte**, pela regra "quem tem
país é canônico".

Uma execução de `merge_drivers.py --apply-reviewed` hoje decidiria esse caso
sem revisão — e decidiria no sentido oposto ao que a única evidência
disponível sugere.

**Avaliação:** `REVIEW_REQUIRED`, e a revisão precisa decidir duas coisas,
não uma: se são a mesma pessoa, e qual grafia representa a verdade
histórica. A segunda tem evidência (a fonte diz `Nicolas`); a primeira não.

---

## 9. Estratégias de seleção do sobrevivente

Sete critérios testados sobre os 1.072 grupos consolidáveis em tese:

| critério | o que favorece |
|---|---|
| mais voltas | o registro mais usado por `laps` |
| mais referências (laps + vínculos) | o mais usado no total |
| mais vínculos em `result_drivers` | o mais usado na classificação |
| mais completo | o de mais atributos preenchidos |
| menor id | o mais antigo |
| maior id | o mais recente |
| grafia da fonte | sobrenome em caixa alta, convenção do publicador |

**Resultado: em 0 dos 1.072 grupos os sete critérios concordam.**

| categoria | grupos | sobreviventes distintos conforme o critério |
|---|---:|---|
| `SAFE_CANDIDATE` | 45 | 2 em 38 grupos, 3 em 5, 4 em 1, 5 em 1 |
| `REVIEW_REQUIRED` | 376 | 2 em 351, 3 em 22, 4 em 1, 5 em 2 |
| `POSSIBLE_HOMONYM` | 651 | 2 em 647, 3 em 3, 4 em 1 |

O exemplo de `Roman De Angelis` (§6.1) tem cinco linhas e **quatro
sobreviventes diferentes** conforme o critério:

```
mais voltas       -> 334779  'Roman DE' 'ANGELIS'
mais referências  ->  30341  'Roman'    'De Angelis'
mais completo     ->  23944  'Roman'    'Deangelis'
menor id          ->   3843  'Roman DE' 'Angelis'
grafia da fonte   -> 334779  'Roman DE' 'ANGELIS'
```

**Nenhum critério é evidentemente correto**, e a escolha tem consequência
material: muda o nome que o acervo passa a exibir, e muda de 5.841 a 12.952
o número de referências movidas.

**Observação contra a regra do menor id:** ela preserva, nos 45 grupos
seguros, o registro mais antigo — que é o criado pelas primeiras ingestões,
com a divisão de partícula do parser (`'Roman DE' 'Angelis'`). É a grafia
menos correta das cinco.

Esta seção é **proposta de decisão, não implementação**. A recomendação é
que o critério seja escolhido por categoria e registrado por escrito antes
de qualquer escrita, e que o grupo de cinco linhas de `Roman De Angelis`
sirva de caso de aceitação.

---

## 10. Riscos e invariantes

### 10.1 Riscos

| # | risco | severidade | evidência |
|---|---|---|---|
| 1 | `--apply-reviewed` fundir os 15 pares pré-aprovados, incluindo `Nic`/`Nicolas` | **alta** | `needs_review.json` commitado com `"action": "merge"` em todos |
| 2 | Fundir homônimos | **alta** | 651 grupos sem nenhuma evidência além do nome |
| 3 | `merge_drivers.py` escolher o sobrevivente por "tem país" | média | país diverge entre linhas da mesma pessoa (PR #45) |
| 4 | Prefixo de 4 letras fundir pessoas diferentes | média | é heurística de similaridade; hoje não dispara, mas é a regra |
| 5 | Perder atributos do registro apagado | média | `DELETE` sem transferir `short_name`, `license`, `hometown` |
| 6 | Commit único sem verificação posterior | média | não há conferência de contagens nem rollback por item |
| 7 | `normalize_drivers.py` aumentar a ambiguidade | média | renomear cria descasamento; onde já há vários candidatos, o carregador passa a recusar |
| 8 | Ingestão concorrente com consolidação | média | um `UPDATE ... SET driver_id` e um `INSERT` de carga podem correr juntos; nada os coordena |
| 9 | `needs_review.json` ilegível fora do Windows | baixa | gravado em cp1252, sem `encoding` explícito |
| 10 | Rollback depender de backup | **alta** | `DELETE FROM drivers` é irreversível sem restauração |

### 10.2 Invariantes a testar antes e depois

**Antes** — reproduzir e conferir contra este documento:

| invariante | valor |
|---|---:|
| `drivers` | 2.845 |
| grupos duplicados | 1.079 |
| `laps` com piloto | 1.602.049 |
| `result_drivers` | 469.457 |
| órfãos em `laps` e `result_drivers` | 0 |

**Depois** — sem exceção:

| invariante | como conferir |
|---|---|
| nenhuma referência órfã | `laps` e `result_drivers` sem `driver_id` inexistente = 0 |
| nenhuma referência perdida | `count(*)` de `laps` com piloto e de `result_drivers` **inalterados** |
| nenhum vínculo duplicado novo | `(result_id, driver_id)` repetido continua em 14 |
| `drivers` encolheu exatamente o previsto | contagem = 2.845 − (linhas fundidas) |
| restrições íntegras | as três de §7.5 |
| nenhum grupo fora do plano tocado | ids alterados ⊆ ids dos grupos aprovados |

A quarta e a sexta são as que pegam uma fusão a mais do que o aprovado.

---

## 11. Decisões humanas necessárias

1. **Se consolidar apenas os 45 `SAFE_CANDIDATE`**, aceitando que isso
   resolve 604 das 11.302 colisões, ou se esperar por evidência externa que
   amplie o conjunto.
2. **Qual critério de sobrevivente**, por categoria, sabendo que nenhum é
   evidentemente certo e que em 100 % dos grupos eles divergem.
3. **O que fazer com os atributos do registro não sobrevivente** — perder,
   transferir quando nulo no sobrevivente, ou preservar noutro lugar.
4. **Se autorizar consulta externa** (`enrich_wikidata.py` ou equivalente)
   para obter a evidência independente que falta aos 651
   `POSSIBLE_HOMONYM`. É a única via identificada para sair do impasse.
5. **O par `Nic` / `Nicolas Minassian`**, e por extensão os 15 pares
   pré-aprovados no `needs_review.json`.
6. **Se `merge_drivers.py` será corrigido ou substituído** antes de
   qualquer uso — hoje ele não enxerga 1.064 dos 1.079 grupos e aprova por
   omissão.

---

## 12. Próximos passos propostos

Nenhum está autorizado.

1. **Neutralizar o risco 1 primeiro.** Trocar o default do
   `needs_review.json` para `"skip"`, ou remover o arquivo commitado. É a
   menor mudança com o maior efeito, e independe de qualquer decisão sobre
   consolidação. **Não feito nesta PR** — ela não altera os scripts.
2. **Decidir o critério de sobrevivente** sobre os 45 `SAFE_CANDIDATE`, com
   `Roman De Angelis` como caso de aceitação.
3. **Ensaiar num ambiente descartável** restaurado de backup, conferindo os
   invariantes de §10.2 — não no banco de desenvolvimento.
4. **Só então** consolidar os 45, se aprovado, e remedir as 11.302 colisões
   para confirmar a queda de 604.
5. **Tratar os 376 `REVIEW_REQUIRED`** caso a caso, no modelo do arquivo de
   revisão — depois de o default ser seguro.
6. **Os 651 `POSSIBLE_HOMONYM` ficam onde estão** até que exista evidência
   externa. Consolidá-los hoje seria decidir por coincidência de texto.

**A conclusão que esta auditoria sustenta é que a consolidação histórica de
pilotos é tecnicamente simples e evidencialmente fraca.** Não há restrição a
violar nem referência a quebrar; o que falta é saber de quem é cada nome.

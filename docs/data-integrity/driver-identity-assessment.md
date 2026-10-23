# Driver identity assessment

Auditoria da tabela `drivers` e endurecimento preventivo da resolução de
piloto nos carregadores.

**Nenhuma fusão histórica foi executada e nenhuma está autorizada.** As
mudanças de código valem para cargas futuras; o acervo não foi tocado.

**Ambiente medido:** `openwec@127.0.0.1:5433`, banco de desenvolvimento.
**Data:** 2026-10-23. **`main` em:** `ab57901` (após a PR #44).
**Escrita no banco persistente:** nenhuma.

---

## 1. Resumo executivo

A PR #44 atribuiu as duplicatas de `drivers` a um `LIMIT 1` sem `ORDER BY`
em `get_driver_id`. **Isso estava errado, e a correção está registrada
naquele documento.** `drivers` tem `UNIQUE (first_name, last_name)`: a
consulta por igualdade exata devolve no máximo uma linha, e sempre devolveu
a mesma.

A causa real é outra, e a evidência é direta:

1. A restrição única é sobre as **cadeias cruas**. `Mark Patterson` e
   `Mark PATTERSON` são duas linhas que ela permite.
2. `normalize_drivers.py` **foi executado** sobre a tabela. A prova está nos
   dados: 116 linhas têm sobrenome composto em caixa mista
   (`Van Der Garde`, `De Vries`, `Ten Voorde`), e o carregador não consegue
   produzi-las — `split_wec_driver_name` corta no último espaço, então
   `Giedo VAN DER GARDE` vira `('Giedo VAN DER', 'GARDE')`.
3. Cargas posteriores reencontraram a grafia original da fonte, não casaram
   no `UNIQUE` contra a linha já renomeada, e `upsert_driver` inseriu uma
   segunda linha para a mesma pessoa.

Medido: **820 dos 1.079 grupos duplicados nasceram em janelas de ingestão
diferentes.**

O mesmo descasamento explica o outro sintoma: `get_driver_id` só encontrava
a grafia exata, então toda volta cujo nome na fonte não coincidisse com o
nome já normalizado na tabela ficava com `driver_id` nulo. **432.174 das
2.034.223 voltas do acervo (21 %) não têm piloto.**

O que esta PR faz: a grafia exata continua vencendo — nada que resolvia hoje
muda —, e quando ela falha, a resolução passa a aceitar **uma única** linha
cujo nome completo normalizado coincida. Vários candidatos recusam, em vez
de escolher por id. Projetado sobre o corpus: **+107.010 linhas de volta
passam a ter piloto**, 9.212 são recusadas por ambiguidade com desfecho
nomeado, e as 2.063.498 que já resolviam resolvem igual.

---

## 2. Metodologia

Consultas somente leitura sobre `drivers` (2.845 linhas — a tabela inteira
cabe em memória, e todas as agregações são de uma passagem) e leitura dos
arquivos de origem para a projeção de cobertura.

A normalização usada em toda a auditoria é `comparable` de
`row_identity.py`, a mesma que a PR #41 introduziu para equipes: dobra caixa
e pontuação, **preserva as letras**, não dobra acentos e não consulta
dicionário de apelidos. Nome e sobrenome são **concatenados antes** de
dobrar, e §4.2 explica por quê.

---

## 3. Como `drivers` é escrita e lida

| função | arquivo | papel |
|---|---|---|
| `upsert_driver` | `load_classification.py` | **única** que cria piloto durante a ingestão |
| `get_driver_id` | `load_laps.py` | **só lê**; associa o piloto à volta |
| `normalize_drivers.py` | `database/enrichment/` | reescreve nomes: partículas e caixa alta |
| `merge_drivers.py` | `database/enrichment/` | funde duplicatas; casos ambíguos vão para `needs_review.json` |
| `enrich_wikidata.py` | `database/enrichment/` | enriquece atributos |

As três de `enrichment/` são ferramentas de Fase 4, não carregadores, e
**nenhuma foi executada nesta PR**. Elas já estabelecem a política da casa:
fusões seguras automatizadas, ambíguas para revisão humana.

### 3.1 Como o nome é dividido

| parser | regra | resultado para `Giedo VAN DER GARDE` |
|---|---|---|
| `split_wec_driver_name` (classificação) | corta no último espaço | `('Giedo VAN DER', 'GARDE')` |
| `parse_driver_name` (voltas) | corta no último espaço | `('Giedo VAN DER', 'GARDE')` |
| `normalize_drivers.py` | reconhece partículas | `('Giedo', 'Van Der Garde')` |

**A fronteira entre nome e sobrenome é artefato de qual caminho escreveu a
linha.** É por isso que a comparação é sobre o nome completo concatenado.

### 3.2 Restrições e referências

```
drivers_pkey                      PRIMARY KEY (id)
drivers_first_name_last_name_key  UNIQUE (first_name, last_name)
idx_drivers_names                 btree (last_name, first_name)
```

Referenciada por duas tabelas:

| tabela | restrição | linhas que apontam para `drivers` |
|---|---|---:|
| `laps` | `laps_driver_id_fkey` | 1.602.049 (432.174 com `driver_id` nulo) |
| `result_drivers` | `result_drivers_driver_id_fkey` | 469.457 |

---

## 4. Distribuição dos grupos de identidade

| categoria | grupos | linhas | critério |
|---|---:|---:|---|
| `EXACT` | **0** | 0 | impossível: `UNIQUE (first_name, last_name)` |
| `NORMALIZED_EQUIVALENT` | **1.072** | 2.201 | mesmo nome completo após dobrar caixa e pontuação, sem atributo discriminante divergente |
| `AMBIGUOUS` | **7** | 18 | mesmo nome normalizado, mas `country` ou `imsa_driver_id` divergem |
| `POSSIBLE_ALIAS` | **127 pares** | — | mesmo sobrenome, um primeiro nome é prefixo do outro |
| `DISTINCT` | **626 nomes** | 626 | nome normalizado com uma linha só |

`1.072 + 7 = 1.079` grupos duplicados, `2.201 + 18 = 2.219` linhas neles, e
`2.219 + 626 = 2.845` — a tabela inteira.

### 4.1 `EXACT` é vazio por construção

Duas linhas não podem ter o mesmo `(first_name, last_name)`. Confirmado:

```sql
SELECT count(*) FROM (SELECT first_name, last_name FROM drivers
                      GROUP BY 1,2 HAVING count(*) > 1) x;  -- 0
```

**Esta é a razão pela qual `get_driver_id` nunca foi não-determinístico.**

### 4.2 `NORMALIZED_EQUIVALENT`, e o que sustenta a equivalência

```
id     32  'Mark'            'Patterson'        caixa
id 329356  'Mark'            'PATTERSON'

id     19  'Giedo'           'Van Der Garde'    divisão
id     20  'Giedo VAN DER'   'GARDE'
```

O segundo par não é grafia: é a mesma pessoa partida em dois pontos
diferentes. Comparar `first_name` e `last_name` separadamente chamaria um
piloto de duas pessoas; por isso a chave concatena antes de dobrar.

**Isto não é prova universal de identidade.** Dois homônimos reais teriam o
mesmo nome normalizado e seriam indistinguíveis por esta regra. A seção
seguinte mede o que a tabela oferece para separá-los — e a resposta é
desconfortável.

### 4.3 `AMBIGUOUS`: nenhum atributo distingue homônimo com segurança

Sete grupos têm `country` ou `imsa_driver_id` divergente. Examinados um a
um, **nenhum é homônimo**:

```
  4237  Custodio  TOLEDO       BRA        59721  Custodio  Toledo       USA
 16984  Devlin    DeFrancesco  CAN 1625   34676  Devlin    Defrancesco  ITA 1625
    22  Gabriel   AUBRY        CAN        23838  Gabriel   Aubry        FRA 1769
```

`Devlin DeFrancesco` tem **o mesmo `imsa_driver_id`** nas duas linhas e
países diferentes: é a mesma pessoa, com divergência de país na fonte.

E o inverso também ocorre. `Job van Uitert` e `Renger van der Zande` estão
na tabela **quatro vezes cada**, com **dois `imsa_driver_id` distintos por
pessoa**:

```
    20  Job VAN         Uitert           —      4795 voltas
 33521  Job             van Uitert       2174      0 voltas
 45616  Job             Van Uitert       3013      0 voltas
329349  Job VAN         UITERT           —      1528 voltas
```

**Conclusão medida: nem `country` nem `imsa_driver_id` servem de
desempate.** Os dois divergem entre linhas da mesma pessoa. A tabela não
contém informação que distinga homônimos, e esta PR não inventa uma.

### 4.4 `POSSIBLE_ALIAS`

127 pares em que um primeiro nome é prefixo do outro:

```
'Nicolas' Minassian   vs  'Nic'       Minassian
'Finn'    Gehrsitz    vs  'Fin'       Gehrsitz
'Frederic' MAKOWIECKI vs  'Fred'      Makowiecki
'Cian'    Carey       vs  'Cian Noel' Carey
```

**Nenhum é tratado como equivalente, nem na leitura nem na escrita.** São
listados para revisão humana — a mesma política que `merge_drivers.py` já
adota com o seu `needs_review.json`.

---

## 5. Relação com os 11.302 conflitos da PR #44

| | |
|---|---:|
| pares de voltas com `driver_id` conflitante | 11.302 |
| — mesmo nome completo normalizado | 11.242 |
| — nome diferente (`Nic` / `Nicolas Minassian`) | 60 |
| pares com lacuna de `driver_id` (`COMPLEMENTARY`) | 695 |

Os 11.242 são exatamente este defeito: duas linhas de `drivers` para uma
pessoa, cada execução do carregador referenciando a que a sua grafia
alcançava. Os 695 são o outro lado — uma execução achou a grafia, a outra
não achou nenhuma e gravou nulo.

Os 60 são um `POSSIBLE_ALIAS`, e continuam em aberto: o §6.2 da PR #44
mostra que a fonte escreve `Nicolas MINASSIAN` nas 60 voltas, o que
corrobora serem as mesmas voltas, mas não estabelece que `Nic` seja a mesma
pessoa.

---

## 6. Mudanças implementadas

### 6.1 `resolve_driver_id`, em `row_identity.py`

Mesma forma das resoluções de sessão e carro: decisão pura sobre lista de
candidatos, SQL que só carrega, desfecho nomeado.

| desfecho | quando |
|---|---|
| `EXACT_NAME` | existe linha com o `(first_name, last_name)` da fonte |
| `BY_NORMALIZED_NAME` | **uma única** linha tem o nome completo normalizado |
| `MULTIPLE_CANDIDATES` | várias têm — recusa |
| `NOT_FOUND` | nenhuma tem |
| `NO_NAME` | a fonte não trouxe nome |

A grafia exata é consultada primeiro. **Toda volta que resolvia antes
resolve do mesmo jeito**, inclusive quando as duas grafias existem na
tabela.

O índice normalizado é dobrado **uma vez por conexão**, e não por processo:
a forma normalizada não é indexável sem migration, e consultá-la por volta
faria uma varredura da tabela para cada uma das 432.174 voltas sem
correspondência exata. O §6.3 descreve o ciclo de vida.

### 6.2 `upsert_driver`, em `load_classification.py`

Devolve `(id, desfecho)`, como as demais resoluções desde a PR #41, e trata
os cinco casos:

| desfecho | o que faz |
|---|---|
| `EXACT_NAME` | o caminho de sempre: `INSERT ... ON CONFLICT DO UPDATE` enriquece a linha existente |
| `BY_NORMALIZED_NAME` | reaproveita a linha única, **sem renomeá-la nem sobrescrever seus atributos** |
| `MULTIPLE_CANDIDATES` | **não insere e não escolhe**; devolve o motivo, que o carregador conta |
| `NOT_FOUND` | cria normalmente |
| `NO_NAME` | não cria |

A primeira versão desta PR caía no `INSERT` quando o desfecho era
`MULTIPLE_CANDIDATES`, acrescentando mais uma linha a um nome que já
respondia a várias — isto é, agravando exatamente a duplicação que a PR
diagnostica. Corrigido.

**O que a recusa custa**, medido sobre os 2.114 pares (nome, sobrenome) dos
arquivos de classificação, em 88.512 ocorrências:

| desfecho | pares | ocorrências |
|---|---:|---:|
| `EXACT_NAME` | 1.935 | 87.655 |
| `BY_NORMALIZED_NAME` | 162 | 761 |
| `NOT_FOUND` — criados normalmente | 15 | 88 |
| **`MULTIPLE_CANDIDATES` — recusados** | **2** | **8** |

Os dois, e os candidatos que os tornam ambíguos:

```
fonte 'Jean Baptiste' 'SIMMENAUER'        (2 ocorrências)
   3343  'Jean-Baptiste' 'Simmenauer'
 329107  'Jean Baptiste' 'Simmenauer'
 334279  'Jean-Baptiste' 'SIMMENAUER'

fonte 'Abdulla ALI' 'AL-KHELAIFI'         (6 ocorrências)
 330226  'Abdulla Ali' 'Al-khelaifi'
 340610  'Abdulla Ali' 'AL-KHELAIFI'
```

Os dois são a mesma pessoa em três e duas grafias. Antes, o carregador
criaria a quarta e a terceira.

**O que se perde:** 8 vínculos piloto↔resultado deixam de ser criados numa
carga futura. A linha de `results` continua existindo, e os demais pilotos
do mesmo carro continuam vinculados — a classificação não se perde, só
aquele vínculo. É o preço de não agravar a duplicação, e está contado.

**O que se ganha:** os 162 pares que hoje criariam uma segunda linha para
quem já está na tabela passam a reaproveitá-la.

### 6.3 Ciclo de vida do índice

Um fold por conexão, guardado numa `WeakKeyDictionary`. É isso que limita a
sua vida, e cada limite responde a um risco concreto:

| limite | como |
|---|---|
| entre bancos | uma conexão não troca de banco, então um índice nunca responde por outro |
| entre execuções | uma execução abre a sua conexão e relê a tabela; os dois `run()` chamam `forget_driver_index` ao começar, cobrindo a execução que reaproveita uma conexão aberta |
| entre conexões | duas conexões nunca compartilham o fold, então uma não serve linhas que a outra escreveu depois |
| dentro da execução | `remember_driver` mantém visível o que esta conexão acabou de escrever |

A entrada **desaparece quando a conexão é coletada**, então nada precisa ser
invalidado à mão no caso comum — em particular, a suíte de testes não chama
mais invalidação nenhuma na sua fixture, e é um teste que verifica essa
propriedade.

A primeira versão desta PR usava um único índice global de processo, que
sobrevivia a conexões e execuções e dependia de os testes o limparem.
Corrigido.

### 6.4 Cobertura projetada

Sobre os 3.454 nomes distintos dos arquivos de volta, pesados pelas
2.569.358 linhas que os contêm:

| | nomes | linhas de volta |
|---|---:|---:|
| regra antiga — resolvia | 1.237 | 2.063.498 |
| regra antiga — ficava nulo | 2.217 | 505.860 |
| **regra nova** — `EXACT_NAME` | 1.237 | 2.063.498 |
| **regra nova** — `BY_NORMALIZED_NAME` | **257** | **+107.010** |
| **regra nova** — `MULTIPLE_CANDIDATES` | 42 | 9.212 |
| **regra nova** — `NOT_FOUND` | 1.918 | 389.638 |

**Nenhuma regressão:** as 2.063.498 que resolviam continuam resolvendo pela
mesma linha. O ganho são 107.010 linhas; as 9.212 recusadas já ficavam nulas
hoje, e passam a ficar nulas **com motivo registrado**.

> **Limite desta projeção.** Ela aplica a regra aos nomes do corpus contra a
> tabela `drivers` **como ela está hoje**. Uma reingestão real também
> criaria linhas, mudando o conjunto de candidatos. Os números são uma
> projeção, não uma previsão do que uma reingestão produziria — e
> reingestão não está autorizada.

### 6.5 Concorrência

`upsert_driver` continua terminando num `INSERT ... ON CONFLICT
(first_name, last_name) DO UPDATE`. **A restrição única serializa duas
cargas simultâneas do mesmo nome exato**, e o teste de concorrência prova
isso com duas conexões reais: as duas devolvem o mesmo id e a tabela fica
com uma linha.

O que **não** é prometido: duas cargas simultâneas trazendo **grafias
diferentes** da mesma pessoa ainda podem criar duas linhas, porque a leitura
normalizada e a inserção não são atômicas e a restrição não enxerga a forma
normalizada. Fechar essa janela exigiria um índice único sobre a expressão
normalizada — **migration, que esta PR não faz** — e §9 registra a proposta.

---

## 7. Limites do que foi implementado

1. **Homônimos não são distinguidos.** Se duas pessoas diferentes tiverem o
   mesmo nome normalizado e apenas uma estiver na tabela,
   `BY_NORMALIZED_NAME` associaria a volta à pessoa errada. §4.3 mostra que
   nenhum atributo da tabela permite evitá-lo. O risco é residual e está
   declarado, não resolvido.
2. **Abreviações continuam sem resolver.** `Nic` não encontra `Nicolas`, por
   decisão. As 9.212 linhas de volta sob `MULTIPLE_CANDIDATES` e parte das
   389.638 sob `NOT_FOUND` ficam sem piloto.
3. **Nada do acervo muda.** As 2.845 linhas, os 1.079 grupos duplicados e os
   432.174 `driver_id` nulos continuam como estão.
4. **A projeção de cobertura não é uma previsão** — ver §6.4.
5. **`normalize_drivers.py` não foi corrigido.** Ele continua podendo
   renomear linhas e recriar o descasamento com a fonte; o carregador agora
   tolera o descasamento, mas a ferramenta que o produz segue como está.

---

## 8. Riscos ainda existentes

| # | risco | severidade | situação |
|---|---|---|---|
| 1 | Homônimo associado à pessoa errada por `BY_NORMALIZED_NAME` | média | residual, declarado; nenhum atributo da tabela o evita |
| 2 | Duas grafias criadas em paralelo por cargas simultâneas | baixa | janela aberta; fecharia com índice único normalizado (migration) |
| 3 | `normalize_drivers.py` recriar o descasamento | média | não corrigido nesta PR |
| 4 | 432.174 voltas sem piloto no acervo | — | inalterado; só cargas futuras aproveitam a correção |
| 5 | Os 127 `POSSIBLE_ALIAS` seguem como pessoas separadas | baixa | decisão humana pendente |

---

## 9. Estratégias futuras de recuperação

Nenhuma está autorizada. Em ordem de evidência disponível:

1. **Consolidar os 1.072 grupos `NORMALIZED_EQUIVALENT`**, re-apontando
   `laps` e `result_drivers` para uma linha canônica. Dissolve 11.242 dos
   11.302 conflitos da PR #44 **antes** que a Fase 2 do plano precise
   arbitrá-los. `merge_drivers.py` já faz trabalho desta natureza e teria de
   ser auditado antes de qualquer uso.
2. **Rever os 7 `AMBIGUOUS` e os 127 `POSSIBLE_ALIAS` manualmente**, no
   modelo `needs_review.json` que a casa já usa.
3. **Índice único sobre a forma normalizada**, fechando o item 2 dos riscos.
   Exige migration e **não pode ser criado enquanto a tabela tiver as 1.079
   duplicatas** — depende do item 1.
4. **Reprocessar o `driver_id` das 432.174 voltas nulas** com a regra nova,
   sem reingestão completa. Projeção em §6.4; exigiria `UPDATE`, que não
   está autorizado.

A ordem importa: **3 depende de 1**, e 4 só vale a pena depois de 1, pelo
mesmo motivo que a Fase 6 do plano espera a consolidação.

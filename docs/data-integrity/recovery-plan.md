# Recovery plan

Sequência futura de recuperação do acervo histórico, suas dependências,
controles e critérios de aceite.

**Nada aqui está autorizado.** Este documento descreve o que seria feito, em
que ordem e sob que condições. Não acompanha script de limpeza, exclusão,
atualização ou reingestão, e nenhuma fase pode começar sem autorização
específica.

As medições que sustentam cada fase estão em
[historical-recovery-assessment.md](historical-recovery-assessment.md).

**Princípio.** Não modificar um registro sem conseguir demonstrar por que
aquela alteração representa a verdade histórica. Onde a demonstração não
existe, o registro fica como está e a limitação é registrada.

---

## 1. Três naturezas de trabalho, que não se misturam

O plano separa deliberadamente:

| natureza | o que é | escreve no banco? |
|---|---|---|
| **preparação** | inventariar, classificar e medir; produzir a evidência que uma regra precisaria | **não** |
| **reconciliação de colisões** | decidir, caso a caso ou por regra aprovada, o que acontece com cada par em conflito | **não** — produz a regra e o ensaio, não o efeito |
| **alteração de referências** | o `UPDATE` que move um `car_id`, a consolidação, a remoção | **sim** |

Nenhuma alteração de referência pode começar enquanto a reconciliação que
lhe corresponde não estiver **aprovada e validada**. Essa é a correção
central deste plano em relação à sua primeira versão, que colocava um
re-apontamento (as voltas das sessões contaminadas) antes de existir regra
para as 3.735 colisões que ele provoca.

---

## 2. Dependências entre as correções

```
Fase 0  backup verificado
   │
   └──► Fase 1  inventário das 35.045 colisões          [preparação]
           │
           └──► Fase 2  regra de colisão aprovada e      [reconciliação]
                        validada em ambiente descartável
                   │
                   ├──► Fase 3  re-apontar as voltas das  [alteração]
                   │            52 sessões contaminadas
                   │            (3.735 colisões)
                   │       │
                   │       └──► Fase 4  tratar as 324.720 linhas
                   │                    contaminadas
                   │
                   └──► Fase 5  reconciliar as voltas     [alteração]
                                espalhadas legítimas
                                (31.310 colisões, 655 pares)
                           │
                           └──► Fase 6  consolidar a classe A
                                   │
                                   ├──► Fase 7  classe B
                                   │
                                   └──► Fase 8  reingestão
                                           │     (exige 6 e 7)
                                           └──► Fase 9  recalcular analytics
```

Fora da linha principal, sem dependência das demais e sem decisão tomada:

```
Fase 10  normalização de teams          (classe C, 1.058 pares)
Fase 11  identidade competitiva         (classe C, 169 pares)
Fase 12  lacuna de load_metadata        (8 sessões WeatherTech)
```

### 2.1 Ordens obrigatórias, e o motivo medido de cada uma

| # | ordem | motivo |
|---|---|---|
| 1 | **Fase 2 antes das Fases 3 e 5** | as duas movem `car_id` de linhas de `laps`, e as duas esbarram nas colisões de `UNIQUE (session_id, car_id, lap_number)`: 3.735 na Fase 3 e 31.310 na Fase 5. Sem regra aprovada e validada, qualquer uma para no meio |
| 2 | **Fase 3 antes da Fase 4** | 1.566 `cars` criados pela contaminação sustentam 158.275 linhas de `laps`. Tratar as linhas de `results` antes de re-apontar as voltas as deixaria órfãs |
| 3 | **Fase 5 antes da Fase 6** | consolidar `cars` por tupla sem reconciliar as voltas faz 31.310 grupos colidirem |

As Fases 3 e 5 são **irmãs**, não sequenciais: ambas dependem só da Fase 2 e
podem ser executadas em qualquer ordem, ou isoladamente. O que nenhuma das
duas pode é começar sem a Fase 2.

---

## 3. Fases

### Fase 0 — backup verificado · *preparação*

**Pré-condições:** nenhuma.
**Elegibilidade:** todo o banco de desenvolvimento.
**Evidência exigida:** dump restaurado num ambiente descartável, com
contagens de `cars`, `results`, `laps`, `result_drivers`, `sessions` e
`analytics_*` conferidas contra a origem, e um hash do conjunto ordenado de
cada tabela grande.
**Validação posterior:** o ambiente restaurado responde às mesmas consultas
do assessment com os mesmos números.
**Critério de interrupção:** qualquer divergência de contagem ou de hash.

Sem isso nenhuma fase seguinte começa. A existência do arquivo de dump não é
validação; a restauração conferida é.

### Fase 1 — inventário das colisões · *preparação, somente leitura*

**Pré-condições:** Fase 0.
**Escopo:** os 35.045 grupos (sessão, tupla, `lap_number`) que teriam mais de
uma linha caso os `cars` de uma tupla fossem consolidados — 31.310 em cars
legítimos e 3.735 em cars de sessão contaminada. Todos têm exatamente duas
linhas.

**O que precisa ser determinado, e hoje não está:**

> **Não está estabelecido que esses pares sejam duplicatas.** O que foi
> medido é que as duas linhas compartilham sessão, identidade de carro e
> número da volta. **Se o conteúdo das duas é igual — tempo de volta,
> setores, velocidade, hora — não foi verificado.** Dois registros com o
> mesmo número de volta podem ser a mesma volta gravada duas vezes, ou
> podem ser leituras diferentes da mesma volta, ou um defeito de numeração
> na fonte. Descartar a segunda linha sem essa análise seria apagar dado
> possivelmente distinto.

**Entregável:** para cada um dos 35.045 pares, a classificação em
*idênticos em todos os campos*, *divergentes em algum campo*, ou
*indeterminados*, com as contagens e a distribuição por sessão e por série.
Uma comparação campo a campo das duas linhas, sem escrita.

**Validação:** as contagens somam 35.045 e reproduzem a divisão
31.310 / 3.735 do assessment.
**Interrupção:** qualquer par fora dos 35.045 previstos.

### Fase 2 — regra de colisão aprovada e validada · *reconciliação*

**Pré-condições:** Fase 1 concluída, com o inventário em mãos.
**Natureza:** decisão humana, seguida de ensaio. **Não escreve no banco de
desenvolvimento.**

**O que a regra precisa cobrir**, por classe saída da Fase 1:

| classe | pergunta que a regra responde |
|---|---|
| pares idênticos em todos os campos | preservar uma e descartar a outra, ou preservar as duas sob chave diferente? |
| pares divergentes | qual sobrevive, e com que justificativa? ou nenhuma, e o par fica retido? |
| pares indeterminados | reter para inspeção, ou tratar como divergentes? |

**Evidência exigida:** a regra tem de ser justificável linha a linha. Uma
regra que diga "descartar a segunda" sem dizer por que aquela segunda linha
não é dado é inaceitável pelo princípio deste plano.

**Validação:** ensaio da regra num ambiente descartável restaurado do backup
da Fase 0, com o resultado conferido contra o inventário da Fase 1 — quantos
pares cada ramo da regra alcança, e se a contagem final de `laps` é a
prevista.
**Interrupção:** qualquer par que a regra não classifique; qualquer
divergência entre o ensaio e a previsão.

### Fase 3 — re-apontar as voltas das 52 sessões contaminadas · *alteração*

**Pré-condições:** Fases 0, 1 e **2 aprovada e validada**.
**Elegibilidade:** as 158.275 linhas de `laps` cujo `car_id` está entre os
1.566 `cars` criados pela contaminação.
**Operação:** para cada par (sessão, tupla), eleger um `car_id` canônico e
re-apontar as voltas para ele. São **1.534** entradas competitivas com
voltas. As outras 131 das 1.665 entradas existem apenas em `results` e não
têm volta nenhuma para re-apontar — ver §7 do
[lap-collision-assessment.md](lap-collision-assessment.md).
**Evidência:** todos os candidatos de um par compartilham as oito colunas de
identidade — são o mesmo carro pela regra do próprio carregador.
**Colisões:** 3.735 grupos, tratados pela regra da Fase 2. **Nenhuma linha é
movida antes disso.**
**Idempotência:** re-executar não encontra nada a mover, porque o `car_id`
canônico de um par é função da tupla, não da ordem de execução.
**Validação:** `count(*)` de `laps` igual ao anterior, menos exatamente o
que a regra da Fase 2 previa descartar; nenhuma volta sem `car_id`;
`count(DISTINCT car_id)` nessas sessões igual a **1.534**.
**Interrupção:** qualquer volta que ficaria órfã; qualquer colisão fora dos
3.735 inventariados.

### Fase 4 — tratar os resultados contaminados · *alteração*

**Pré-condições:** Fases 0 e 3, e **a decisão 1 do §9**.
**Elegibilidade:** as 324.720 linhas de `results` das 51 sessões, e os
324.720 `cars` correspondentes.
**Evidência:** duas independentes, §3.1 do assessment. Além disso, as linhas
não carregam nenhum valor: seis campos nulos em 100 % delas.
**Identificação:** sempre pela linha de `results` nas 52 sessões. **Nunca
pela tupla** — 42 tuplas aparecem também entre os `cars` legítimos.

**Três tratamentos possíveis, nenhum escolhido:**

| tratamento | o que exige |
|---|---|
| **remover** | decisão 1 aprovada; nada mais, tecnicamente |
| **marcar** | coluna nova em `results` e `cars` — mudança de schema, fora do escopo atual |
| **preservar intacto** | nada; é o estado de hoje, e os relatórios passam a excluir as 52 sessões por predicado |

**O que se perde, se o tratamento for remover.** Esta avaliação **mudou**.
Em 2026-10-22 ela era: "nada que a fonte ainda tenha. O conteúdo de
classificação nunca existiu para essas sessões." `MEASURED` (PRs #48–#50): o
conteúdo **existe publicado e responde**. Removê-las hoje apagaria registros
cuja substituição está demonstrada como disponível para 49 das 52 entradas,
**antes** de qualquer tentativa de obtê-la. Isso continua sendo uma
constatação sobre conteúdo, **não uma autorização** — em nenhuma das duas
direções.

**Idempotência:** o conjunto é definido por predicado, não por lista de ids.
**Validação:** ver §6.2 — as 52 sessões têm invariantes próprios, e a queda
de `results` para 346.633 é o resultado esperado do tratamento "remover",
não uma violação.
**Interrupção:** qualquer linha do conjunto com campo não nulo; qualquer
`car` do conjunto ainda referenciado por `laps` ou `analytics`.

### Fase 5 — reconciliar as voltas espalhadas legítimas · *alteração*

**Pré-condições:** Fases 0, 1 e **2 aprovada e validada**.
**Elegibilidade:** 655 pares (sessão, tupla) com voltas em mais de um
`car_id` entre os cars legítimos, e os 31.310 grupos que colidiriam.
**Evidência:** os `car_id` de um par compartilham as oito colunas.
**Colisões:** 31.310 grupos, tratados pela regra da Fase 2.
**Validação:** nenhum par legítimo com voltas em mais de um `car_id`; total
de `laps` igual ao anterior menos exatamente o que a regra previa descartar.
**Interrupção:** qualquer colisão fora dos 31.310 inventariados.

### Fase 6 — consolidar a classe A · *alteração*

**Pré-condições:** Fases 0 e 5.
**Elegibilidade:** 83.595 grupos (sessão, tupla) uniformes nas oito colunas,
em sessões legítimas.
**Evidência:** por serem uniformes, manter uma linha não perde nenhum valor.
É a única classe em que a consolidação é demonstrável sem decisão nova.
**Preservação de ids:** sobrevive o menor `id` de cada grupo — representante
canônico entre linhas comprovadamente idênticas, estável entre execuções, e a
mesma regra que `row_identity.choose_car` já usa em tempo de carga.
**Relacionamentos:** `result_drivers`, `laps` e `analytics_*` precisam ser
re-apontados para o sobrevivente antes de qualquer remoção. Nenhum `cars`
legítimo é órfão e cada um tem exatamente uma linha de `results` (§4.0 do
assessment), então consolidar `cars` obriga a consolidar `results` junto.
**Idempotência:** o grupo é definido pela tupla; re-executar não acha nada.
**Validação:** nenhum par (sessão, tupla) com mais de uma linha de `results`
nas sessões legítimas; `result_drivers` sem referência pendente; contagem de
entradas lógicas legítimas inalterada em 94.438.
**Interrupção:** qualquer grupo que deixe de ser uniforme na releitura.

### Fase 7 — classe B · *alteração*

**Pré-condições:** Fase 6 e **regra aprovada** (decisão 3 do §9).
**Elegibilidade:** 156 grupos não uniformes.
**Evidência disponível:** tomando apenas a linha mais recente de cada
entrada, as divergências contra a fonte caem a zero nos oito campos. Isso
sustenta a regra "preservar a mais recente", mas **é uma regra de escolha e
precisa de aprovação explícita**.
**Interrupção:** qualquer grupo em que a linha mais recente divirja da fonte.

### Fase 8 — reingestão dos campos não lidos · *alteração*

**Pré-condições:** Fases 6 e 7.
**Elegibilidade:** as sessões legítimas, com os carregadores de `0ec4574`.
**O que recupera:** 312.144 valores — `position` 79.136, `fl_lap_number`
79.526, `fl_time_s` 75.235, `fl_kph` 75.235, `laps_completed` 3.012.
**Por que só agora:** sobre o acervo atual, `ON CONFLICT ... DO UPDATE`
escreveria numa linha arbitrária de cada grupo.
**Idempotência:** garantida pelas PRs #39 e #41 — `upsert_car` procura antes
de inserir, e `insert_result` atualiza as 14 colunas.
**Validação:** nenhuma linha nova em `cars` ou `results`; os 312.144 valores
presentes; nenhuma divergência contra a fonte na comparação do §7 do
assessment.
**Interrupção:** qualquer crescimento de `cars` ou `results`.

### Fase 9 — recalcular as derivadas · *alteração*

**Pré-condições:** Fase 8. `analytics_car_session` e `analytics_stints` são
derivadas e ficam inconsistentes depois de qualquer consolidação.

### Fases 10 a 12 — sem decisão tomada

| fase | escopo | o que falta |
|---|---|---|
| 10 | normalizar `teams` nos 1.058 pares de grafia | decisão sobre dado mestre, com efeito em todo o acervo |
| 11 | identidade competitiva dos 169 pares | critério externo; nada legível resolve |
| 12 | 8 sessões WeatherTech ausentes | corrigir `load_metadata`, fora do escopo das PRs recentes |

---

## 4. Critérios de elegibilidade, resumidos

Um registro só entra numa fase quando satisfaz **todos** os critérios da
fase. Nenhum critério é "não encontramos problema nele".

| fase | natureza | predicado de elegibilidade |
|---|---|---|
| 1 | preparação | grupo (sessão, tupla, `lap_number`) com mais de uma linha sob consolidação por tupla |
| 2 | reconciliação | — (decide sobre o inventário da Fase 1, não sobre registros) |
| 3 | alteração | `laps.car_id` entre os `cars` referenciados por `results` das 52 sessões |
| 4 | alteração | `results.session_id` nas 52 sessões **e** todos os seis campos nulos |
| 5 | alteração | par (sessão, tupla) legítimo com `count(DISTINCT car_id) > 1` em `laps` |
| 6 | alteração | grupo (sessão, tupla) legítimo com `n > 1` e uniforme nas oito colunas |
| 7 | alteração | grupo (sessão, tupla) legítimo com `n > 1` e não uniforme |
| 8 | alteração | sessão legítima, resolvida sem ambiguidade nem contradição |

---

## 5. Estratégia de backup e restauração

1. Dump antes de cada fase que escreve, não um só no início.
2. Cada dump nomeia a fase que vai começar e o commit de `main` vigente.
3. Restauração conferida em ambiente descartável, com as contagens do §2.1 do
   assessment e hash do conjunto ordenado das tabelas grandes.
4. Ponto de retorno declarado por escrito antes de começar: qual dump, qual
   procedimento, quanto tempo.
5. O ensaio da Fase 2 usa esse mesmo ambiente descartável. Nenhuma regra
   chega ao banco de desenvolvimento sem ter sido executada ali primeiro.
6. O gate de escrita dos testes (`OPENWEC_TEST_DB_IS_DISPOSABLE` mais o teto
   de linhas de `tests/loader/sandbox.py`) permanece ativo. Nenhuma fase o
   remove.

---

## 6. Validação antes e depois de cada etapa

**Antes:** reproduzir as contagens da fase e confirmar que batem com o
assessment. Divergência é motivo de parada — significa que o acervo mudou
desde a medição.

**Depois:** cada fase tem a sua validação específica, acima. Além delas,
valem os invariantes abaixo — **separados por população, porque as duas não
obedecem aos mesmos critérios.**

### 6.1 Invariantes das sessões legítimas

Valem sempre, em todas as fases, para as **2.851 sessões legítimas**:

| invariante | como conferir | valor |
|---|---|---|
| nenhuma entrada lógica perdida | `count(DISTINCT (session_id, tupla))` restrito às sessões legítimas | **94.438**, antes e depois |
| nenhuma volta órfã | `laps` sem `car_id` correspondente em `cars` | 0 |
| nenhuma restrição única violada | as quatro de `results`, `laps`, `result_drivers` e `analytics_car_session` | íntegras |

> **A preservação das 94.438 entradas lógicas é um invariante das sessões
> legítimas, não do banco inteiro.** Aplicá-lo globalmente faria qualquer
> tratamento das 52 sessões contaminadas parecer uma violação, o que ele não
> é: as 1.665 entradas daquelas sessões nunca foram classificação.

### 6.2 Critérios próprios das 52 sessões contaminadas

As 52 sessões identificadas no §3.1 do assessment têm critérios distintos,
porque o que se espera delas depende do tratamento escolhido na decisão 1:

| tratamento | o que se espera depois | o que **não** é violação |
|---|---|---|
| **preservar intacto** | 324.720 linhas de `results`, 1.665 entradas, nada muda | — |
| **marcar** | as mesmas 324.720 linhas, com a marca; contagens inalteradas | — |
| **remover** | 0 linhas de `results` nessas sessões; `results` global em 346.633 | a queda de 1.665 entradas lógicas nessas sessões, que é o efeito pretendido |

Em **todos** os tratamentos, dois invariantes valem sem exceção:

| invariante | valor |
|---|---|
| `laps` dessas sessões preservadas e apontando para `cars` existentes | 158.275 |
| entradas competitivas representadas em `laps` | **1.534** |
| entradas que existem só em `results`, fora do alcance da Fase 3 | 131 |

> A remoção de registros contaminados **não é violação automática** do
> invariante global — ela tem população e critérios próprios. E o fato de
> existirem critérios para avaliá-la **não é autorização implícita para
> executá-la**: a decisão 1 continua pendente, e sem ela o tratamento vigente
> é "preservar intacto".

---

## 7. Idempotência

Toda fase é definida por **predicado sobre o estado**, nunca por lista de ids
colhida antes. Re-executar uma fase concluída não encontra registro elegível
e não escreve nada. Uma fase interrompida no meio pode ser retomada sem
desfazer o que já fez.

O representante canônico de um grupo é o menor `id`, que é função do conteúdo
e não da ordem de execução. É a mesma regra que `row_identity.choose_car`
aplica em tempo de carga, então carga e recuperação elegem o mesmo registro.

---

## 8. Critérios objetivos de interrupção

Parar imediatamente, sem tentar contornar, quando:

1. Uma contagem de validação prévia diverge do assessment.
2. Uma restrição única é violada, ou seria.
3. Uma linha elegível se revela fora do predicado na releitura.
4. Uma volta ficaria órfã.
5. O número de registros afetados diverge do previsto em mais de 0 %. Não há
   margem: os conjuntos são definidos por predicado e contados de antemão.
6. Uma operação precisaria de uma regra que não está escrita e aprovada.
7. O backup da fase não foi restaurado e conferido.
8. Uma alteração de referências começaria sem que a reconciliação de colisões
   correspondente estivesse aprovada **e** validada em ambiente descartável.

---

## 9. Automatizável contra decisão humana

| automatizável, com evidência suficiente | exige decisão humana |
|---|---|
| identificar as 324.720 linhas contaminadas | se remover, marcar ou preservar |
| inventariar e comparar os 35.045 pares em colisão | o que fazer com cada classe de par |
| eleger o `car_id` canônico de um par (sessão, tupla) | — |
| re-apontar `laps`, `result_drivers` e `analytics_*` | — (depois da regra aprovada) |
| consolidar os 83.595 grupos da classe A | — |
| reingerir os campos não lidos | quando, sobre quais séries, com qual janela |
| recalcular as derivadas | — |
| detectar os 1.058 pares de grafia | se normalizar `teams` |
| detectar os 169 pares da classe C | qual identidade competitiva cada um é |
| detectar as 8 sessões ausentes | corrigir `load_metadata` |
| — | a semântica de `status = 'Other'` em 630.869 linhas |

**Cinco decisões bloqueiam o início:**

1. O destino das 324.720 linhas: remover, marcar ou preservar.
2. ~~Se as 51 sessões ficam sem classificação ou se ela deve ser derivada do
   arquivo de análise — derivar é construir dado novo, não recuperar.~~

   **Reformulada pelas PRs #48–#50.** A alternativa não é mais "ficar sem"
   contra "derivar da análise": `MEASURED`, a classificação **existe
   publicada** para as 51, e 49 das 52 entradas de catálogo têm finalidade
   corroborada. A decisão passou a ser **obter a fonte ou não**, e sob quais
   pré-condições — ver
   [evidence-reconciliation.md](evidence-reconciliation.md).
3. A regra de sobrevivência da classe B.
4. **O que acontece com cada classe dos 35.045 pares em colisão**, depois que
   a Fase 1 disser quantos são idênticos, quantos divergem e quantos ficam
   indeterminados. Esta decisão bloqueia as Fases 3 e 5, isto é, **toda
   alteração de referência prevista no plano**.
5. Se `teams` será normalizado.

---

## 10. Critérios de aceite da recuperação

A recuperação está concluída quando, e só quando:

| # | critério | valor esperado |
|---|---|---|
| 1 | nenhum par (sessão, tupla) legítimo com mais de uma linha em `results` | 0 |
| 2 | nenhum par (sessão, tupla) com voltas em mais de um `car_id` | 0 |
| 3 | `laps` sem perda não justificada | 2.034.223 menos exatamente o que a regra da Fase 2 previu descartar |
| 4 | entradas lógicas legítimas preservadas | 94.438 |
| 5 | entradas representadas nas voltas das 52 sessões | **1.534**, qualquer que seja o tratamento |
| 6 | valores recuperados presentes | 312.144 |
| 7 | restrições únicas íntegras | as quatro |
| 8 | derivadas recalculadas | `analytics_*` consistentes com `laps` |
| 9 | cada alteração justificada por evidência registrada | sem exceção |

O critério 9 não é formalidade. Um registro alterado sem evidência escrita de
por que aquela alteração representa a verdade histórica é um defeito novo,
não uma recuperação.

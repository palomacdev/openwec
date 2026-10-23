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

## 1. Dependências entre as correções

```
Fase 0  backup verificado
   │
   ├──► Fase 1  re-apontar as voltas das 52 sessões contaminadas
   │       │
   │       └──► Fase 2  remover as 324.720 linhas de results e seus cars
   │
   └──► Fase 3  reconciliar laps espalhadas por car_id
           │
           └──► Fase 4  consolidar a classe A
                   │
                   ├──► Fase 5  classe B, sob regra aprovada
                   │
                   └──► Fase 6  reingestão dos campos não lidos
                           │
                           └──► Fase 7  recalcular analytics
```

Fora da linha principal, sem dependência das demais e sem decisão tomada:

```
Fase 8  normalização de teams          (classe C, 1.058 pares)
Fase 9  identidade competitiva         (classe C, 169 pares)
Fase 10 lacuna de load_metadata        (8 sessões WeatherTech)
```

Duas ordens são obrigatórias, e por motivos medidos:

- **Fase 1 antes da Fase 2.** 1.566 `cars` contaminados sustentam 158.275
  linhas de `laps`. Removê-los antes de re-apontar órfã as voltas.
- **Fase 3 antes da Fase 4.** 35.045 grupos de `laps` colidiriam em
  `UNIQUE (session_id, car_id, lap_number)` numa consolidação por tupla, e a
  operação pararia no meio.

---

## 2. Fases

### Fase 0 — backup verificado

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

### Fase 1 — re-apontar as voltas das 52 sessões contaminadas

**Pré-condições:** Fase 0.
**Elegibilidade:** as 158.275 linhas de `laps` cujo `car_id` está entre os
1.566 `cars` criados pela contaminação.
**Operação:** para cada par (sessão, tupla), eleger um `car_id` canônico e
re-apontar as voltas para ele. São 1.665 entradas competitivas reais.
**Evidência:** todos os candidatos de um par compartilham as oito colunas de
identidade — são o mesmo carro pela regra do próprio carregador.
**Conflitos:** 3.735 grupos de duas linhas colidiriam em
`UNIQUE (session_id, car_id, lap_number)`. Precisam de regra antes.
**Idempotência:** re-executar não encontra nada a mover, porque o `car_id`
canônico de um par é função da tupla, não da ordem de execução.
**Validação:** `count(*)` de `laps` inalterado; nenhuma volta sem `car_id`;
`count(DISTINCT car_id)` nessas sessões igual a 1.665.
**Interrupção:** qualquer volta que ficaria órfã, ou colisão sem regra.

### Fase 2 — remover os resultados contaminados

**Pré-condições:** Fases 0 e 1.
**Elegibilidade:** as 324.720 linhas de `results` das 51 sessões, e os
324.720 `cars` correspondentes.
**Evidência:** duas independentes, §3.1 do assessment. Além disso, as linhas
não carregam nenhum valor: seis campos nulos em 100 % delas.
**Identificação:** sempre pela linha de `results` nas 52 sessões. **Nunca
pela tupla** — 42 tuplas aparecem também entre os `cars` legítimos.
**O que se perde:** nada que a fonte ainda tenha. O conteúdo de classificação
nunca existiu para essas sessões, e as voltas permanecem em `laps`.
**Idempotência:** o conjunto é definido por predicado, não por lista de ids.
**Validação:** `results` cai para 346.633; `cars` cai para 346.633; `laps`
inalterado em 2.034.223; `result_drivers` inalterado.
**Interrupção:** qualquer linha do conjunto com campo não nulo; qualquer
`car` do conjunto ainda referenciado por `laps` ou `analytics`.

> **Decisão humana pendente.** A remoção depende da decisão 1 do §7 do
> diagnóstico operacional: o destino dessas 324.720 linhas. Marcá-las em vez
> de removê-las é alternativa viável e exige coluna nova — mudança de schema
> fora do escopo atual.

### Fase 3 — reconciliar as voltas espalhadas

**Pré-condições:** Fase 0.
**Elegibilidade:** 687 pares (sessão, tupla) com voltas em mais de um
`car_id`, e os 35.045 grupos que colidiriam.
**Evidência:** os `car_id` de um par compartilham as oito colunas.
**Conflitos:** todos os 35.045 grupos têm exatamente duas linhas. A regra
precisa dizer o que acontece com a segunda — preservar, descartar como
duplicata comprovada, ou reter para inspeção — e essa é decisão humana.
**Validação:** nenhum par com voltas em mais de um `car_id`; total de `laps`
igual ao anterior menos exatamente o número de duplicatas descartadas, se a
regra descartar.
**Interrupção:** qualquer colisão fora dos 35.045 previstos.

### Fase 4 — consolidar a classe A

**Pré-condições:** Fases 0 e 3.
**Elegibilidade:** 83.595 grupos (sessão, tupla) uniformes nas oito colunas,
em sessões legítimas.
**Evidência:** por serem uniformes, manter uma linha não perde nenhum valor.
É a única classe em que a consolidação é demonstrável sem decisão nova.
**Preservação de ids:** sobrevive o menor `id` de cada grupo — representante
canônico entre linhas comprovadamente idênticas, estável entre execuções, e a
mesma regra que `row_identity.choose_car` já usa em tempo de carga.
**Relacionamentos:** `result_drivers`, `laps` e `analytics_*` precisam ser
re-apontados para o sobrevivente antes de qualquer remoção.
**Idempotência:** o grupo é definido pela tupla; re-executar não acha nada.
**Validação:** nenhum par (sessão, tupla) com mais de uma linha de `results`;
`result_drivers` sem referência pendente; contagem de entradas lógicas
inalterada em 94.438.
**Interrupção:** qualquer grupo que deixe de ser uniforme na releitura.

### Fase 5 — classe B

**Pré-condições:** Fase 4 e **regra aprovada**.
**Elegibilidade:** 156 grupos não uniformes.
**Evidência disponível:** tomando apenas a linha mais recente de cada
entrada, as divergências contra a fonte caem a zero nos oito campos. Isso
sustenta a regra "preservar a mais recente", mas **é uma regra de escolha e
precisa de aprovação explícita**.
**Interrupção:** qualquer grupo em que a linha mais recente divirja da fonte.

### Fase 6 — reingestão dos campos não lidos

**Pré-condições:** Fases 4 e 5.
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

### Fase 7 — recalcular as derivadas

**Pré-condições:** Fase 6. `analytics_car_session` e `analytics_stints` são
derivadas e ficam inconsistentes depois de qualquer consolidação.

### Fases 8 a 10 — sem decisão tomada

| fase | escopo | o que falta |
|---|---|---|
| 8 | normalizar `teams` nos 1.058 pares de grafia | decisão sobre dado mestre, com efeito em todo o acervo |
| 9 | identidade competitiva dos 169 pares | critério externo; nada legível resolve |
| 10 | 8 sessões WeatherTech ausentes | corrigir `load_metadata`, fora do escopo das PRs recentes |

---

## 3. Critérios de elegibilidade, resumidos

Um registro só entra numa fase quando satisfaz **todos** os critérios da
fase. Nenhum critério é "não encontramos problema nele".

| fase | predicado de elegibilidade |
|---|---|
| 1 | `laps.car_id` entre os `cars` referenciados por `results` das 52 sessões |
| 2 | `results.session_id` nas 52 sessões **e** todos os seis campos nulos |
| 3 | par (sessão, tupla) com `count(DISTINCT car_id) > 1` em `laps` |
| 4 | grupo (sessão, tupla) com `n > 1` e uniforme nas oito colunas |
| 5 | grupo (sessão, tupla) com `n > 1` e não uniforme |
| 6 | sessão legítima, resolvida sem ambiguidade nem contradição |

---

## 4. Estratégia de backup e restauração

1. Dump antes de cada fase que escreve, não um só no início.
2. Cada dump nomeia a fase que vai começar e o commit de `main` vigente.
3. Restauração conferida em ambiente descartável, com as contagens do §2.1 do
   assessment e hash do conjunto ordenado das tabelas grandes.
4. Ponto de retorno declarado por escrito antes de começar: qual dump, qual
   procedimento, quanto tempo.
5. O gate de escrita dos testes (`OPENWEC_TEST_DB_IS_DISPOSABLE` mais o teto
   de linhas de `tests/loader/sandbox.py`) permanece ativo. Nenhuma fase o
   remove.

---

## 5. Validação antes e depois de cada etapa

**Antes:** reproduzir as contagens da fase e confirmar que batem com o
assessment. Divergência é motivo de parada — significa que o acervo mudou
desde a medição.

**Depois:** além da validação específica de cada fase, três invariantes
valem para todas:

| invariante | como conferir |
|---|---|
| nenhuma volta órfã | `laps` sem `car_id` correspondente em `cars` = 0 |
| nenhuma restrição única violada | as quatro de `results`, `laps`, `result_drivers` e `analytics_car_session` íntegras |
| nenhuma entrada lógica perdida | `count(DISTINCT (session_id, tupla))` igual antes e depois |

---

## 6. Idempotência

Toda fase é definida por **predicado sobre o estado**, nunca por lista de ids
colhida antes. Re-executar uma fase concluída não encontra registro elegível
e não escreve nada. Uma fase interrompida no meio pode ser retomada sem
desfazer o que já fez.

O representante canônico de um grupo é o menor `id`, que é função do conteúdo
e não da ordem de execução. É a mesma regra que `row_identity.choose_car`
aplica em tempo de carga, então carga e recuperação elegem o mesmo registro.

---

## 7. Critérios objetivos de interrupção

Parar imediatamente, sem tentar contornar, quando:

1. Uma contagem de validação prévia diverge do assessment.
2. Uma restrição única é violada, ou seria.
3. Uma linha elegível se revela fora do predicado na releitura.
4. Uma volta ficaria órfã.
5. O número de registros afetados diverge do previsto em mais de 0 %. Não há
   margem: os conjuntos são definidos por predicado e contados de antemão.
6. Uma operação precisaria de uma regra que não está escrita e aprovada.
7. O backup da fase não foi restaurado e conferido.

---

## 8. Automatizável contra decisão humana

| automatizável, com evidência suficiente | exige decisão humana |
|---|---|
| identificar as 324.720 linhas contaminadas | se remover ou marcar |
| eleger o `car_id` canônico de um par (sessão, tupla) | — |
| re-apontar `laps`, `result_drivers` e `analytics_*` | o que fazer com as 35.045 colisões |
| consolidar os 83.595 grupos da classe A | — |
| reingerir os campos não lidos | quando, sobre quais séries, com qual janela |
| recalcular as derivadas | — |
| detectar os 1.058 pares de grafia | se normalizar `teams` |
| detectar os 169 pares da classe C | qual identidade competitiva cada um é |
| detectar as 8 sessões ausentes | corrigir `load_metadata` |
| — | a semântica de `status = 'Other'` em 630.869 linhas |

**Cinco decisões bloqueiam o início:**

1. O destino das 324.720 linhas: remover, marcar ou preservar.
2. Se as 51 sessões ficam sem classificação ou se ela deve ser derivada do
   arquivo de análise — derivar é construir dado novo, não recuperar.
3. A regra de sobrevivência da classe B.
4. O que acontece com a segunda linha de cada um dos 35.045 grupos de voltas.
5. Se `teams` será normalizado.

---

## 9. Critérios de aceite da recuperação

A recuperação está concluída quando, e só quando:

| # | critério | valor esperado |
|---|---|---|
| 1 | nenhum par (sessão, tupla) com mais de uma linha em `results` | 0 |
| 2 | nenhum par (sessão, tupla) com voltas em mais de um `car_id` | 0 |
| 3 | `laps` sem perda não justificada | 2.034.223 menos as duplicatas descartadas pela regra da Fase 3 |
| 4 | entradas lógicas preservadas | 94.438 nas sessões legítimas |
| 5 | valores recuperados presentes | 312.144 |
| 6 | restrições únicas íntegras | as quatro |
| 7 | derivadas recalculadas | `analytics_*` consistentes com `laps` |
| 8 | cada alteração justificada por evidência registrada | sem exceção |

O critério 8 não é formalidade. Um registro alterado sem evidência escrita de
por que aquela alteração representa a verdade histórica é um defeito novo,
não uma recuperação.

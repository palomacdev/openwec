# Elegibilidade das sessões contaminadas para recuperação

`MEASURED` em 2026-10-23, somente leitura sobre o banco histórico. A pergunta
é operacional: **quantas das 50 sessões pendentes o código que já temos
recuperaria?**

**Resposta: 30 das 50.** Dessas, **24** têm também a contagem de voltas
batendo com o log em todos os carros, que é o critério extra para a próxima
rodada de ensaios.

---

## 1. Como a resposta é obtida

Nada aqui decide elegibilidade por conta própria. O veredito vem de rodar
`plan_recovery` e `assert_complete_match` — as duas funções que o recuperador
executa antes de escrever — e relatar o que elas dizem.

| veredito | significado |
|---|---|
| `ELIGIBLE` | as duas passam: `recover_session` escreveria |
| `BLOCKED` | uma delas recusa, com a mesma mensagem |
| `SOURCE_UNAVAILABLE` | o catálogo não oferece classificação legível |

`DERIVED`: contagens iguais **não** bastam, e o escopo pede essa distinção.
Os números têm de ser os mesmos números, cada um carregado por um único
`car_id`. Um teste fixa o caso: dois lados de 2 carros sem nenhum número em
comum é `BLOCKED`.

A população vem do critério da PR #43 §3.1, aplicado ao banco: sessões cujo
maior grupo (sessão, tupla de carro) tem **26 linhas ou mais**. As duas
populações são disjuntas — fora da lista o maior grupo é 7. `MEASURED`: 51
sessões, das quais 2226 já foi recuperada na PR #53 e entra aqui **como
controle**, não como pendente.

A conexão é aberta com `default_transaction_read_only=on`: uma escrita
falharia em vez de passar despercebida.

---

## 2. O resultado

| | sessões |
|---|---:|
| **`ELIGIBLE`** | **30** |
| `BLOCKED` | 20 |
| `SOURCE_UNAVAILABLE` | **0** |
| *(controle 2226, já recuperada)* | *1* |
| **total** | **51** |

`MEASURED`: **toda** sessão contaminada tem fonte de classificação legível no
catálogo — 50 na família `05_Classification*`, 1 em `03_Results`. Nenhuma
ficou sem fonte.

### 2.1 Bloqueadas, por motivo

| motivo | sessões | o que é |
|---|---:|---|
| **pareamento incompleto** | **10** | a fonte publica um carro que o log de voltas não conhece |
| **número em mais de um `car_id`** | **7** | o mesmo número aparece em dois ou mais carros nas voltas |
| **sem voltas no banco** | **3** | a sessão não tem volta nenhuma para parear |
| número repetido na fonte | 0 | — |
| linha da fonte sem `NUMBER` | 0 | — |

---

## 3. As 10 incompletas têm todas a mesma causa

`MEASURED`: nas 10, `laps_without_source` está **vazio** — todo carro com
voltas está publicado. O que bloqueia é o outro lado: **9 sessões por um
único carro**, 1 por dois.

E os 11 carros têm todos o mesmo perfil:

| sessão | carro | posição | `LAPS` | `STATUS` |
|---|---|---:|---:|---|
| 999 | #47 | 23 | **0** | Not classified |
| 1011 | #60 | 25 | **0** | Not started |
| 1025 | #71 | 38 | **0** | Not started |
| 1048 | #41 | 37 | **0** | Not classified |
| 1067 | #62 | 31 | **0** | Not classified |
| 1071 | #44 | 30 | **0** | Not classified |
| 1120 | #33 | 39 | **0** | Not classified |
| 2183 | #43 | 20 | **0** | Not classified |
| 2204 | #5, #98 | 18, 19 | **0** | Not classified |
| 2210 | #33 | 18 | **0** | Not classified |

`DERIVED`: **não é contradição entre as fontes.** Um carro que não completou
volta nenhuma não pode aparecer num log de voltas; a classificação o lista
porque ele foi inscrito. A regra de completude trata as duas situações como
uma só.

`PROPOSED`: um carro publicado com `LAPS = 0` e status de não-largada ou
não-classificação poderia dispensar volta correspondente, e as 10 sessões
passariam a elegíveis — **30 → 40**. É uma mudança no recuperador, com a sua
própria decisão, e **não foi implementada aqui**.

---

## 4. As 7 com número em mais de um `car_id`

`MEASURED`: a dispersão vai de 1 número a 12.

| sessões | números afetados |
|---:|---|
| 2 | 1 número |
| 2 | 2 números |
| 1 | 4 números |
| 1 | 10 números |
| 1 | 12 números |

`DERIVED`: é a classe A4/C1 das avaliações anteriores — as voltas de um
número penduradas em `car_id` diferentes. O recuperador recusa por projeto:
escolher um dos `car_id` atribuiria metade das voltas ao resultado de outra
inscrição. Resolver isso é consolidação de identidade, não recuperação.

---

## 5. As 3 sem voltas

| sessão | o que se mediu |
|---|---|
| **2189**, **2190** — LMC 2017, Le Mans, Road to Le Mans `Race 1` e `Race 2` | `MEASURED`: 1.104 e 1.048 linhas de `results`, **0 voltas**. O arquivo de análise existe em `raw/.../Race 1/other/Race/23_Analysis_Race 1.CSV`, sob um nível a mais do que o habitual. `UNKNOWN`: por que o carregador de voltas não produziu nada; não investigado aqui |
| **2721** — IMSA 2016, Watkins Glen | `MEASURED`: 13.488 linhas de `results`, **0 voltas**, porque `load_laps.RAW_DIRS` não inclui IMSA. Ver §6 |

---

## 6. IMSA Watkins Glen: a sessão que guarda as linhas não é a da fonte

Uma precisão sobre a PR #50 §7.3, com a medição daquela PR preservada.

`MEASURED`, no banco:

| | |
|---|---|
| sessão **2721** | `raw_id` `201607021225_Race`, `source_url` do **Continental Tire** |
| suas linhas de `results` | 13.488, com **39** números de carro distintos |
| interseção com `05_Results by Hour.CSV` do **WeatherTech** | **39 de 39** |
| interseção com `03_Results.CSV` do **Continental Tire** | **10 de 36** |
| sessão **2725** — a corrida WeatherTech | **0 `results`, 0 voltas** |
| `raw/imsa/16_2016/10_Watkins Glen International/` | contém **apenas** a pasta `01_IMSA WeatherTech`, com `Race/other/Hour 6/23_Analysis_Race.CSV` |

`DERIVED`: o **arquivo** que contaminou é o do WeatherTech — como a PR #50
mediu, e os 39/39 continuam valendo. A **linha de sessão** que recebeu essas
linhas é a do Continental Tire, porque as duas se chamam `Race` dentro do
mesmo evento e a resolução anterior à PR #41 escolhia por
`ORDER BY id LIMIT 1`. A sessão WeatherTech ficou vazia.

`DERIVED`: 2721 **não é recuperável** por reposição de classificação.
Recuperá-la com a fonte do WeatherTech gravaria resultados de um campeonato
na sessão de outro; com a do Continental Tire, gravaria 36 carros que não têm
volta nenhuma no banco. O que falta é reatribuição de sessão, que é outro
problema.

---

## 7. Monza 2018: o veredito revisto

`MEASURED`, sessão 2235, contra as 1.517 voltas que o banco guarda para 27
carros:

| | `Hour 1` | `Hour 2` |
|---|---|---|
| líder | #3, **59** voltas, `1:55:12.216` = 6.912,216 s | #3, **62** voltas, `2:00:36.438` = **7.236,438 s** |
| diferença da nominal de 2 h | **−287,784 s** | **+36,438 s** |
| carros em comum com o log | 27 de 27 | 27 de 27 |
| carros com `LAPS` divergente do log | **24 de 27** | **0 de 27** |

`DERIVED`: a medição da PR #50 — "líder 59 voltas, `1:55:12.216`, −288 s; 24
de 27 carros divergem" — **reproduz exatamente o `Hour 1`**. Ela está correta
sobre o arquivo que leu; o arquivo não era o final.

`DERIVED`: o `Hour 2` satisfaz os dois critérios de corroboração que a PR #50
§9.1 define — líder além da duração nominal e zero divergências de voltas.

`PROPOSED`: **Monza 2018 passa de `PARTIAL_OR_DIVERGENT` a
`FINALITY_CORROBORATED`**, pelo arquivo `Hour 2`, com o mesmo alcance de
sempre: posterior à chegada, **não** homologação comprovada. As contagens
passam de **49 / 1 / 2** para **50 / 0 / 2**, e `PARTIAL_OR_DIVERGENT` fica
vazia.

A medição da PR #50 fica registrada como o que o `Hour 1` mostra. Ver
`classification-snapshot-finality-assessment.md` §7.2 e a matriz de
`evidence-reconciliation.md`.

---

## 8. Um defeito de seleção, corrigido durante a varredura

`MEASURED`: `hour_key` reconhecia `Hour 6` mas não `6H`, `1 Hour` nem
`3 Hours`. Para essas grafias devolvia 0 em todas as horas, e o desempate
alfabético escolhia a **primeira** — um retrato do meio da corrida
apresentado como classificação final.

| | |
|---|---:|
| arquivos com essas grafias no catálogo da ELMS | **152** |
| pastas assim em `raw/` | 6 |
| sessões desta varredura que escolhiam a hora errada | **3** (970, 1007, 1015) |

`MEASURED`, efeito da correção nessas 3: as divergências de voltas caem de
20 de 21, 26 de 26 e 26 de 26 para **0, 0 e 1**.

`MEASURED`, regressão: as **2.732** seleções que `find_classification_sessions`
faz hoje sobre `raw/` são **idênticas** antes e depois. A correção só muda a
escolha onde as grafias convivem, e em `raw/` elas não convivem.

---

## 9. Shortlist para os próximos ensaios

`ELIGIBLE` **e** com `LAPS` da fonte batendo com o log em todos os carros — o
sinal que distinguiu `Hour 2` de `Hour 1` em Monza. **24 sessões**, fora o
controle:

`970` · `1003` · `1007` · `1035` · `1044` · `1056` · `1086` · `1090` ·
`1109` · `1125` · `1156` · `1162` · `1176` · `1189` · `1225` · `1243` ·
`1300` · `2217` · `2235` · `2251` · `2260` · `2269` · `2279` · `2288`

As outras **6** elegíveis divergem em poucos carros e merecem olhar antes:
`1015` (1), `1114` (1), `1205` (2), `1274` (2), `1315` (1), `2197` (1).

`DERIVED`: o controle **2226** também diverge em **1** carro. Isso não
invalida a recuperação da PR #53 — lá os 420 campos comparados batem com o
CSV. É a classificação que credita a um carro um número de voltas diferente
do que o log registra, e aparece aqui porque a medição é outra.

---

## 10. O que esta varredura não faz

1. **Não escreve.** Nenhum `INSERT`, `UPDATE` ou `DELETE`; conexão somente
   leitura.
2. **Não recupera nada.** `recover_session` não foi chamada.
3. **Não altera o recuperador.** A proposta do §3 fica proposta.
4. **Não resolve** as 7 sessões com número em dois `car_id`, nem as 3 sem
   voltas, nem a reatribuição de 2721.
5. **Não decide sobre homologação.** `FINALITY_CORROBORATED` continua
   significando posterior à chegada.
6. **Não gravou nada em `raw/`.** Os 51 arquivos foram lidos para área
   temporária da sessão e descartados.

---

## 11. Reproduzir

```bash
python -m database.recovery.eligibility \
  --host 127.0.0.1 --port 5433 --dbname openwec --user openwec \
  --password <senha> --area <diretorio temporario fora de raw/> \
  --json eligibility.json
```

Imprime uma linha por sessão e o resumo por motivo. `--only <ids>` restringe,
`--limit N` corta. Não há modo de escrita.

---

## Anexo A — as 20 bloqueadas

| sessão | série | temporada | evento | fonte | voltas | motivo | números |
|---|---|---|---|---:|---:|---|---|
| 1082 | ELMS | 10_2015 | 03_RED BULL RING | 29 | 30 | numero em mais de um car_id | 46 |
| 1100 | ELMS | 11_2016 | 01_SILVERSTONE | 44 | 46 | numero em mais de um car_id | 17, 46 |
| 1104 | ELMS | 11_2016 | 02_IMOLA | 41 | 43 | numero em mais de um car_id | 17, 18 |
| 1134 | ELMS | 12_2017 | 01_SILVERSTONE | 35 | 45 | numero em mais de um car_id | 16, 17, 18, 19, 34, 39, … (10 ao todo) |
| 1146 | ELMS | 12_2017 | 02_AUTODROMO NAZIONALE DI MONZA | 36 | 48 | numero em mais de um car_id | 12, 13, 16, 17, 18, 19, … (12 ao todo) |
| 1151 | ELMS | 12_2017 | 03_RED BULL RING | 36 | 40 | numero em mais de um car_id | 15, 18, 19, 6 |
| 1217 | ELMS | 13_2018 | 03_RED BULL RING | 42 | 43 | numero em mais de um car_id | 27 |
| 999 | ELMS | 08_2013 | 01_SILVERSTONE | 23 | 22 | pareamento incompleto | 47 |
| 1011 | ELMS | 08_2013 | 04_HUNGARORING | 25 | 24 | pareamento incompleto | 60 |
| 1025 | ELMS | 09_2014 | 01_SILVERSTONE | 38 | 37 | pareamento incompleto | 71 |
| 1048 | ELMS | 09_2014 | 04_PAUL RICARD | 37 | 36 | pareamento incompleto | 41 |
| 1067 | ELMS | 10_2015 | 01_SILVERSTONE | 31 | 30 | pareamento incompleto | 62 |
| 1071 | ELMS | 10_2015 | 02_IMOLA | 30 | 29 | pareamento incompleto | 44 |
| 1120 | ELMS | 11_2016 | 05_SPA FRANCORCHAMPS | 39 | 38 | pareamento incompleto | 33 |
| 2183 | LEMANSCUP | 01_2017 | 01_AUTODROMO NAZIONALE DI MONZA | 20 | 19 | pareamento incompleto | 43 |
| 2204 | LEMANSCUP | 01_2017 | 04_PAUL RICARD | 19 | 17 | pareamento incompleto | 5, 98 |
| 2210 | LEMANSCUP | 01_2017 | 05_SPA FRANCORCHAMPS | 18 | 17 | pareamento incompleto | 33 |
| 2189 | LEMANSCUP | 01_2017 | 02_LE MANS | 48 | 0 | sem voltas no banco | 10, 11, 12, 14, 15, 18, … (48 ao todo) |
| 2190 | LEMANSCUP | 01_2017 | 02_LE MANS | 49 | 0 | sem voltas no banco | 10, 11, 12, 14, 15, 18, … (49 ao todo) |
| 2721 | IMSA | 16_2016 | 10_Watkins Glen International | 36 | 0 | sem voltas no banco | 09, 12, 15, 17, 18, 19, … (36 ao todo) |

## Anexo B — as 31 elegíveis

| sessão | série | temporada | evento | arquivo | carros | voltas divergem |
|---|---|---|---|---|---:|---:|
| 970 | ELMS | 07_2012 | 01_PAUL RICARD | `6H/05_Classification_Race_6H.CSV` | 21 | 0 |
| 1003 | ELMS | 08_2013 | 02_IMOLA | `03_Race/05_Classification_Race_Race.CSV` | 28 | 0 |
| 1007 | ELMS | 08_2013 | 03_RED BULL RING | `3 Hours/05_Classification_Race_3 Hours.CSV` | 26 | 0 |
| 1015 | ELMS | 08_2013 | 05_PAUL RICARD | `3 Hours/05_Classification_Race_3 Hours.CSV` | 26 | 1 |
| 1035 | ELMS | 09_2014 | 02_IMOLA | `Hour 4/05_Classification_Race_Hour 4.CSV` | 37 | 0 |
| 1044 | ELMS | 09_2014 | 03_RED BULL RING | `Hour 4/05_Classification_Race_Hour 4.CSV` | 33 | 0 |
| 1056 | ELMS | 09_2014 | 05_ESTORIL | `Hour 4/05_Classification_Race_Hour 4.CSV` | 35 | 0 |
| 1086 | ELMS | 10_2015 | 04_PAUL RICARD | `Hour 4/05_Classification_Race_Hour 4.CSV` | 27 | 0 |
| 1090 | ELMS | 10_2015 | 05_ESTORIL | `Hour 4/05_Classification_Race_Hour 4.CSV` | 30 | 0 |
| 1109 | ELMS | 11_2016 | 03_RED BULL RING | `Hour 4/05_Classification_Race_Hour 4.CSV` | 38 | 0 |
| 1114 | ELMS | 11_2016 | 04_PAUL RICARD | `Hour 4/05_Classification_Race_Hour 4.CSV` | 40 | 1 |
| 1125 | ELMS | 11_2016 | 06_ESTORIL | `Hour 4/05_Classification_Race_Hour 4.CSV` | 36 | 0 |
| 1156 | ELMS | 12_2017 | 04_PAUL RICARD | `Hour 4/05_Classification_Race_Hour 4.CSV` | 35 | 0 |
| 1162 | ELMS | 12_2017 | 05_SPA FRANCORCHAMPS | `Hour 4/05_Classification_Race_Hour 4.CSV` | 35 | 0 |
| 1176 | ELMS | 12_2017 | 06_AUTODROMO DO ALGARVE | `Hour 4/05_Classification_Race_Hour 4.CSV` | 35 | 0 |
| 1189 | ELMS | 13_2018 | 01_PAUL RICARD | `Hour 4/05_Classification_Race_Hour 4.CSV` | 45 | 0 |
| 1205 | ELMS | 13_2018 | 02_AUTODROMO NAZIONALE DI MONZA | `Hour 4/05_Classification_Race.CSV` | 42 | 2 |
| 1225 | ELMS | 13_2018 | 04_SILVERSTONE | `Hour 4/05_Classification_Race.CSV` | 41 | 0 |
| 1243 | ELMS | 13_2018 | 05_SPA FRANCORCHAMPS | `Hour 4/05_Classification_Race.CSV` | 40 | 0 |
| 1274 | ELMS | 13_2018 | 06_AUTODROMO DO ALGARVE | `Hour 4/05_Classification_Race.CSV` | 39 | 2 |
| 1300 | ELMS | 14_2019 | 01_PAUL RICARD | `Hour 4/05_Classification_Race.CSV` | 41 | 0 |
| 1315 | ELMS | 14_2019 | 02_AUTODROMO NAZIONALE DI MONZA | `Hour 4/05_Classification_Race.CSV` | 41 | 1 |
| 2197 | LEMANSCUP | 01_2017 | 03_RED BULL RING | `Hour 2/05_Classification_Race_Hour 2.CSV` | 21 | 1 |
| 2217 | LEMANSCUP | 01_2017 | 06_AUTODROMO DO ALGARVE | `Hour 2/05_Classification_Race_Hour 2.CSV` | 19 | 0 |
| 2226 *(controle)* | LEMANSCUP | 02_2018 | 01_PAUL RICARD | `Hour 2/05_Classification_Race.CSV` | 28 | 1 |
| 2235 | LEMANSCUP | 02_2018 | 02_AUTODROMO NAZIONALE DI MONZA | `Hour 2/05_Classification_Race.CSV` | 27 | 0 |
| 2251 | LEMANSCUP | 02_2018 | 04_RED BULL RING | `Hour 2/05_Classification_Race.CSV` | 26 | 0 |
| 2260 | LEMANSCUP | 02_2018 | 05_SPA FRANCORCHAMPS | `Hour 2/05_Classification_Race.CSV` | 24 | 0 |
| 2269 | LEMANSCUP | 02_2018 | 06_AUTODROMO DO ALGARVE | `Hour 2/05_Classification_Race.CSV` | 25 | 0 |
| 2279 | LEMANSCUP | 03_2019 | 01_PAUL RICARD | `Hour 2/05_Classification_Race.CSV` | 31 | 0 |
| 2288 | LEMANSCUP | 03_2019 | 02_AUTODROMO NAZIONALE DI MONZA | `Hour 2/05_Classification_Race.CSV` | 30 | 0 |

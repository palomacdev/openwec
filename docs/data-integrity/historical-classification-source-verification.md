# Historical classification source verification

Transformar "o arquivo existe no catálogo" em evidência concreta de que a
reconstrução é possível. **Somente leitura.** Nenhuma escrita no banco,
nenhum arquivo gravado em `raw/`, nenhum loader, migration, enrichment ou
script de recuperação executado.

**Ambiente:** `openwec@127.0.0.1:5433` para leitura, e requisições HTTP de
leitura aos hosts do publicador. **Data:** 2026-10-23. **`main` em:**
`b5cd404` (após a PR #48).

Estatutos, como nas PRs anteriores: **`MEASURED`** demonstrado,
**`DERIVED`** calculado, **`PROPOSED`** recomendado, **`UNKNOWN`** não
estabelecido.

---

## 1. Resumo executivo

A PR #48 mostrou que o catálogo publica classificação para as 51 sessões
contaminadas e propôs verificar se essas fontes ainda respondem. **Elas
respondem.**

| | |
|---|---:|
| URLs verificadas nas sessões contaminadas | **182** |
| responderam `HTTP 200` | **182** (100 %) |
| com `Content-Type: text/csv` | **182** |
| decodificam como UTF-8 | **182** |
| delimitador `;` | **182** |
| **com todos os 14 campos que o parser lê** | **182** |
| falhas, timeouts, redirecionamentos, HTML, vazios | **0** |

`MEASURED`. Nenhuma URL falhou.

Por sessão, separando o que é classificação final do que é snapshot:

| estado | sessões |
|---|---:|
| `VERIFIED_FINAL_CLASSIFICATION` | **16** |
| `VERIFIED_PARTIAL_CLASSIFICATION` | **32** |
| `ACCESSIBLE_SCHEMA_UNKNOWN` | **4** |
| `TEMPORARILY_UNAVAILABLE` · `NOT_FOUND` · `UNVERIFIED` | **0** |

`MEASURED`: as fontes acessíveis somam **1.719 linhas de classificação**,
contra as **1.665** entradas competitivas reais que a PR #43 mediu no banco
para essas sessões. `DERIVED`: a cobertura é da ordem certa.

**Mas 32 das 48 verificadas são parciais**, e essa é a ressalva central:
para elas o publicador só emitiu snapshots horários. Tratá-los como
classificação final é uma decisão, não uma leitura.

### Três coisas que a verificação desmentiu

1. **`HTTP 200` não é evidência de arquivo.** `MEASURED`: nas outras 28
   sessões, **26 URLs devolveram `200` com `Content-Type: text/html`** — uma
   página no lugar do CSV. Nenhuma das 182 contaminadas teve esse problema,
   mas a distinção precisou ser medida, não suposta.
2. **A família `05_` não é um formato novo.** `MEASURED`: o cabeçalho é o
   dialeto A que a PR #38 já ensinou o parser a ler. O que falta é o filtro
   de nome, não o parser.
3. **A investigação das outras 28 encontrou 8 sessões com problema novo**,
   que nenhuma PR anterior tinha visto.

---

## 2. Metodologia

**Inventário.** As 51 pastas contaminadas foram reidentificadas pelo mesmo
critério da PR #43 — pasta com `23_Analysis*` e sem classificação legítima —
e casadas com o catálogo, produzindo 52 entradas e 182 URLs. As outras 28
sessões que publicam `05_Classification*` deram 67 URLs.

**Requisições.** `GET` com `User-Agent` próprio, sem credenciais nem
cabeçalho interno; timeout de 10 s para conexão e 25 s para leitura; teto de
512 KB por resposta; **0,3 a 0,35 s entre requisições**; `requests.Session`
reutilizando a conexão. Hosts: `elms`, `lemanscup`, `alms`, `imsa` em
`alkamelsystems.com`, e `imsa.results.alkamelcloud.com`.

**Conteúdo analisado em memória.** `MEASURED`: nenhum byte baixado foi
gravado em `raw/` nem em qualquer outro lugar do repositório. O que ficou
registrado são **metadados derivados** — status, tipo, cabeçalho, contagens,
conjuntos de números de carro — e é deles que este documento é feito.

**Três passagens**, porque cada pergunta exigiu mais do conteúdo que a
anterior: acessibilidade; depois `LAPS`, `POSITION` e números de carro;
depois largura de linha e codificação. `DERIVED`: 431 requisições no total,
em cerca de 5 minutos somados.

### 2.1 Os quatro estados que o escopo pede para não confundir

| situação | como foi verificada | resultado |
|---|---|---|
| 1. publicado no catálogo | leitura de `catalog/*/sessions.json` | 182 URLs, 52 entradas |
| 2. URL acessível | `GET`, status e tipo | 182 de 182 |
| 3. conteúdo válido e da sessão | decodificação, cabeçalho e **números de carro contra a análise local** | 48 de 52 |
| 4. adequado à reconstrução | presença dos 14 campos, alinhamento, dialeto | ver §6 |

São degraus, não sinônimos: `MEASURED` nas outras 28, 26 URLs passaram no
degrau 2 e falharam no 3.

---

## 3. Inventário das 51 sessões

`MEASURED`. 52 entradas de catálogo para 51 pastas — uma chave casa duas
entradas, como na PR #43.

| subgrupo | sessões | `results` no banco | família publicada |
|---|---:|---:|---|
| **S1** | 1 | 13.488 | `03_Results.CSV` |
| **S2** | 50 | 311.232 | `05_Classification_*.CSV` |

Distribuição: ELMS 36, Le Mans Cup 14, IMSA 1. Todas sessões de corrida.

### 3.1 Nomes publicados

`MEASURED`, normalizando dígitos:

| padrão | arquivos |
|---|---:|
| `05_Classification_Race_Hour N.CSV` | 110 |
| `05_Classification_Race.CSV` | 39 |
| `05_Results by Hour.CSV` | 12 |
| `05_Classification_Race_NH.CSV` | 6 |
| `05_Classification_Race_N Hours.CSV` | 6 |
| `05_Classification_Race_N Hour.CSV` | 3 |
| `05_Classification_Race_Race.CSV` | 2 |
| `03_Results.CSV` | 2 |
| `05_Classification_Race N_N.CSV` · `..._Race.CSV` | 2 |

`DERIVED`: **seis grafias distintas para "a hora N"** — `Hour N`, `NH`,
`N Hours`, `N Hour`, e o sufixo `_Race` para o arquivo não horário. Qualquer
reconhecedor futuro precisa das seis.

---

## 4. Resultados de acessibilidade

`MEASURED`, as 182 URLs das sessões contaminadas:

| dimensão | resultado |
|---|---|
| status | `200` em 182 |
| `Content-Type` | `text/csv` em 182 |
| redirecionamentos | 0 |
| timeouts | 0 |
| HTML no lugar de CSV | 0 |
| respostas vazias | 0 |
| codificação | UTF-8 em 182 — **176 com BOM, 6 sem** |
| delimitador | `;` em 182 |
| larguras de linha variáveis dentro de um arquivo | 0 |

> **Uma suspeita minha que a medição desmentiu.** Ao inspecionar um arquivo
> isoladamente vi `L?onard` onde deveria estar `Léonard` e suspeitei de
> codificação. `MEASURED`: os 182 decodificam como UTF-8 em modo estrito. O
> caractere era do console, não do arquivo. **Não há defeito de codificação
> nessas fontes.**

---

## 5. Classificações horárias contra finais

A pergunta do §3 do escopo, e a ressalva mais importante deste documento.

### 5.1 O que são os arquivos horários

`MEASURED`: em **36 de 36** sessões com dois ou mais arquivos horários, o
maior `LAPS` cresce monotonicamente de hora em hora. Exemplo real, ELMS 2012
Paul Ricard, corrida de 6 horas:

| arquivo | linhas | `LAPS` máximo |
|---|---:|---:|
| `05_Classification_Race_1H.CSV` | 21 | 32 |
| `05_Classification_Race_2H.CSV` | 21 | 64 |
| `05_Classification_Race_3H.CSV` | 21 | 95 |
| `05_Classification_Race_4H.CSV` | 21 | 127 |
| `05_Classification_Race_5H.CSV` | 21 | 158 |
| `05_Classification_Race_6H.CSV` | 21 | 189 |

`DERIVED`: são **snapshots cumulativos** da mesma corrida, não
classificações de segmentos independentes.

### 5.2 Por que o último não é automaticamente o final

`UNKNOWN`: se o snapshot da última hora coincide com a bandeirada. Uma
corrida de 6 horas termina quando o líder cruza a linha **após** as 6 horas,
e o snapshot "6H" pode ser tirado no instante das 6 horas, antes disso.
`UNKNOWN`: se há volta registrada depois do último snapshot.

`MEASURED`: há evidência que **poderia** resolver isso e não foi usada aqui
— o arquivo de análise local traz todas as voltas com `ELAPSED`, então
comparar a última volta de cada carro com o snapshot da última hora diria se
coincidem. **Não foi feito**, e fica como lacuna explícita.

Por isso: 32 sessões ficam em `VERIFIED_PARTIAL_CLASSIFICATION`, e **nenhum
arquivo horário foi tratado como classificação final.**

### 5.3 Os quatro tipos de arquivo, comparados

| arquivo | o que é | evidência |
|---|---|---|
| `05_Classification_Race_<hora>.CSV` | snapshot cumulativo na marca da hora | `MEASURED`: `LAPS` monotônico |
| `05_Classification_Race.CSV` | classificação da corrida, sem marca de hora | `MEASURED`: 39 arquivos; onde coexiste com horárias, `LAPS` ≥ todas |
| `05_Classification_Race_Race.CSV` | o mesmo, com o nome da sessão repetido | `MEASURED`: 2 arquivos |
| `05_Results by Hour.CSV` | — | `UNKNOWN`: 12 arquivos, não inspecionados individualmente |
| `03_Results.CSV` | a família que o loader já lê | `MEASURED`: 2 arquivos, S1 |

---

## 6. Compatibilidade com os parsers

### 6.1 O dialeto é o que o parser já lê

`MEASURED`, cabeçalho de `05_Classification_Race_1H.CSV`:

```
POSITION;NUMBER;TEAM;DRIVER_1;DRIVER_2;DRIVER_3;DRIVER_4;VEHICLE;TYRES;
CLASS;GROUP;DIVISION;STATUS;LAPS;TOTAL_TIME;GAP_FIRST;GAP_PREVIOUS;
FL_LAPNUM;FL_TIME;FL_KPH
```

É o **dialeto A** que a PR #38 descreve. `MEASURED`: dos 14 campos que
`insert_result` e `car_identity` leem, **nenhum está ausente em nenhum dos
182 arquivos**.

| campo | ausente em |
|---|---:|
| `POSITION`, `NUMBER`, `TEAM`, `VEHICLE`, `CLASS`, `GROUP`, `STATUS`, `LAPS`, `TOTAL_TIME`, `GAP_FIRST`, `FL_LAPNUM`, `FL_TIME`, `FL_KPH` | **0 arquivos** |

`MEASURED`: `TYRES` em 177, `TIRES` em 5 — e `car_identity` já lê as duas
grafias.

`DERIVED`: **o parser não precisa mudar.** O que falta é
`is_classification_file`, hoje `^03_(classification|results)`, reconhecer a
família `05_`. Isso **não foi implementado nesta PR**, conforme o escopo.

### 6.2 A incompatibilidade que existe

`MEASURED`: **5 dos 182 arquivos têm cabeçalho mais largo que as linhas** —
93 colunas no cabeçalho, 73 campos nas linhas. São 4 `05_Classification_Race.CSV`
e 1 `03_Results.CSV`.

```
cabeçalho (93): POSITION;NUMBER;STATUS;LAPS;TOTAL_TIME;GAP_FIRST;GAP_PREVIOUS;
                FL_LAPNUM;FL_TIME;FL_KPH;TEAM;CLASS;GROUP;DIVISION;VEHICLE;TIRES;…
linha     (73): 1;3;Classified;62;2:01:25.448;-;-;30;1:52.439;185.4;
                DKR Engineering;LMP3;;;Norma M30 - Nissan;M;…
```

`DERIVED`: as **16 primeiras colunas alinham**, e são elas que carregam
tudo o que o loader lê. Além disso o alinhamento se perde. `csv.DictReader`
preencheria as 20 colunas excedentes com `None` — não quebraria, mas os
campos finais do cabeçalho não correspondem aos dados.

`UNKNOWN`: a causa. `PROPOSED`: quem reconstruir a partir desses 5 precisa
ler por posição nas 16 primeiras colunas, ou validar a largura antes.

### 6.3 Um terceiro dialeto, nas outras 28

`MEASURED`: 5 arquivos `05_Classification_Race.CSV` de Le Mans Cup
2018–2019 usam outro cabeçalho:

```
Pos;Number;Driver 1;Driver 2;Driver 3;Driver 4;Group;Class;…
```

Caixa mista e espaços nos nomes. `DERIVED`: é o terceiro dialeto que o
diagnóstico operacional de 2026-10-22 já havia identificado no corpus local
e que o loader descarta, porque `field(row, "NUMBER")` não encontra
`Number`. **Nenhum deles está entre as 51.**

### 6.4 Diferenças de schema entre temporadas

`MEASURED`: dentro das 51, duas variantes do dialeto A — uma com
`DRIVER_1..4;VEHICLE;TYRES` antes de `STATUS`, outra com `STATUS;LAPS` logo
após `NUMBER` e `VEHICLE;TIRES` mais adiante. As duas têm todos os campos.
`UNKNOWN`: se a variante correlaciona com temporada; não foi testado.

---

## 7. As outras 28 sessões

`MEASURED`. Não estavam contaminadas, e a investigação encontrou outra
coisa.

| situação | sessões |
|---|---:|
| **ingeridas por outra fonte** (`03_` local presente), dados no banco | **20** |
| **sem `03_` local e sem nenhum `results`** | **5** |
| **com `03_` local mas zero `results`** | **3** |

`DERIVED`: **8 sessões com problema novo**, que nenhuma PR anterior
registrou. Não são contaminação — são ausência.

- **As 5 sem nada** são corridas da Le Mans Cup (Le Mans 2018 e 2019,
  `Race 1` e `Race 2`). `MEASURED`: o que o publicador oferece para elas é o
  terceiro dialeto do §6.3, que o loader descarta. **Falta de download e
  falta de parser, somadas.**
- **As 3 com arquivo e sem linhas** são ELMS 2011 (Imola, Silverstone,
  Estoril, corridas). `UNKNOWN`: por quê — o arquivo está em `raw/` e
  nenhuma linha chegou ao banco.

`MEASURED`: das 67 URLs dessas 28, **26 devolveram `200` com `text/html`**,
todas de ALMS 2025-2026 — o servidor responde uma página no lugar do CSV.
36 vieram bem e 5 no terceiro dialeto.

---

## 8. Limitações

1. **Verificação num instante.** `MEASURED` hoje; `UNKNOWN` se as URLs
   responderão amanhã.
2. **Nada foi guardado.** Por decisão de escopo, nenhum byte foi persistido.
   Reproduzir exige repetir as requisições, e o conteúdo pode mudar.
3. **A correspondência com a sessão é por números de carro.** `MEASURED`:
   36 sessões têm os números da fonte **contidos** nos da análise local, 12
   têm interseção parcial, 2 não têm interseção e 2 não têm referência
   local. Interseção parcial é esperada — a análise traz quem completou
   voltas, a classificação traz quem largou — mas **não é prova** de que o
   arquivo é daquela sessão.
4. **Os 4 `ACCESSIBLE_SCHEMA_UNKNOWN`** não foram investigados caso a caso.
5. **`05_Results by Hour.CSV`** (12 arquivos) não foi inspecionado
   individualmente.
6. **A pergunta do §5.2 ficou em aberto** e é a mais consequente: se o
   último snapshot é a classificação final.
7. **Cobertura por linhas, não por carro.** `MEASURED`: 1.719 linhas contra
   1.665 entradas. `UNKNOWN`: se cada entrada tem exatamente uma linha
   correspondente — não foi verificado carro a carro.

---

## 9. Implicações para as estratégias B e D

A PR #48 condicionou B e D à verificação da fonte. `MEASURED`: a fonte
responde, é CSV, está no dialeto que o parser lê, e tem todos os campos.

| | antes da #49 | depois |
|---|---|---|
| S1 — 1 sessão, 13.488 linhas | `NEEDS_SOURCE_VERIFICATION` | **`VERIFIED_FINAL_CLASSIFICATION`** |
| S2 — 50 sessões, 311.232 linhas | `NEEDS_SOURCE_VERIFICATION` | **15 finais, 32 parciais, 4 desconhecidas** |

`DERIVED`: **B e D deixam de depender de uma incógnita.** O que resta não é
"a fonte existe?", e sim três decisões concretas — baixar, estender o filtro
de nome, e resolver se um snapshot horário serve de classificação final.

`DERIVED`: **C — remoção — perde o último argumento a seu favor.** Remover
as 324.720 linhas agora apagaria registros cuja substituição está
demonstrada como disponível para 48 das 52 entradas.

`PROPOSED`: o estado de `UNRECOVERABLE` atribuído pela PR #43 **deve ser
corrigido nos documentos originais**, porque a condição que a PR #48 pôs
para isso — que a verificação desse resultado — está satisfeita. §10 propõe
quando.

> **Aplicado pela PR #51**, e com duas correções a este documento. As
> contagens 16/32/4 desta PR continuam registradas como o que esta
> investigação mediu; a PR #50 as revisou para **49 corroboradas, 1
> divergente e 2 indeterminadas**, e encontrou dois defeitos de medição
> meus aqui — duas entradas da Le Mans Cup 2018 dadas como "sem interseção"
> por descarte de linhas mais curtas que o cabeçalho, e a sessão IMSA de
> Watkins Glen comparada com o arquivo de outro campeonato. A
> rastreabilidade está em
> [evidence-reconciliation.md](evidence-reconciliation.md) §2.

---

## 10. Próximas decisões necessárias

| # | decisão | o que a destrava | bloqueia |
|---|---|---|---|
| 1 | **Autorizar o download** dos arquivos verificados para `raw/` | nada; a verificação está feita | B e D |
| 2 | **Estender `is_classification_file`** para a família `05_`, com as seis grafias de hora | decisão 1 | B e D em S2 |
| 3 | **Um snapshot horário vale como classificação final?** | comparar a última volta da análise local com o snapshot — leitura, não feita | 32 das 48 sessões |
| 4 | Corrigir `UNRECOVERABLE` nos documentos da #43 e #48 | este documento | nada; é registro |
| 5 | Investigar as 8 sessões novas do §7 | — | nada; é escopo novo |
| 6 | Tratar o terceiro dialeto | decisão 5 | as 5 sessões da Le Mans Cup |

`PROPOSED`: a decisão 3 é a de maior alcance e **não exige autorização
nenhuma** — é leitura de arquivos que já estão em `raw/`. Resolvê-la
converteria até 32 sessões de parcial para final, ou mostraria que não.

**Nenhuma decisão foi tomada aqui, e nenhuma operação de recuperação está
autorizada.**

# Primeiro ensaio de recuperação em ambiente isolado

`MEASURED` salvo onde marcado. Executado em 2026-10-23 sobre uma cópia
descartável do banco histórico. **Nada foi escrito no banco histórico.**

---

## 1. Os ambientes, e qual é qual

`MEASURED`, `docker ps -a`:

| container | imagem | porta | o que é | usado aqui |
|---|---|---|---|---|
| `openwec-db` | `timescale/timescaledb:latest-pg16` | 5433 | **o banco histórico**: é o endereço de `DB_CONFIG` e de `.env`, e é onde as PRs #43–#52 mediram | **somente leitura** (`pg_dump`, `SELECT`) |
| `openwec-test-db` | mesma imagem, digest `87f39af0…` | nenhuma | criado em 2026-10-21, banco `openwec_test`, volume `openwec_test_db_data`. Nenhum arquivo do repositório o menciona | **não tocado, não iniciado** |
| `openwec-recovery-rehearsal` | mesmo digest de `openwec-db` | 5455 | criado para este ensaio, volume `openwec_rehearsal_data` | **o alvo das escritas** |

`DERIVED`: `openwec-test-db` não foi usado. O escopo pedia para não presumir
que ele fosse descartável, e nada do que foi medido estabelece que seja — seu
conteúdo não foi inspecionado, porque inspecioná-lo exigiria iniciá-lo. Um
container novo, com volume novo, dá isolamento que não depende de descobrir
isso.

Containers de outros projetos — `repasse`, `talos`, `argos`, `nexus`,
`registro-rh`, `sistema-cotacoes`, `alphecca` — não foram tocados.

---

## 2. Backup, verificado em três perguntas

O `MAINTENANCE.md` documenta um `pg_dump` diário às 03:00 UTC para
DigitalOcean Spaces, com 7 dias de retenção — **na VM de produção**. Para o
banco histórico local não há mecanismo documentado. O ensaio criou o seu.

| pergunta do escopo | como foi respondida | resultado |
|---|---|---|
| existe backup íntegro e utilizável? | `pg_dump -Fc --no-owner --no-privileges`, lido do container por stdout | **83.543.503 bytes em 21 s**, sem erro |
| ele pode ser restaurado? | `pg_restore` em container novo | **25,8 s, zero erros** |
| a restauração modifica o original? | contagem das 14 tabelas de `public` antes e depois, nos dois bancos | **origem idêntica**; cópia idêntica à origem |

`MEASURED`, as 14 tabelas, origem e cópia, linha a linha:

```
analytics_car_session 8243 · analytics_stints 42558 · api_key_requests 9
cars 671353 · drivers 2845 · events 528 · ingest_log 0 · laps 2034223
result_drivers 469457 · results 671353 · seasons 56 · series 5
sessions 5425 · teams 1111
```

`UNKNOWN`: o dump emitiu um aviso sobre chaves estrangeiras circulares em
`continuous_agg`, catálogo interno do TimescaleDB. A restauração não falhou e
as contagens batem; a interação não foi investigada além disso.

---

## 3. A sessão piloto

**`2226` — LEMANSCUP / 02_2018 / 01_PAUL RICARD / 201804141605_Race.**

`MEASURED`, antes de qualquer escrita:

| | |
|---|---:|
| linhas em `results` | **3.108** |
| `car_id` distintos nessas linhas | **3.108** |
| linhas em `result_drivers` | **0** |
| linhas em `laps` | **1.554** |
| `car_id` distintos nas voltas | **28** |

`DERIVED`: um `cars` por cruzamento de volta — a assinatura da contaminação.

**Por que ela permite um ensaio independente**, `MEASURED`:

- a fonte publica **28 carros**; as voltas conhecem **28 números**; os dois
  conjuntos são **iguais**, e cada número aparece **uma vez** de cada lado.
  O pareamento é uma bijeção provada, não uma escolha;
- nenhum número das voltas está espalhado por dois `car_id`;
- `analytics_car_session` e `analytics_stints` apontam para 28 e 64 linhas,
  **nenhuma** delas em um carro que o ensaio removeria;
- `result_drivers` é zero, então não há vínculo anterior a desfazer.

Monza ficou de fora, como pedido.

---

## 4. A fonte

`05_Classification_Race.CSV` da pasta `Hour 2`, a hora final das duas que a
sessão publica.

```
https://lemanscup.alkamelsystems.com/Results/02_2018/01_PAUL%20RICARD/
  218_Michelin%20Le%20Mans%20Cup/201804141605_Race/Hour%202/05_Classification_Race.CSV
HTTP 200 · text/csv · 7.784 bytes
sha256 034e73c53cea3fdc5f67f636eeade3dabb6894372fc1409fd5839973f472e29c
```

`MEASURED`: 93 colunas de cabeçalho sobre linhas de 73 campos — a forma que a
PR #52 validou. Baixado para fora de `raw/`, em área temporária da sessão.

`DERIVED`: a hora final é a da pasta `Hour 2`. A de `Hour 1` registra o líder
com 59 voltas em `1:55:12.216`; a de `Hour 2`, com **62 voltas em
`2:00:36.438`** — leitura consistente com uma corrida de 2 h já encerrada.
É a mesma distinção que a PR #52 levantou sobre Monza.

---

## 5. O que o ensaio escreveu

| | antes | depois |
|---|---:|---:|
| `results` | 3.108 | **28** |
| `car_id` distintos em `results` | 3.108 | **28** |
| `result_drivers` | 0 | **54** |
| `laps` | 1.554 | **1.554** |
| `car_id` distintos nas voltas | 28 | **28** |

`MEASURED`, operações:

- **3.080** linhas de `results` removidas — todas as que nenhuma volta
  aponta;
- **3.080** linhas de `cars` removidas, cada uma sem nenhuma outra
  referência;
- **28** linhas de `results` gravadas sobre os **mesmos `car_id` que já
  sustentavam as voltas**;
- **56** colunas de `cars` preenchidas (`vehicle` e `tires` dos 28 carros),
  todas onde a coluna estava NULL;
- **0** colunas sobrescritas.

### 5.1 Conferência contra o CSV

`MEASURED`: **420 campos comparados** — 15 por carro, nos 28 carros —
**0 divergências**. Os campos: `position`, `status`, `laps_completed`,
`total_time_raw`, `total_time_s`, `gap_to_first_raw`, `gap_to_first_s`,
`gap_to_prev_s`, `fl_lap_number`, `fl_time_raw`, `fl_time_s`, `fl_kph`,
`vehicle`, `car_class`, `tires`.

### 5.2 Os três campos corrigidos pela PR #52

`MEASURED`, nas 28 linhas gravadas:

| campo | linhas que exercitaram a correção | leitura |
|---|---:|---|
| `total_time_s` | **25** com `TOTAL_TIME` em `H:MM:SS` | líder `2:01:25.448` → **7285,448 s**. Antes da #52 teria sido 121,0 s |
| `gap_to_first_s` | **3** com gap em minutos | `+1:16.850` → **76,850 s**. Antes, NULL |
| `status` | **5** `Not classified` | gravadas como **`Not Classified`**, com `position` NULL. Antes, `Classified` |

`DERIVED`: as 23 posições 1–23 e os 5 não classificados reproduzem a ordem
publicada.

### 5.3 Pilotos

`MEASURED`: a fonte traz **56** nomes em 28 carros, nas colunas
`DRIVER1_FIRSTNAME` / `DRIVER1_SECONDNAME` — a forma IMSA, num arquivo da Le
Mans Cup. Com o extrator escolhido pelo formato das colunas:

| | |
|---|---:|
| nomes na fonte | 56 |
| **vinculados** | **54** |
| resolvidos por nome exato | 53 |
| resolvidos por nome normalizado | 1 (`Andy MEYRICK` → `Andy Meyrick`) |
| **recusados por ambiguidade** | **2** |
| pilotos criados | 17 |
| pilotos já existentes, reaproveitados | 37 |

Os dois recusados, `MEASURED`:

- **`Job VAN UITERT`** — 4 linhas em `drivers` com recortes diferentes de
  "van Uitert";
- **`Eric de DONCKER`** — 2 linhas.

`DERIVED`: nenhuma fusão foi feita. Os dois carros ficaram com 1 piloto
vinculado em vez de 2, que é o estado honesto: a evidência não diz qual
linha é a certa.

### 5.4 Equipes: divergência relatada, não resolvida

`MEASURED`: 4 dos 28 carros têm no banco uma grafia de equipe diferente da
publicada.

| carro | no banco (log de voltas) | na classificação |
|---|---|---|
| 2, 79 | `Ecurie Ecosse/Nielsen` | `Ecurie Ecosse / Nielsen` |
| 14 | `RLR M Sport` | `RLR MSport` |
| 51 | `Spirit of Race` | `Spirit Of Race` |

`DERIVED`: `teams` não foi tocado e o `team_id` dos carros não foi movido.
Duas fontes grafando a mesma equipe de modo diferente não é evidência de que
a armazenada esteja errada.

---

## 6. Idempotência

`MEASURED`, três execuções seguidas da mesma recuperação:

| | execução 1 | execução 2 | execução 3 |
|---|---:|---:|---:|
| `results` removidas | 3.080 | **0** | 0 |
| `cars` removidos | 3.080 | **0** | 0 |
| colunas de `cars` preenchidas | 56 | **0** | 0 |
| `results` gravadas | 28 | 28 | 28 |
| `result_drivers` ao final | 54 | 54 | 54 |

`MEASURED`: digest do conteúdo das 28 linhas — id, carro, posição, status,
voltas, tempos e pilotos — **idêntico** após a 2ª e a 3ª execução
(`4e33590b996c2786ff1413ac70057764`). As contagens globais das 14 tabelas
também são idênticas entre a 2ª e a 3ª.

---

## 7. O que não se moveu

`MEASURED`:

- **as outras 5.424 sessões**: digest de `(id, results, laps)` idêntico
  antes e depois de cada execução — `6642dbbe3be5e96076b5c9fc386b13f4`;
- **o banco histórico**: as 14 contagens de `openwec-db` ao fim do ensaio são
  as mesmas do início;
- **`raw/`**: sem alteração;
- na cópia, as únicas grandezas globais que mudaram são as da própria
  sessão: `cars` 671.353 → 668.273, `results` 671.353 → 668.273,
  `result_drivers` 469.457 → 469.511, `drivers` 2.845 → 2.862.

---

## 8. Medido no banco, não deduzido dos arquivos

O escopo pede a distinção, e ela importa: a contagem de **formatos** no
acervo local e a contagem de **linhas divergentes** no banco são grandezas
diferentes.

| | medido nos arquivos (PR #52) | medido na cópia do banco, fora da sessão piloto |
|---|---:|---:|
| `TOTAL_TIME` em `H:MM:SS` | 11.123 linhas de CSV | **19.227** linhas de `results`, **todas** com `total_time_s` diferente do valor correto |
| gap em minutos com sinal | 726 linhas de CSV | **1.922** linhas com `gap_to_first_s` NULL |
| `Not classified` | 855 linhas de CSV | **789** linhas `Classified` sem `position` — assinatura, não prova |

`DERIVED`: as duas primeiras são correções pendentes de decisão, não deste
ensaio. A terceira é um indício: `status='Classified'` com `position` NULL é
o que o defeito produzia, mas o banco não guarda o texto original de
`STATUS`, então a contagem exata não é recuperável de dentro dele.

**Nenhuma dessas linhas foi corrigida.** O ensaio tocou uma sessão.

---

## 9. Como reproduzir

```bash
docker exec openwec-db pg_dump -U openwec -d openwec -Fc \
  --no-owner --no-privileges > openwec_historical.dump

docker volume create openwec_rehearsal_data
docker run -d --name openwec-recovery-rehearsal \
  -e POSTGRES_USER=openwec -e POSTGRES_PASSWORD=rehearsal_only \
  -e POSTGRES_DB=openwec -p 127.0.0.1:5455:5432 \
  -v openwec_rehearsal_data:/var/lib/postgresql/data \
  timescale/timescaledb:latest-pg16

docker exec -i openwec-recovery-rehearsal pg_restore -U openwec -d openwec \
  --no-owner --no-privileges < openwec_historical.dump

docker exec openwec-recovery-rehearsal psql -U openwec -d openwec -c \
  "CREATE TABLE recovery_rehearsal (declared_at timestamptz NOT NULL DEFAULT now(), note text NOT NULL)"

OPENWEC_RECOVERY_IS_DISPOSABLE=1 \
RECOVERY_DB_HOST=127.0.0.1 RECOVERY_DB_PORT=5455 \
RECOVERY_DB_NAME=openwec RECOVERY_DB_USER=openwec \
RECOVERY_DB_PASSWORD=rehearsal_only \
python -m database.recovery.recover_classification \
  --session-id 2226 --csv 05_Classification_Race.CSV --dry-run
```

Para descartar: `docker rm -f openwec-recovery-rehearsal && docker volume rm
openwec_rehearsal_data`.

### 9.1 As três recusas, verificadas

`MEASURED`, cada uma devolvendo saída 2 sem abrir transação:

```
sem OPENWEC_RECOVERY_IS_DISPOSABLE
  → BLOQUEADO: OPENWEC_RECOVERY_IS_DISPOSABLE nao declarado
apontando para 127.0.0.1:5433
  → BLOQUEADO: o alvo 127.0.0.1:5433 e o endereco historico do projeto
alvo sem a tabela recovery_rehearsal
  → BLOQUEADO: o alvo nao tem a tabela recovery_rehearsal, que so uma copia
    descartavel recebe
```

---

## 10. O que este ensaio não faz

1. **Não recupera as outras 50 sessões.** Uma.
2. **Não reingere nada**, não resolve as 35.045 colisões de voltas, não
   funde pilotos, não corrige os 19.227 tempos nem os 1.922 gaps.
3. **Não decide sobre as grafias de equipe divergentes.**
4. **Não vincula os 2 pilotos ambíguos.**
5. **Não toca no banco histórico**, em `raw/`, nem em ambiente de produção.
6. `UNKNOWN`: se a classificação usada é a homologada. A PR #50 corrobora
   que é posterior à chegada; isso continua sendo tudo o que se sabe.

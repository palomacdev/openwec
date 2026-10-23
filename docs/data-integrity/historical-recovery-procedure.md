# Recuperação da sessão 2226 no banco histórico — procedimento

`MEASURED` em 2026-10-23. **Nada foi escrito no banco histórico.** Este
documento e a PR que o acompanha **não são autorização**: nenhuma variável,
flag ou arquivo aqui concede permissão de escrita.

A pergunta que ele responde: *se a recuperação da sessão 2226 for autorizada,
sabemos exatamente o que executar, o que será modificado, como validar o
resultado e como voltar ao estado anterior?*

---

## 1. O alvo

**Sessão 2226 — LEMANSCUP / 02_2018 / 01_PAUL RICARD / 201804141605_Race.**

É a única que este caminho conhece: o identificador é comparado com uma
constante, não há `--all`, `--batch` nem lista, e um teste afirma a ausência
dessas opções sobre o código-fonte do módulo.

| | |
|---|---|
| cluster do banco histórico | **7642299396037144610** |
| fonte | `Hour 2/05_Classification_Race.CSV`, 7.784 bytes |
| sha256 da fonte | `034e73c53cea3fdc5f67f636eeade3dabb6894372fc1409fd5839973f472e29c` |

---

## 2. Pré-condições, medidas no banco histórico

`MEASURED`, conexão com `default_transaction_read_only=on`, executando a
mesma rotina `preflight` que a execução real usaria. **Todas passaram.**

| verificação | resultado |
|---|---|
| sessão é 2226 | sim |
| cluster declarado = cluster reportado | `7642299396037144610` |
| identidade da sessão | `LEMANSCUP / 02_2018 / 01_PAUL RICARD / 201804141605_Race` |
| hash da fonte | confere |
| carros na fonte | **28** |
| estado atual | **contaminada** — `results` 3.108, `cars_in_results` 3.108, `result_drivers` 0, `laps` 1.554, `cars_in_laps` 28 |
| pareamento | **28 de 28**, bijeção; nenhum número duplicado; nenhum número em dois `car_id`; nenhum carro admitido por exceção |
| `cars` a preservar | 28, por id: `367379 … 368873` |
| `cars` a remover | **3.080** (menor id 367380, maior 612995) |
| referências externas a esses 3.080 | `results` de outras sessões **0**, `laps` **0**, `analytics_car_session` **0**, `analytics_stints` **0** |
| outras sessões | 5.424, digest `6642dbbe3be5e96076b5c9fc386b13f4` |

`DERIVED`: o estado é idêntico ao medido antes dos ensaios. Se qualquer uma
dessas grandezas mudar, a execução **para** — não há adaptação automática a
um estado inesperado.

---

## 3. Backup

### 3.1 Gerar, imediatamente antes

```bash
docker exec openwec-db pg_dump -U openwec -d openwec -Fc \
  --no-owner --no-privileges > openwec-prerecovery.dump
sha256sum openwec-prerecovery.dump
```

`MEASURED`: 20,4 s, **83.543.503 bytes**, sha256
`c097353ac42e8d40bb4ca3d86c022b270fb8e100a72ec7171a9017b23fcd3560`. A saída
é lida do contêiner por stdout; nada é escrito dentro dele.

O executável **recusa** um backup que não exista, esteja vazio ou não comece
com a assinatura `PGDMP`. Um dump SQL simples passaria por uma checagem de
existência e não restauraria — e o procedimento de reversão supõe
`pg_restore`.

### 3.1.1 A evidência que acompanha o dump

Assinatura e hash dizem que o arquivo é um arquivo bem formado. **Não dizem
de qual banco ele veio, quando, nem se restaura.** Um sidecar
`<dump>.attestation.json` responde às três, em dois passos que têm de
acontecer de verdade:

```bash
# logo depois do dump, contra a origem (somente leitura)
python -m database.recovery.backup_attestation capture \
  --dump openwec-prerecovery.dump --port 5433 --password "$DB_PASSWORD"

# depois de restaurar numa instancia descartavel, contra ela
python -m database.recovery.backup_attestation verify-restore \
  --dump openwec-prerecovery.dump --port 5457 --password <senha descartavel>
```

`capture` grava o `system_identifier` da origem, o momento, as contagens das
7 tabelas que a recuperação toca, o estado da sessão 2226 e o sha256 do
arquivo. `verify-restore` **conta as mesmas tabelas na instância restaurada**
e registra se bateram, junto do cluster onde a verificação ocorreu —
verificar no próprio cluster de origem é recusado, porque não provaria nada.

`MEASURED`, a evidência do backup desta PR:

```
sha256              c097353ac42e8d40bb4ca3d86c022b270fb8e100a72ec7171a9017b23fcd3560
cluster_de_origem   7642299396037144610
capturado_em        2026-10-23T20:10:58+00:00
contagens           sessions 5.425 · cars 671.353 · results 671.353
                    result_drivers 469.457 · laps 2.034.223 · drivers 2.845 · teams 1.111
sessao 2226         results 3.108 · laps 1.554
restauracao         ok, cluster 7699958109218349090, divergencias {}
```

`api_key_requests` fica **fora** da comparação: ela anda sozinha enquanto a
API atende, e um backup não está velho porque uma requisição foi registrada
depois dele.

**A verificação não é decorativa.** `MEASURED`: a primeira tentativa de
`verify-restore` foi feita contra a cópia em 5456 e **falhou** —
`drivers 2.845 → 2.862` —, porque aquela cópia carrega as 17 linhas órfãs
que a reversão testada do §8.1 deixou para trás. Ela não é mais uma
restauração fiel daquele dump, e a evidência disse isso.

### 3.2 Verificar que restaura

`MEASURED`, numa instância nova e descartável:

```bash
docker volume create openwec_historical_rehearsal_data
docker run -d --name openwec-historical-rehearsal \
  -e POSTGRES_USER=openwec -e POSTGRES_PASSWORD=<senha descartavel> \
  -e POSTGRES_DB=openwec -p 127.0.0.1:5456:5432 \
  -v openwec_historical_rehearsal_data:/var/lib/postgresql/data \
  timescale/timescaledb:latest-pg16

# OBRIGATORIO antes do restore nesta imagem
docker exec openwec-historical-rehearsal psql -U openwec -d openwec \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

docker exec -i openwec-historical-rehearsal pg_restore -U openwec -d openwec \
  --no-owner --no-privileges < openwec-prerecovery.dump
```

`MEASURED`: **sem o `DROP SCHEMA`, o restore falha com 167 erros** e deixa o
banco praticamente vazio. A imagem já instala `timescaledb` no esquema
`public`, e o dump traz a sua própria `CREATE EXTENSION` junto dos objetos do
catálogo da extensão; os dois colidem. Com o `DROP SCHEMA`, o restore termina
com **zero erros**, e as 14 tabelas de `public` ficam com contagens
**idênticas** às da origem.

---

## 4. As dez portas

Todas têm de abrir, e **nenhuma é uma flag de autorização**.

| # | porta |
|---:|---|
| 1 | a sessão pedida é 2226 |
| 2 | o backup existe, é `pg_dump -Fc`, **e a evidência ao lado dele prova**: o hash descreve aquele arquivo, foi capturado deste cluster dentro da janela de idade, teve a restauração verificada **em outro cluster**, e as contagens ainda batem com o alvo |
| 3 | o cluster declarado em `--expect-cluster` é o que `pg_control_system()` reporta |
| 4 | a identidade da sessão no banco é a ensaiada |
| 5 | a fonte tem o sha256 ensaiado |
| 6 | o estado da sessão é **contaminada** ou **já recuperada**; qualquer outro para |
| 7 | o plano é o pareamento completo de 28 carros |
| 8 | nada fora da sessão referencia um `car` a remover |
| 9 | o advisory lock está livre — duas execuções não se sobrepõem |
| 10 | **o operador digita**, no teclado, o número da sessão e o `system_identifier` do cluster, e os dois batem com a realidade |

`DERIVED`: a porta 10 é a que nenhuma variável abre antecipadamente. Uma
execução sem ninguém respondendo termina sem abrir escrita.

O recuperador de ensaio (`recover_classification`) **continua exigindo as
suas cinco portas** e não foi enfraquecido: este módulo não lê as variáveis
dele nem importa as suas funções de guarda, e um teste afirma as duas coisas.

---

## 5. Os comandos de uma execução autorizada

```bash
# 1. backup, imediatamente antes
docker exec openwec-db pg_dump -U openwec -d openwec -Fc \
  --no-owner --no-privileges > openwec-prerecovery.dump

# 1b. a evidencia: capturar na origem, provar a restauracao numa descartavel
python -m database.recovery.backup_attestation capture \
  --dump openwec-prerecovery.dump --port 5433 --password "$DB_PASSWORD"
#     ... restaurar o dump numa instancia descartavel (ver 3.2) ...
python -m database.recovery.backup_attestation verify-restore \
  --dump openwec-prerecovery.dump --port 5457 --password <senha descartavel>

# 2. a fonte, para fora de raw/
curl -s -o 05_Classification_Race.CSV \
  "https://lemanscup.alkamelsystems.com/Results/02_2018/01_PAUL%20RICARD/218_Michelin%20Le%20Mans%20Cup/201804141605_Race/Hour%202/05_Classification_Race.CSV"

# 3. a recuperação — pergunta duas coisas e espera o operador digitar
python -m database.recovery.historical_recovery \
  --session-id 2226 \
  --csv 05_Classification_Race.CSV \
  --backup openwec-prerecovery.dump \
  --expect-cluster 7642299396037144610 \
  --host 127.0.0.1 --port 5433 --dbname openwec --user openwec \
  --password "$DB_PASSWORD" \
  --report relatorio-2226.json
```

O comando imprime o plano, pergunta:

```
confirme o numero da sessao a recuperar:
confirme o system_identifier do cluster de destino:
```

e só então abre a escrita. Saída **0** com `COMMIT`, **2** com `BLOQUEADO`
ou `ROLLBACK`.

### 5.1 O que será modificado

| tabela | efeito |
|---|---|
| `results` | **−3.080**, **+1 a +28** linhas reescritas sobre os 28 `car_id` preservados |
| `cars` | **−3.080**; os 28 preservados recebem `vehicle` e `tires` onde estão NULL |
| `result_drivers` | **0 → ~54**, conforme a resolução de identidade |
| `drivers` | pode ganhar linhas para nomes ainda não existentes |
| `laps` | **nenhuma alteração** |
| qualquer outra sessão | **nenhuma alteração** |

---

## 6. Validação antes do commit

Dentro da mesma transação, depois da escrita:

- `results` = 28, `cars_in_results` = 28;
- `laps` = 1.554 e `cars_in_laps` = 28, **inalterados**;
- pelo menos um piloto vinculado;
- **nenhum** carro com voltas sem resultado;
- **nenhuma** linha `Classified` sem posição;
- digest das outras 5.424 sessões **idêntico** ao de antes;
- comparação campo a campo com o CSV: **420 campos, 0 divergências**.

Uma única falha derruba tudo por `ROLLBACK`.

---

## 7. O exercício integrado

`MEASURED`, contra a restauração descartável em `127.0.0.1:5456`, cluster
`7699952836394844194`, **com o mesmo mecanismo transacional da execução
real**.

### 7.1 Onze recusas, nenhuma escrita

| tentativa | resultado |
|---|---|
| sessão 1035 | `este caminho recupera apenas a sessao 2226` |
| `--expect-cluster` do histórico contra a cópia | `o cluster do alvo nao e o declarado` |
| backup inexistente | `backup nao encontrado` |
| backup que é um CSV | `nao e um arquivo pg_dump de formato custom (-Fc)` |
| **backup sem evidência ao lado** | `o backup nao tem evidencia: …attestation.json nao existe` |
| **evidência de outro cluster** | `o backup e do cluster 7642299396037144610, e o alvo e 7699952836394844194` |
| **evidência fora da janela de idade** | `o backup tem 0:00:42, acima do limite de 0 h` |
| fonte adulterada em 15 bytes | `a fonte nao e a que foi ensaiada: sha256 f80cc960…` |
| **nada digitado** | `confirmacao ausente` |
| sessão confirmada como 9999 | `confirmacao invalida` |
| cluster confirmado como o do histórico | `confirmacao invalida` |

`MEASURED`: saída 2 nas **onze**; depois delas `results` continua **3.108** e
o digest da sessão está **inalterado**.

### 7.2 Falha depois da escrita, antes do commit

`MEASURED`: com a escrita já aplicada dentro da transação, uma falha injetada
devolve saída 2 e deixa `results` = 3.108, `laps` = 1.554, digest da sessão
inalterado, outras sessões intactas. **O rollback cobre a escrita inteira.**

### 7.3 Execução autorizada

| | antes | depois |
|---|---:|---:|
| `results` da sessão | 3.108 | **28** |
| `result_drivers` | 0 | **54** |
| `laps` | 1.554 | **1.554** |
| `cars` com voltas | 28 | **28** |
| `cars` global | 671.353 | 668.273 |
| `results` global | 671.353 | 668.273 |

`MEASURED`: **420 campos comparados com o CSV, 0 divergências**;
invariantes **todas satisfeitas**; outras 5.424 sessões **intactas**;
transação **commit**.

### 7.4 Reexecução

`MEASURED`: com o **mesmo** backup, a segunda execução é **recusada** —

```
ROLLBACK: o backup nao corresponde ao alvo: o banco mudou desde o backup:
{'cars': (671353, 668273), 'results': (671353, 668273),
 'result_drivers': (469457, 469511)}
```

`DERIVED`: é a consequência correta da porta 2. Depois da recuperação, aquele
dump deixou de ser uma pré-imagem do alvo, e portanto deixou de ser um
caminho de volta. **Toda execução exige um backup novo**, inclusive a
segunda.

`MEASURED`: com backup novo, capturado e com restauração verificada, a
segunda execução é aceita com estado inicial **"já recuperada"**, remove
**0** `cars`, grava as mesmas 28 linhas, e o digest da sessão fica
**idêntico** — `638332c41df474dc6fa215f483a38925`.

---

## 8. Reversão

`ROLLBACK` não serve depois de um `COMMIT`. São dois caminhos, e o primeiro
foi **executado e verificado**.

### 8.1 Reversão dirigida — testada

`MEASURED`, na cópia descartável já recuperada:

```bash
# 1. o backup num banco auxiliar da mesma instancia
docker exec <instancia> psql -U openwec -d postgres \
  -c "CREATE DATABASE revert_src OWNER openwec;"
docker exec <instancia> psql -U openwec -d revert_src \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker exec -i <instancia> pg_restore -U openwec -d revert_src \
  --no-owner --no-privileges < openwec-prerecovery.dump

# 2. so as linhas da sessao
docker exec <instancia> psql -U openwec -d revert_src \
  -c "COPY (SELECT c.* FROM cars c WHERE c.id IN (SELECT car_id FROM results WHERE session_id=2226)) TO '/tmp/cars.csv' CSV" \
  -c "COPY (SELECT r.* FROM results r WHERE r.session_id=2226) TO '/tmp/results.csv' CSV" \
  -c "COPY (SELECT rd.* FROM result_drivers rd JOIN results r ON r.id=rd.result_id WHERE r.session_id=2226) TO '/tmp/result_drivers.csv' CSV"
```

```sql
BEGIN;
CREATE TEMP TABLE cars_backup (LIKE cars INCLUDING DEFAULTS);
CREATE TEMP TABLE results_backup (LIKE results INCLUDING DEFAULTS);
CREATE TEMP TABLE result_drivers_backup (LIKE result_drivers INCLUDING DEFAULTS);
\copy cars_backup FROM '/tmp/cars.csv' CSV
\copy results_backup FROM '/tmp/results.csv' CSV
\copy result_drivers_backup FROM '/tmp/result_drivers.csv' CSV

DELETE FROM results WHERE session_id = 2226;          -- result_drivers cai por cascade

INSERT INTO cars SELECT * FROM cars_backup
  ON CONFLICT (id) DO UPDATE SET
    number = EXCLUDED.number, team_id = EXCLUDED.team_id,
    vehicle = EXCLUDED.vehicle, manufacturer = EXCLUDED.manufacturer,
    car_class = EXCLUDED.car_class, car_group = EXCLUDED.car_group,
    tires = EXCLUDED.tires, imsa_car_id = EXCLUDED.imsa_car_id,
    imsa_team_id = EXCLUDED.imsa_team_id, tyre_supplier = EXCLUDED.tyre_supplier;

INSERT INTO results SELECT * FROM results_backup;
INSERT INTO result_drivers SELECT * FROM result_drivers_backup;

SELECT setval('cars_id_seq', GREATEST((SELECT max(id) FROM cars), 1));
SELECT setval('results_id_seq', GREATEST((SELECT max(id) FROM results), 1));
SELECT setval('result_drivers_id_seq', GREATEST((SELECT max(id) FROM result_drivers), 1));
COMMIT;
```

`MEASURED`, resultado da reversão testada:

| | depois da reversão | no backup |
|---|---:|---:|
| `results` da sessão | **3.108** | 3.108 |
| `cars_in_results` | **3.108** | 3.108 |
| `result_drivers` | **0** | 0 |
| `laps` | **1.554** | 1.554 |
| `cars` global | **671.353** | 671.353 |
| `results` global | **671.353** | 671.353 |
| digest da sessão | **`ece5e88ef375b242934b719a6b23b99b`** | `ece5e88ef375b242934b719a6b23b99b` |

`MEASURED`: os 28 `cars` preservados voltaram a ter `vehicle` NULL — as
colunas que a recuperação preenchera foram revertidas também. O `ON CONFLICT`
é o que faz isso: ele reescreve o carro preservado com os valores do backup,
não apenas reinsere os removidos.

**Resíduo, `MEASURED`:** **17 linhas de `drivers`** criadas pela recuperação
permanecem, sem nenhuma referência depois da reversão. `teams` não muda.
`DERIVED`: apagá-las é opcional e mexe em identidade de piloto; a opção
conservadora é deixá-las. Para contá-las:

```sql
SELECT count(*) FROM drivers d
 WHERE NOT EXISTS (SELECT 1 FROM result_drivers rd WHERE rd.driver_id = d.id)
   AND NOT EXISTS (SELECT 1 FROM laps l WHERE l.driver_id = d.id);
```

### 8.2 Restauração completa — alternativa

Derrubar e restaurar o banco inteiro a partir do mesmo dump. É exato se
nenhuma outra escrita ocorreu entre o backup e a reversão. `UNKNOWN`: se a
API estiver atendendo, `api_key_requests` pode ter recebido linhas no
intervalo, e elas se perderiam. Não foi executado.

---

## 9. O que esta PR não faz

1. **Não escreve no banco histórico.** Toda medição nele foi somente leitura.
2. **Não é autorização.** A porta 10 exige uma pessoa digitando.
3. **Não recupera outra sessão.** Só a 2226, e não há modo de lote.
4. **Não enfraquece o recuperador de ensaio.**
5. **Não toca** em `raw/`, migrations, reingestão, consolidação de `car_id`,
   fusão de pilotos, IMSA ou Road to Le Mans 2017.
6. **Não corrige** os 19.227 tempos nem os 1.922 gaps do acervo.

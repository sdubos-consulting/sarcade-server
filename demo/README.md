# Démonstration SARCADE

## Prérequis Windows
- Docker Desktop démarré
- Git

## Démarrer
Dans PowerShell :
```powershell
git clone https://github.com/sdubos-consulting/sarcade-server.git
cd sarcade-server\demo
.\start-demo.ps1
```

Puis ouvrir : http://localhost:8000/docs

## Simuler quatre opérateurs
```powershell
docker compose -f docker-compose.demo.yml --profile simulate run --rm simulator
```
Le simulateur crée un événement et envoie 20 positions pour chacun des quatre opérateurs.

## Test automatique
```powershell
docker compose -f docker-compose.demo.yml run --rm --entrypoint sh simulator -c "pip install -q httpx && python /demo/verify.py"
```
Résultat attendu : PASS avec une opération acceptée, sa retransmission détectée comme duplicate, un seul message et une entrée de main courante.

## Arrêter
```powershell
.\stop-demo.ps1
```

## Simuler une coupure
```powershell
docker compose -f docker-compose.demo.yml stop server
```
Puis redémarrer :
```powershell
docker compose -f docker-compose.demo.yml start server
```

Le véritable test Outbox est réalisé avec SARCADE App. Ce package valide d'abord Server/PostGIS, le simulateur GPS et l'idempotence.


## Offline First V0.1 recipe

With SARCADE App open on an existing event, run from PowerShell:

```powershell
.\offline-v01.ps1 -EventId "<EVENT_ID>"
```

The recipe stops only the API server (PostgreSQL stays running), asks the operator to create a message containing `OFFLINE-V01`, restarts the server, waits for Outbox synchronization, then verifies that the offline-created message reached the server without a duplicate ID. Do not use `docker compose down -v` during this test because it destroys the demo database volume.

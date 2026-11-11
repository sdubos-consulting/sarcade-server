# Implémentation serveur de synchronisation v0.1

Le serveur doit accepter les SyncOperation du dépôt sarcade-protocol et répondre par SyncResult.

## Statuts
- accepted
- duplicate
- conflict
- rejected

## Garanties
- operation_id idempotent
- journal des opérations reçues
- server_time attribué à réception
- curseur de synchronisation monotone côté serveur
- aucune création en double lors d'une retransmission
- conflit explicite lorsque base_version est obsolète

## Première implémentation
Créer un service SyncService indépendant du transport HTTP/WebSocket/MQTT afin de pouvoir réutiliser les mêmes règles via SARCADE Gateway.

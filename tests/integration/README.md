# Tests d'intégration

Scénario cible MVP :
1. démarrer PostgreSQL/PostGIS et SARCADE Server
2. créer un événement
3. simuler deux clients
4. client A produit positions/messages pendant une indisponibilité serveur
5. rétablir le serveur
6. rejouer l'Outbox
7. vérifier absence de doublons
8. vérifier réception WebSocket par B
9. produire ACK received/read
10. vérifier main courante

Les tests unitaires couvrent immédiatement l'idempotence du journal. Le scénario Docker de bout en bout sera activé dans la CI après stabilisation des migrations et du bootstrap d'événement.

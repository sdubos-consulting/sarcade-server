# Architecture SARCADE Server

## Flux principal
Client SARCADE → API → services métier → PostgreSQL/PostGIS

Les événements temps réel utilisent WebSocket. MQTT est prévu pour les échanges asynchrones, la télémétrie et l'intégration des SARCADE Gateways.

## Principes
- offline-first côté clients
- API indépendante des transports radio
- données géographiques dans PostGIS
- composants conteneurisés
- auto-hébergement
- fonctionnement sur LAN sans Internet
- architecture extensible par connecteurs/plugins

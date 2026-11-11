# Modèle métier Server v0.1

Le serveur implémente les contrats publiés dans sarcade-protocol.

## Agrégats initiaux
Organization → Event → Team → Membership
User → Membership
User/Team → Device → Position
Event → Message → ACK
Event → POI
Gateway → transports

## Principes
- UUID/identifiants opaques
- PostGIS pour les données géographiques
- positions, messages et ACK append-only
- version optimiste sur les objets modifiables
- tombstones pour les suppressions synchronisées
- authentification séparée du profil User

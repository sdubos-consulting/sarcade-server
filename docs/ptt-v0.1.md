# PTT natif, talkie-walkie (V0.1)

Carte « PTT natif (talkie-walkie) » du Kanban ; décisions du porteur du 6 novembre 2026 (1 h 15) : **WebRTC avec LiveKit** (logiciel libre, licence Apache 2.0) installé sur le NAS, et **un port UDP ouvert sur la box** vers le NAS.

## Principe
- La voix passe **entre les appareils et LiveKit**, jamais par le serveur SARCADE, et **n'est jamais enregistrée**.
- Le serveur SARCADE donne seulement l'accès : un jeton court (6 h) par appareil et par canal, signé avec la clé LiveKit du NAS.
- Canaux : « Général » (tous les opérateurs de l'événement) et un canal par groupe de l'événement (ses membres, ou tout le monde pour un groupe ouvert). Groupe en écoute seule : seuls ses émetteurs, ses responsables et le PCO parlent. Groupe archivé : pas de canal.

## API
| Point d'accès | Rôle |
|---|---|
| `GET /api/v0.1/events/{id}/ptt?device_id=` | `available` (LiveKit réglé ou non) et canaux de l'appareil, avec `can_talk` |
| `POST /api/v0.1/events/{id}/ptt/token` | `{device_id, channel, name}` → `{url, room, token, can_talk}` ; 503 sans LiveKit, 403 pour un canal refusé ou un autre appareil |

## Mise en service sur le NAS (par le porteur)
1. Choisir une clé et un secret LiveKit (le secret : au moins 32 caractères aléatoires) et les écrire **dans le `.env` du NAS seulement**, jamais ailleurs : `SARCADE_LIVEKIT_API_KEY`, `SARCADE_LIVEKIT_API_SECRET`.
2. Adresse que les appareils utiliseront pour la signalisation (WebSocket, passe par le tunnel Cloudflare comme le reste) : `SARCADE_LIVEKIT_URL=wss://ptt.sarcade.org` ; dans le tunnel Cloudflare, router `ptt.sarcade.org` vers `http://<NAS>:7880`, **sans** Cloudflare Access sur ce nom (le jeton LiveKit protège l'accès).
3. **Box** : rediriger le port **UDP 7882** (voix) et, en secours, **TCP 7881** vers le NAS. Le tunnel Cloudflare ne transporte pas l'UDP.
4. Démarrer LiveKit et reconstruire le serveur :

```sh
cd /volume1/docker/sarcade-server
docker compose --profile ptt up -d livekit
docker compose up -d server
docker compose logs --tail 20 livekit
```

Résultat attendu : dans les journaux de LiveKit, une ligne `starting LiveKit server` avec `rtc.portUDP: 7882`, puis aucune erreur ; `https://app.sarcade.org/api/v0.1/events/<événement>/ptt?device_id=<appareil>` répond `"available": true`.

## Limites de la V0.1
- iPhone : l'écoute continue écran éteint repose sur le mode audio en arrière-plan ; le cadre « Push to Talk » d'Apple viendra si l'usage le demande.
- Montre, pédale USB et passerelle vers une radio VHF/UHF : plus tard (carte du Kanban).

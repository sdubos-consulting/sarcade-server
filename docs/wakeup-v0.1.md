# Réveil des appareils — v0.1

Note fonctionnelle : « Réveil des appareils (notifications application fermée) », validée le 28 octobre 2026.

## Principe
À chaque nouveau message, le serveur envoie aux appareils concernés un **signal sans contenu** :
`{"type": "wake", "kind": "message", "event_id": "…", "priority": "immediate"}`.
Le signal porte aussi `tag`, la **clé de notification** (30 octobre 2026, « Détresse, une seule notification ») : l'identifiant du message, ou pour une détresse (immédiat « 🆘 » à tous) et sa « Fin de détresse » une clé de l'expéditeur `sos-xxxxxxxx` (FNV-1a de `événement|appareil`, sans indicatif). L'application donne la même clé à sa notification : une seule notification par message quel que soit le chemin (temps réel, synchronisation, réveil), la fin de détresse remplace la détresse (Android : même identifiant ; iPhone : `apns-collapse-id` ; web : même `tag`).
L'appareil se réveille, synchronise par SARCADE et affiche sa propre notification. Ni Google, ni Apple, ni les services Web Push ne voient le texte, l'expéditeur ou une position.

| Plateforme | Service | Réglage serveur |
|---|---|---|
| Web / PWA | Web Push (VAPID, chiffrement aes128gcm) | aucun : clé créée par le serveur (table `wake_keys`) |
| Android | Firebase Cloud Messaging, API HTTP v1, message de données | `SARCADE_FCM_SERVICE_ACCOUNT` (JSON du compte de service ou chemin) |
| iPhone | APNs | plus tard, avec la signature iOS |

## Qui est réveillé
- Message direct (uniquement des identifiants d'appareils) : ces destinataires.
- Diffusion ou groupe : tous les appareils de l'événement, l'appareil décide quoi afficher.
- Jamais l'expéditeur. Immédiat et urgent : à chaque message. Routine : au plus un réveil par minute et par appareil.

## API
- `GET /api/v0.1/wakeup/config` → `webpush_public_key`, `fcm`, `apns`.
- `PUT /api/v0.1/events/{event}/devices/{device}/wakeup` `{platform: fcm|webpush|apns, token}` : inscription ou mise à jour (une par plateforme).
- `DELETE /api/v0.1/events/{event}/devices/{device}/wakeup[?platform=]` : retrait.
- `POST /api/v0.1/events/{event}/devices/{device}/wakeup/test` : « Tester le réveil » → `{sent, failed, removed}`.
- `GET /api/v0.1/events/{event}/wakeup/devices` : appareils réveillables (PCO).

Jetons refusés par le service (404, 410, `UNREGISTERED`) supprimés ; jetons non revus depuis 30 jours supprimés.

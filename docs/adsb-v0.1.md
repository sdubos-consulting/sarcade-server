# ADS-B v0.1 — aéronefs sur la carte (module ADRASEC)

Étape 1 de la carte « ADS-B : aéronefs sur la carte » (1er novembre 2026) :
source **OpenSky Network** par Internet. Fonction du module ADRASEC : un
événement sans ce module refuse les routes (`409 module_inactive:adrasec`).

## Réglage du serveur (`.env` du NAS)

| Variable | Rôle |
|---|---|
| `SARCADE_OPENSKY_CLIENT_ID` | client API OpenSky (OAuth2, client credentials) |
| `SARCADE_OPENSKY_CLIENT_SECRET` | secret du client : **seulement dans le `.env`** du NAS |
| `SARCADE_ADSB_INTERVAL_S` | intervalle des requêtes, 30 s au moins (défaut 30) |

Sans identifiants, rien n'est demandé à OpenSky et la couche reste vide.

## Quota

Compte Standard : 4 000 crédits par jour. Une requête `/states/all` sur une
zone de moins de 25 degrés carrés coûte 1 crédit ; le serveur borne la zone
à 5° × 5°. Le serveur n'interroge OpenSky **que si une application affiche la
couche « Aéronefs »** : environ 2 900 crédits par jour pour un événement
suivi sans interruption, aucun sinon. Quota atteint : pause du délai indiqué
par OpenSky.

## API

- `POST /api/v0.1/events/{id}/adsb/watch` : `{device_id, lamin, lomin, lamax, lomax, on}` ;
  l'application qui affiche la couche le rappelle chaque minute (expiration
  120 s). Réponse : dernière liste connue.
- `GET /api/v0.1/events/{id}/adsb` : `{time, aircraft:[…]}`.
- `GET /api/v0.1/events/{id}/adsb/{icao24}/track` : trace connue de l'aéronef (30 min au plus), pour l'aéronef sélectionné.
- `GET /api/v0.1/adsb/status` : état de la liaison, requêtes du jour, pause de quota.
- Temps réel : `adsb.updated` (liste complète), `adsb.emergency` (une fois par
  aéronef et code).

Aéronef : `icao24`, `callsign`, `country`, `lat`, `lon`, `alt_m`, `alt_ft`,
`on_ground`, `speed_kt`, `track_deg`, `vertical_rate_mps`, `squawk`,
`emergency` (`emergency` 7700, `radio_failure` 7600,
`unlawful_interference` 7500), `rotorcraft`, `last_contact`, `trail`
(10 dernières positions).

Codes 7700 / 7600 / 7500 : inscrits une fois à la main courante (alerte PCO).

## Limites

Les aéronefs sans ADS-B (militaires, État, ULM non équipés) n'apparaissent
pas. Couverture selon les récepteurs bénévoles d'OpenSky. Usage non
commercial : à vérifier avant SARCADE Pro.

## Smart TV

La couche « Aéronefs » de la TV suit les réglages de l'application envoyés avec la vue
(`layers.aircraft`, `aircraft_min_ft`, `aircraft_max_ft`, `aircraft_ground`, `aircraft_labels`).
Un écran qui l'affiche compte comme une application qui regarde (zone d'environ 50 km autour de sa vue).
Couleur selon l'altitude, étiquette seulement en cas d'urgence (ou indicatifs demandés), bandeau rouge pour 7700 / 7600 / 7500.

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
- `GET /api/v0.1/events/{id}/adsb` : `{time, now, aircraft:[…]}` (`now` : horloge du serveur, secondes epoch).
- `GET /api/v0.1/events/{id}/adsb/{icao24}/track` : trace connue de l'aéronef (30 min au plus), pour l'aéronef sélectionné.
- `GET /api/v0.1/adsb/status` : état de la liaison, requêtes du jour, pause de quota.
- Temps réel : `adsb.updated` (liste complète et `now`), `adsb.emergency` (une fois par
  aéronef et code).

Aéronef : `icao24`, `callsign`, `country`, `lat`, `lon`, `alt_m`, `alt_ft`,
`on_ground`, `speed_kt`, `track_deg`, `vertical_rate_mps`, `squawk`,
`emergency` (`emergency` 7700, `radio_failure` 7600,
`unlawful_interference` 7500), `category` (catégorie OpenSky, `null` si
absente ; 0 et 1 = non transmise), `rotorcraft` (catégorie 8), `last_contact`,
`position_time` (heure OpenSky de la position, à défaut `last_contact`),
`trail` (10 dernières positions, gardé pour les anciennes applications : plus
dessiné).

Position estimée (1er novembre 2026, 11 h 55) : l'application et la TV font
avancer l'avion à partir de `position_time`, `speed_kt` et `track_deg`, avec
l'horloge du serveur (`now`), 60 s au plus ; au-delà, icône grisée « position
ancienne ». Le centre de l'icône est la position.

Codes 7700 / 7600 / 7500 : inscrits une fois à la main courante (alerte PCO).

## Limites

Les aéronefs sans ADS-B (militaires, État, ULM non équipés) n'apparaissent
pas. Couverture selon les récepteurs bénévoles d'OpenSky. Usage non
commercial : à vérifier avant SARCADE Pro.

## Smart TV

La couche « Aéronefs » de la TV suit les réglages de l'application envoyés avec la vue
(`layers.aircraft` puis, lot 17, 1er novembre 2026) :

| Clé | Rôle | Défaut |
|---|---|---|
| `aircraft_low_ft`, `aircraft_high_ft` | seuils entre Bas, Moyen et Haut (0 à 45 000 ft, `low ≤ high`) | 3 000, 10 000 |
| `aircraft_bands` | `{low, mid, high}` : tranches affichées | Bas et Moyen |
| `aircraft_ground` | avions au sol | non |
| `aircraft_labels` | indicatifs | non |
| `aircraft_followed` | adresses OACI des avions suivis (20 au plus) | aucun |

Une vue envoyée avant les tranches (`aircraft_min_ft`, `aircraft_max_ft` de
#181) est convertie : une tranche est affichée si elle rencontre l'intervalle
(0 → 10 000 ft donne Bas et Moyen). Un aéronef sans altitude suit Bas ; une
urgence et un avion suivi sont toujours affichés.

État de la TV : `aircraft` (sans `trail`), `aircraft_now` (horloge du
serveur), `aircraft_tracks` (`{icao24: [[lat, lon], …]}`, route de 30 min des
avions suivis encore reçus). Un écran qui affiche la couche compte comme une
application qui regarde (zone d'environ 50 km autour de sa vue).

Page TV : même palette que l'application (Bas orange `#F57C00`, Moyen bleu
`#1565C0`, Haut gris-bleu `#607D8B`, sol ou altitude inconnue gris `#78909C`,
urgence rouge `#D32F2F`, avion suivi entouré de violet `#8E24AA`), icône
selon la catégorie (hélicoptère non orienté), petite légende des couleurs en
haut à gauche de la carte, aucune trace sauf les routes des avions suivis,
étiquette seulement en cas d'urgence (ou indicatifs demandés), bandeau rouge
pour 7700 / 7600 / 7500.

# Opérations d'événement (V0.1)

Réglages décidés par le PCO, dernier contact des appareils et fin d'événement.
Spécifications : notes « Synchronisation client-serveur » et « Suivi de position » du vault.

## Réglages de l'événement

`GET /api/v0.1/events/{event_id}` renvoie l'événement avec ses réglages (valeurs par défaut pour les événements existants).

`PATCH /api/v0.1/events/{event_id}/settings` (corps : `actor_id`, `settings`) modifie un ou plusieurs réglages :

| Réglage | Défaut | Valeurs |
|---|---|---|
| `low_bandwidth` | `false` | mode liaison faible imposé par le PCO |
| `low_bandwidth_interval_s` | 60 | envoi groupé, 15 à 3600 s |
| `sync_alert_minutes` | 5 | alerte des éléments non partis et appareils en retard, 1 à 120 min |
| `tracking_required` | `false` | suivi de position imposé par le PCO |
| `tracking_default_interval_s` | 30 | 10, 30, 60, 120, 300 ou 600 s |
| `tracking_min_interval_s` / `tracking_max_interval_s` | 10 / 600 | bornes fixées par le PCO |

| `zone_modes` | `{}` | zone probable par signal recherché : clé du signal (`search_key` : apostrophes unifiées, accents et casse ignorés) → `multi` (Plusieurs balises) ou `single` (Une seule balise) ; absent = `auto` |
| `sater` | `false` (`true` à la création d'un événement de type `sater` avec le module ADRASEC) | mission ou exercice SATER : l'application affiche le bouton « Signal entendu ici » (lot 24). Module ADRASEC requis (422 `module_inactive:adrasec` sinon) ; remis à `false` quand le module est retiré |
| `car_quick_messages` | « Bien reçu », « J'arrive », « Besoin d'aide » (urgent), « Retard » | messages types envoyés d'un appui depuis la voiture (lot 24) : liste de **1 à 4** objets `{"text", "priority"}`, `text` de 1 à 40 caractères (espaces réduits, sans doublon, casse ignorée), `priority` = `routine` (défaut) ou `urgent` (jamais détresse) ; une chaîne seule vaut `{"text": …, "priority": "routine"}` ; `null` remet la liste par défaut ; 422 `invalid_value:car_quick_messages` sinon. Distinct de `quick_messages` (messages prédéfinis du téléphone, 20 au plus) |

`PUT /api/v0.1/events/{event_id}/zone-mode` (corps : `actor_id`, `search`, `mode` = `auto`, `multi` ou `single`) change le mode d'**un** signal ; ouvert à **tout opérateur** de l'événement (pas de droit PCO), depuis la fenêtre d'une zone ou la fiche d'un relevé. Main courante « Zone probable « … » : Plusieurs balises », diffusion `event.settings.updated` ; 422 si le mode est inconnu, 409 après la fin de l'événement.

Chaque changement est inscrit dans la main courante et diffusé (`event.settings.updated`). Les réglages sont figés après la fin de l'événement (409).

## Dernier contact des appareils

`POST /api/v0.1/events/{event_id}/devices/{device_id}/heartbeat` : contact périodique du client (libellé, plateforme, version, batterie, éléments en attente, suivi actif et fréquence souhaitée). La réponse indique ce que l'appareil doit appliquer : réglages, fréquence de suivi ramenée dans les bornes du PCO, suivi imposé, fin d'événement.

Chaque position reçue (API ou synchronisation) met aussi à jour le dernier contact, la dernière position et la batterie.

`GET /api/v0.1/events/{event_id}/devices` : vue PCO (liste des opérateurs), appareils en retard d'abord (`late` au-delà de `sync_alert_minutes`, `unknown`, `ok`). Chaque appareil porte `platform`, `app_version` et `last_seen` (= `last_contact_at`) ; même objet dans la diffusion `device.updated`.

### Main courante des appareils

- `device_version` : « <libellé> : SARCADE <ancienne> → <nouvelle> (<plateforme>) » quand la version envoyée par le heartbeat change (pas au premier envoi).
- `contact` : « Arrivée dans l'événement » au premier contact (heartbeat ou position) ; « Perte de contact » quand l'appareil n'a pas été vu depuis plus de `sync_alert_minutes` (balayage toutes les 60 s, événements ouverts) ; « Contact rétabli (après X min) » à son retour. Une ligne par changement d'état : l'état d'un appareil est sa dernière ligne `contact`.
- Ces deux types ne sont jamais affichés sur une TV.

## Statut de l'opérateur (lot 24, 3 novembre 2026)

Carte « Android Auto : statut en un appui ». Un statut par appareil de l'événement, choisi d'un appui (voiture, téléphone, PC), gardé jusqu'au suivant.

| Valeur | Libellé | Couleur de la pastille (TV) |
|---|---|---|
| `en_route` | En route | `#FFB020` |
| `sur_place` | Sur place | `#4ADE80` |
| `disponible` | Disponible | `#3DD6F5` |
| `pause` | Pause | `#9AA3AD` |
| `null` | aucun statut | — |

- **Envoi immédiat** : `PUT /api/v0.1/events/{event_id}/devices/{device_id}/status`, corps `{"status": "en_route" | "sur_place" | "disponible" | "pause" | null, "at": "<ISO 8601, facultatif>", "actor_id": "<facultatif>"}`. `at` = heure du choix sur l'appareil (absent : heure du serveur ; plus de 5 min dans le futur : ramenée à l'heure du serveur). Réponse `{"event_id", "device_id", "operator_status", "operator_status_at", "changed"}`. 422 `invalid_status` / `invalid_time`, 404 `event_not_found`.
- **Envoi hors connexion** (file d'envoi) : synchronisation `object_type = "operator_status"`, `action = "update"`, `object_id` = `device_id`, charge `{"device_id", "status", "at", "actor_id"?}`. Statuts : `accepted` ; `conflict` si un statut plus récent est déjà connu ; `rejected` si mal formé (statut inconnu, `object_id` ≠ `device_id`, autre action). Le flux `…/sync/changes` porte l'état résultant (`{"event_id", "device_id", "operator_status", "operator_status_at"}`).
- **Règle** : le plus récent (`at`) gagne ; un statut parti en retard ne remplace jamais un statut plus récent. Le même statut renvoyé ne change rien (son heure reste celle du premier choix : « depuis 8 h 10 »).
- **Temps réel** : `operator.status` avec `{"event_id", "device_id", "operator_status", "operator_status_at"}`, seulement quand le statut change.
- **Lecture** : `GET /api/v0.1/events/{event_id}/operator-statuses` (appareils qui ont un statut) ; `GET …/positions/latest` et `GET …/devices` (et la diffusion `device.updated`) portent aussi `operator_status` et `operator_status_at` (à ne pas confondre avec `status` des appareils, qui reste l'état du contact : `ok`, `late`, `unknown`).
- **Main courante** (`kind = "operator_status"`) : « Statut de <libellé, indicatif ou appareil> : En route », « Statut de … retiré ».
- **TV** : pastille de couleur devant le nom de l'opérateur (`positions[].operator_status` de l'état).
- Stockage : colonnes `devices.operator_status` et `devices.operator_status_at` (migration `0028`).

Vérification : `tests/test_operator_status.py`, `demo/verify_operator_status.py` (CI).

## Signaux par défaut du relevé

`GET /api/v0.1/signals/defaults` → `{"signals": [{"label", "frequency_mhz", "exercise", "hidden"}, …]}` (liste initiale : 121,5 MHz détresse réelle, 121,375 MHz exercice, 406 MHz Cospas-Sarsat).
`PUT /api/v0.1/signals/defaults` (même corps) : administrateur seulement (e-mail vérifié par Cloudflare Access, sinon 403) ; 20 signaux au plus, libellé de 1 à 60 caractères, fréquence de 1 à 10 000 MHz, ordre conservé (422 sinon).

## Positions

Les positions acceptent `battery_pct` (0 à 100) et portent une `source` (`device` pour l'instant, `aprs` ensuite).

## Fin de l'événement

`POST /api/v0.1/events/{event_id}/close` (corps : `actor_id`) : statut `closed`, heure de fin, entrée de main courante, diffusion `event.closed`. Les clients arrêtent alors le suivi de position. Idempotent.

## Limite

L'authentification (ADR-002) n'est pas encore en place : `actor_id` et `device_id` sont déclarés par le client.

Vérification : `demo/verify_event_operations.py`, exécuté par la CI.

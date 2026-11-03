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
- **Main courante** (`kind = "operator_status"`) : « Statut de <libellé, indicatif ou appareil> : En route », « Statut de … retiré » ; quand le serveur le pose (lot 27), suivi de la raison : « Statut de … : En route, ordre accepté ».
- **TV** : pastille de couleur devant le nom de l'opérateur (`positions[].operator_status` de l'état).
- Stockage : colonnes `devices.operator_status` et `devices.operator_status_at` (migration `0028`).

Vérification : `tests/test_operator_status.py`, `demo/verify_operator_status.py` (CI).

### « Disponible » par défaut (lot 27, 3 novembre 2026)

Carte « Statut de l'opérateur : « Disponible » par défaut » (choix validés le 3 novembre 2026, 17 h 21).

- Au **heartbeat** d'un appareil qui n'a **jamais eu** de statut dans l'événement (`operator_status_at` vide), le serveur pose **`disponible`** (une seule fois) : main courante « Statut de … : Disponible, par défaut », diffusion `operator.status` (et `device.updated`), flux `…/sync/changes`.
- **Jamais d'écrasement** : un statut déjà choisi, « Aucun statut » compris, n'est jamais remplacé (relance de l'application, nouvel heartbeat).
- **Changement d'appareil d'une même personne** (même indicatif sans SSID, réglé dans Paramètres › Profil et envoyé par le heartbeat) : le nouvel appareil reprend le statut le plus récent de la personne (« Statut de … : Pause, repris de F4ABC-9 ») ; si elle a choisi « Aucun statut », rien n'est posé.
- **Hors connexion** : rien n'est posé tant que le heartbeat annonce des opérations en attente (`pending_count` > 0) : un statut choisi hors connexion est peut-être dans la file d'envoi.
- Rien dans un événement clos.

### Statut qui suit les ordres de déplacement (lot 27, 3 novembre 2026)

Carte « Ordres de déplacement : statut mis à jour automatiquement ». La règle est **sur le serveur**, appliquée quand l'ordre (`move_order` synchronisé) change d'état : la même pour le téléphone, le PC et la voiture, quelle que soit l'heure d'arrivée de la réponse (file d'envoi).

| Événement de l'ordre | Statut posé | Main courante |
|---|---|---|
| `accepted` | `en_route` | « Statut de … : En route, ordre accepté » |
| `arrived` (bouton, ou arrivée détectée confirmée) | `sur_place` | « …: Sur place, arrivée sur zone » |
| `refused` ou `cancelled` | statut d'avant l'ordre | « …: Disponible, ordre annulé » (« … retiré, ordre refusé » s'il n'y en avait pas) |
| « Terminé, disponible » (`finished_at` posé sur un ordre `arrived`) | `disponible` | « …: Disponible, mission terminée » |

- **Appareils concernés** : l'appareil visé (`target_id`) et, quand la réponse vient d'un autre terminal de la même personne (lot 20), ce terminal aussi. Un appareil jamais vu dans l'événement n'a pas de statut posé.
- **Statut d'avant** : mémorisé sur l'ordre à la première réponse (`operator_status_before`, `{device_id: statut | null}`), gardé par le serveur (les clients ne l'envoient pas) ; effacé quand l'ordre est refusé, annulé ou terminé. Un refus direct (sans acceptation) ne change rien.
- **Changement manuel** : le statut automatique porte l'heure de la réponse (`updated_at` de l'ordre) ; la règle « le plus récent gagne » garde un statut choisi à la main après elle, jusqu'au prochain événement de l'ordre. Une note modifiée par le PCO n'est pas un événement de l'ordre.
- **`finished_at`** : champ synchronisé de l'ordre, accepté seulement quand il est `arrived` (ignoré sinon), gardé par le serveur tant qu'il le reste. L'ordre reste `arrived` (anciennes applications inchangées).
- Diffusion `operator.status` par appareil changé, flux `…/sync/changes` (`operator_status`).
- Non traité : un ordre **retiré de la carte** (suppression) ne change pas le statut.

Vérification : `tests/test_order_status.py`, `demo/verify_order_status.py` (CI).

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

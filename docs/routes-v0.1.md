# Routes avec points de passage (V0.1)

Spécification : note « Route avec points de passage » du vault (décisions du 10 octobre 2026).

## Objets synchronisés (ADR-001)
| `object_type` | Contenu |
|---|---|
| `route` | nom, couleur, mode de tracé par défaut (`straight` ou `paths`), profil (`foot` ou `vehicle`), statut, **ordre des points** (`point_order`), affectation (`assigned_team_id` ou `assigned_group_id`) |
| `route_waypoint` | **un objet par point** : nom, type (départ, passage, point de contrôle, ravitaillement, arrivée), position, commentaire, heure prévue, **rayon d'approche (50 m par défaut)**, mode du tronçon qui y arrive et son tracé calculé (`leg_geometry`, `leg_needs_routing`) |
| `route_passage` | passage d'une équipe à un point, `auto` ou `manual`, annulable |

Chaque point étant un objet distinct, deux personnes qui modifient deux points différents ne s'écrasent pas.

## Règles
- **Route affectée** : modifiable (route et points) seulement par son auteur et le PCO ; sinon `rejected`.
- **Équipe prévenue** : toute modification d'une route affectée (affectation, point ajouté, déplacé, modifié, supprimé, ordre changé) envoie **un message urgent** au groupe de l'équipe, un seul par lot de synchronisation, avec demande d'accusé de réception.
- **Ordre modifié en même temps** : le client envoie l'ordre sur lequel il a travaillé (`base_point_order`) ; la dernière version gagne, et si l'ordre avait changé entre-temps, le PCO est prévenu (main courante et `route.order_conflict`).
- Main courante : création, affectation, suppression de route, passages et annulations, signalements.

## API
| Point d'accès | Rôle |
|---|---|
| `GET /api/v0.1/events/{id}/routes` | routes avec points ordonnés, longueur de chaque tronçon (le long du tracé calculé s'il existe), longueur cumulée et totale, passages |
| `GET /api/v0.1/events/{id}/routes/{route_id}.gpx` | export GPX : route (points nommés) et trace pour les tronçons par les chemins |
| `POST /api/v0.1/events/{id}/routes/{route_id}/reports` | l'équipe signale un point impraticable (main courante, `route.report`) ; le PCO décide |

Le calcul des tronçons par les chemins (Valhalla) arrive avec la navigation niveau 2.

Vérification : `tests/test_routes.py`, `demo/verify_routes.py` (CI).

## Ordres de déplacement : heure d'arrivée estimée (lot 24, 3 novembre 2026)

Rappel : un ordre de déplacement est l'objet synchronisé `move_order` (`target_id`, `status` = `sent` | `accepted` | `refused` | `arrived` | `cancelled`, `destination {lat, lon, label}`, `note`), donné par le PCO, auquel l'opérateur répond ; liste : `GET /api/v0.1/events/{id}/move-orders` ; temps réel `move_order.upserted` / `move_order.deleted`.

Carte « Android Auto : heure d'arrivée estimée envoyée au PCO » :

- `PUT /api/v0.1/events/{id}/move-orders/{order_id}/eta`, corps `{"device_id": "<terminal qui envoie>", "eta": "<ISO 8601>" | null, "remaining_m": <mètres, facultatif>}`.
- Droits : l'opérateur visé (`target_id`), un autre terminal de la même personne (même indicatif sans SSID, comme les réponses, lot 20) ou le PCO ; sinon 403 `not_the_target`.
- Seulement pour un ordre **accepté** : sinon 409 `order_not_accepted` (un `eta: null` sur un ordre non accepté répond 200 sans rien changer). 404 `move_order_not_found` (ordre inconnu ou supprimé), 422 `invalid_eta` (hors de [maintenant − 1 h, maintenant + 48 h]) ou `invalid_remaining_m` (0 à 2 000 km).
- Effet : l'ordre porte `eta` (UTC, « Z »), `eta_updated_at` (heure du serveur à la réception), `eta_by` et, s'il est envoyé, `eta_remaining_m`. `eta: null` les retire. Le même `eta` renvoyé (toutes les 2 min) rafraîchit `eta_updated_at`.
- L'estimation ne touche pas `updated_at` de l'ordre (les réponses de l'opérateur et l'annulation du PCO gardent leur règle du dernier écrivain) ; `revision` augmente ; réponse et diffusion `move_order.upserted` avec l'ordre complet ; le flux `…/sync/changes` porte l'ordre (`object_type: move_order`, `action: update`).
- Une modification synchronisée de l'ordre garde l'estimation tant qu'il reste `accepted` ; `arrived`, `refused` ou `cancelled` l'effacent. Les champs `eta*` envoyés dans un `move_order` synchronisé sont ignorés : seule cette route les pose.
- Pas de ligne de main courante par estimation (envoyée toutes les 2 min).

Vérification : `tests/test_move_order_eta.py`, `demo/verify_move_orders.py` (CI).

## Ordres de déplacement : statut de l'opérateur (lot 27, 3 novembre 2026)

Accepté → « En route », arrivé → « Sur place », refusé ou annulé → statut d'avant l'ordre, « Terminé, disponible » (`finished_at` sur un ordre `arrived`) → « Disponible ». Champs gardés par le serveur : `operator_status_before`, `finished_at`. Détail : `event-operations-v0.1.md`, « Statut qui suit les ordres de déplacement ».

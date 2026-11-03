# Temps réel et lecture des positions — v0.1

## REST
- GET /api/v0.1/events/{event_id}/positions/latest (porte aussi `operator_status` et `operator_status_at` de l'appareil, lot 24)
- GET /api/v0.1/events/{event_id}/devices/{device_id}/positions
- GET /api/v0.1/events/{event_id}/pois
- POST /api/v0.1/events/{event_id}/pois

L'historique d'une trace est paginé/limité par le paramètre limit.

## WebSocket
Connexion : /api/v0.1/events/{event_id}/ws

Événements initiaux :
- position.updated
- poi.created
- poi.updated (nom modifié)
- poi.deleted (point supprimé)
- operator.status (statut d'un opérateur changé, lot 24 : `{"event_id", "device_id", "operator_status", "operator_status_at"}`, voir `event-operations-v0.1.md`)

Le flux est strictement isolé par event_id. À ce stade, l'authentification WebSocket sera ajoutée avec la couche IAM.

## Objectif
Un client abonné à un événement reçoit immédiatement une nouvelle position ou un nouveau POI après validation et persistance en base.

## Points d'intérêt (29 octobre 2026)
- Champs : `id`, `event_id`, `kind`, `label`, `lat`, `lon`, `created_at`, `created_by` (auteur), `color` (ARGB, couleur de dessin de l'auteur), `icon` (forme de l'icône, 30 octobre 2026 : `pin`, `gathering`, `command_post`, `danger`, `parking`, `water`, `helipad`, `vehicle`, `viewpoint`, `medical`, `radio`, `shelter`, `flag` ; absent = épingle), `version`, `updated_by`, `updated_at`.
- Création : `POST …/pois` ou synchronisation `object_type = poi`, `action = create` ; l'auteur est `created_by` (à défaut `updated_by`).
- Modification : synchronisation `action = update`, charge `{label?, icon?, updated_by}` ; seuls le nom et l'icône changent (un champ absent est conservé, une icône inconnue est refusée).
- Suppression : synchronisation `action = delete`, charge `{updated_by}` ; une ligne en main courante (« Point d'intérêt « … » supprimé »). Le point disparaît de `GET …/pois`.
- Droits : l'auteur ou le PCO (identifiant `PCO`/`PCO-…` ou rôle PCO de l'adresse vérifiée par Cloudflare Access) ; sinon `rejected` et entrée au journal de sécurité. Un point sans auteur (antérieur au 29 octobre) : PCO seulement.
- Le flux de changements (`…/sync/changes`) porte l'état résultant ; une suppression porte `deleted: true`. Une suppression d'un point déjà supprimé répond `conflict`.

## Ordres de déplacement (lot 24)
`move_order.upserted` porte aussi l'heure d'arrivée estimée d'un ordre accepté (`eta`, `eta_updated_at`, `eta_by`, `eta_remaining_m`), voir `routes-v0.1.md`.

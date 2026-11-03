# Objets cartographiques v0.1

Objets dessinés sur la carte par les opérateurs : point, ligne, flèche, cercle, rectangle, zone, texte, tracé libre, mesure.

## Synchronisation
Type d'objet `map_feature` dans `POST /api/v0.1/sync`, actions `create`, `update` et `delete`.

- `create` et `update` portent l'état complet de l'objet : `id`, `event_id`, `kind`, `points` (liste de `[lat, lon]`), `radius_m` (cercle), `color` (ARGB), `stroke_width`, `label`, `created_by`, `updated_by`, `updated_at`.
- `delete` porte `id`, `event_id`, `updated_by`, `updated_at`.

## Règle de conflit (ADR-001)
Dernier écrivain gagnant par objet, sur le couple (`updated_at`, `updated_by`).

| Statut | Signification |
|---|---|
| `accepted` | appliqué, diffusé en temps réel et dans le flux de changements |
| `conflict` | plus ancien que l'état connu, ignoré. Le client retire l'opération de son Outbox et reçoit l'état gagnant par le flux |
| `duplicate` | `operation_id` déjà reçu |
| `rejected` | données invalides ou événement inconnu, jamais applicable |

La suppression pose une pierre tombale. Une modification plus récente fait réapparaître l'objet, ce qui permet d'annuler une suppression. Un horodatage plus de 5 minutes dans le futur est ramené à l'heure du serveur.

Le flux `GET /events/{id}/sync/changes` ne contient que les opérations acceptées, avec l'état résultant (dont `revision` et `deleted`).

## Lecture
- `GET /api/v0.1/events/{id}/map-features` : objets actifs (`include_deleted=true` pour les pierres tombales)
- `GET /api/v0.1/events/{id}/map-features.geojson` : FeatureCollection GeoJSON

## Temps réel
`map_feature.upserted` et `map_feature.deleted`, avec l'état résultant.

## Main courante
Création, suppression et restauration sont journalisées. Les modifications ne le sont pas, pour ne pas noyer la main courante.

## Vérification
`demo/verify_map_features.py`, exécuté par la CI contre PostgreSQL/PostGIS.

## Relevés goniométriques (`kind: "bearing"`)

Le bloc `bearing` accepte aussi `exercise` (booléen, conservé seulement s'il est vrai) et `frequency_mhz` (1 à 10 000, facultatif). Un relevé d'exercice est inscrit en main courante avec le préfixe « EXERCICE · ».

## Points d'écoute SATER (`kind: "listening"`, lot 24, 3 novembre 2026)

Carte « Android Auto : « Signal entendu ici » (SATER) » : un appui envoie au PCO « signal de balise entendu » avec la position, le cap du véhicule et l'heure ; il apparaît comme un **relevé sans azimut** (point d'écoute).

- Objet `map_feature` ordinaire (synchronisation, conflit ADR-001, flux, `map_feature.upserted` / `map_feature.deleted`), `kind: "listening"`, **un seul point** `points: [[lat, lon]]`. `color` et `stroke_width` sont facultatifs (défaut `0xFFFF9F1C`, 2) ; `label` facultatif.
- Bloc `listening` (facultatif en entier) : `heard_at` (ISO 8601, heure où le signal a été entendu ; défaut `updated_at`), `heading_deg` (cap du véhicule, 0 à 360, facultatif), `speed_mps` (0 à 150), `accuracy_m`, `exercise` (gardé seulement s'il est vrai), `search` (signal recherché, 80 car.), `frequency_mhz` (1 à 10 000), `comment` (300 car.) — tous facultatifs sauf `heard_at` qui est toujours rendu.
- Exemple de création (synchronisation `object_type: "map_feature"`, `action: "create"`) : `{"id", "event_id", "kind": "listening", "points": [[48.75, 2.05]], "listening": {"heard_at": "2026-11-03T08:00:00Z", "heading_deg": 135}, "created_by": "<appareil>", "updated_by": "<appareil>", "updated_at": "2026-11-03T08:00:00Z"}`.
- Module ADRASEC requis (comme les relevés) : `rejected` sinon. Le réglage `sater` de l'événement ne bloque pas l'envoi (il ne fait qu'afficher le bouton) : un signal entendu n'est jamais perdu.
- Droits : modification et suppression par l'auteur (`created_by`) ou le PCO ; **l'auteur annule** (action `delete`) seulement dans les **10 s** qui suivent `heard_at` (plus 5 s de marge réseau, comparaison avec l'`updated_at` de la suppression, heure de l'appareil : une annulation partie hors connexion avec son point reste valable) ; le PCO supprime à tout moment. Sinon `rejected`.
- Main courante : « Signal de balise entendu ici par <auteur>, cap 135° (point d'écoute) » (préfixe « EXERCICE · » si `exercise`), « Point d'écoute de <auteur> annulé ».
- GeoJSON : propriétés `listening:*`. Les anciennes applications ignorent ce type (objet d'un type inconnu sauté).
- TV : liste `listening` de l'état (`id`, `lat`, `lon`, `heard_at`, `heading_deg`, `exercise`, `by`) avec la couche Relevés : anneau orange (pointillé en exercice), trait court dans le cap du véhicule, étiquette « Écoute 08:00 », ligne « Point d'écoute » dans la légende.

Vérification : `tests/test_sater_listening.py`, `demo/verify_map_features.py` (CI).

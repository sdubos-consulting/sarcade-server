# Affichage sur Smart TV : état et vue (v0.1)

## Vue envoyée par un téléphone

`PUT /api/v0.1/events/{event_id}/displays/{display_id}/view` `{"view": {...} | null}` (`null` = cadrage automatique, options par défaut). Clés gardées (les autres sont ignorées) :

- `lat`, `lon`, `zoom`, `basemap`, `tile_url` (https seulement), `attribution` ;
- `layers` : `operators`, `aprs`, `offline`, `drawings`, `bearings` (vrai par défaut), `highpoints`, `relays` (faux par défaut) ;
- `opacity` : `ops`, `draw`, `brg`, `ref`, de 0,1 à 1 (1 par défaut) ;
- `messages` : panneau des messages affiché (vrai par défaut ; le bandeau des messages immédiats reste).

Type invalide : 422 (`invalid_layers`, `invalid_opacity`, `invalid_messages`…).

## État lu par la TV

`GET /api/v0.1/tv/state[?fr=…&rr=…]` (en-tête `Authorization: Display <jeton>`) :

- `positions` filtrées selon `layers` (`offline` faux : opérateurs sans position depuis plus de 10 min masqués) ;
- `options` : `layers`, `opacity`, `messages` appliqués ;
- `weather` : `{"temp_c", "icon", "vigilance"}` de l'heure en cours (`icon` parmi `sun`, `sun_cloud`, `cloud`, `rain`, `storm`, `snow`, `fog`, `moon`, `moon_cloud` — lune la nuit, entre coucher et lever du soleil ; `vigilance` `null`, `yellow`, `orange`, `red`), tiré du cache météo de l'événement, recalculé au plus toutes les 10 min ; `null` sans point météo ;
- `drawings` (objets cartographiques hors relevés : `kind`, `points`, `radius_m`, `color`, `stroke_width`, `label`, `opacity`), `bearings` (`lat`, `lon`, `azimuth_deg`, `length_m`, `search`, `label`, `exercise`, `color` stable par recherche) et `references` (points hauts `HIGH_POINT`, relais `RELAY` : `name`, `callsign`, `lat`, `lon`) ;
- `features_rev`, `references_rev` : la TV les renvoie en `fr` / `rr` ; tant qu'ils n'ont pas changé, `drawings`, `bearings` et `references` ne sont pas renvoyés.

- `messages` : messages généraux seulement (la main courante n'est plus envoyée ni affichée, 29 octobre 2026) ;
- `alert` : bandeau rouge (`sender_id`, `body`, `created_at`) = dernier message immédiat général des 10 dernières minutes, sauf s'il a été retiré sur cet écran ou si c'est une détresse (« 🆘 … ») close par un message « Fin de détresse — <même expéditeur> » (ou « End of distress ») ; `null` sinon ;
- `sos` : expéditeurs dont la détresse n'est pas close (pastille « SOS » clignotante de l'en-tête, qui reste après retrait du bandeau).

## Retirer le bandeau SOS d'un écran

`POST /api/v0.1/events/{event_id}/displays/{display_id}/alert/clear` : les messages reçus jusque-là n'affichent plus le bandeau sur cet écran ; un nouveau message immédiat le rallume. Droits : la personne qui a appairé l'écran (e-mail vérifié par Cloudflare Access, champ `owner`) ou un administrateur (`admin`) ; sinon 403 `owner_or_admin_required`. Sans e-mail vérifié (réseau local sans Access) : permis. Ligne « Bandeau SOS retiré de l'écran » en main courante.

`GET /api/v0.1/events/{event_id}/displays` donne en plus `owner`, `alert` (bandeau affiché) et `can_clear_alert` (pour l'appelant).

Vérification : `tests/test_tv.py`, `demo/verify_map_features.py`.

## Lot 4 (30 octobre 2026)

- **Opacité par couche** dans `opacity` : `ops`, `aprs`, `draw`, `brg`, `high`, `relay` (10 à 100 %). Une vue de l'ancien format (`ops`, `draw`, `brg`, `ref`) est acceptée : `aprs` reprend `ops`, `high` et `relay` reprennent `ref`.
- **`from`** (texte, 64 car. au plus) : terminal qui a envoyé la vue ; la TV affiche le petit bandeau « Vue de <terminal> » (texte échappé).
- **Pas de retour automatique** : la vue reste jusqu'à « Revenir à la vue par défaut » (`view: null`) ou un nouvel envoi. Vue par défaut : cadrage sur les opérateurs actifs (position de moins de 10 min, sinon toutes les stations), fond de carte de l'événement (`default_base` de l'état : `tile_url` https, `attribution` ; `null` si le fond n'est pas en https → OpenStreetMap).
- **Colonne des messages par écran** : `PUT /api/v0.1/events/{event_id}/displays/{display_id}/messages` `{"messages": bool}`, appliqué tout de suite (main courante « Messages affichés / masqués sur l'écran ») ; un envoi de vue la règle aussi (case « Colonne des messages »). La liste des écrans donne `messages`, `revoked`, `view_from`.
- **Relevés** : `bearings[].cone_deg` (5 par défaut) ; couleurs alignées sur l'application (406 MHz `#9B4DFF`, 121,5 MHz `#E53935`, exercice 121,375 MHz `#1E88E5`, sinon palette `#3DD6F5`, `#FFB020`, `#4ADE80`, `#FF7AB6`, `#FFE066` selon le nom, même calcul que `searchColor`).
- **`zones`** : par recherche, `search`, `color`, `exercise`, `bearings` (nombre), `crossings` (`[[lat, lon], …]`) et, s'il y a au moins un croisement, `lat`, `lon`, `radius_m` (au moins 50 m), `label` (« ±426 m · 3 relevés ») — même règle que `crossing` / `probableZone` de l'application. Vide quand la couche Relevés est masquée ; suit l'opacité `brg`. La TV n'affiche plus d'étiquette au bout des relevés : légende en bas à gauche, toujours visible (trait de couleur + signal, badge EXERCICE, zone probable). Plusieurs balises sur un signal : une zone par balise (`index` 1, 2…, « Zone 1 · ±700 m · 3 relevés »), même règle que `probableZones` de l'application. Chaque zone porte `mode` (`auto`, `multi`, `single`), lu dans le réglage `zone_modes` de l'événement : `multi` (Plusieurs balises : graines et zones jusqu'à 5 km), `single` (Une seule balise : un seul cercle). L'étiquette indique le mode hors Auto (« Zone 1 · plusieurs balises · … », « Une seule balise · … »). Un relevé gardé à la main rejoint la zone avec laquelle il est d'accord, sinon la zone 1 ; un relevé écarté à la main reste écarté ; une zone qui dépasserait 3 km (5 km en Plusieurs balises) garde sa graine et les relevés qui ne l'élargissent pas au-delà. Un changement de mode change `features_rev` : la TV reçoit aussitôt les nouvelles zones.
- **Validation depuis le même réseau** : `POST /tv/pair` note l'adresse publique de la TV (`CF-Connecting-IP`, sinon adresse du client). `GET /api/v0.1/tv/nearby` donne à **tous les participants de l'événement** (décision du porteur du 30 octobre 2026, 13 h 19 ; auparavant PCO et administrateurs seulement ; restriction par rôle prévue plus tard dans la console) les codes en attente derrière la **même adresse publique** que l'appelant : `[{"code", "name", "expires_in"}]`. Rien n'est validé par cette route : l'application affiche l'alerte avec le code, l'opérateur confirme, puis `POST /tv/approve` habituel. QR code et saisie du code restent possibles.

## Lot 7 (30 octobre 2026)

- **Mode jour / nuit** : la vue envoyée porte `theme` (`day`, `night`, `auto` ; `auto` par défaut, 422 `invalid_theme` sinon), le mode réglé dans Paramètres › Affichage de l'appareil qui envoie. L'état de la TV donne `options.theme` et `night` (booléen calculé par le serveur ; `auto` : nuit entre le coucher et le lever du soleil de `weather.sunset` / `sunrise`, nuit sans heures connues). La page TV s'adapte entière : bandeau, colonne, légende, cartouches ; en nuit les tuiles de plan sont assombries comme dans l'application (pas les photos aériennes).
- **Messages masqués** : la colonne de droite disparaît, la carte prend toute la largeur (`invalidateSize`, centre et zoom conservés), légende en bas à gauche.
- **Nom de chaque écran** : `POST /tv/pair` accepte `{"name"}` (nom par défaut calculé par la page TV : marque ou système de l'user-agent + résolution, « TV Samsung · 1920×1080 ») ; `GET /api/v0.1/tv/code/{code}` → `{"code", "name"}` d'un code en attente (404 sinon), pour préremplir le nom ; `POST /tv/approve` accepte `name` (nom choisi, sinon le nom par défaut). Noms nettoyés (caractères de contrôle, espaces, 60 caractères), toujours échappés à l'affichage.
- `PUT /api/v0.1/events/{event_id}/displays/{display_id}/name` `{"name"}` (« Renommer », 422 si vide) ; `POST …/identify` (« Identifier ») : la TV affiche son nom en très grand 5 s à sa requête suivante (champ `identify` de l'état pendant 15 s). Droits : comme les autres actions sur un écran.
- Liste des écrans : `name`, `last_seen` (dernière requête de la TV, en mémoire, `null` après un redémarrage du serveur). Main courante : les lignes nomment l'écran (« Salle PCO (code ABC123) »).

Vérification : `tests/test_tv.py`, `demo/verify_tv.py`.

## Bouton « Écrans » de la carte (lot 11, 30 octobre 2026)
- `GET /api/v0.1/events/{event_id}/displays` donne en plus `mine` : écran appairé par l'appelant (e-mail vérifié = `owner`) ; sans e-mail vérifié (réseau local sans Access), tous les écrans comptent. L'application n'affiche le bouton « Écrans » que s'il existe au moins un écran actif `mine` (PCO et admin : tout écran actif de l'événement).
- `PUT …/displays/{display_id}/view` accepte `live: true` (« Suivre ma vue ») : la vue suit la carte de l'expéditeur ; seule la première vue d'un suivi va en main courante (« Vue suivie en direct sur l'écran »), les suivantes non. Un envoi sans `live` ou `view: null` termine le suivi.

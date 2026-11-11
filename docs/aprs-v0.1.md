# APRS (V0.1)

Spécification : note « APRS » du vault (décisions du 10 octobre 2026).

## Principe
Le **serveur** est le seul à parler à APRS ; il redistribue les positions aux clients par le flux temps réel habituel (`position.updated`, `source: "aprs"`). Seules les stations des **groupes d'indicatifs sélectionnés** sont affichées et enregistrées : les autres ne sont ni affichées, ni enregistrées.

## Entrées
| Entrée | Activation | Comportement |
|---|---|---|
| APRS-IS (Internet) | `SARCADE_APRS_IS=1` (`SARCADE_APRS_IS_HOST`, `SARCADE_APRS_IS_PORT`, défaut `euro.aprs2.net:14580`) | connexion en lecture seule (code d'accès -1) sous l'indicatif `SARCADE_APRS_IS_LOGIN` (à défaut `SARCADE_APRS_CALLSIGN`) — obligatoire, les serveurs ferment la connexion de N0CALL, filtre `b/` limité aux indicatifs suivis, mis à jour à chaud ; le serveur n'envoie jamais rien vers APRS-IS |
| Radio par le Gateway | `SARCADE_KISS_HOST`, `SARCADE_KISS_PORT` (défaut 8001) | modem logiciel KISS TCP (Dire Wolf) ; toutes les trames sont reçues, seules celles des indicatifs suivis sont gardées |
| Lignes poussées par le Gateway | `POST /api/v0.1/aprs/frames` (`via`: `rf` ou `is`, `lines`) | pour un modem sans KISS TCP |

Les deux entrées sont **fusionnées et dédoublonnées** : un même paquet (émetteur et contenu) entendu par radio et sur APRS-IS dans les 30 s n'est gardé qu'une fois.

Décodage : positions non compressées et compressées (avec ou sans horodatage), **Mic-E** (la plupart des postes mobiles), objets, trafic tiers. Vérifié contre la bibliothèque aprslib sur près de 3 000 trames.

Serveur APRS-IS réglable dans l'**outil d'administration** (jeton d'administration) : `PATCH /api/v0.1/admin/settings` avec `{"settings":{"aprs":{"is_host":"euro.aprs2.net","is_port":14580}}}`. Ordre de priorité : réglage d'administration, puis `SARCADE_APRS_IS_HOST` / `SARCADE_APRS_IS_PORT` du `.env`, puis `euro.aprs2.net:14580`. Un changement est appliqué sans redémarrage (reconnexion dans la minute) et inscrit au journal de sécurité ; `null` ou vide revient au `.env`.

## Groupes d'indicatifs
- `GET/POST /api/v0.1/aprs/groups`, `PUT/DELETE /api/v0.1/aprs/groups/{id}` (nom, indicatifs) : créés par l'administrateur de l'organisation.
  Deux groupes du même nom (casse et espaces ignorés) sont refusés : `409 aprs_group_name_taken` (lot 5, 30 octobre 2026).
- Un indicatif sans SSID couvre **tous ses SSID** (F4JPO couvre F4JPO-7, F4JPO-9…) ; avec SSID, seulement cette station.
- Un événement suit des groupes et le PCO peut ajouter des indicatifs : réglages `aprs_groups` et `aprs_callsigns` (`PATCH /events/{id}/settings`). `GET /events/{id}/aprs/callsigns` donne la liste effective. Un événement terminé ne suit plus rien.

## Rattachement aux opérateurs
Un opérateur déclare son indicatif dans le heartbeat (`callsign`) : les positions APRS de **cet indicatif exact, SSID compris**, se rattachent à son appareil (F4JPO ne récupère pas F4JPO-5 ; décision du 28 octobre 2026). Sinon, la station apparaît sous `aprs:<indicatif>`. La position garde son `callsign` pour l'affichage.

## Émission radio locale (désactivée par défaut)
Réglage `aprs_tx_rf` du PCO, consentement de l'opérateur (`aprs_tx_consent` dans le heartbeat), indicatif de l'ADRASEC (`SARCADE_APRS_CALLSIGN`) et liaison radio active : la position de l'opérateur est émise **en radio seulement**, comme objet portant son indicatif, sous l'indicatif de l'ADRASEC, au plus toutes les 2 minutes. Jamais vers APRS-IS.

## Stations fixes (iGates, relais)
L'application classe seule une station APRS en fixe (symbole iGate, relais, maison, météo ; SSID -10 ou -1 à -3 ; pas de déplacement de plus de 50 m en plusieurs heures). Le classement forcé à la main dans la fiche est partagé par l'événement (décision du 30 octobre 2026, migration 0025) :
- `GET /api/v0.1/events/{id}/aprs/classes` → `{"classes": {"aprs:F6KRK-10": "fixed", …}}` ;
- `PUT /api/v0.1/events/{id}/aprs/classes/{device_id}` avec `{"actor_id", "kind": "fixed" | "mobile" | "auto"}` (`auto` retire le choix) ; ouvert à tous les participants ; diffusé en temps réel (`aprs.class.updated`).

## État
`GET /api/v0.1/aprs/status` : état des deux liaisons, filtre APRS-IS courant, paquets reçus, positions enregistrées.

Vérifications : `tests/test_aprs.py`, `tests/test_aprs_links.py` (faux serveurs APRS-IS et KISS), `demo/verify_aprs.py` (CI).

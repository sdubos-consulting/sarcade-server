# Format d'événement commun v0.1

Carte « SIEM, en avance de phase : format d'événement commun » (lot 29, groupe 8, feu vert du porteur le 5 novembre 2026).

Un seul format pour la main courante, le journal de sécurité et les exports. Les noms de champs suivent **ECS** (Elastic Common Schema), lu tel quel par Wazuh, Elastic, Splunk et Microsoft Sentinel. Code : `src/sarcade/security/event_format.py`.

## Champs

| Champ | Contenu |
|---|---|
| `@timestamp` | horodatage UTC à la milliseconde (`2026-11-05T21:17:04.123Z`) |
| `event.id` | identifiant unique : `sec-<n>` (journal de sécurité), `log-<n>` (main courante) |
| `event.dataset` | `sarcade.security` ou `sarcade.logbook` |
| `event.action` | type et action (`auth.login`, `note`, `message`, `route`…) |
| `event.category` | catégorie ECS (journal de sécurité) |
| `event.outcome` | `success`, `failure` ou `unknown` |
| `event.sequence`, `event.hash` | rang et empreinte SHA-256 de la chaîne |
| `user.id` | auteur |
| `host.id` | appareil |
| `source.ip` | adresse d'origine (journal de sécurité) |
| `geo.location` | position facultative (`lat`, `lon`) |
| `message` | texte de l'entrée (main courante) |
| `observer` | serveur SARCADE qui a écrit l'entrée |
| `sarcade.event_id`, `sarcade.object_id`, `sarcade.details` | événement SARCADE, objet concerné, détails |

L'organisation (`sarcade.organization`) sera ajoutée avec les modules par organisation.

## Exemples

Un SOS dans la main courante :

```json
{"@timestamp":"2026-11-05T21:17:04.000Z","event":{"id":"log-812","kind":"event","dataset":"sarcade.logbook","action":"message","outcome":"unknown","sequence":812,"hash":"9f2c…"},"message":"🆘 SOS de TEL-01 (48.80000, 2.13000)","user":{"id":"TEL-01"},"geo":{"location":{"lat":48.8,"lon":2.13}},"observer":{"name":"sarcade-nas","product":"SARCADE","vendor":"SDUBOS Consulting"},"sarcade":{"event_id":"exercice-78"}}
```

Une connexion refusée dans le journal de sécurité :

```json
{"@timestamp":"2026-11-05T21:20:11.512Z","event":{"id":"sec-4410","kind":"event","dataset":"sarcade.security","category":["authentication"],"action":"auth.login","outcome":"failure","sequence":4410,"hash":"1ab0…"},"user":{"id":"inconnu@example.org"},"source":{"ip":"203.0.113.8"},"observer":{"name":"sarcade-nas","product":"SARCADE","vendor":"SDUBOS Consulting"},"sarcade":{"outcome":"refused"}}
```

## Envoi au SIEM

`SARCADE_SIEM_FORMAT=ecs` : une ligne JSON par entrée, dans ce format (module Pro « siem », syslog TCP/TLS ou fichier). `syslog` (RFC 5424) reste la valeur par défaut, `json` garde l'ancien format du journal.

## Export CSV

Colonnes dans l'ordre de `CSV_COLUMNS` : `event.id`, `@timestamp`, `event.dataset`, `event.action`, `user.id`, `host.id`, `sarcade.event_id`, `geo.location.lat`, `geo.location.lon`, `event.outcome`, `message`, `event.hash`. L'export CSV de la main courante de l'application suivra cet ordre dans un second temps.

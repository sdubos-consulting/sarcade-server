# Vérification du jeton Cloudflare Access par le serveur

Spécification : note « Accès protégé par Cloudflare Access » du vault (décisions 6 et 7 du 23 octobre 2026).

Cloudflare contrôle l'utilisateur avant que la requête n'arrive. Le serveur peut le contrôler une seconde fois sur l'adresse publique, avec le jeton `Cf-Access-Jwt-Assertion` (ou le cookie `CF_Authorization`) :
- signature RS256 avec les clés de l'équipe (`https://<équipe>.cloudflareaccess.com/cdn-cgi/access/certs`), gardées 1 h et rechargées en cas de nouvelle clé (une fois par minute au plus) ;
- émetteur `https://<équipe>.cloudflareaccess.com` ;
- audience : l'étiquette AUD de l'application SARCADE ;
- expiration, avec une tolérance de 60 s.

Une requête arrivée sur l'adresse publique sans passer par Access (tunnel mal réglé, politique retirée) est alors refusée.

## Réglages (environnement du service `server`)

| Variable | Valeur |
|---|---|
| `SARCADE_ACCESS_VERIFY` | `off` (par défaut), `log` (contrôle et compte, ne bloque jamais), `enforce` (refuse) |
| `SARCADE_ACCESS_TEAM_DOMAIN` | `sdubos.cloudflareaccess.com` |
| `SARCADE_ACCESS_AUD` | étiquette AUD de l'application (Zero Trust → Access → Applications → SARCADE → *Application Audience (AUD) Tag*) ; plusieurs, séparées par des virgules |
| `SARCADE_ACCESS_HOSTS` | `app.sarcade.org` |

Seules les adresses listées sont contrôlées. L'adresse locale (`http://192.168.0.110:8000`) reste sans Access (décision 4). Un réglage incomplet désactive le contrôle et l'écrit dans le journal du conteneur. `/health` n'est jamais contrôlé.

## Refus
- HTTP : `403` `{"detail":"access_denied"}`, en-têtes `WWW-Authenticate: Cloudflare-Access` (lu par les applications comme « Connexion requise ») et `X-Sarcade-Access: <raison>`. `503` si aucune clé n'a jamais pu être chargée.
- WebSocket : poignée de main refusée (403).
- Raisons : `missing_token`, `malformed_token`, `unexpected_algorithm`, `unknown_key`, `keys_unavailable`, `expired`, `wrong_audience`, `wrong_issuer`, `bad_signature`, `invalid_token`.
- Chaque refus est écrit dans le journal du conteneur (`sarcade.access`), sans le jeton. Le journal de sécurité de la PR #15 le recevra une fois fusionnée.

## Diagnostic
- `GET /api/v0.1/access/status` : mode, adresses, nombre d'audiences, clés chargées, compteurs acceptés et refusés par raison, dernier refus. Aucun secret.
- `GET /api/v0.1/access/session` : `verified` vaut `true` (contrôlé), `false` (refusé, mode `log`) ou `null` (adresse non contrôlée), avec `verify_reason`.

## Main courante
À la synchronisation, la première opération acceptée d'un opérateur inscrit une ligne `access` : « ADR78-01 connecté par Cloudflare Access : <email> » (jeton de service : « … (jeton de service <id>) »). Une fois par opérateur, événement et email toutes les 12 h.

## Mise en service conseillée
1. `SARCADE_ACCESS_VERIFY=log`, utilisation normale quelques jours, puis contrôle de `/api/v0.1/access/status` : aucun refus attendu.
2. `SARCADE_ACCESS_VERIFY=enforce`.

# Comptes, connexion par code e-mail et appareils enrôlés — v0.1

Lot 26, décisions du porteur du 3 novembre 2026 (« Groupes d'organisation et habilitations ») : identité par **code e-mail à la première connexion**, puis **appareil enrôlé** (jeton gardé par l'appareil, vérifié par ce serveur sans Internet). Un seul serveur (le NAS) : pas de jeton signé hors ligne ni de réplication ADR-002 dans cette version.

## Principes
- Un **compte** = une adresse e-mail (clé unique, en minuscules, sans espace) + un **identifiant interne fixe** (`id`).
- Le compte est créé à la **première connexion** (code e-mail ou Cloudflare Access).
- L'appareil reçoit un **jeton d'appareil** `sdt_…` (≈ 47 caractères), **montré une seule fois**, à garder dans le stockage sécurisé. Il est envoyé à chaque appel : `Authorization: Bearer sdt_…`. Sans date d'expiration ; **révocable** par son propriétaire ou un administrateur.
- Le serveur ne garde que les **empreintes** (SHA-256) des codes et des jetons.
- **Cloudflare Access** reste une preuve de l'adresse : un appel vérifié par Access est rattaché au compte de cette adresse ; `POST /auth/access/enroll` enrôle l'appareil sans code.
- Sans jeton ni Access : appareil **sans compte** (droits du Core, voir `directory-v0.1.md`, période de transition).

## Anti-abus
| Règle | Valeur |
|---|---|
| Durée de vie d'un code | 10 minutes |
| Essais par code | 5 (ensuite, demander un nouveau code) |
| Délai entre deux codes pour une adresse | 60 s |
| Codes par heure pour une adresse | 5 |
| Demandes par heure depuis une adresse IP | 20 |
| Un nouveau code annule le précédent | oui |

La réponse à une demande de code est **la même**, que le compte existe ou non.

## Envoi des e-mails (Brevo)
Variables du `.env` du serveur : `SARCADE_SMTP_HOST` (ex. `smtp-relay.brevo.com`), `SARCADE_SMTP_PORT` (`587` STARTTLS par défaut, `465` TLS), `SARCADE_SMTP_USER`, `SARCADE_SMTP_PASSWORD`, `SARCADE_SMTP_FROM` (ex. `SARCADE <no-reply@sarcade.org>`).

**Tant qu'une valeur manque, l'envoi est simulé** : le code est écrit au **journal de sécurité** (catégorie `auth`, action `login_code_simulated`, `details.email`, `details.code`), lisible par l'administrateur (`GET /api/v0.1/admin/security-journal?category=auth`). Il n'est **jamais** écrit dans les journaux applicatifs.

## Routes

### `GET /api/v0.1/auth/state` (public)
```json
{"methods": ["email_code", "access"], "delivery": "smtp|simulated", "code_length": 6,
 "code_ttl_s": 600, "resend_after_s": 60, "max_attempts": 5}
```

### `POST /api/v0.1/auth/code/request` (public)
Corps : `{"email": "chef@adrasec78.fr"}` → **202**
```json
{"status": "sent", "delivery": "smtp|simulated", "expires_in_s": 600, "resend_after_s": 60, "code_length": 6}
```
Erreurs :
| Code | `detail` | Cas |
|---|---|---|
| 422 | `"invalid_email"` | adresse invalide |
| 429 | `{"error": "resend_too_soon", "retry_after_s": n}` | moins de 60 s depuis le dernier code (en-tête `Retry-After`) |
| 429 | `{"error": "too_many_codes", "retry_after_s": n}` | 5 codes dans l'heure |
| 429 | `{"error": "too_many_requests", "retry_after_s": 3600}` | 20 demandes dans l'heure depuis cette IP |
| 503 | `"mail_failed"` | le serveur SMTP a refusé (code annulé) |

### `POST /api/v0.1/auth/code/verify` (public)
Corps : `{"email": "…", "code": "123456", "device_id": "TEL-01", "label": "Téléphone de Paul", "platform": "android"}` (`device_id` = identifiant déclaré par l'application, `label` et `platform` facultatifs) → **200**
```json
{"token": "sdt_…",
 "device": {"id": "uuid", "user_id": "uuid", "device_id": "TEL-01", "label": "…", "platform": "android",
            "method": "email_code", "created_at": "…Z", "last_seen_at": "…Z", "revoked_at": null,
            "revoked_by": null, "active": true},
 "account": {"id": "uuid", "email": "…", "display_name": null, "status": "active",
             "created_at": "…Z", "last_login_at": "…Z"},
 "created": true}
```
`created` : compte créé par cette connexion (les invitations de l'adresse deviennent alors des appartenances).

Erreurs : 422 `"invalid_email"` ; 400 `"no_code"` (aucun code en cours ou déjà utilisé) ; 401 `{"error": "invalid_code", "attempts_left": n}` ; 410 `"code_expired"` ; 429 `"too_many_attempts"` ; 403 `"account_disabled"`.

### `POST /api/v0.1/auth/access/enroll` (Cloudflare Access)
Corps : `{"device_id", "label", "platform"}` (facultatifs) → même réponse que `verify` avec `"method": "access"`. 403 `"access_email_required"` sans e-mail vérifié par Access.

### `POST /api/v0.1/auth/logout` (jeton d'appareil)
Révoque le jeton de l'appareil appelant → `{"revoked": true}` ; 409 `"no_device_token"` sans jeton.

### Jeton refusé (toutes les routes)
Un jeton `sdt_…` inconnu ou révoqué, ou un compte désactivé, donne **401** sur **n'importe quelle route**, avec `WWW-Authenticate: Bearer realm="SARCADE"` et `detail` = `"device_revoked"` (révoqué ou compte désactivé) ou `"device_token_invalid"` (inconnu). L'application oublie alors le jeton et propose « Se connecter » ; elle peut rejouer l'appel sans jeton (droits du Core).

### `GET /api/v0.1/me`
```json
{"email": "chef@adrasec78.fr", "checked": true, "roles": ["admin", "pco"],
 "via": "device|access|null", "account": { …compte… } | null, "device": { …appareil… } | null}
```
Les champs des droits (`groups`, `admin_rights`, `modules`, `transition`…) sont décrits dans `directory-v0.1.md`. Un appel vérifié par Access crée le compte s'il n'existe pas.

### `GET /api/v0.1/me/devices` · `DELETE /api/v0.1/me/devices/{id}`
Mes appareils actifs (chacun avec `"current": true|false`) ; révocation d'un de mes appareils → l'appareil (`active: false`). 401 `"account_required"` sans compte ; 404 `"device_not_found"`.

### Administration (administrateur du serveur)
Autorisation : compte administrateur du serveur, adresse de `SARCADE_ADMIN_EMAILS`, ou `Authorization: Bearer <SARCADE_ADMIN_TOKEN>` (accès de secours du `.env`). Sinon 401 (sans identité) ou 403 `"admin_required"`.
- `GET /api/v0.1/admin/users?q=…` → comptes `[compte…]`.
- `GET /api/v0.1/admin/devices?user_id=…&include_revoked=false` → appareils enrôlés, avec `email`.
- `POST /api/v0.1/admin/devices/{id}/revoke` → l'appareil révoqué.
- `GET /api/v0.1/admin/mail` → `{"delivery", "host", "port", "sender", "missing": [variables absentes]}` (jamais l'identifiant ni la clé).

## Temps réel
Sur tous les canaux d'événement (`/api/v0.1/events/{id}/ws`) :
```json
{"type": "device.revoked", "data": {"id": "<id appareil enrôlé>", "user_id": "…", "device_id": "TEL-01"}}
```
L'appareil concerné oublie son jeton.

## Journal de sécurité
`auth` : `login_code_sent` (success/failure), `login_code_simulated` (info, avec le code), `login_code_refused`, `login_code_checked` (failure), `account_created`, `device_token_refused`, `login` (refused) ; `device` : `device_enrolled`, `device_revoked` ; `rights` : `admin_required` (refused).

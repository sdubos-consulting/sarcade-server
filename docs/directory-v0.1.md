# Annuaire : groupes d'organisation et habilitations — v0.1

Lot 26, note « Groupes d'organisation et habilitations » (décisions du 23 octobre et du 3 novembre 2026). Un seul serveur (le NAS) écrit l'annuaire. Comptes et connexion : `accounts-v0.1.md`.

## Modèle
- **Groupe** : `short_name` (unique, 40 car.), `name`, `description`, `color` (`#RRGGBB`), `parent_id` (rattachement).
- **Rattachement** (`parent_id`, un seul parent) : dit **qui administre**. **Appartenance** (groupes membres, plusieurs parents) : dit **qui hérite des droits**. Un sous-groupe créé sous un groupe est **les deux** par défaut (`member_of_parent: true`).
- **Organisations** = groupes sans parent, hors groupes système (`organization: true`). La **licence** s'attache à une organisation.
- **Groupes système** (non supprimables, `system: true`) :
  - `public` « Public » : **tous les comptes** et les appareils sans compte, sans liste de membres ; ne contient pas de groupe, n'est membre d'aucun ; **aucune habilitation** possible (droits du Core) ;
  - `server-admins` « Administrateurs du serveur » : porte le droit `server_admin`.
- **Cycles refusés** avec le chemin ; **8 niveaux** au plus (appartenance et rattachement).
- **Invitation** : une adresse sans compte ajoutée à un groupe ; elle devient une appartenance à la création du compte.

## Droits
| `right` | Libellé | Portée (`scope_group_id`) |
|---|---|---|
| `server_admin` | Administrateur du serveur | aucune (serveur) |
| `group_admin` | Administrateur de groupe | un groupe **et les groupes rattachés sous lui** |
| `member_manager` | Gestionnaire des membres | un groupe (adresses seulement) |
| `event_organizer` | Organisateur d'événements | un groupe (événements de ce groupe, rôle PCO) |
| `auditor` | Auditeur | un groupe (et ses rattachés) ou le serveur (`null`) |
| `module.adrasec`, `module.race`, `module.assist`, `module.siem` | Module … | aucune |

- Habilitations **attachées aux groupes**, **positives**, `until` (date de fin) facultative. Pour une portée « un groupe », `scope_group_id` vaut par défaut le groupe porteur.
- **Héritage par l'appartenance** : un compte a l'union des habilitations de tous ses groupes (directs, imbriqués, et Public).
- **Pas d'élévation** : pour accorder (ou modifier, retirer) une habilitation, il faut administrer le groupe porteur **et** détenir le droit : `server_admin` et `auditor` du serveur par un administrateur du serveur seulement ; un droit d'administration sur la portée S par un administrateur de groupe couvrant S ; un module par quelqu'un qui a lui-même ce module.
- Le droit d'administration **suit le rattachement**, pas l'appartenance.
- **Module utilisable = habilitation ET licence de l'organisation** du groupe porteur (licence de l'organisation, ou licence du serveur si son champ `organisation` porte le nom court ou complet de l'organisation). Le module ADRASEC est gratuit pour les ADRASEC : licence `association` du même mécanisme.
- **Toujours au moins un administrateur actif** : un changement qui laisserait zéro compte actif administrateur du serveur est refusé (409 `last_admin`), y compris une date de fin. **Accès de secours** par le `.env` : `SARCADE_ADMIN_EMAILS` (adresses toujours administrateurs) et `SARCADE_ADMIN_TOKEN` (jeton d'administration).
- **Cache** des droits effectifs, recalculé à chaque changement (version des droits) et à la date de fin d'une habilitation.

## `/me` (champs ajoutés)
```json
{"groups": [{"id": "…", "short_name": "ADRASEC 78", "name": "…", "direct": true}],
 "admin_rights": [{"right": "event_organizer", "scope_group_id": "…", "scope_name": "ADRASEC 78", "until": null}],
 "modules": ["adrasec"],
 "module_rights": [{"module": "adrasec", "licensed": true}],
 "is_server_admin": false, "can_administer": true, "rights_version": 12}
```
`modules` : modules **utilisables** (habilitation et licence). `module_rights` : modules donnés par un groupe, avec `licensed`. `can_administer` : montrer Paramètres › Administration. Un appareil sans compte reçoit `groups` = Public seul, listes vides. Le serveur reste juge de chaque appel.

## API d'administration (`/api/v0.1/admin/…`)
Autorisation : un compte ayant **au moins un droit d'administration** (ou administrateur du serveur, `SARCADE_ADMIN_EMAILS`, `Authorization: Bearer <SARCADE_ADMIN_TOKEN>`). Sans identité : 401 `account_required` ; sans droit : 403 `admin_required`. Chaque route vérifie ensuite le droit précis.

Erreurs de l'annuaire : `{"detail": "<code>"}` ou `{"detail": {"error": "<code>", …}}`. Chaque refus est inscrit au journal (`rights` / `directory_refused`). Codes : `group_not_found` 404, `group_not_visible` 403, `group_admin_required` 403, `member_manager_required` 403, `server_admin_required` 403, `no_elevation` 403 (`right`), `cycle` 409 (`path` : noms courts du chemin, `message` : « Impossible : VCMB contient déjà Signaleurs VCMB, qui contient ADRASEC 78 »), `too_deep` 409 (`levels`, `max_depth`, `message`), `last_admin` 409 (`message`), `short_name_taken` 409, `habilitation_exists` 409 (`id`), `group_has_subgroups` 409, `public_not_nested` 422, `public_has_no_member_list` 422, `public_rights_refused` 422, `system_group` 422, `unknown_right` 422, `scope_not_allowed` 422, `invalid_until` 422, `until_in_past` 422, `invalid_email` 422, `invalid_short_name` / `invalid_name` / `invalid_color` 422, `email_or_group_id` 422, `too_many_addresses` 413, `member_not_found` / `invitation_not_found` / `habilitation_not_found` / `user_not_found` 404.

### Groupes
- `GET /admin/directory/catalogue` → `{"rights": [{"right", "label", "kind": "admin|module", "scope": "server|group|either|none", "module"?}], "max_depth": 8}`
- `GET /admin/groups` → groupes **visibles** de l'appelant (administrateur du serveur ou auditeur du serveur : tous ; sinon les sous-arbres de ses droits) :
  ```json
  [{"id", "short_name", "name", "description", "color", "parent_id", "system", "organization", "organization_id",
    "member_groups": [ids], "member_of": [ids], "children": [ids], "users": 3, "invitations": 1,
    "created_at", "updated_at", "can_admin": true, "can_manage_members": true}]
  ```
- `POST /admin/groups` `{"short_name", "name"?, "description"?, "color"?, "parent_id"?, "member_of_parent"?: true}` → 201 groupe. Sans `parent_id` (organisation) : administrateur du serveur.
- `GET /admin/groups/{id}` → groupe + `users`, `invitations`, `groups` (membres), `habilitations`, `ancestors`, `can_admin`, `can_manage_members`.
- `PATCH /admin/groups/{id}` `{"short_name"?, "name"?, "description"?, "color"?, "parent_id"?}` → groupe (rattacher ailleurs : administrer aussi le nouveau parent ; `null` : administrateur du serveur).
- `DELETE /admin/groups/{id}` → `{"deleted": true}` (refusé : groupe système, sous-groupes rattachés).

### Membres
- `GET /admin/groups/{id}/members` → `{"group_id", "public", "users": [{"user_id", "email", "display_name", "status", "added_at", "added_by"}], "invitations": [{"email", "invited_at", "invited_by"}], "groups": [{"id", "short_name"}]}`
- `POST /admin/groups/{id}/members` `{"email": "…"}` → `{"kind": "user", "user_id", "email", "status": "added|already"}` ou `{"kind": "invitation", "email", "status": "invited|already"}` ; `{"group_id": "…"}` → `{"kind": "group", "group_id", "status": "added|already"}` (administrer le groupe d'accueil).
- `POST /admin/groups/{id}/members/import` : corps `text/csv` (une adresse par ligne ; `,` et `;` acceptés ; en-tête `email` ignoré ; 256 Kio, 2000 adresses) ou JSON `{"csv": "…"}` → `{"added", "invited", "already", "invalid": [lignes]}`.
- `DELETE /admin/groups/{id}/members/users/{user_id}` · `DELETE /admin/groups/{id}/members/groups/{member_id}` · `DELETE /admin/groups/{id}/invitations/{email}` → `{"removed": true}`.
- `GET /admin/invitations` → invitations en attente des groupes visibles `[{"group_id", "short_name", "email", "invited_at", "invited_by"}]`.

### Habilitations
- `GET /admin/groups/{id}/habilitations` → `[{"id", "group_id", "right", "label", "scope_group_id", "scope_name", "until", "expired", "granted_at", "granted_by"}]`
- `POST /admin/groups/{id}/habilitations` `{"right", "scope_group_id"?, "until"?}` → 201 habilitation.
- `PATCH /admin/habilitations/{id}` `{"until": "…Z" | null}` → habilitation.
- `DELETE /admin/habilitations/{id}` → `{"revoked": true}`.

### Droits effectifs, « Pourquoi ce droit ? »
- `GET /admin/users/{user_id}/rights` · `GET /admin/rights?email=…` (adresse sans compte acceptée : droits de Public, `invitations`) :
  ```json
  {"user_id", "email", "version", "server_admin": false, "modules": ["adrasec"],
   "groups": [{"id", "short_name", "name", "direct", "path": ["FNRASEC", "ADRASEC 78"]}],
   "rights": [{"right": "module.adrasec", "label": "Module ADRASEC", "scope_group_id": null, "scope_name": null,
               "until": null, "habilitation_id", "holder_group_id", "holder_name": "FNRASEC",
               "path": [{"id", "short_name": "FNRASEC"}, {"id", "short_name": "ADRASEC 78"}],
               "path_text": "Module ADRASEC ← FNRASEC ← ADRASEC 78 ← paul@adrasec78.fr",
               "module": "adrasec", "licensed": true, "organization_id", "organization_name": "FNRASEC"}],
   "account": { …compte… } | null, "invitations"?: [group ids]}
  ```
  Visible de l'administrateur ou de l'auditeur du serveur, sinon seulement pour un compte membre d'un groupe visible (403 `user_not_visible`).
- `GET /admin/users/{user_id}/groups` → groupes directs visibles.
- `PATCH /admin/users/{user_id}` `{"status": "active|disabled", "display_name"}` (administrateur du serveur) → compte. Désactiver un compte refuse ses jetons (401 `device_revoked`).

### Licence d'une organisation
- `GET /admin/groups/{id}/licence` → `{"organization_id", "licence": {…état…} | null, "modules": [modules couverts]}`
- `PUT /admin/groups/{id}/licence` (corps : fichier JSON signé ; administrateur du serveur) → état ; 422 `{"error": "licence_refused", "status", "reason"}` ; 422 `not_an_organization`.
- `DELETE /admin/groups/{id}/licence` → `{"removed": true}`.

Émission (porteur, hors ligne) : `python -m sarcade.licensing.issue sign --key … --organisation "FNRASEC" --kind association --modules adrasec --out fnrasec.licence.json`.

### Journal de sécurité
`GET /admin/security-journal` est aussi ouvert à l'**auditeur du serveur** (lecture seule). Entrées : `admin` / `group_created`, `group_updated`, `group_deleted`, `organization_licence_uploaded`, `organization_licence_removed` ; `rights` / `member_added`, `member_removed`, `invitation_added`, `invitation_removed`, `invitation_accepted`, `members_imported`, `group_member_added`, `group_member_removed`, `habilitation_granted`, `habilitation_until_changed`, `habilitation_revoked`, `account_updated`, `directory_refused`, `admin_required` — toujours avec l'auteur (`actor`).

## Temps réel
Sur tous les canaux d'événement, après chaque changement :
```json
{"type": "rights.changed", "data": {"version": 13, "revocation": true, "reason": "member_removed"}}
```
`revocation: true` quand un droit ou une appartenance est retiré : l'application relit `/me` **en priorité** et masque aussitôt ce qui n'est plus permis.

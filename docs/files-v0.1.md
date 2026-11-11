# Fichiers : dossiers et sous-dossiers (v0.1)

Écran Fichiers, onglets « Événement » et « Personnel ». Chaque fichier porte un `folder` : `""` pour la racine, sinon 1 à 3 segments séparés par `/` (segment de 1 à 60 caractères, sans `/`, `\`, caractère de contrôle, ni `.` / `..`). Un dossier vide existe à part entière (table `file_folders`) ; la liste `folders` contient tous les dossiers connus, parents compris, y compris ceux déduits des fichiers, triés.

Erreurs communes : 400 `invalid_folder`, `folder_too_deep`, `invalid_name` ; 404 `folder_not_found`, `file_not_found` ; 409 `folder_exists`, `folder_inside_itself`, `folder_not_empty`.

## Événement — `/api/v0.1/events/{event_id}/files`

Droits : ceux du dépôt d'un fichier d'événement (tout opérateur). Chaque action est inscrite en main courante (type `file`) ; `actor_id` facultatif (sinon e-mail vérifié, sinon « opérateur »).

| Action | Requête | Réponse |
|---|---|---|
| Liste | `GET …/files` | tableau de fichiers (inchangé), chacun avec `folder` |
| Liste avec dossiers | `GET …/files?with_folders=true` | `{"files": [...], "folders": [...]}` |
| Dossiers | `GET …/files/folders` | `{"folders": [...]}` |
| Créer | `POST …/files/folders` `{"path": "Procédures/Radio", "actor_id"?}` | 201 `{"path", "folders"}` ; parents créés |
| Renommer / déplacer | `PATCH …/files/folders` `{"from": "A/B", "to": "C/B", "actor_id"?}` | 200 `{"from", "to", "folders"}` ; fichiers et sous-dossiers suivent |
| Supprimer | `DELETE …/files/folders?path=A/B[&actor_id=…]` | 204, seulement si vide |
| Déplacer / renommer un fichier | `PATCH …/files/{file_id}` `{"folder"?, "name"?, "actor_id"?}` | 200 fichier |
| Déposer | `POST …/files` (multipart), champ `folder` ou paramètre `?folder=` | 201 fichier |

Diffusions : `file.folders.updated` (`{"folders"}`), `file.updated` (fichier).

## Personnel — `/api/v0.1/me/files`

Propriétaire seulement (e-mail vérifié par Cloudflare Access) ; rien en main courante.

| Action | Requête | Réponse |
|---|---|---|
| Liste | `GET /me/files` | `{"files": [...], "folders": [...], "used_bytes", "quota_bytes"}` |
| Dossiers | `GET /me/files/folders` | `{"folders": [...]}` |
| Créer | `POST /me/files/folders` `{"path"}` | 201 `{"path", "folders"}` |
| Renommer / déplacer | `PATCH /me/files/folders` `{"from", "to"}` | 200 `{"from", "to", "folders"}` |
| Supprimer | `DELETE /me/files/folders?path=…` | 204, seulement si vide |
| Déplacer / renommer un fichier | `PATCH /me/files/{file_id}` `{"folder"?, "name"?}` | 200 fichier |
| Déposer | `POST /me/files` (multipart), champ `folder` ou `?folder=` | 201 fichier |

Les noms de fichiers peuvent se répéter, comme au dépôt. Migration `0021` : les anciens dossiers fixes restent des dossiers (`consignes`, `cartes`…), sauf `divers` qui devient la racine.

Vérification : `tests/test_file_folders.py`, `demo/verify_message_photos.py`.

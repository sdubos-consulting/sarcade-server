# Sauvegarde et essai de restauration du NAS — v0.1

Carte « Consolidation : sauvegarde et essai de restauration du NAS actuel » (lot 28). **Résultat attendu** : perdre au plus une journée de données. **Critère de validation** : le porteur restaure la sauvegarde de la veille sur le serveur de test et y retrouve ses événements, messages et objets de carte.

## Principe

| | |
|---|---|
| Quoi | base de l'exploitation (`pg_dump`, SQL compressé) et fichiers envoyés (`SARCADE_FILE_ROOT` du serveur : dossiers personnels, partagés, photos, licence…) |
| Quand | chaque nuit (Planificateur de tâches DSM), `sarcade-backup` |
| Où | dossier partagé Synology **`sarcade-sauvegardes`** (`/volume1/sarcade-sauvegardes`), hors des volumes Docker ; un dossier `sarcade-AAAAMMJJ-HHMMSS` par sauvegarde |
| Hors du NAS | Hyper Backup copie ce dossier partagé vers un disque externe ou un stockage distant |
| Chiffrement | une clé de session aléatoire par sauvegarde (AES-256, `openssl enc -pbkdf2`), elle-même chiffrée par la **clé publique** du porteur (RSA 4096, OAEP). La **clé privée** est protégée par le mot de passe du porteur et ne sert qu'à restaurer : la sauvegarde de nuit n'a besoin d'aucun secret |
| Rétention | 14 jours (la plus récente est toujours gardée) |
| Alerte | e-mail de DSM si la sauvegarde échoue, et si la dernière sauvegarde réussie a plus de 26 h (`sarcade-backup --check`) |
| Restauration | `sarcade-restore` sur le **serveur de test** (`/volume1/docker/sarcade-test`) ; l'exploitation est refusée sauf secours explicite (`--exploitation` + confirmation tapée) |
| Essai | avant l'exercice, puis chaque mois |

Contenu d'une sauvegarde : `key.enc` (clé de session chiffrée), `db.sql.gz.enc`, `files.tar.gz.enc`, `SHA256SUMS` (contrôlé avant toute restauration), `manifest.json` (date, migration, commit du serveur, empreinte de la clé ; rien de secret).

Non sauvegardé (se reconstruit) : fonds de carte hors ligne, extrait OpenStreetMap, graphe routier, Valhalla, image Docker. Les secrets (`.env`, `secrets/`) ne sont pas dans la sauvegarde : ils restent chez le porteur.

Outils sans dépendance nouvelle : bash, `openssl`, `python3`, `gzip`, `tar`, `sha256sum` du NAS (DSM) ; `pg_dump`/`psql` et `tar` dans les conteneurs existants.

## État lisible par le serveur (Diagnostic administrateur)

Fichiers écrits sur le NAS dans `/volume1/docker/sarcade/backups/`, montés **en lecture seule** dans le serveur d'exploitation à `/var/lib/sarcade/backup-state/` (`tool/nas/backup/compose.backup.yaml`). Variable d'environnement du serveur : `SARCADE_BACKUP_STATE=/var/lib/sarcade/backup-state/last.json` (le fichier de restauration est à côté). Fichier absent : « sauvegarde non configurée ».

`last.json` (écrit par `sarcade-backup` à chaque passage, atomique, UTF-8) :
```json
{
  "format": 1,
  "status": "ok",
  "last_attempt_at": "2026-11-05T01:47:03Z",
  "last_success_at": "2026-11-05T01:47:41Z",
  "last_success_name": "sarcade-20261105-024703",
  "duration_s": 38,
  "db_bytes": 1843200,
  "files_bytes": 52428800,
  "files_count": 112,
  "alembic": "0031",
  "server_commit": "5d07d45",
  "key_fingerprint": "3f9a0c1d2e4b5a69",
  "destination": "/volume1/sarcade-sauvegardes",
  "kept": 14,
  "retention_days": 14,
  "max_age_hours": 26,
  "error": null
}
```
- `status` : `ok` ou `error` (dernier passage) ; `error` : message en français si `status` = `error`, sinon `null`.
- `last_success_*`, tailles, `alembic`, `server_commit`, `key_fingerprint`, `kept` : ceux de la **dernière sauvegarde réussie** (gardés tels quels après un échec ; `null` si aucune).
- Dates en UTC, format `AAAA-MM-JJTHH:MM:SSZ`. **Âge de la dernière sauvegarde** = maintenant − `last_success_at` ; en retard si l'âge dépasse `max_age_hours`.
- `format` : 1 ; un champ ajouté plus tard ne change pas le format, un champ retiré ou changé de sens le passe à 2.

`last-restore.json` (écrit par `sarcade-restore`, dernier essai de restauration) :
```json
{
  "format": 1,
  "status": "ok",
  "restored_at": "2026-11-05T19:12:30Z",
  "backup_name": "sarcade-20261105-024703",
  "target": "/volume1/docker/sarcade-test",
  "alembic": "0031",
  "counts": {"events": 4, "messages": 230, "map_features": 57, "pois": 12, "logbook": 410, "users": 9, "shared_files": 6, "personal_files": 18},
  "error": null
}
```

## Actions du porteur (dans l'ordre)

Les blocs se lancent **en root sur le NAS** (`sudo su -`), se relancent sans risque et n'affichent aucun secret. Les blocs entre parenthèses s'arrêtent à la première erreur sans fermer la session. Le mot de passe de la clé n'est jamais dans un bloc : `openssl` le demande lui-même, sans l'afficher.

### 1. Dossier partagé (DSM)
**Panneau de configuration → Dossier partagé → Créer** : nom `sarcade-sauvegardes`, emplacement Volume 1, cocher « Masquer ce dossier partagé dans "Mes emplacements réseau" », corbeille non cochée, chiffrement du dossier inutile (sauvegardes déjà chiffrées) ; droits : administrateurs en lecture/écriture, personne d'autre.

### 2. Outils et contrôle (NAS)
```
( set -e; for t in sarcade-backup sarcade-restore; do curl -fsSL "https://raw.githubusercontent.com/sdubos-consulting/sarcade-server/main/tool/nas/$t.sh" -o "/usr/local/bin/$t"; chmod 755 "/usr/local/bin/$t"; done
openssl version; python3 --version; ls -ld /volume1/sarcade-sauvegardes; df -h /volume1 | tail -1; sarcade-backup --status )
```
Résultat attendu : `OpenSSL 1.1.1…` ou `OpenSSL 3.…`, `Python 3.…`, `drwx… /volume1/sarcade-sauvegardes`, la place libre, puis `Aucune sauvegarde enregistrée (…/backups/last.json absent)` et `Destination : /volume1/sarcade-sauvegardes (0 sauvegardes, …)`. (Après la fusion du « serveur de test », `sarcade-autoupdate` réinstalle ensuite lui-même ces outils.)

### 3. Clé de chiffrement (NAS, une fois)
```
sarcade-backup --init-key
```
`openssl` demande **trois fois** le mot de passe (choix, confirmation, vérification) : une phrase longue (5 mots ou plus), gardée dans le gestionnaire de mots de passe. Résultat attendu : `Clé créée : /volume1/docker/sarcade/secrets/backup-key.pem (privée, protégée par le mot de passe) et …backup-key.pub.pem (publique)` puis `Empreinte : ` suivie de 16 caractères. Relancé, le bloc répond `clé déjà présente` et ne change rien.

Copie de la clé privée hors du NAS (sans elle et sans le mot de passe, **aucune sauvegarde n'est lisible**, même par le porteur) :
```
U=steph; install -m 600 -o "$U" /volume1/docker/sarcade/secrets/backup-key.pem "/volume1/homes/$U/sarcade-backup-key.pem" && ls -l "/volume1/homes/$U/sarcade-backup-key.pem"
```
Résultat attendu : `-rw------- 1 steph … /volume1/homes/steph/sarcade-backup-key.pem` (remplacer `steph` par l'utilisateur DSM si besoin). Puis **File Station → home** : télécharger `sarcade-backup-key.pem` sur le PC, le joindre à l'entrée du gestionnaire de mots de passe (avec le mot de passe), puis le **supprimer** du dossier home.

### 4. État visible par le serveur (NAS)
Ajoute `compose.backup.yaml` à la liste `COMPOSE_FILE` du `.env` de l'exploitation (sans toucher à `compose.yaml` ni à `docker-compose.override.yml`), puis recrée le conteneur du serveur (coupure d'une vingtaine de secondes).
```
( set -e; cd /volume1/docker/sarcade
curl -fsSL https://raw.githubusercontent.com/sdubos-consulting/sarcade-server/main/tool/nas/backup/compose.backup.yaml -o compose.backup.yaml
mkdir -p backups && chmod 755 backups
main=$(for f in compose.yaml compose.yml docker-compose.yml docker-compose.yaml; do [ -f "$f" ] && echo "$f" && break; done)
ovr=$(for f in docker-compose.override.yml docker-compose.override.yaml compose.override.yaml compose.override.yml; do [ -f "$f" ] && echo "$f" && break; done)
[ -f .env.avant-sauvegardes ] || cp -p .env .env.avant-sauvegardes
if grep -q '^COMPOSE_FILE=' .env; then grep '^COMPOSE_FILE=' .env | grep -q 'compose.backup.yaml' || sed -i 's/^COMPOSE_FILE=\(.*\)$/COMPOSE_FILE=\1:compose.backup.yaml/' .env
else echo "COMPOSE_FILE=$main${ovr:+:$ovr}:compose.backup.yaml" >> .env; fi
docker compose config --services >/dev/null || { cp -p .env.avant-sauvegardes .env; echo "configuration refusée : .env remis comme avant"; exit 1; }
echo "Fichiers Compose : $(grep '^COMPOSE_FILE=' .env | cut -d= -f2)"
docker compose up -d server
for i in $(seq 1 45); do curl -fsS --max-time 3 http://localhost:8000/health >/dev/null 2>&1 && break; sleep 2; done
docker compose exec -T server sh -c 'echo "$SARCADE_BACKUP_STATE"; ls -ld /var/lib/sarcade/backup-state'
curl -fsS http://localhost:8000/health; echo )
```
Résultat attendu : `Fichiers Compose : compose.yaml:docker-compose.override.yml:compose.backup.yaml`, la recréation de `sarcade-server-1` (ou nom équivalent), `/var/lib/sarcade/backup-state/last.json`, `drwxr-xr-x … /var/lib/sarcade/backup-state`, puis `{"status":"ok"…}`. En cas de refus de la configuration, le `.env` est remis comme avant et rien ne change.

### 5. Première sauvegarde à la main (NAS)
```
sarcade-backup && sarcade-backup --status && sarcade-backup --check
```
Résultat attendu (de quelques secondes à quelques minutes selon les fichiers) : `… sauvegarde sarcade-AAAAMMJJ-HHMMSS : base …, fichiers … (N), migration 00NN, 1 gardées`, puis `État : ok`, `Dernière OK : …, il y a 0 h …`, la liste, et `OK : dernière sauvegarde réussie il y a 0 h`.

### 6. Tâches planifiées et e-mail (DSM)
Prérequis : **Panneau de configuration → Notification → E-mail** activé (adresse du porteur), « Envoyer un e-mail de test » reçu.

**Panneau de configuration → Planificateur de tâches → Créer → Tâche planifiée → Script défini par l'utilisateur**, deux tâches :
1. « SARCADE sauvegarde » — utilisateur **root** ; Programmer : **tous les jours**, **02:47** ; Paramètres de tâche : cocher « Envoyer les détails d'exécution par e-mail » et « Envoyer les détails d'exécution uniquement lorsque le script se termine de manière anormale » ; script : `/usr/local/bin/sarcade-backup`.
2. « SARCADE contrôle sauvegarde » — utilisateur **root** ; **tous les jours**, **08:13** ; mêmes cases e-mail ; script : `/usr/local/bin/sarcade-backup --check`.

Vérification : clic droit sur « SARCADE contrôle sauvegarde » → **Exécuter** : pas d'e-mail (tout va bien). Pour recevoir une fois l'alerte : tâche ponctuelle (utilisateur root, mêmes cases e-mail) avec le script `SARCADE_BACKUP_MAX_AGE_H=-1 /usr/local/bin/sarcade-backup --check`, **Exécuter** : e-mail « ERREUR : dernière sauvegarde réussie il y a 0 h (plus de -1 h) … » ; puis supprimer cette tâche.

### 7. Copie hors du NAS (Hyper Backup)
**Hyper Backup → + → Tâche de sauvegarde de données** : destination **disque USB local** (ou Synology C2 / autre NAS) ; dossier source **`sarcade-sauvegardes`** ; planification **tous les jours à 04:30** (après la sauvegarde de 02:47) ; rotation : garder 30 versions. Chiffrement côté Hyper Backup facultatif (contenu déjà chiffré). Lancer une première fois « Sauvegarder maintenant » : état **Réussi**.

### 8. Essai de restauration sur le serveur de test (NAS, avant l'exercice puis chaque mois)
Prérequis : serveur de test installé (`docs/nas-test-server-v0.1.md`). **La base et les fichiers du serveur de test sont remplacés.**
```
sarcade-restore --list | tail -n 3 && sarcade-restore
```
Le mot de passe de la clé est demandé une fois. Résultat attendu : les trois dernières sauvegardes, puis `== Sauvegarde sarcade-… vers /volume1/docker/sarcade-test`, `== Base : effacement de sarcade puis restauration`, `== Fichiers envoyés` (nombre de fichiers), `== Redémarrage du serveur cible`, `== Terminé`, `Base : migration 00NN` et `Contenu : events=… messages=… map_features=… pois=… logbook=… users=… shared_files=… personal_files=…`.

**Recette** (critère de la carte) : sur `test.sarcade.org` (application ou navigateur), retrouver les événements, messages et objets de carte de la veille. `sarcade-backup --status` affiche ensuite `Restauration : ok le …`.

Une autre sauvegarde que la dernière : `sarcade-restore sarcade-AAAAMMJJ-HHMMSS`.

## Secours : restaurer l'exploitation
Seulement si la base de l'exploitation est perdue, après accord du porteur :
```
SARCADE_RESTORE_ROOT=/volume1/docker/sarcade sarcade-restore --exploitation
```
Le script demande de taper `EXPLOITATION`, puis le mot de passe de la clé.

## Limites
- La base et les fichiers sont lus l'un après l'autre (quelques secondes d'écart) : un fichier envoyé pendant la sauvegarde peut manquer à l'une des deux parties.
- Un NAS perdu avec ses disques : il faut la copie Hyper Backup **et** la clé privée **et** son mot de passe (gardés par le porteur) pour restaurer sur une autre machine.
- Pas d'essai sur un vrai Synology dans la CI : la CI fait l'essai de bout en bout avec de vrais conteneurs (`tests/nas/e2e_backup_restore.sh`).

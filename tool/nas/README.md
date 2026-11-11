# Outils du NAS

Deux serveurs sur le NAS (lot 28, `docs/nas-test-server-v0.1.md`) : **exploitation** (`/volume1/docker/sarcade`, `app.sarcade.org`, port 8000), sur une version de référence mise à la main ; **test** (`/volume1/docker/sarcade-test`, `test.sarcade.org`, port 8010), mis à jour à la main. Les outils visent l'exploitation par défaut, le test avec `SARCADE_ROOT=/volume1/docker/sarcade-test`.

Depuis le 10 novembre 2026 (carte « GitHub de nouveau actif (10 novembre 2026) »), le serveur d'exploitation est mis à jour par **Watchtower** depuis le registre du NAS : l'image est construite sur l'i9, client web compris, par `publish-registry.ps1` (secours : `publish-nas.ps1`). Plus rien n'est pris sur GitHub, qui ne reçoit que les grandes versions validées (`tool/publish-github.ps1`). L'ancien `sarcade-autoupdate` (qui interrogeait GitHub et la pré-version `web-dev`) est retiré.

## sarcade-switch
Bascule un serveur du NAS sur une étiquette ou une branche en une commande : sauvegarde de la base si la cible ajoute des migrations, refus de revenir sur une version plus ancienne que la base, reconstruction (avec le dernier client web), attente de `/health`.

Utilisation : `sarcade-switch v0.1.0-exercice1` (exploitation, version de référence), `SARCADE_ROOT=/volume1/docker/sarcade-test sarcade-switch feat/aprs` (recette d'une PR sur le test), `sarcade-switch --status`, `sarcade-switch --list`. Une branche est refusée sur l'exploitation, sauf dépannage avec `--branche`.

## Clé Météo-France (radar de pluie)
`meteofrance-cle-installer.ps1`, lancé par le porteur sur l'i9 (PowerShell normal) : clé de l'API « Données radar » (jeton de type API Key du portail Météo-France) prise dans `C:\dev\secrets\API Meteo-France.txt` ou collée à la demande, rangée dans `C:\dev\secrets\meteofrance\api-key.txt`, envoyée au NAS dans un fichier à part et placée par `meteofrance-nas.sh` dans le `.env` de l'exploitation (`SARCADE_METEOFRANCE_API_KEY`), serveur redémarré, puis contrôle de `/api/v0.1/weather/radar` (`ok` attendu). Jamais affichée ; relançable (nouvelle clé à l'expiration).

## Client web à jour
Le client web est intégré à l'image construite sur l'i9 (`SARCADE_WEB=local`, valeur par défaut du `Dockerfile`, archive `sarcade-web.tar.gz` posée à côté). Sans cette archive, l'image ne sert que l'API. Vérification sur le NAS :
```
curl -fsS http://localhost:8000/sarcade-web.sha256 && curl -fsS http://localhost:8000/sarcade_build.json
```
Résultat attendu : l'empreinte `sha256:…` du client servi, puis `"build":"main@<commit>"` du dernier `main` de l'application publié par `publish-registry.ps1`.

## Inventaire des composants (SBOM)
Chaque construction de l'image (`publish-registry.ps1` sur l'i9, `sarcade-switch`, `docker compose build server`) écrit l'inventaire des composants de l'image réellement construite (CycloneDX : paquets Python, paquets du système, client web embarqué), servi à `/sarcade-sbom.cdx.json`, à côté de `/sarcade-web.sha256`. Si sa génération échoue, la construction continue et l'adresse répond 404 (journal de construction : `sbom: not generated (…)`). Détail : `docs/security-vex-v0.1.md`.

Vérification sur le NAS :
```
curl -fsS -o /tmp/sarcade-sbom.json http://localhost:8000/sarcade-sbom.cdx.json && grep -o '"bomFormat": *"CycloneDX"' /tmp/sarcade-sbom.json && grep -c '"purl"' /tmp/sarcade-sbom.json
```
Résultat attendu : `"bomFormat": "CycloneDX"` puis le nombre de composants (de l'ordre de quelques centaines). `curl: (22) … 404` : SBOM non produit, voir le journal de construction.

## Ancienne mise à jour automatique (retirée)
`sarcade-autoupdate` n'existe plus dans le dépôt. Si la tâche DSM « SARCADE mise à jour automatique » existe encore sur le NAS : **Panneau de configuration → Planificateur de tâches**, la sélectionner, **Supprimer**, puis en root `rm -f /usr/local/bin/sarcade-autoupdate`.

## sarcade-backup et sarcade-restore
Sauvegarde chiffrée de l'exploitation chaque nuit (base et fichiers envoyés, 14 jours, dossier partagé `sarcade-sauvegardes` copié par Hyper Backup, e-mail DSM si une sauvegarde manque) et restauration sur le serveur de test. Détail, format de l'état `backups/last.json` lu par le Diagnostic et blocs d'installation : `docs/nas-backup-v0.1.md`.

Commandes : `sarcade-backup --status`, `sarcade-backup --check`, `sarcade-restore --list`, `sarcade-restore` (dernière sauvegarde vers le serveur de test, mot de passe de la clé demandé).

# Outils du NAS de test

## sarcade-switch
Bascule le serveur du NAS sur une branche en une commande : sauvegarde de la base si la branche ajoute des migrations, refus de revenir sur une branche plus ancienne que la base, reconstruction (avec le dernier client web), attente de `/health`.

Utilisation : `sarcade-switch main`, `sarcade-switch feat/aprs`, `sarcade-switch --status`, `sarcade-switch --list`.

## Client web à jour
L'image du serveur vérifie elle-même, à chaque construction, la dernière publication du client web (`web-dev` de `sarcade-app`) : une reconstruction normale prend le nouveau client s'il y en a un, et garde le cache sinon. Plus besoin de `SARCADE_WEB_CACHEBUST` (gardé seulement pour forcer à la main).

Mise à jour manuelle sur le NAS (en root), serveur et client web :
```
sarcade-switch main
```
Client web seul, sans toucher au code du serveur :
```
cd /volume1/docker/sarcade && docker compose build server && docker compose up -d server && sleep 20 && curl -fsS http://localhost:8000/sarcade-web.sha256 && curl -fsS https://api.github.com/repos/sdubos-consulting/sarcade-app/releases/tags/web-dev | grep -o '"digest": *"[^"]*"'
```
Résultat attendu : deux fois la même empreinte `sha256:…` (celle du client servi, puis celle de la dernière publication `web-dev`). Si rien n'a été publié depuis la construction précédente, Docker garde son cache et la construction ne prend que quelques secondes. `sarcade-switch` finit par `Terminé` et `Santé : {"status":"ok"…}` ; la même vérification d'empreinte s'applique ensuite.

## Inventaire des composants (SBOM)
Chaque construction de l'image (`sarcade-switch`, `sarcade-autoupdate`, `docker compose build server`) écrit l'inventaire des composants de l'image réellement construite (CycloneDX : paquets Python, paquets du système, client web embarqué), servi à `/sarcade-sbom.cdx.json`, à côté de `/sarcade-web.sha256`. Si sa génération échoue, la construction continue et l'adresse répond 404 (journal de construction : `sbom: not generated (…)`). Détail : `docs/security-vex-v0.1.md`.

Vérification sur le NAS :
```
curl -fsS -o /tmp/sarcade-sbom.json http://localhost:8000/sarcade-sbom.cdx.json && grep -o '"bomFormat": *"CycloneDX"' /tmp/sarcade-sbom.json && grep -c '"purl"' /tmp/sarcade-sbom.json
```
Résultat attendu : `"bomFormat": "CycloneDX"` puis le nombre de composants (de l'ordre de quelques centaines). `curl: (22) … 404` : SBOM non produit, voir le journal de construction.

## sarcade-autoupdate
Met le NAS à jour tout seul depuis GitHub, toutes les 5 minutes (Planificateur de tâches de DSM) :
- **nouveau commit sur `main`** dont la CI est au vert → `sarcade-switch main` (sauvegarde, migrations, reconstruction) ;
- **nouveau client web** publié (`web-dev`) → reconstruction du serveur seul ;
- **NAS basculé à la main** sur une autre branche (recette d'une PR) → rien, jusqu'au retour sur `main` (`sarcade-switch main`) ;
- **CI en échec** → rien, le NAS reste sur la dernière version verte ;
- **échec d'une mise à jour** → pause automatique (fichier `autoupdate.off`) et message dans le journal.

Commandes : `sarcade-autoupdate --status` (état et derniers passages), `sarcade-autoupdate --dry-run` (dit ce qu'il ferait).
Pause : `touch /volume1/docker/sarcade/autoupdate.off` ; reprise : `rm /volume1/docker/sarcade/autoupdate.off`.
Journal : `/volume1/docker/sarcade/autoupdate.log`.

Les outils se réinstallent eux-mêmes depuis le dépôt après chaque mise à jour du serveur.

## Installation (une fois, en root)
```
cd /volume1/docker/sarcade && git -C sarcade-server remote set-url origin https://github.com/sdubos-consulting/sarcade-server.git && for t in sarcade-switch sarcade-autoupdate; do curl -fsSL https://raw.githubusercontent.com/sdubos-consulting/sarcade-server/main/tool/nas/$t.sh -o /usr/local/bin/$t && chmod +x /usr/local/bin/$t; done && sarcade-autoupdate --dry-run
```

Puis dans DSM : **Panneau de configuration → Planificateur de tâches → Créer → Tâche planifiée → Script défini par l'utilisateur** :
- Général : nom `SARCADE mise à jour automatique`, utilisateur **root** ;
- Programmer : **tous les jours**, fréquence **toutes les 5 minutes**, de 00:00 à 23:55 ;
- Paramètres de tâche : `/usr/local/bin/sarcade-autoupdate`.

## sarcade-backup et sarcade-restore
Sauvegarde chiffrée de l'exploitation chaque nuit (base et fichiers envoyés, 14 jours, dossier partagé `sarcade-sauvegardes` copié par Hyper Backup, e-mail DSM si une sauvegarde manque) et restauration sur le serveur de test. Détail, format de l'état `backups/last.json` lu par le Diagnostic et blocs d'installation : `docs/nas-backup-v0.1.md`.

Commandes : `sarcade-backup --status`, `sarcade-backup --check`, `sarcade-restore --list`, `sarcade-restore` (dernière sauvegarde vers le serveur de test, mot de passe de la clé demandé).

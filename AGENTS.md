# AGENTS.md — sarcade-server

Consignes pour tout assistant de code (Claude, ChatGPT/Codex, autre) qui reprend ce dépôt. À lire avant toute modification.

## Le projet en bref
SARCADE coordonne les bénévoles ADRASEC (radioamateurs, sécurité civile) : carte partagée, positions, messages prioritaires, relevés goniométriques, APRS, main courante. Ce dépôt est le **serveur** (FastAPI, PostgreSQL/PostGIS, Alembic). Clients : `sdubos-consulting/sarcade-app` (Flutter). État actuel : `SARCADE/00 - Accueil/Reprise du projet par un autre assistant.md` du coffre (seule page d'état à jour ; `99 - Archives` = notes périmées). Spécifications et suivi : dépôt privé `sdubos-consulting/sarcade-vault` (coffre Obsidian, dossier `SARCADE/`, Kanban dans `08 - Backlog/SARCADE - Kanban.md`).

## Règles de travail avec le porteur (Stéphane, SDUBOS Consulting)
1. **Aucun développement sans demande explicite.** Une idée nouvelle → proposition écrite avec des **choix par défaut numérotés**, validée avant de coder.
2. Réponses **en français**, style direct.
3. **Aucun secret dans la conversation** (mots de passe, clés, contenu de fichiers JSON de comptes de service). Les secrets vivent dans les secrets GitHub ou le `.env` du serveur, placés par le porteur.
4. Commandes à exécuter par le porteur : **un seul bloc copiable par machine**, avec le **résultat attendu**.
5. Une branche et une PR par sujet ; **fusion seulement après accord du porteur et CI verte**.
   **Une fusion n'est pas une recette** : après fusion, la carte du Kanban va en « À recetter » ; elle ne passe en « Validé » que sur confirmation du porteur.
6. Commits signés `SDUBOS Consulting <github@sdubos.com>` (identité git du dépôt), et **certifiés DCO** : `git commit -s` ajoute `Signed-off-by: SDUBOS Consulting <github@sdubos.com>` (contrôle « DCO » de la CI, voir `CONTRIBUTING.md`).
7. Tenir le coffre à jour après chaque étape : note de spécification (statut), Kanban, fiche de test.

## Lancer les tests
```bash
pip install -e '.[dev]'          # ou : uv sync --extra dev
python -m pytest -q              # tests unitaires (SQLite, rapides)
```
La CI (`.github/workflows/ci.yml`) lance aussi `demo-integration` : PostGIS + serveur en Docker, puis les scripts `demo/verify_*.py` (un par fonction : APRS, groupes, réveil, HTTPS…). Ajouter un `verify_*.py` pour toute nouvelle API.

## Travail à plusieurs assistants (Claude et ChatGPT en parallèle)
Décision du porteur, 28 octobre 2026, 23 h 10.
1. **Un sujet = un assistant.** Prendre une carte la passe en « En cours » du Kanban avec l'étiquette `#claude` ou `#chatgpt` ; une carte étiquetée par l'autre ne se touche pas. Le porteur attribue les cartes.
2. **Branches préfixées** `claude/…` ou `chatgpt/…`, une PR par sujet, branche remise à jour depuis `main` avant la PR et après chaque fusion.
3. **Zones séparées** autant que possible (par dépôt ou par écran) ; `lib/src/features/map/operational_map_page.dart` est le fichier le plus exposé aux conflits : y faire des modifications courtes, ou extraire d'abord le code concerné dans un fichier à part.
4. **Ressources numérotées réservées** sur la carte avant écriture : numéro de migration Alembic, ajout de dépendance (`uv.lock`, `pubspec.lock`) — un seul à la fois.
5. **Une seule IA fusionne : Claude** (décision du porteur, 28 octobre 2026, 23 h 15), après accord du porteur, une PR à la fois, CI verte, y compris les PR de ChatGPT. **ChatGPT ne fusionne jamais** : il ouvre ses PR et les signale au porteur.
7. **Découpage** (23 h 15) : Claude = serveur (dont toutes les migrations), Paramètres, structure de l'application (rail, onglets, démarrage, pastille des liaisons) ; ChatGPT = carte et relevés (signal recherché, sélection, édition graphique, filtres, couches, couleurs des opérateurs, favoris dans Équipes, liste par fraîcheur). Messages, Fichiers, Météo, Itinéraires : au premier libre, sur attribution du porteur.
6. **Coffre partagé** : `git pull` juste avant d'écrire, petit commit juste après ; section « Qui fait quoi » dans la note de reprise.

## Pièges connus
- **Migrations Alembic** dans `migrations/versions/`, numérotées `00NN_*.py` en chaîne stricte. Avant de créer une migration, vérifier le dernier numéro sur `main` (deux branches ont déjà créé la même 0015).
- **Dépendances** : `uv.lock` est figé (l'image Docker installe avec hachages). Toute dépendance ajoutée dans `pyproject.toml` impose `uv lock` (un oubli a cassé l'image le 28 octobre 2026).
- Deux serveurs sur le NAS (lot 28, `docs/nas-test-server-v0.1.md`) : le **serveur de test** (`test.sarcade.org`), mis à jour à la main par `sarcade-switch` ; l'**exploitation** (`app.sarcade.org`) est mise à jour par Watchtower depuis le registre du NAS (image construite sur l'i9 par `tool/nas/publish-registry.ps1`). `sarcade-autoupdate` est retiré et GitHub ne reçoit que les grandes versions validées (`tool/publish-github.ps1`, carte « GitHub de nouveau actif (10 novembre 2026) »).
- Le compose du NAS est personnalisé : toute nouvelle variable d'environnement doit être ajoutée par le porteur dans `docker-compose.override.yml` du NAS (`/volume1/docker/sarcade`).
- Secrets serveur : `.env` du NAS ; fichiers (ex. compte de service Firebase) dans `/volume1/docker/sarcade/secrets/`, montés en lecture seule.

## Repères dans le code
- `src/sarcade/api/app.py` : application, synchronisation (`/api/v0.1/sync`, une opération = un savepoint), temps réel.
- `src/sarcade/<domaine>/` : un module par fonction (aprs, groups, routes, wakeup, updates, security, licensing…), routes dans `src/sarcade/api/`.
- `docs/*-v0.1.md` : contrat de chaque fonction (à mettre à jour avec le code).

## Accès dont l'assistant a besoin
- GitHub sur `sarcade-server`, `sarcade-app`, `sarcade-vault` : Contents, Pull requests, Actions en lecture/écriture ; Checks en lecture.
- Un environnement Linux avec git, Python ≥ 3.12, pytest, uv ; réseau vers GitHub et PyPI.
- Pas d'accès au NAS, à la Play Console, à Firebase ni à Cloudflare : le porteur exécute, l'assistant prépare les blocs.

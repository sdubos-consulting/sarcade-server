# Client Web (PWA) servi par le serveur

Le serveur SARCADE sert lui-même le client Web : accès depuis n'importe quel navigateur du réseau, et application installable (PWA), canal principal sur ChromeOS. Le client partage alors l'origine de l'API : pas de CORS, et il fonctionne sur un réseau local sans Internet.

## Activation
**Avec Docker (cas normal)** : l'image du serveur contient déjà le client Web, téléchargé à la construction depuis la pré-version `web-dev` de `sarcade-app` (mise à jour par sa CI). Après `docker compose up -d --build`, le client s'ouvre à l'adresse du serveur, par exemple `http://serveur:8000/`, dans Chrome, Edge, Firefox ou Safari.

- **Client toujours à jour, sans réglage** : le client est téléchargé par un `ADD` du `Dockerfile`, que Docker vérifie à chaque construction. Si une nouvelle version a été publiée sur `web-dev`, la construction suivante la prend (même quand le serveur a été fusionné avant l'application) ; sinon le cache est gardé. Une reconstruction normale suffit : `docker compose build server` puis `docker compose up -d server`. Le journal de construction affiche `web client: embedded in /app/web, {"app_name":…,"version":…,"build_number":…}`.
- Forçage manuel (normalement inutile) : `--build-arg SARCADE_WEB_CACHEBUST=$(date +%s)` réinstalle le client.
- Autre archive : `--build-arg SARCADE_WEB_URL=<adresse d'un sarcade-web.tar.gz>`.
- Image sans client Web (API seule, aucun téléchargement du client) : `--build-arg SARCADE_WEB=none`.
- Construction sans accès à GitHub : la construction s'arrête sur l'étape `ADD` (le serveur en place continue de tourner) ; construire avec `--build-arg SARCADE_WEB=none`, ou utiliser l'image publiée sur ghcr.io.
- Docker avec BuildKit requis (par défaut depuis Docker 23, Docker Desktop, Container Manager de Synology DSM 7.2).

**Sans Docker** : extraire l'artefact `sarcade-web.tar.gz` dans un dossier, par exemple `/opt/sarcade/web`, puis définir `SARCADE_WEB_ROOT=/opt/sarcade/web` et redémarrer le serveur.

Sans client Web (variable absente ou dossier sans `index.html`), rien n'est servi à `/` et l'API fonctionne comme avant.

## Comportement
- Monté en dernier, à `/` : les routes `/health` et `/api/...` restent prioritaires.
- `index.html`, `manifest.json`, `flutter_bootstrap.js`, `version.json` et le service worker sont envoyés avec `Cache-Control: no-cache`, pour qu'une nouvelle version du client soit prise en compte au démarrage suivant.

## Limite actuelle : HTTPS
Chrome n'autorise l'installation de la PWA et la géolocalisation que sur HTTPS (ou `localhost`). En HTTP sur le réseau, le client s'ouvre dans un onglet mais ne s'installe pas et ne partage pas la position. Le passage en HTTPS est une étape à part.

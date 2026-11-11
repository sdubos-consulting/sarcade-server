# Client Web (PWA) servi par le serveur

Le serveur SARCADE sert lui-même le client Web : accès depuis n'importe quel navigateur du réseau, et application installable (PWA), canal principal sur ChromeOS. Le client partage alors l'origine de l'API : pas de CORS, et il fonctionne sur un réseau local sans Internet.

## Activation
**Avec Docker (cas normal)** : l'image du serveur contient le client Web construit sur l'i9 (`SARCADE_WEB=local`, valeur par défaut du `Dockerfile` depuis le 10 novembre 2026, carte « GitHub de nouveau actif (10 novembre 2026) ») : archive `sarcade-web.tar.gz` de `sarcade-app` (`tool/build-web.ps1`) posée à côté du `Dockerfile`, ce que fait `tool/nas/publish-registry.ps1`. Le client s'ouvre à l'adresse du serveur, par exemple `http://serveur:8000/`, dans Chrome, Edge, Firefox ou Safari. La pré-version `web-dev` de GitHub n'existe plus.

- Sans archive à côté du `Dockerfile` : image sans client Web (API seule), la construction ne s'arrête pas.
- Dernière grande version publiée sur GitHub : `--build-arg SARCADE_WEB=release` (archive de `releases/latest` de `sarcade-app`, déposée par `tool/publish-github.ps1`), ou une autre archive avec `--build-arg SARCADE_WEB_URL=<adresse d'un sarcade-web.tar.gz>`.
- Forçage manuel (normalement inutile) : `--build-arg SARCADE_WEB_CACHEBUST=$(date +%s)` réinstalle le client.
- Image sans client Web (API seule) : `--build-arg SARCADE_WEB=none`.
- Docker avec BuildKit requis (par défaut depuis Docker 23, Docker Desktop, Container Manager de Synology DSM 7.2).

**Sans Docker** : extraire l'artefact `sarcade-web.tar.gz` dans un dossier, par exemple `/opt/sarcade/web`, puis définir `SARCADE_WEB_ROOT=/opt/sarcade/web` et redémarrer le serveur.

Sans client Web (variable absente ou dossier sans `index.html`), rien n'est servi à `/` et l'API fonctionne comme avant.

## Comportement
- Monté en dernier, à `/` : les routes `/health` et `/api/...` restent prioritaires.
- `index.html`, `manifest.json`, `flutter_bootstrap.js`, `version.json` et le service worker sont envoyés avec `Cache-Control: no-cache`, pour qu'une nouvelle version du client soit prise en compte au démarrage suivant.

## Limite actuelle : HTTPS
Chrome n'autorise l'installation de la PWA et la géolocalisation que sur HTTPS (ou `localhost`). En HTTP sur le réseau, le client s'ouvre dans un onglet mais ne s'installe pas et ne partage pas la position. Le passage en HTTPS est une étape à part.

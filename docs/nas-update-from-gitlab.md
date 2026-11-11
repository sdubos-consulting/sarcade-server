# Mise à jour du NAS sans GitHub

GitHub est suspendu depuis le 6 novembre 2026 : le NAS ne peut plus prendre
son code sur `github.com` ni le client web sur la préversion `web-dev`.
Depuis le 8 novembre 2026, le serveur d'exploitation (`/volume1/docker/sarcade`,
port 8000, `app.sarcade.org`) est mis à jour depuis l'i9 :

| Élément | Source |
|---|---|
| Code du serveur | dépôt `sarcade/sarcade-server` du Forgejo du NAS (`/volume1/docker/forgejo/data/git/repositories/sarcade/sarcade-server.git`), alimenté par l'i9 après chaque fusion (depuis le 11 novembre 2026 ; avant : copie GitLab du Git Server `/volume1/git/sarcade-server.git`) |
| Client web | archive construite sur l'i9 par `sarcade-app/tool/build-web.ps1` (`C:\dev\sarcade\publications\web\`) |
| Image | construite sur le NAS, `SARCADE_WEB=local` (étape `web-local` du `Dockerfile`) |

## Lancer la mise à jour

PowerShell normal, sur l'i9 (mot de passe de `steph` sur le NAS, trois fois) :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\dev\sarcade\sarcade-server\tool\nas\publish-nas.ps1
```

Le script copie l'archive du client web dans `/tmp` du NAS, puis lance
`tool/nas/sarcade-update-gitlab.sh` en root :

1. vérifie que le service `server` se construit depuis le clone `sarcade-server` ;
2. pointe `origin` du clone sur la copie GitLab du NAS ;
3. sauvegarde la base (`sauvegardes/sarcade-<date>-avant-<commit>.sql.gz`) ;
4. garde l'image en service sous `sarcade-server:avant-<commit>` ;
5. passe le code sur `main`, reconstruit l'image avec le client web et le
   commit (`/version` le donne), redémarre ;
6. attend `/health`, puis affiche `/version` et `sarcade_build.json`.

Résultat attendu : `== Terminé`, puis `"commit":"<main du serveur>"` et
`{"build":"main@<main de l'application>", …}`.

## En cas d'échec

Rien n'est annulé automatiquement. Le journal du serveur est affiché avec la
marche à suivre : restaurer la sauvegarde, revenir au commit précédent et
réétiqueter l'image `sarcade-server:avant-<commit>`.

## Limites

- `sarcade-switch` lit encore le dépôt cloné sur le NAS : à reprendre si le
  serveur de test (`/volume1/docker/sarcade-test`, absent au 8 novembre 2026)
  est réinstallé. `sarcade-autoupdate` est retiré (10 novembre 2026, carte
  « GitHub de nouveau actif (10 novembre 2026) ») : l'exploitation est mise à
  jour par Watchtower depuis le registre du NAS (`publish-registry.ps1`).
- Le `docker-compose.yml` du NAS n'est pas celui du dépôt : les nouveautés du
  fichier du dépôt (LiveKit pour le PTT, profil `ptt`) ne sont pas reprises.

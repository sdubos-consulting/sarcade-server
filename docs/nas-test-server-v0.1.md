# Serveur de test distinct de l'exploitation (NAS) — v0.1

Carte « Consolidation : serveur de test distinct de la production » (lot 28). **Résultat attendu** : une fusion n'arrive jamais directement sur le serveur de l'exercice. **Critère de validation** : une fusion met à jour `test.sarcade.org` et laisse `app.sarcade.org` inchangé.

## Principe

| | Exploitation | Test |
|---|---|---|
| Adresse | `app.sarcade.org` | `test.sarcade.org` (Cloudflare Access) |
| Dossier du NAS | `/volume1/docker/sarcade` | `/volume1/docker/sarcade-test` |
| Projet Compose | celui du NAS (`compose.yaml` + `docker-compose.override.yml`) | `sarcade-test` (`tool/nas/test/compose.yaml`) |
| Port du NAS | 8000 | 8010 |
| Base, fichiers | volumes de l'exploitation | volumes `sarcade-test_db`, `sarcade-test_files`, `sarcade-test_tiles` |
| Code suivi | une **version de référence** (étiquette git), à la main : `sarcade-switch <étiquette>` | `main`, automatiquement quand la CI est verte (`sarcade-autoupdate`) |
| Recette d'une PR | non (sauf dépannage : `--branche`) | `SARCADE_ROOT=/volume1/docker/sarcade-test sarcade-switch <branche>` |

Même image : les deux serveurs sont construits par le même `Dockerfile` (chacun depuis son clone `sarcade-server`). Les services portent les mêmes noms (`postgres`, `server`) : les outils `tool/nas/*` s'appliquent à l'un ou à l'autre par `SARCADE_ROOT`. Le fichier `instance.env` du dossier (sans secret) donne à chaque outil le nom de l'instance et son adresse de santé :

```
SARCADE_INSTANCE=test
SARCADE_HEALTH=http://localhost:8010/health
```

Les applications choisissent le serveur par la liste des serveurs mémorisés (déjà en place) : ajouter `test.sarcade.org` sur les appareils d'essai.

## Outils

- `sarcade-switch <étiquette>` : exploitation sur une version de référence (code détaché sur l'étiquette). `sarcade-switch <branche>` est **refusé sur l'exploitation** (message : « les essais vont sur le serveur de test ») ; dépannage seulement : `sarcade-switch <branche> --branche`. `--list` montre les étiquettes puis les branches ; `--status` montre l'instance et la version (étiquette, sinon branche).
- `sarcade-autoupdate` (tâche DSM existante, inchangée) : suit désormais **le serveur de test** (`/volume1/docker/sarcade-test`, `main`). Il ne touche jamais à l'exploitation (sauf `SARCADE_AUTO_PRODUCTION=1`, ancien fonctionnement, déconseillé). Tant que le serveur de test n'est pas installé, il ne fait rien et l'écrit une fois par jour dans `/volume1/docker/sarcade/autoupdate.log`. Après chaque mise à jour du test, il réinstalle tous les outils `tool/nas/sarcade-*.sh` dans `/usr/local/bin`.
- Journal et pause du test : `/volume1/docker/sarcade-test/autoupdate.log`, `/volume1/docker/sarcade-test/autoupdate.off`.

## Actions du porteur (dans l'ordre)

Chaque bloc se lance **en root sur le NAS** (`sudo su -`), se relance sans risque (idempotent) et n'affiche aucun secret. Les blocs entre parenthèses s'arrêtent à la première erreur sans fermer la session.

### 1. Place disque (NAS)
```
df -h /volume1 | tail -1 && docker system df
```
Résultat attendu : une ligne `/dev/…  … Avail …` avec au moins **20 Go libres**, puis le tableau des images et volumes Docker. Moins de 20 Go : s'arrêter et me coller le résultat.

### 2. Installation du serveur de test (NAS)
```
( set -e; T=/volume1/docker/sarcade-test; P=/volume1/docker/sarcade; g() { git -c safe.directory='*' "$@"; }
mkdir -p "$T" && cd "$T"
[ -d sarcade-server/.git ] || g clone -q https://github.com/sdubos-consulting/sarcade-server.git sarcade-server
g -C sarcade-server fetch -q origin main && g -C sarcade-server checkout -q --force -B main origin/main
cp sarcade-server/tool/nas/test/compose.yaml compose.yaml
printf 'SARCADE_INSTANCE=test\nSARCADE_HEALTH=http://localhost:8010/health\n' > instance.env
touch .env && chmod 600 .env
add() { grep -q "^$1=" .env || printf '%s=%s\n' "$1" "$2" >> .env; }
add POSTGRES_PASSWORD "$(openssl rand -hex 24)"; add SARCADE_ADMIN_TOKEN "$(openssl rand -hex 24)"
add SARCADE_SERVER_ID sarcade-test; add SARCADE_PUBLIC_URL https://test.sarcade.org; add SARCADE_ACCESS_HOSTS test.sarcade.org
img=$(cd "$P" && docker compose config --images 2>/dev/null | grep -i -m1 postgis || true); add SARCADE_TEST_POSTGRES_IMAGE "${img:-postgis/postgis:16-3.4}"
for k in SARCADE_ACCESS_VERIFY SARCADE_ACCESS_TEAM_DOMAIN SARCADE_ACCESS_AUD SARCADE_ADMIN_EMAILS SARCADE_SMTP_HOST SARCADE_SMTP_PORT SARCADE_SMTP_USER SARCADE_SMTP_PASSWORD SARCADE_SMTP_FROM SARCADE_DEPARTMENT SARCADE_GEOCODER_URL; do l=$(grep -m1 "^$k=" "$P/.env" || true); [ -n "$l" ] && ! grep -q "^$k=" .env && echo "$l" >> .env; done
for f in sarcade-server/tool/nas/sarcade-*.sh; do install -m 755 "$f" "/usr/local/bin/$(basename "$f" .sh)"; done
docker compose up -d --build
for i in $(seq 1 60); do curl -fsS --max-time 3 http://localhost:8010/health >/dev/null 2>&1 && break; sleep 3; done
echo; echo "== Test"; SARCADE_ROOT=$T sarcade-switch --status; echo "== Exploitation"; sarcade-switch --status; echo "Réglages du test : $(cut -d= -f1 .env | tr '\n' ' ')" )
```
Résultat attendu (la construction prend quelques minutes) :
```
== Test
Instance : test (/volume1/docker/sarcade-test)
Version  : main
Commit   : … (dernier commit de main)
Base     : migration 00NN
Santé    : {"status":"ok"…}
== Exploitation
Instance : exploitation (/volume1/docker/sarcade)
Version  : main            (ou l'étiquette si l'étape 5 est faite)
…
Santé    : {"status":"ok"…}
Réglages du test : POSTGRES_PASSWORD SARCADE_ADMIN_TOKEN SARCADE_SERVER_ID SARCADE_PUBLIC_URL SARCADE_ACCESS_HOSTS SARCADE_TEST_POSTGRES_IMAGE SARCADE_ACCESS_VERIFY …
```
Seuls les **noms** des réglages s'affichent. Le mot de passe de la base et le jeton d'administration du test sont tirés au hasard ; Access, administrateurs et Brevo sont recopiés depuis le `.env` de l'exploitation (sans affichage). Le serveur de test démarre avec une base vide (comptes et événements à créer, ou restauration d'une sauvegarde de l'exploitation : `sarcade-restore`, carte « sauvegarde et essai de restauration »). Pas de fonds de carte hors ligne ni de calcul d'itinéraire sur le test (Valhalla reste propre à l'exploitation).

### 3. Cloudflare : route du tunnel (tableau de bord Cloudflare)
**Zero Trust → Networks → Tunnels →** le tunnel qui sert `app.sarcade.org` **→ Configure → Public Hostname → Add a public hostname** :
- Subdomain `test`, Domain `sarcade.org`, Path vide ;
- Service : **le même type et la même adresse que la ligne `app.sarcade.org`, avec le port `8010` au lieu de `8000`** (par ex. `HTTP` `192.168.0.110:8010` si `app` pointe sur `192.168.0.110:8000`) ;
- mêmes « Additional application settings » que `app.sarcade.org` (le cas échéant).

### 4. Cloudflare : application Access (tableau de bord Cloudflare)
**Zero Trust → Access → Applications →** l'application **SARCADE** (celle de `app.sarcade.org`) **→ Edit → Overview (ou Application domain) → Add domain** : `test.sarcade.org`. Enregistrer.

Ajouter le domaine à l'application existante garde **la même politique** (mêmes personnes autorisées) et **la même étiquette AUD** : le contrôle Access du serveur de test (`SARCADE_ACCESS_*`, recopié à l'étape 2) marche sans autre réglage. (Variante possible : une application Access à part ; il faudrait alors placer son AUD dans `SARCADE_ACCESS_AUD` du `.env` du test.)

Vérification depuis le PC (PowerShell) :
```
curl.exe -sI https://test.sarcade.org/health | Select-String -Pattern "^HTTP|^location"
```
Résultat attendu : `HTTP/1.1 302 Found` (ou `HTTP/2 302`) et `location: https://sdubos.cloudflareaccess.com/…` : la page est bien protégée par Access. Puis, dans un navigateur, `https://test.sarcade.org/health` : connexion Access, puis `{"status":"ok"…}`.

### 5. Exploitation sur la version de référence (NAS, quand l'étiquette existe)
L'étiquette (ex. `v0.1.0-exercice1`) est posée par la carte « Consolidation : version de référence et versions déployées ». En attendant, l'exploitation reste sur sa version actuelle : plus aucune fusion ne l'atteint.
```
sarcade-switch --list | head -n 8 && sarcade-switch v0.1.0-exercice1 && sarcade-switch --status
```
Résultat attendu : la liste des étiquettes (dont `v0.1.0-exercice1`), la reconstruction, puis `Instance : exploitation`, `Version  : v0.1.0-exercice1`, `Santé : {"status":"ok"…}`. Une étiquette inconnue donne `ERREUR : étiquette ou branche inconnue` et ne change rien.

### 6. Vérifier la mise à jour automatique (NAS)
```
sarcade-autoupdate --status && sarcade-autoupdate --dry-run; tail -n 3 /volume1/docker/sarcade/autoupdate.log
```
Résultat attendu : `Instance : test (/volume1/docker/sarcade-test)`, `Branche suivie : main`, `Pause : non`, puis rien (déjà à jour) ou `mise à jour serveur : … (essai : rien n'est fait)`. La tâche planifiée DSM « SARCADE mise à jour automatique » ne change pas.

**Recette** (critère de la carte) : après la fusion suivante sur `main`, `sarcade-autoupdate --status` montre le nouveau commit sur le test et `sarcade-switch --status` montre l'exploitation inchangée.

## Recette d'une PR sur le serveur de test
```
SARCADE_ROOT=/volume1/docker/sarcade-test sarcade-switch <branche>
```
Retour au suivi automatique : `SARCADE_ROOT=/volume1/docker/sarcade-test sarcade-switch main`.

## Limites
- Le test n'a ni Valhalla, ni extrait OpenStreetMap, ni fonds de carte : ces fonctions se recettent sur l'exploitation ou après ajout dans `compose.yaml` du test.
- `SARCADE_*` ajoutés plus tard au `.env` de l'exploitation ne sont pas recopiés : les ajouter aussi au `.env` du test si besoin (le `compose.yaml` du test transmet tout son `.env` au serveur, sans override).

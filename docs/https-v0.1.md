# HTTPS du serveur (v0.1)

Note du coffre : « Mises à jour et sécurité », section HTTPS (décisions validées par le porteur le 10 octobre 2026).

Le serveur SARCADE reste derrière un **proxy inverse Caddy** qui porte le certificat, redirige HTTP vers HTTPS et renouvelle le certificat tout seul. Le serveur lui-même ne change pas : il apprend seulement le mode et l'adresse HTTPS, qu'il annonce aux applications pour qu'elles basculent d'elles-mêmes.

## Deux options

| | Option A (par défaut) | Option B |
|---|---|---|
| Certificat | Let's Encrypt, défi **DNS-01**, certificat générique (`adrasec78.sarcade.org` et `*.adrasec78.sarcade.org`) | **autorité de certification locale** (Caddy) |
| Pour | toutes les installations qui ont Internet de temps en temps | les installations qui ne sont **jamais** en ligne |
| Ouverture du serveur sur Internet | **aucune** : le défi passe par le DNS | aucune |
| Sur les appareils | rien à installer | certificat racine à installer **une fois** sur chaque appareil |
| Renouvellement | automatique (tous les 60 jours environ, avec Internet) | automatique, sans Internet |

### Option A : Let's Encrypt par le DNS

Prérequis côté porteur :
1. **le domaine `sarcade.org`** (ou un autre nom de l'organisation) chez un fournisseur DNS gérable par API : Cloudflare, OVH ou Gandi sont intégrés ;
2. **un jeton d'API DNS** limité à la zone, placé par le porteur dans le fichier `.env` du serveur (jamais dans le dépôt ni dans une conversation) ;
3. dans le **DNS local** (box, routeur, Pi-hole, DSM), le nom `adrasec78.sarcade.org` pointe vers l'adresse locale du serveur. Les appareils du réseau du PCO joignent ainsi le serveur en HTTPS, même sans Internet.

`.env` :

```
SARCADE_TLS_MODE=acme-dns
SARCADE_DOMAIN=adrasec78.sarcade.org
SARCADE_PUBLIC_URL=https://adrasec78.sarcade.org
SARCADE_ACME_EMAIL=contact@exemple.org
SARCADE_DNS_PROVIDER=ovh            # ou cloudflare, gandi
OVH_APPLICATION_KEY=...             # placé par le porteur
OVH_APPLICATION_SECRET=...
OVH_CONSUMER_KEY=...
SARCADE_HTTP_BIND=127.0.0.1         # HTTP simple réservé à la machine
SARCADE_TRUSTED_PROXIES=*
```

Puis `docker compose --profile https up -d`.

Le certificat est valable 90 jours et renouvelé automatiquement 30 jours avant la fin : un serveur qui passe **plus de 60 jours sans Internet** doit être reconnecté un moment pour le renouveler (sinon, option B).

La vérification de la publication du défi passe par des résolveurs publics (`SARCADE_ACME_RESOLVERS`, par défaut `1.1.1.1 9.9.9.9`), pour ne pas être trompée par le DNS local.

### Option B : autorité de certification locale

`.env` :

```
SARCADE_TLS_MODE=local-ca
SARCADE_DOMAIN=sarcade.local
SARCADE_TLS_NAMES=192.168.1.10      # autres noms ou adresses IP du serveur
SARCADE_PUBLIC_URL=https://sarcade.local
SARCADE_HTTP_BIND=127.0.0.1
SARCADE_TRUSTED_PROXIES=*
```

Le certificat racine est servi **en HTTP** à l'adresse `http://sarcade.local/sarcade-root.crt` (seule ressource accessible sans HTTPS), pour qu'un nouvel appareil puisse le récupérer avant de faire confiance au serveur. Installation :

- **Windows** : double-clic sur le fichier, « Installer le certificat », magasin « Autorités de certification racines de confiance » ;
- **Android** : Paramètres, Sécurité, Chiffrement et identifiants, Installer un certificat, Certificat CA (l'application SARCADE accepte les autorités installées par l'utilisateur) ;
- **iPhone, iPad** : ouvrir le fichier, installer le profil, puis Réglages, Général, Informations, Réglages des certificats, activer la confiance ;
- **Linux, Chromebook** : importer dans les autorités du navigateur (Paramètres, Confidentialité et sécurité, Gérer les certificats).

La racine est conservée dans le volume `sarcade-caddy-data` : **à sauvegarder** avec le reste, sinon il faut réinstaller une nouvelle racine sur tous les appareils.

## Synology

Sur un NAS Synology, le **proxy inverse de DSM** remplace Caddy : DSM obtient et renouvelle déjà un certificat Let's Encrypt (Panneau de configuration, Sécurité, Certificat). Règle de proxy inverse : source `https://adrasec78.sarcade.org:443`, destination `http://localhost:8000`, avec les en-têtes WebSocket (bouton « Créer » de l'onglet En-tête personnalisé). Dans `.env` : `SARCADE_TLS_MODE=acme-dns`, `SARCADE_PUBLIC_URL`, `SARCADE_TRUSTED_PROXIES=*`, `SARCADE_HTTP_BIND=127.0.0.1`, sans le profil `https`.

## Bascule des applications

Le serveur annonce l'adresse HTTPS dans la configuration des clients (`GET /api/v0.1/clients/config`, contrôle de version au démarrage et battement de cœur) :

```json
{"navigation": {...}, "https": {"url": "https://adrasec78.sarcade.org", "root_certificate_url": null}}
```

Une application connectée en HTTP **essaie l'adresse HTTPS** ; si elle répond, elle l'adopte et la garde. Sinon (certificat racine non installé en option B, DNS local absent), elle reste en HTTP et le signale.

Une fois tous les appareils passés en HTTPS, l'autorisation du trafic HTTP simple est retirée de l'application Android (carte du Kanban).

## Vérification

La CI démarre le proxy en option B devant le serveur de démonstration et vérifie : racine servie en HTTP, redirection 308 de tout le reste, réponse HTTPS avec le certificat de l'autorité locale, adresse annoncée aux clients.

L'option A n'est vérifiable qu'avec le vrai domaine et le jeton DNS du porteur.

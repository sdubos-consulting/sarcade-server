# Diagnostic du serveur (V0.1)

Carte « Paramètres › Diagnostic étoffé », validée par le porteur le 30 octobre 2026 (2 h 58), lot 5.

- `GET /api/v0.1/diagnostics/server` — **tous** depuis le lot 28 ; le serveur calcule le **niveau** (`level`) selon les droits du lot 26 et ne renvoie que ce que ce niveau permet (carte « Diagnostic : trois niveaux de vue selon les droits, et test de Brevo », 4 novembre 2026, 7 h 19) :
  - `user` (compte sans droit d'administration, ou appareil sans compte après la période de transition, ou sans e-mail sur une adresse protégée par Access) : `level`, `status`, `version`, `reference`, `at` ;
  - `organizer` (PCO : liste des PCO, organisateur d'événements ou administrateur de groupe) : en plus `services` (`aprs`, `weather`, `wakeup` = notifications, `routes` = itinéraires) et `devices_connected` (connexions temps réel) ;
  - `admin` (administrateur du serveur, auditeur du serveur sans portée, jeton `SARCADE_ADMIN_TOKEN`) : tout le bloc ci-dessous, plus `build` (= `/version`), `addresses` (adresses du conteneur) et `mail` (Brevo, voir plus bas) ;
  - `transition` (appareil sans compte pendant la période de transition, réseau local) : le bloc d'avant, sans `build`, `addresses` ni `mail`.
- Bloc complet : `status` (`ok` / `degraded` / `down`), `version`, `started_at`, `uptime_s`, `components` (`database`, `realtime`, `tunnel`, `wakeup`, `aprs`, `weather` : `green` / `orange` / `red` / `off`), `detail`, `resources` (`cpu_pct`, `cpu_count`, `load`, `memory{used_mb,total_mb,pct}`, `disk{path,used_gb,total_gb,pct}`, `alert` au-delà de 80 %), `alert_pct`, `last_update`, `last_backup`, `update_status`.
- `GET /api/v0.1/diagnostics/client` (tous) : `ip` publique vue par le serveur (`cf-connecting-ip`, sinon `x-forwarded-for`, sinon la connexion), `via_cloudflare`, `email` vérifié par Access, `version` ; sert aussi à mesurer le temps de réponse.
- Ressources lues dans `/proc` et `os.statvfs` (vues depuis le conteneur, aucune dépendance) ; disque : dossier des fichiers (`SARCADE_FILE_ROOT`) ou `/`.
- Tunnel : vert si une requête est passée par Cloudflare (en-tête `cf-ray`) dans les 15 dernières minutes ; orange sinon (Access actif) ; `off` sans Access ni passage par Cloudflare.
- Dernière sauvegarde : déduite du dernier rapport de l'outil de mise à jour (`backup …`), `null` sinon.

## Brevo (lot 28, administrateur)
- `mail` : `delivery` (`smtp` / `simulated`), `host`, `port`, `sender`, `missing`, `last_success_at`, `last_failure_at`, `last_failure` (type et réponse du serveur SMTP, jamais l'identifiant, la clé ni l'adresse), `probe` : `{ok, error, at}` — connexion **avec identification, sans envoi**, faite au chargement du Diagnostic et gardée 60 s ; `null` en envoi simulé.
- `POST /api/v0.1/admin/mail/test` (administrateur du serveur) : « Envoyer un e-mail d'essai » à l'adresse de l'administrateur → `{"status": "sent" | "simulated", "to", "at"}` ; `503 {"error": "mail_failed", "reason"}` en cas d'échec ; `422 admin_email_required` avec le jeton `.env` (pas d'adresse). Journal de sécurité : `admin` / `mail_test`.
- Derniers envois (codes de connexion et e-mails d'essai) gardés dans les réglages du serveur (`mail_stats`).

## Échéances (lot 28, administrateur)
Carte « Diagnostic administrateur : Échéances » (4 novembre 2026, 7 h 26), qui reprend « Brevo : la clé SMTP expire ».
- Dans le Diagnostic administrateur : `deadlines` = `{items, summary, tls}` ; chaque élément : `id`, `name`, `date` (`AAAA-MM-JJ` ou `null`), `days_left`, `level` (`green` ; `orange` à 30 jours ; `red` à 7 jours et le jour même ; `expired` ; `unknown` sans date), `kind` (`auto` / `manual`), `url` (page de renouvellement), `note`, `mail_related` ; triés du plus urgent au moins urgent. `summary` = `{worst, alerts}`.
- Détectées par le serveur : certificat HTTPS de `SARCADE_PUBLIC_URL` (lu une fois par jour, sans le vérifier), licence du serveur et licences des organisations, clé Brevo inutilisée 90 jours (dernier envoi réussi + 90 jours).
- Saisies par l'administrateur : `GET /api/v0.1/admin/deadlines` (administrateur ou auditeur du serveur) → `{items, summary, tls, manual}` ; `PUT /api/v0.1/admin/deadlines` `{"items": [{id?, name, date, url, note}]}` (administrateur du serveur ; 50 au plus ; lien `https://` seulement ; **jamais le secret** : aucun champ prévu). Liste préremplie : clé SMTP Brevo (3 novembre 2027), domaine `sarcade.org`, adhésion Apple Developer (22 octobre 2027), clé App Store Connect, certificat de signature Windows, clé Météo-France, identifiants OpenSky.
- Alertes à 30 jours, 7 jours et le jour J, une fois chacune (contrôle quotidien, `SARCADE_DEADLINE_CHECK=0` pour l'arrêter) : e-mail aux administrateurs par Brevo, sauf si Brevo est en cause (`mail_related`) ; journal de sécurité `admin` / `deadline_alert`. Dans l'application : `/me` donne `deadlines` = `{worst, alerts}` aux administrateurs (pastille rouge sur Paramètres, bandeau dans le Diagnostic).
- Non détectables par le serveur (à saisir à la main) : jetons de service Cloudflare (leur date n'est pas transmise au serveur) ; la signature des images est sans clé (Sigstore), rien n'expire.

## Version (lot 28)
- `GET /version` et `GET /api/v0.1/version` (même exposition que `/sarcade-web.sha256`) : `version`, `commit`, `commit_short`, `tag`, `reference` (l'étiquette `v…` si l'image vient d'une étiquette, sinon `main@<commit court>`, `dev` pour une construction locale), `web_sha256` (client web servi), `sbom_sha256` (empreinte de `/sarcade-sbom.cdx.json`).
  - `public_repo` : adresse du dépôt public GitHub (`SARCADE_PUBLIC_REPO_URL`, forme `https://github.com/propriétaire/dépôt`), sinon `null`. Présente, elle affiche dans l'application le lien « Ou sur GitHub (compte requis) » de « Signaler un problème » (fiche du 11 novembre 2026, choix 3). Variable à ajouter par le porteur dans `docker-compose.override.yml` du NAS le jour où GitHub est rétabli.
- Réglés à la construction de l'image (`image.yml`) : `SARCADE_VERSION`, `SARCADE_COMMIT`, `SARCADE_TAG`.

Vérification : `tests/test_diagnostics.py`, `tests/test_diagnostics_levels.py`, `tests/test_deadlines.py`, `demo/verify_diagnostics.py`.

# Inventaire des composants (SBOM) et failles écartées — v0.1 (serveur)

Carte « SBOM : produire et exploiter l'inventaire des composants » (lot 22), points 2, 3 et 4.

## SBOM de l'image réellement construite

- Produit **à la construction** de l'image par le `Dockerfile` (celle du NAS, `sarcade-switch main`, comme celle de la CI) : dernière étape, `docker/make_sbom.py` lance syft (image officielle `anchore/syft`, prise sur Docker Hub comme l'image Python, montée pour cette seule étape, jamais gardée dans l'image) sur la racine de l'image.
- Format **CycloneDX JSON**. Contenu : paquets Python installés, paquets du système (Debian), interpréteur Python, et le **client web embarqué** comme un composant (`sarcade-web`, version de son `version.json`, empreinte SHA-256 identique à `/sarcade-web.sha256`). Les paquets Dart du client web sont compilés dans `main.dart.js` : ils relèvent du SBOM de l'application (point 1 de la carte, dépôt `sarcade-app`).
- Hors ligne : aucune requête réseau pendant le scan (pas de vérification de mise à jour, pas de recherche de licences en ligne).
- Rien de sensible : seule l'image est inventoriée (le `.env` et les secrets du NAS sont montés au lancement, jamais à la construction), sans liste de fichiers.
- **Servi** à `GET /sarcade-sbom.cdx.json` (`application/vnd.cyclonedx+json`, `Cache-Control: no-cache`), même exposition que `/sarcade-web.sha256` : sans compte SARCADE, derrière Cloudflare Access sur les adresses publiques. Fichier dans l'image : `/app/sbom/sarcade-image.json` (variable `SARCADE_SBOM_FILE`).
- **Jamais bloquant** : si syft manque ou échoue, la construction continue, le journal affiche `sbom: not generated (…)` et l'adresse répond **404** `{"detail": "sbom_not_available"}`. `--build-arg SARCADE_SBOM=none` construit sans SBOM.
- Vérifié en CI par `demo/verify_sbom.py` (job `demo-integration`).

L'image publiée sur ghcr.io (`image.yml`) garde son SBOM **SPDX** attesté et y ajoute la version **CycloneDX** (attestée aussi, jointe aux mêmes publications `v*`).

## Contrôle de nuit (`.github/workflows/sbom-nightly.yml`)

Chaque nuit (01 h 37 UTC) et à la main (« Run workflow », case « SBOM d'essai ») :

1. construit l'image de `main` comme le NAS et en extrait le SBOM (à défaut, le fabrique avec syft sur l'image) ;
2. le passe à **OSV-Scanner** (`ghcr.io/google/osv-scanner`), sur une copie où la version Debian des purl est ramenée à la version majeure (`distro=debian-13.7` → `debian-13`, nom de l'écosystème OSV `Debian:13` ; sans cela aucune faille Debian n'est trouvée), `tool/security/osv_input.py` ;
3. applique le registre `security/vex.json` (ci-dessous) avec `tool/security/osv_report.py` ;
4. tient **un seul** ticket GitHub « Faille dans un composant » (étiquette `security`) : ouvert avec la liste s'il reste une faille non écartée, mis à jour sinon (un commentaire, donc un e-mail de GitHub, quand une faille est **nouvelle**), fermé quand il n'y a plus rien.

Le contrôle n'échoue jamais à cause d'une faille, seulement s'il est cassé (registre invalide, OSV-Scanner en erreur). Il n'utilise que le `GITHUB_TOKEN` du workflow (`issues: write`). Les PR qui touchent au contrôle (`security/`, `tool/security/`, le workflow, `docker/make_sbom.py`) le lancent à blanc, sans ticket ; les failles déjà présentes ne font jamais échouer la CI d'une PR.

**Essai de l'alerte** (critère de validation de la carte) : « Run workflow » avec « SBOM d'essai » coché ajoute `security/test-sbom.cdx.json` (jinja2 2.10, faille connue CVE-2019-10906) ; le ticket s'ouvre ou reçoit un commentaire. La nuit suivante, sans l'essai, il est mis à jour ou fermé.

L'e-mail est celui des notifications GitHub : le porteur doit « surveiller » le dépôt (Watch → All activity, ou Custom → Issues) avec les notifications par e-mail actives.

## Registre des failles écartées (`security/vex.json`)

Format **OpenVEX** v0.2.0. Une déclaration par faille écartée :

```json
{
  "vulnerability": {"name": "CVE-2019-10906", "aliases": ["GHSA-462w-v97r-4m45"]},
  "products": [{"@id": "pkg:pypi/jinja2"}],
  "status": "not_affected",
  "justification": "vulnerable_code_not_in_execute_path",
  "impact_statement": "Jinja2 n'est jamais utilisé avec des gabarits venant d'un utilisateur",
  "status_notes": "réexamen 2027-02-01",
  "timestamp": "2026-11-02T00:00:00Z"
}
```

| Champ | Règle |
|---|---|
| `vulnerability.name` (+ `aliases`) | identifiant OSV, GHSA ou CVE ; comparé à tous les alias renvoyés par OSV |
| `products` | facultatif : purl du composant (`pkg:pypi/jinja2`, `pkg:deb/debian/libexpat1`), avec `@version` pour limiter à une version ; absent = tous les composants |
| `status` | `not_affected` ou `fixed` écartent la faille ; `under_investigation` et `affected` la laissent dans le ticket, avec la mention |
| `justification` | **obligatoire** pour `not_affected`, une des cinq valeurs OpenVEX (`component_not_present`, `vulnerable_code_not_present`, `vulnerable_code_not_in_execute_path`, `vulnerable_code_cannot_be_controlled_by_adversary`, `inline_mitigations_already_exist`) |
| `impact_statement` | **raison obligatoire**, en clair (« pas concerné, parce que… ») |
| `status_notes` | **date de réexamen obligatoire** : `réexamen AAAA-MM-JJ`. Passé ce jour, la faille revient dans le ticket (« réexamen dépassé ») |

Un registre invalide fait échouer le contrôle (et `pytest`, qui le vérifie à chaque PR) avec la liste des problèmes. Vérification locale :

```
python tool/security/osv_report.py --check-vex security/vex.json
```

Le ticket rappelle les failles écartées (dépliant en bas) avec leur raison et leur date de réexamen. Le tri, les délais et le lien avec les signalements du Cyber Resilience Act relèvent de la procédure du point 5 de la carte.

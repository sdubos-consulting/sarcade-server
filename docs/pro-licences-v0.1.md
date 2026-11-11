# Licences SARCADE Pro signées (v0.1)

Notes du coffre : « 11 - Architecture modulaire et SARCADE Pro », « Mises à jour et sécurité » (décisions validées par le porteur le 10 octobre 2026).

Le code reste entièrement libre (AGPL). La licence n'active que les **modules Pro** (`siem`, `assist` ; `locate` retiré le 25 octobre 2026, ADR-006 : recherche de balises, SDR et 406 MHz dans le Core) ; **Core ne dépend jamais d'une licence**. Les applications sont gratuites : elles affichent les fonctions Pro quand le serveur les annonce.

## Chaîne de confiance (Ed25519)

```
clé racine (hors ligne, chez le porteur)
  └── certifie une clé de licence (identifiant, clé publique, validité)
        └── la clé de licence signe les licences
```

- La **clé publique racine** est inscrite dans `src/sarcade/licensing/roots.py`. La clé privée racine ne sert qu'à certifier une nouvelle clé de licence, tous les quelques années ; elle reste hors ligne et sauvegardée.
- Une **clé de licence** compromise se révoque sans toucher à la racine.
- **Aucune clé privée** n'entre dans le dépôt ni dans une conversation.

## Fichier de licence

```json
{
  "format": "sarcade-licence/1",
  "licence": {"id": "L-2026-1a2b3c4d", "organisation": "ADRASEC 78", "kind": "association",
              "modules": ["assist", "siem"], "issued_at": "2026-10-10",
              "expires_at": "2027-10-10", "price_eur": 0},
  "signature": "…",
  "key": {"id": "K-2026-1", "root": "R-2026", "public_key": "…", "not_before": "2026-10-10",
          "not_after": "2029-10-10", "signature": "…"}
}
```

- **Une licence par organisation**, nombre de serveurs illimité (le même fichier sur chaque serveur).
- **Durée d'un an** par défaut.
- **Licence association gratuite** : même mécanisme, `kind: association`, prix 0.

## Règles

| Situation | Modules Pro | Affichage |
|---|---|---|
| Pas de licence | arrêtés | « Core » |
| Licence valide | actifs | organisation, échéance |
| Moins de 30 jours avant l'échéance | actifs | avertissement |
| Échue depuis moins de **30 jours** (grâce) | actifs | avertissement, jours restants |
| Échue depuis plus de 30 jours | **arrêtés** | « expirée » |
| Signature invalide, racine inconnue, révoquée | arrêtés | motif |

- Vérification **hors ligne**, au démarrage puis **chaque jour**.
- Une licence invalide ou expirée **ne remplace jamais** la licence installée.
- **Liste de révocation** (`src/sarcade/licensing/revoked.py`) livrée avec les mises à jour du serveur.
- Chaque vérification qui change l'état et chaque téléversement sont inscrits au journal de sécurité.

## Outil d'administration

| Méthode | Chemin | Rôle |
|---|---|---|
| `GET` | `/api/v0.1/admin/licence` | état : organisation, modules, échéance, jours restants, grâce, motif |
| `PUT` | `/api/v0.1/admin/licence` | téléversement du fichier (corps JSON) ; 422 avec le motif si refusé |
| `POST` | `/api/v0.1/admin/licence/check` | nouvelle vérification immédiate |

Le fichier est conservé dans `SARCADE_LICENCE_FILE` (par défaut `/var/lib/sarcade/files/licence.json`). Les applications reçoivent `{"pro": {"modules": [...], "status": "..."}}` dans la configuration des clients.

## Émission (hors ligne, chez le porteur)

```
python -m sarcade.licensing.issue init-root --id R-2026 --out root.key
#   affiche la clé publique à ajouter dans roots.py (par une PR)
python -m sarcade.licensing.issue new-key --root root.key --root-id R-2026 --id K-2026-1 --years 3 --out licence-key.key
python -m sarcade.licensing.issue sign --key licence-key.key --organisation "ADRASEC 78" --kind association \
    --modules siem,assist --out adrasec78.licence.json
python -m sarcade.licensing.issue verify adrasec78.licence.json
```

Les clés privées sont écrites chiffrées par une phrase de passe (demandée, ou `SARCADE_LICENCE_PASSPHRASE`), avec les droits 600 ; aucune clé existante n'est écrasée.

## Licences de test

La démonstration et la CI génèrent une chaîne **éphémère** (`demo/make_test_licence.py`). Un serveur n'accepte une racine de test que si `SARCADE_LICENCE_TEST_ROOT` est défini, uniquement pour des licences `kind: test`, toujours affichées comme telles.

## Transition

`SARCADE_PRO_MODULES` (v0.1) est supprimé : sans licence, les modules Pro sont arrêtés. Tant que la racine n'est pas générée et inscrite dans `roots.py`, aucune licence réelle n'est vérifiable.

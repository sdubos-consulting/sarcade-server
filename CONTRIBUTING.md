# Contribuer à SARCADE Server

Merci de votre intérêt pour SARCADE, plateforme open source de coordination terrain pour les bénévoles de la sécurité civile (ADRASEC, radioamateurs).

*English summary: contributions are welcome under the repository license (AGPL-3.0-only). Every commit must carry a `Signed-off-by` line (Developer Certificate of Origin, `git commit -s`). No CLA. Never commit secrets or real operational data.*

## Licence
Ce dépôt est publié sous **AGPL-3.0-only** (voir `LICENSE`). En contribuant, vous acceptez que votre contribution soit diffusée sous cette licence.

## Certificat d'origine (DCO)
SARCADE utilise le **Developer Certificate of Origin 1.1** (<https://developercertificate.org>) et **pas de CLA** : vous gardez vos droits d'auteur.

**Chaque commit** d'une Pull Request doit porter une ligne :

```
Signed-off-by: Prénom Nom <adresse@exemple.fr>
```

avec **la même adresse que l'auteur du commit**. Par cette ligne, vous certifiez avoir le droit de soumettre ce code sous la licence du dépôt (vous l'avez écrit, ou il provient d'une source compatible que vous indiquez).

- Ajouter la ligne automatiquement : `git commit -s`
- Oubliée sur le dernier commit : `git commit --amend -s --no-edit` puis `git push --force-with-lease`
- Oubliée sur plusieurs commits : `git rebase --signoff origin/main` puis `git push --force-with-lease`
- Depuis l'interface web de GitHub : ajoutez la ligne à la main dans le message du commit.

Le contrôle **DCO** de la CI vérifie chaque commit de la PR (hors commits de fusion et robots Dependabot / GitHub Actions) ; une PR dont un commit n'est pas certifié ne peut pas être fusionnée.

## Comment contribuer
- Ouvrez une issue avant une évolution significative, pour en discuter.
- Une branche et une Pull Request par sujet ; PR à jour avec `main`.
- `python -m pytest -q` passe (tests unitaires) ; toute nouvelle API a son script `demo/verify_*.py`.
- Ne committez **jamais** de secret (mot de passe, clé, certificat, compte de service) ni de donnée opérationnelle réelle (positions, messages, identités).
- Les failles de sécurité se signalent en privé, jamais dans une issue publique.

## En-têtes des fichiers source
Tout **nouveau** fichier source commence par :

```python
# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
```

Les fichiers existants recevront cet en-tête dans une PR dédiée.

## Assistants de code
Les consignes pour les assistants (Claude, ChatGPT/Codex…) sont dans `AGENTS.md` quand le dépôt en a un. Leurs commits sont certifiés au nom de SDUBOS Consulting : `Signed-off-by: SDUBOS Consulting <github@sdubos.com>`.

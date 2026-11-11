# Groupes de communication (V0.1)

Spécification : note « Groupes de communication » du vault (décisions du 10 octobre 2026).

## Groupes créés automatiquement
- À la création d'un événement : les **groupes par défaut ADRASEC** — Tous, Diffusion PCO (écoute seule, émetteur : PCO), PCO, Équipes terrain, Transmissions, Logistique. Identifiants stables (UUID v5 de l'événement et du groupe), identiques sur tous les serveurs.
- À la création d'une équipe : un **groupe d'équipe**.
- `POST /api/v0.1/events/{event_id}/groups/defaults` crée les groupes par défaut manquants (événements antérieurs). Idempotent.

## Groupes des opérateurs
- Objets synchronisés (`object_type` : `comm_group`), règle de l'ADR-001 : la dernière modification l'emporte, suppression par pierre tombale.
- Tout opérateur crée un groupe ; il en devient responsable (`managers`). Ensuite, seuls les responsables et le PCO le modifient, l'archivent ou le suppriment (sinon : `rejected`).
- Champs : `name`, `description`, `color`, `mode` (`discussion` ou `listen_only`), `kind`, `members`, `roles`, `senders`, `managers`, `team_id`, `radio_channel`, `archived`.
- `GET /api/v0.1/events/{event_id}/groups` : **tous les groupes**, y compris ceux des opérateurs et les archivés (le PCO voit tout).
- Création, modification, archivage et suppression sont inscrits dans la main courante.

## Messages de groupe
- Un message adressé à un groupe porte `group:<id>` dans `recipient_ids` ; les destinataires sont figés à l'envoi.
- Le serveur refuse (`rejected`) un message vers un groupe **en écoute seule** si l'émetteur n'est ni le PCO, ni un émetteur désigné, ni un responsable, et tout message vers un groupe **archivé**. Un groupe encore inconnu du serveur (créé hors connexion) n'est pas bloquant.

## Limite
En attendant les rôles de l'ADR-002, le PCO est reconnu à son identifiant déclaré : `PCO` ou `PCO-…`.

Vérification : `demo/verify_groups.py`, exécuté par la CI.

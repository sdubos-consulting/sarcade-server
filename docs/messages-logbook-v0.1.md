# Messages, ACK et main courante — v0.1

Les messages et ACK sont append-only et passent par le mécanisme Offline First.

## Message
Priorités initiales : routine, urgent, immediate. Les destinataires sont des identifiants d'utilisateurs/équipes. La V0.1 limite le texte à 2048 caractères côté protocole.

## ACK
États : received, read, accepted, rejected. Un ACK applicatif est distinct d'un acquittement de transport radio.

## Main courante
Le serveur produit une chronologie immuable des événements opérationnels significatifs. La V0.1 journalise au minimum les messages et ACK. Les positions ne sont pas inscrites une à une afin d'éviter le bruit.

## Temps réel
Événements WebSocket : message.created, message.deleted et ack.created.

## Photos et pièces jointes
Un message peut référencer jusqu'à 4 fichiers dans `attachments` : `file_id`, `name`, `mime_type`, `size_bytes`. Le message ne transporte pas les octets.

1. Le client choisit l'identifiant du fichier (UUID) et envoie la photo par `POST /events/{id}/files` avec les champs `file_id` et `mime_type`.
2. L'envoi est idempotent : un nouvel essai avec le même `file_id` et le même contenu renvoie le fichier existant (200) ; un contenu différent renvoie 409.
3. Le message est ensuite synchronisé normalement. Hors connexion, le client garde la photo et le message, et envoie la photo d'abord au retour du réseau.

Des pièces jointes mal formées font rejeter le message. La main courante indique le nombre de photos, par exemple « Véhicule repéré [1 photo] ». Les fichiers téléchargés portent `X-Content-Type-Options: nosniff`, car le client Web partage l'origine du serveur.

## Suppression d'un message (29 octobre 2026)
« Supprimer pour moi » est purement local (le client masque le message sur l'appareil) : rien n'est envoyé au serveur.

« Supprimer pour tous » passe par la synchronisation : opération `object_type = "message"`, `action = "delete"`, `object_id` = identifiant du message, `payload = {"deleted_by": "<identifiant de l'opérateur>"}`.

- Autorisé pour l'**auteur** (`deleted_by == sender_id`) pendant **2 jours** après `created_at`, et pour le **PCO** (rôle PCO de l'adresse vérifiée par Cloudflare Access, ou identifiant `PCO` / `PCO-…`) sans délai. Sinon : `rejected` (journal de sécurité `write_refused`, raison `delete_not_allowed`).
- **Jamais pour tous, même par le PCO** (lot 21, 2 novembre 2026) : messages de **détresse** (priorité `distress`, ou `immediate` commençant par « 🆘 »), leur **fin de détresse** (« Fin de détresse… » / « End of distress… ») et les **ordres de déplacement** (« 📍 Ordre de déplacement… »). Réponse `rejected`, raison du journal `delete_protected`. « Supprimer pour moi » reste possible sur l'appareil. Une sélection de plusieurs messages est supprimée message par message (une opération `delete` chacun), il n'y a pas de route groupée.
- Message déjà supprimé : `conflict` ; message inconnu : `rejected`.
- Effets : `deleted_at`, `deleted_by`, `deleted_by_pco` sur le message (migration 0022). Le corps et les photos sont retirés de la création dans le flux `sync/changes`, de `GET /messages` (champs `deleted`, `deleted_at`, `deleted_by`, `deleted_by_pco`, `body` vide, `attachments` vide) et de la ligne de main courante du message (« Ce message a été supprimé » / « … par le PCO »). Une ligne `message_deleted` est ajoutée à la main courante (auteur de la suppression, heure).
- Temps réel : `message.deleted` avec `{id, event_id, deleted, deleted_at, deleted_by, deleted_by_pco}` ; le flux de changements porte la même charge sur l'opération `delete`.
- Traçabilité : le texte et les pièces jointes d'origine restent sur le serveur ; `GET /events/{id}/messages/deleted` les renvoie au PCO / administrateur seulement (`require_pco`).
- Les photos d'un message supprimé répondent `410 message_deleted` au téléchargement, sauf pour un PCO / administrateur vérifié.

## Réponse à un message (31 octobre 2026)
Un message peut citer un autre message du même événement : champ facultatif `reply_to` (identifiant) dans la charge utile de création (`/sync`, objet `message`). Un identifiant inconnu ou d'un autre événement est ignoré (le message est livré sans citation). `GET /events/{id}/messages` et le flux de changements renvoient `reply_to`. Les applications affichent la citation à partir du message cité qu'elles ont déjà ; s'il a été supprimé, « Message supprimé ». Migration 0026 (colonne `messages.reply_to`, facultative). Notifications inchangées.

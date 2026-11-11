# Plan de recette automatisée Offline First

## Cas 1 — retransmission
Envoyer deux fois la même operation_id. Attendu : accepted puis duplicate, un seul objet métier.

## Cas 2 — coupure
Accumuler N opérations dans l'Outbox sans serveur. Attendu : aucune perte locale.

## Cas 3 — reprise
Rétablir le serveur et synchroniser. Attendu : Outbox vidée pour accepted/duplicate, curseur avancé.

## Cas 4 — communication
Message A→B. Attendu : message.created, ACK received automatique de B, ack.created vers A, entrée main courante.

## Cas 5 — diffusion événement
recipient_ids vide. Attendu : tous les clients abonnés à l'événement reçoivent le message.

## Cas 6 — destinataire
user:<id> ou team:<id>. Le serveur transporte le ciblage. L'autorisation et la résolution des équipes seront testées avec IAM.

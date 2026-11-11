# Offline First — Server v0.1

Le serveur maintient un journal idempotent des opérations synchronisées. Chaque operation_id n'est accepté qu'une fois.

Le curseur serveur est le numéro séquentiel du journal pour un événement. Le client conserve le dernier curseur entièrement traité.

La V0.1 traite d'abord les objets append-only/terrain. Les conflits de mise à jour des objets versionnés seront enrichis dans une itération suivante.

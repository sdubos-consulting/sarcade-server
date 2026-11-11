# Recherche d'adresses (V0.1)

Barre de recherche de la carte (demande du 26 octobre 2026) : les adresses et les lieux viennent de la **Base Adresse Nationale** (`api-adresse.data.gouv.fr`), gratuite, française, sans clé.

- `GET /api/v0.1/geocode?q=…&lat=…&lon=…&limit=8` → `{"results":[{"label","kind","city","postcode","score","lat","lon"}]}` ; résultats proches du centre de la carte d'abord.
- Les applications passent par le serveur SARCADE : la BAN ne voit que le serveur, jamais les appareils. Réponses gardées 24 h en mémoire (500 recherches).
- `503 geocoder_unavailable` si le service ne répond pas : les applications cherchent alors seulement dans leurs données (opérateurs, référentiel, points, coordonnées).
- `SARCADE_GEOCODER_URL` : adresse du service (défaut : la BAN) ; vide pour couper la recherche d'adresses (installation jamais en ligne).

Vérification : `tests/test_geocode.py`.

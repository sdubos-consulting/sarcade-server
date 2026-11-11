# Localisation lisible (V0.1)

Décision du porteur, 29 octobre 2026 (option A) : à côté des coordonnées GPS, une ligne compréhensible.

- `GET /api/v0.1/places/describe?lat=…&lon=…[&sectors=8|16][&online=true]` →
  `{"lat","lon","commune":{"code","name","lat","lon"},"distance_m","bearing_deg","direction","direction_en","label","label_en","road","altitude_m","department"}`
  - `label` : « 1,2 km au sud-est de Saint-Germain-de-la-Grange », « 800 m au nord de Plaisir », « au centre de Plaisir » (moins de 150 m) ; `label_en` en anglais.
  - Direction vue depuis la commune vers le point, en 8 secteurs (défaut) ou 16.
  - `commune`, `label` : `null` au-delà de 60 km de toute commune (mer, étranger, outre-mer).
  - `road` : `{"name","city","postcode","label","distance_m"}` ou `null`.
- **Sans Internet** : la commune la plus proche vient de la liste embarquée `src/sarcade/places/communes.csv.gz` (557 Ko, 35 191 communes de métropole, Corse comprise ; pas d'outre-mer). Point retenu : centre géométrique de la commune (ou un point intérieur), pas la mairie.
- **Avec Internet** : la rue la plus proche par le géocodage inverse de l'IGN Géoplateforme (`https://data.geopf.fr/geocodage/reverse`), délai 2,5 s, repli silencieux (`road: null`) ; réponses gardées 24 h (2 000 points, arrondi ~10 m). Seul le serveur interroge l'IGN.
- `SARCADE_REVERSE_GEOCODER_URL` : adresse du service (défaut : l'IGN) ; vide pour couper.
- **Altitude** (lot 5, fiche « Infos du point ») : `altitude_m` (m, sol, RGE ALTI de l'IGN Géoplateforme `https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json`), `null` sans Internet, hors données (-99999) ou en cas d'échec (repli silencieux) ; gardée 24 h comme la rue. `SARCADE_ALTIMETRY_URL` : adresse du service, vide pour couper.
- `department` : code du département de la commune (`78`, `2A`).

## Données
Source : [france-geojson](https://github.com/gregoiredavid/france-geojson) (`communes-version-simplifiee.geojson`), elle-même tirée d'IGN Admin Express et du Code officiel géographique de l'INSEE (millésime 2018), **Licence Ouverte / Etalab**. Régénération : `python tool/places/build_communes.py communes-version-simplifiee.geojson`.
Limite connue : millésime 2018 (communes nouvelles créées depuis sous leur ancien nom).

Vérification : `tests/test_places.py`, `demo/verify_places.py`.

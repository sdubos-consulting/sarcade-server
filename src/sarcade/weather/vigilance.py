"""Météo-France weather vigilance (department colour map).

Public data under Licence Ouverte 2.0, available only through the
Météo-France API portal with a key (``SARCADE_METEOFRANCE_API_KEY``).
The parser is deliberately tolerant: the structure is ``product.periods[]
.timelaps.domain_ids[]`` with, per department, a maximum colour and the
colour of each phenomenon. To be confirmed with a real key.

Card « Météo : limiter les appels à Météo-France » (10 Nov 2026): one
national map for all the events (choice 1), each event reads its
departments in it; several departments (choice 11): the highest colour,
with the department concerned.
"""
from __future__ import annotations

DEFAULT_URL = "https://public-api.meteofrance.fr/public/DPVigilance/v1/cartevigilance/encours"

COLORS = {1: "verte", 2: "jaune", 3: "orange", 4: "rouge"}
PHENOMENA = {
    "1": "vent violent", "2": "pluie-inondation", "3": "orages", "4": "crues",
    "5": "neige-verglas", "6": "canicule", "7": "grand froid", "8": "avalanches",
    "9": "vagues-submersion",
}


NAMES = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence", "05": "Hautes-Alpes",
    "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes", "09": "Ariège", "10": "Aube", "11": "Aude",
    "12": "Aveyron", "13": "Bouches-du-Rhône", "14": "Calvados", "15": "Cantal", "16": "Charente",
    "17": "Charente-Maritime", "18": "Cher", "19": "Corrèze", "2A": "Corse-du-Sud", "2B": "Haute-Corse",
    "21": "Côte-d'Or", "22": "Côtes-d'Armor", "23": "Creuse", "24": "Dordogne", "25": "Doubs", "26": "Drôme",
    "27": "Eure", "28": "Eure-et-Loir", "29": "Finistère", "30": "Gard", "31": "Haute-Garonne", "32": "Gers",
    "33": "Gironde", "34": "Hérault", "35": "Ille-et-Vilaine", "36": "Indre", "37": "Indre-et-Loire",
    "38": "Isère", "39": "Jura", "40": "Landes", "41": "Loir-et-Cher", "42": "Loire", "43": "Haute-Loire",
    "44": "Loire-Atlantique", "45": "Loiret", "46": "Lot", "47": "Lot-et-Garonne", "48": "Lozère",
    "49": "Maine-et-Loire", "50": "Manche", "51": "Marne", "52": "Haute-Marne", "53": "Mayenne",
    "54": "Meurthe-et-Moselle", "55": "Meuse", "56": "Morbihan", "57": "Moselle", "58": "Nièvre", "59": "Nord",
    "60": "Oise", "61": "Orne", "62": "Pas-de-Calais", "63": "Puy-de-Dôme", "64": "Pyrénées-Atlantiques",
    "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales", "67": "Bas-Rhin", "68": "Haut-Rhin", "69": "Rhône",
    "70": "Haute-Saône", "71": "Saône-et-Loire", "72": "Sarthe", "73": "Savoie", "74": "Haute-Savoie",
    "75": "Paris", "76": "Seine-Maritime", "77": "Seine-et-Marne", "78": "Yvelines", "79": "Deux-Sèvres",
    "80": "Somme", "81": "Tarn", "82": "Tarn-et-Garonne", "83": "Var", "84": "Vaucluse", "85": "Vendée",
    "86": "Vienne", "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne", "90": "Territoire de Belfort",
    "91": "Essonne", "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis", "94": "Val-de-Marne", "95": "Val-d'Oise",
    "971": "Guadeloupe", "972": "Martinique", "973": "Guyane", "974": "La Réunion", "976": "Mayotte",
}


def name_of(department: str | None) -> str | None:
    """« Yvelines » for 78 (also 7 → 07)."""
    if not department:
        return None
    d = department.strip().upper()
    return NAMES.get(d.zfill(2) if d.isdigit() else d)


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def _domains(period: dict) -> list:
    timelaps = period.get("timelaps") or {}
    if isinstance(timelaps, dict):
        return timelaps.get("domain_ids") or []
    if isinstance(timelaps, list):  # some versions: list of time slots
        items = []
        for slot in timelaps:
            items.extend((slot or {}).get("domain_ids") or [])
        return items
    return []


def department_vigilance(data: dict, department: str) -> dict | None:
    """Vigilance of today (first period) for one department, or None."""
    product = (data or {}).get("product") or data or {}
    periods = product.get("periods") or []
    if not periods:
        return None
    period = periods[0]
    wanted = department.upper().lstrip("0") or "0"
    best = None
    for domain in _domains(period):
        if str(domain.get("domain_id", "")).upper().lstrip("0") != wanted:
            continue
        color = _int(domain.get("max_color_id"))
        phenomena = []
        for item in domain.get("phenomenon_items") or []:
            c = _int(item.get("phenomenon_max_color_id"))
            color = max(color, c)
            if c >= 2:
                phenomena.append(PHENOMENA.get(str(item.get("phenomenon_id")), "autre"))
        if best is None or color > best["color_id"]:
            best = {"department": department.upper(), "department_name": name_of(department),
                    "color_id": color, "color": COLORS.get(color, "inconnue"),
                    "phenomena": phenomena, "begin": period.get("begin_validity_time"),
                    "end": period.get("end_validity_time")}
    return best


def highest(data: dict, departments: list[str]) -> dict | None:
    """Highest vigilance among ``departments`` (choice 11), the first one on
    a tie (the department set by the PCO or of the forecast point comes
    first); ``departments`` lists all the departments followed."""
    best = None
    for d in departments:
        v = department_vigilance(data, d)
        if v is not None and (best is None or v["color_id"] > best["color_id"]):
            best = v
    if best is not None:
        best["departments"] = list(departments)
    return best


def is_alert(vigilance: dict | None) -> bool:
    """Orange or red vigilance: alert the PCO (decision of 10 Oct 2026)."""
    return bool(vigilance) and vigilance["color_id"] >= 3

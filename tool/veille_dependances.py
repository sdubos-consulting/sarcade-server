# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Dependency watch on the i9 (card « GitHub de nouveau actif (10 novembre
2026) », choices 4b to 4g): the part of tool/veille-dependances.ps1 that
reads the outputs of the tools and writes the results. Python standard
library only, no network except ``images``.

    python -I tool/veille_dependances.py images --dockerfile Dockerfile [...] [--local-digests digests.json]
    python -I tool/veille_dependances.py rapport --collecte collecte.json --kanban "SARCADE - Kanban.md" \
        --fiches fiches --rapports C:/dev/sarcade/publications/veille

``rapport`` reads what the PowerShell script collected (OSV-Scanner JSON
reports and registers for ``failles``; ``flutter pub outdated --json``,
``uv lock --upgrade --dry-run``, ``uv tree --outdated``, Flutter and base
images for ``versions``), writes the full report, and keeps ONE card
« Veille des dépendances » at the top of the column « À valider » with its
fiche (sections between markers replaced, everything else kept). Nothing is
created when everything is up to date. Prints a JSON summary.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

TITLE = "Veille des dépendances"
SLUG = "veille-des-dependances"
TAGS = "#securite #dependances"
COLUMN = "## À valider"
MARK = "<!-- veille:{0}:{1} -->"
SECTIONS = ("failles", "versions")
SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "MEDIUM": 2, "LOW": 3}


# --- failles: OSV-Scanner reports -> findings -------------------------------

def _fixed_versions(vuln: dict, name: str) -> list[str]:
    fixed: set[str] = set()
    for aff in vuln.get("affected", []) or []:
        if (aff.get("package") or {}).get("name") not in (name, None):
            continue
        for rng in aff.get("ranges", []) or []:
            for ev in rng.get("events", []) or []:
                if ev.get("fixed"):
                    fixed.add(ev["fixed"])
    return sorted(fixed, key=_version_key)


def _version_key(v: str) -> tuple:
    parts = re.split(r"[.+-]", v.lstrip("vV"))
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts)


def _severity(group: dict, vuln: dict) -> str:
    """CVSS score of OSV-Scanner (« 7.5 ») turned into a word, else the
    severity of the database."""
    score = str(group.get("max_severity") or "").strip()
    try:
        s = float(score)
    except ValueError:
        s = None
    if s is not None:
        return "CRITICAL" if s >= 9 else "HIGH" if s >= 7 else "MODERATE" if s >= 4 else "LOW" if s > 0 else ""
    db = (vuln.get("database_specific") or {}).get("severity")
    return str(db or "").upper()


def findings(scans: list[dict]) -> list[dict]:
    """One finding per (repository, package, version, flaw group), from the
    OSV-Scanner JSON reports (dismissed flaws already left out by --config)."""
    found: dict[tuple, dict] = {}
    for scan in scans:
        path = scan.get("osv")
        if not path or not Path(path).is_file():
            continue
        try:
            report = json.loads(Path(path).read_text(encoding="utf-8-sig") or "{}")
        except ValueError:
            continue
        for result in report.get("results", []) or []:
            for pkg in result.get("packages", []) or []:
                info = pkg.get("package", {}) or {}
                vulns = {v.get("id"): v for v in pkg.get("vulnerabilities", []) or []}
                if not vulns:
                    continue
                for group in pkg.get("groups") or [{"ids": [i]} for i in vulns]:
                    ids = sorted(i for i in group.get("ids", []) if i)
                    if not ids:
                        continue
                    main_id = next((i for i in ids if i.startswith("GHSA-")), ids[0])
                    v = vulns.get(main_id) or next(iter(vulns.values()))
                    name, version = info.get("name", ""), info.get("version", "")
                    key = (scan["depot"], name, version, main_id)
                    if key in found:
                        continue
                    summary = (v.get("summary") or v.get("details") or "").strip().splitlines()
                    found[key] = {
                        "depot": scan["depot"], "fichier": scan.get("fichier", ""),
                        "ecosystem": info.get("ecosystem", ""), "package": name, "version": version,
                        "id": main_id, "aliases": sorted(set(group.get("aliases", []) or []) | set(ids)),
                        "summary": summary[0][:160] if summary else "",
                        "severity": _severity(group, v), "fixed": _fixed_versions(v, name)}
    return sorted(found.values(), key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["depot"], f["package"], f["id"]))


def _fix_for(items: list[dict]) -> str | None:
    """Smallest version that fixes every flaw of a package, None if one has
    no fix yet."""
    best = None
    for f in items:
        later = [x for x in f["fixed"] if _version_key(x) > _version_key(f["version"])]
        if not later:
            return None
        cand = later[0]
        best = cand if best is None or _version_key(cand) > _version_key(best) else best
    return best


def image_packages(path: str | None) -> set[str] | None:
    """Python packages of the image served by the NAS (its CycloneDX SBOM),
    None when unknown: a flaw of uv.lock absent from the image only touches
    the development tools."""
    if not path or not Path(path).is_file():
        return None
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except ValueError:
        return None
    out = set()
    for comp in doc.get("components") or []:
        purl = str(comp.get("purl") or "")
        if purl.startswith("pkg:pypi/"):
            out.add(_norm(purl[9:].split("@")[0]))
    return out


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def failles_section(data: dict, items: list[dict], detail: bool = False) -> tuple[str, int]:
    """Section of the fiche (one row per component) or of the report
    ([detail]: one row per flaw as well)."""
    when = f"{data['date']} à {data.get('heure', '')}".strip()
    lines = [f"## Failles (contrôle du {when})", ""]
    problems = [s for s in data.get("scans", []) if s.get("erreur")]
    registers = [r for r in data.get("registres", []) if r.get("code")]
    if not items and not problems and not registers:
        lines.append("Aucune faille non écartée dans les composants contrôlés.")
        lines += ["", "Contrôlés : " + ", ".join(f"`{s['depot']}/{s['fichier']}`" for s in data.get("scans", [])) + "."]
        return "\n".join(lines) + "\n", 0
    in_image = image_packages(data.get("image_sbom"))
    if items:
        by_pkg: dict[tuple, list[dict]] = {}
        for f in items:
            by_pkg.setdefault((f["depot"], f["fichier"], f["package"], f["version"]), []).append(f)
        lines += [f"**{len(items)} faille(s) non écartée(s)** dans {len(by_pkg)} composant(s), la plus grave d'abord "
                  "(détail : rapport complet).", "",
                  "| Gravité | Où | Composant | Version | Failles | Corrigée en |", "|---|---|---|---|---|---|"]
        for (depot, fichier, package, version), group in by_pkg.items():
            fix = _fix_for(group)
            note = ""
            if fichier == "uv.lock" and in_image is not None and _norm(package) not in in_image:
                note = " (absent de l'image : outil de développement)"
            ids = ", ".join(f"[{f['id']}](https://osv.dev/vulnerability/{f['id']})" for f in group[:3])
            more = f" et {len(group) - 3} autre(s)" if len(group) > 3 else ""
            lines.append(f"| {group[0]['severity'] or '?'} | {depot}/{fichier} | `{package}`{note} | {version} | {ids}{more} | "
                         f"{fix or 'pas pour toutes'} |")
        if detail:
            lines += ["", "Toutes les failles :", "", "| Gravité | Où | Composant | Version | Faille | Corrigée en | Résumé |",
                      "|---|---|---|---|---|---|---|"]
            for f in items:
                link = f"[{f['id']}](https://osv.dev/vulnerability/{f['id']})"
                lines.append(f"| {f['severity'] or '?'} | {f['depot']}/{f['fichier']} | `{f['package']}` | {f['version']} | {link} | "
                             f"{', '.join(f['fixed']) or 'pas encore'} | {f['summary'].replace('|', '/')} |")
        lines += ["", "Choix par défaut :"]
        n = 0
        system = [k for k, g in by_pkg.items() if g[0]["ecosystem"].lower().startswith(("debian", "ubuntu"))]
        for key, group in by_pkg.items():
            if key in system:
                continue
            depot, fichier, package, version = key
            n += 1
            fix = _fix_for(group)
            if fix:
                lines.append(f"{n}) `{package}` ({depot}/{fichier}, {len(group)} faille(s)) : passer de {version} à {fix} ou plus, "
                             f"sur une branche `veille-{re.sub(r'[^a-z0-9]+', '-', package.lower())}`, CI et fusion habituelles ;")
            else:
                lines.append(f"{n}) `{package}` ({depot}/{fichier}, {len(group)} faille(s)) : pas de version corrigée pour toutes ; "
                             "examiner, puis écarter si SARCADE n'est pas concerné ;")
        if system:
            n += 1
            names = ", ".join(f"`{k[2]}`" for k in system)
            lines.append(f"{n}) paquets du système de l'image ({names}) : image reconstruite avec `docker build --pull` "
                         "à la prochaine publication (correctifs Debian) ; ceux sans correctif, écartés s'ils ne servent "
                         "pas à SARCADE ;")
        n += 1
        lines.append(f"{n}) une faille qui ne concerne pas SARCADE : l'écarter dans `osv-scanner.toml` du dépôt, "
                     "avec la raison (`reason`) et une date de réexamen (`ignoreUntil`).")
    for s in problems:
        lines += ["", f"Contrôle impossible : `{s['depot']}/{s['fichier']}` ({s['erreur']})."]
    for r in registers:
        lines += ["", f"Registre `{r['depot']}/{r['fichier']}` invalide : " + "; ".join(r.get("lignes", [])[:5])]
    return "\n".join(lines) + "\n", len(items) + len(problems) + len(registers)


# --- versions ----------------------------------------------------------------

def pub_items(path: str | None) -> list[dict]:
    if not path or not Path(path).is_file():
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except ValueError:
        return []
    out = []
    for p in data.get("packages", []) or []:
        cur = (p.get("current") or {}).get("version")
        latest = (p.get("latest") or {}).get("version")
        if not cur or not latest or cur == latest:
            continue
        out.append({"package": p.get("package", ""), "kind": p.get("kind", ""), "current": cur,
                    "upgradable": (p.get("upgradable") or {}).get("version"),
                    "resolvable": (p.get("resolvable") or {}).get("version"), "latest": latest,
                    "discontinued": bool(p.get("isDiscontinued")),
                    "advisory": bool(p.get("isCurrentAffectedByAdvisory"))})
    return out


UV_UPDATE = re.compile(r"^Update (\S+) v(\S+) -> v(\S+)", re.M)
UV_TREE = re.compile(r"^\W*(\S+?)(?:\[[^\]]*\])? v(\S+)(?: \((?:extra|group): ([\w-]+)\))? \(latest: v(\S+)\)", re.M)


def uv_items(dry_run: str, tree: str) -> list[dict]:
    """Updates within the constraints of pyproject.toml (uv lock --upgrade
    --dry-run), then the direct dependencies whose latest version is beyond
    them (uv tree --outdated --depth 1)."""
    out = {m.group(1): {"package": m.group(1), "current": m.group(2), "lock": m.group(3), "latest": m.group(3),
                        "extra": None} for m in UV_UPDATE.finditer(dry_run or "")}
    for m in UV_TREE.finditer(tree or ""):
        name, cur, extra, latest = m.group(1), m.group(2), m.group(3), m.group(4)
        item = out.setdefault(name, {"package": name, "current": cur, "lock": None, "latest": latest, "extra": extra})
        item["latest"], item["extra"] = latest, extra
    return sorted(out.values(), key=lambda i: i["package"])


def _major(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    ka, kb = _version_key(a), _version_key(b)
    # 0.x: the minor is the breaking part.
    n = 2 if ka and ka[0] == (0, 0, "") else 1
    return ka[:n] != kb[:n]


def versions_section(data: dict) -> tuple[str, int]:
    when = f"{data['date']} à {data.get('heure', '')}".strip()
    lines = [f"## Nouvelles versions (contrôle du {when})", ""]
    count = 0
    choices: list[str] = []
    pub = [p for p in pub_items(data.get("pub")) if p["kind"] in ("direct", "dev")]
    pub_trans = [p for p in pub_items(data.get("pub")) if p["kind"] not in ("direct", "dev")]
    if pub or pub_trans:
        lines += ["**Application** (`flutter pub outdated`) :", "", "| Paquet | Actuelle | Possible sans changer pubspec.yaml | Possible | Dernière |",
                  "|---|---|---|---|---|"]
        for p in pub:
            flag = " (abandonné)" if p["discontinued"] else ""
            lines.append(f"| `{p['package']}`{flag} | {p['current']} | {p['upgradable'] or '-'} | {p['resolvable'] or '-'} | {p['latest']} |")
        if pub_trans:
            lines.append(f"| {len(pub_trans)} dépendances indirectes | | | | |")
        lines.append("")
        count += len(pub) + len(pub_trans)
        minor = [p for p in pub if p["resolvable"] and p["resolvable"] != p["current"] and not _major(p["current"], p["resolvable"])]
        major = [p for p in pub if _major(p["current"], p["latest"])]
        if minor or pub_trans:
            choices.append("application : mises à jour compatibles regroupées sur une branche `veille-versions-app` "
                           "(`flutter pub upgrade`), CI et fusion habituelles")
        if major:
            choices.append("application : versions majeures (" + ", ".join(f"`{p['package']}` {p['latest']}" for p in major[:8])
                           + ("…" if len(major) > 8 else "") + ") : une carte chacune, après lecture des notes de version")
    if data.get("pub_erreur"):
        lines += [f"Application : contrôle impossible ({data['pub_erreur']}).", ""]
        count += 1
    uv = uv_items(data.get("uv_maj", ""), data.get("uv_arbre", ""))
    if uv:
        lines += ["**Serveur** (`uv`) :", "", "| Paquet | Actuelle | Par `uv lock --upgrade` | Dernière |", "|---|---|---|---|"]
        for i in uv:
            extra = " (dev)" if i["extra"] else ""
            lines.append(f"| `{i['package']}`{extra} | {i['current']} | {i['lock'] or '-'} | {i['latest']} |")
        lines.append("")
        count += len(uv)
        if any(i["lock"] for i in uv):
            choices.append("serveur : mises à jour compatibles regroupées sur une branche `veille-versions-serveur` "
                           "(`uv lock --upgrade`), `uv run pytest`, fusion habituelle")
        beyond = [i for i in uv if i["latest"] != (i["lock"] or i["current"])]
        if beyond:
            choices.append("serveur : au-delà des bornes de `pyproject.toml` (" + ", ".join(
                f"`{i['package']}` {i['latest']}" for i in beyond) + ") : une carte chacune")
    if data.get("uv_erreur"):
        lines += [f"Serveur : contrôle impossible ({data['uv_erreur']}).", ""]
        count += 1
    fl = data.get("flutter") or {}
    if fl.get("stable") and fl.get("installe") and fl["stable"] != fl["installe"]:
        lines += [f"**Flutter** : stable {fl['stable']} ({fl.get('date', '')}), installée sur l'i9 : {fl['installe']}.", ""]
        count += 1
        choices.append(f"Flutter {fl['stable']} : `flutter upgrade` sur l'i9, puis CI complète de l'application avant toute fusion")
    elif fl.get("erreur"):
        lines += [f"Flutter : contrôle impossible ({fl['erreur']}).", ""]
    for img in data.get("images", []) or []:
        notes = []
        if img.get("plus_recente"):
            notes.append(f"nouvelle version `{img['plus_recente']}`")
            choices.append(f"image `{img['image']}` → `{img['plus_recente']}` ({img['fichier']}) : une carte, CI complète du serveur")
        if img.get("reconstruite"):
            notes.append(f"étiquette reconstruite le {img['reconstruite']} (correctifs du système)")
            choices.append(f"image `{img['image']}` reconstruite : prochaine publication avec `docker build --pull`")
        if img.get("erreur"):
            notes.append(f"contrôle impossible ({img['erreur']})")
        if notes:
            lines += [f"**Image de base** `{img['image']}` ({img['fichier']}) : " + " ; ".join(notes) + ".", ""]
            count += 1
    if count == 0:
        return "\n".join(lines + ["Tout est à jour (application, serveur, Flutter, images de base)."]) + "\n", 0
    lines.append("Choix par défaut (aucune mise à jour appliquée seule) :")
    lines += [f"{n}) {c} ;" for n, c in enumerate(choices, 1)]
    if choices:
        lines[-1] = lines[-1][:-2] + "."
    return "\n".join(lines) + "\n", count


# --- base images (Docker Hub) -------------------------------------------------

FROM_RE = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", re.M | re.I)
TAG_RE = re.compile(r"^(v?)(\d+(?:\.\d+)*)(.*)$")


def images_in(dockerfile: Path) -> list[str]:
    stages = set(re.findall(r"\bAS\s+(\S+)", dockerfile.read_text(encoding="utf-8"), re.I))
    out = []
    for ref in FROM_RE.findall(dockerfile.read_text(encoding="utf-8")):
        if ref == "scratch" or "$" in ref or ref in stages or ":" not in ref:
            continue
        if ref not in out:
            out.append(ref)
    return out


def _hub_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "sarcade-veille"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def newer_tag(tag: str, names: list[str]) -> str | None:
    """« 3.15-slim » among Docker Hub tags → « 3.16-slim » when it exists
    (same prefix, suffix and number of parts)."""
    m = TAG_RE.match(tag)
    if not m:
        return None
    prefix, nums, suffix = m.groups()
    parts = len(nums.split("."))
    best = None
    for name in names:
        n = TAG_RE.match(name)
        if not n or n.group(1) != prefix or n.group(3) != suffix or len(n.group(2).split(".")) != parts:
            continue
        key = tuple(int(x) for x in n.group(2).split("."))
        if key > tuple(int(x) for x in nums.split(".")) and (best is None or key > best[0]):
            best = (key, name)
    return best[1] if best else None


def check_image(ref: str, fichier: str, local_digest: str | None, hub=_hub_get) -> dict:
    repo, tag = ref.rsplit(":", 1)
    path = repo if "/" in repo else f"library/{repo}"
    out = {"image": ref, "fichier": fichier, "plus_recente": None, "reconstruite": None, "erreur": None}
    try:
        cur = hub(f"https://hub.docker.com/v2/repositories/{path}/tags/{urllib.parse.quote(tag)}")
        m = TAG_RE.match(tag)
        names = []
        if m:
            search = m.group(3) or m.group(1)
            for page in (1, 2, 3):
                q = urllib.parse.urlencode({"page_size": 100, "page": page, "ordering": "last_updated",
                                            **({"name": search} if search else {})})
                data = hub(f"https://hub.docker.com/v2/repositories/{path}/tags?{q}")
                names += [t["name"] for t in data.get("results", [])]
                if not data.get("next"):
                    break
        out["plus_recente"] = newer_tag(tag, names)
        remote = cur.get("digest")
        if local_digest and remote and local_digest != remote:
            out["reconstruite"] = str(cur.get("last_updated", ""))[:10]
    except Exception as exc:  # noqa: BLE001 - network, format: reported, never fatal
        out["erreur"] = type(exc).__name__
    return out


# --- fiche and Kanban -----------------------------------------------------------

def _between(text: str, name: str) -> tuple[int, int] | None:
    a, b = text.find(MARK.format(name, "debut")), text.find(MARK.format(name, "fin"))
    return (a, b + len(MARK.format(name, "fin"))) if a >= 0 and b > a else None


def new_fiche() -> str:
    body = [
        "---", "statut: À valider", "fait: non", "tags: [securite, dependances]", "---", "",
        f"# {TITLE}", "", "Retour au [[SARCADE - Kanban]].", "",
        f"**{TITLE}** {TAGS}",
        "- Fiche tenue par `sarcade-server/tool/veille-dependances.ps1` (carte « GitHub de nouveau actif "
        "(10 novembre 2026) », choix 4b à 4g) : failles chaque jour à 6 h, nouvelles versions chaque lundi à 7 h. "
        "Rapports complets : `C:\\dev\\sarcade\\publications\\veille\\`. Aucune mise à jour n'est appliquée seule : "
        "après validation, branche, CI et fusion habituelles. Les sections ci-dessous sont réécrites à chaque "
        "contrôle ; le reste de la fiche n'est jamais touché.", ""]
    for name in SECTIONS:
        body += [MARK.format(name, "debut"), "_Pas encore de contrôle._", MARK.format(name, "fin"), ""]
    body += ["## Décisions du porteur", ""]
    return "\n".join(body) + "\n"


def set_section(fiche: str, name: str, content: str) -> str:
    span = _between(fiche, name)
    block = f"{MARK.format(name, 'debut')}\n{content.rstrip()}\n{MARK.format(name, 'fin')}"
    if span:
        return fiche[:span[0]] + block + fiche[span[1]:]
    return fiche.rstrip("\n") + "\n\n" + block + "\n"


def card_line(link: str) -> str:
    return f"- [ ] **{TITLE}** [[{link}|fiche]] {TAGS}\n"


def column_bounds(lines: list[str]) -> tuple[int, int]:
    start = next(i for i, l in enumerate(lines) if l.strip() == COLUMN)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def existing_link(kanban: str) -> str | None:
    lines = kanban.splitlines(keepends=True)
    start, end = column_bounds(lines)
    for line in lines[start + 1:end]:
        if f"**{TITLE}**" in line:
            m = re.search(r"\[\[(fiches/[^|\]]+)", line)
            if m:
                return m.group(1)
    return None


def put_card_on_top(kanban: str, link: str) -> str:
    """Our card first in « À valider », once (any other copy in that column
    removed); every line keeps its line break."""
    lines = kanban.splitlines(keepends=True)
    start, end = column_bounds(lines)
    kept = [l for l in lines[start + 1:end] if f"**{TITLE}**" not in l]
    first = next((i for i, l in enumerate(kept) if l.strip()), len(kept))
    lead = kept[:first] or ["\n"]
    if not lead[-1].endswith("\n"):
        lead[-1] += "\n"
    body = lead + [card_line(link)] + kept[first:]
    out = lines[:start + 1] + body + lines[end:]
    return "".join(out) if "".join(out).endswith("\n") else "".join(out) + "\n"


# --- rapport -----------------------------------------------------------------

def rapport(collecte: Path, kanban: Path, fiches: Path, rapports: Path) -> dict:
    data = json.loads(collecte.read_text(encoding="utf-8-sig"))
    mode = data["mode"]
    if mode == "failles":
        items = findings(data.get("scans", []))
        section, count = failles_section(data, items)
        full, _ = failles_section(data, items, detail=True)
        summary = {"failles": len(items), "signalements": count}
    else:
        section, count = versions_section(data)
        full = section
        summary = {"versions": count, "signalements": count}
    rapports.mkdir(parents=True, exist_ok=True)
    stamp = f"{data['date']}-{data.get('heure', '0000').replace(':', '')}"
    report = rapports / f"veille-{mode}-{stamp}.md"
    extra = data.get("journal", "")
    report.write_text(f"# {TITLE} : {mode} ({data['date']} {data.get('heure', '')})\n\n{full}\n"
                      + (f"\n## Journal\n\n```\n{extra.strip()}\n```\n" if extra.strip() else ""), encoding="utf-8")
    summary["rapport"] = str(report)
    text = kanban.read_text(encoding="utf-8")
    link = existing_link(text)
    created = link is None
    if count == 0 and link is None:
        summary["carte"] = "aucune (tout est à jour)"
        return summary
    if link is None:
        link = f"fiches/{data['date']} {SLUG}"
    fiche_path = fiches / (Path(link).name + ".md")
    fiche = fiche_path.read_text(encoding="utf-8") if fiche_path.is_file() else new_fiche()
    fiche_path.parent.mkdir(parents=True, exist_ok=True)
    fiche_path.write_text(set_section(fiche, mode, section), encoding="utf-8")
    text = put_card_on_top(text, link)
    kanban.write_text(text, encoding="utf-8")
    summary["carte"] = "créée" if created else "mise à jour"
    summary["fiche"] = str(fiche_path)
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("images")
    i.add_argument("--dockerfile", action="append", required=True, help="label=path")
    i.add_argument("--local-digests", help="JSON {image: digest} of the images present on the i9")
    r = sub.add_parser("rapport")
    r.add_argument("--collecte", required=True)
    r.add_argument("--kanban", required=True)
    r.add_argument("--fiches", required=True)
    r.add_argument("--rapports", required=True)
    a = p.parse_args(argv)
    if a.cmd == "images":
        local = {}
        if a.local_digests and Path(a.local_digests).is_file():
            local = json.loads(Path(a.local_digests).read_text(encoding="utf-8-sig") or "{}")
        out = []
        for spec in a.dockerfile:
            label, _, path = spec.partition("=")
            for ref in images_in(Path(path)):
                out.append(check_image(ref, label, local.get(ref)))
        print(json.dumps(out, ensure_ascii=False))
        return 0
    summary = rapport(Path(a.collecte), Path(a.kanban), Path(a.fiches), Path(a.rapports))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

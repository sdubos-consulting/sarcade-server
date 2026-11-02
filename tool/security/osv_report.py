"""Nightly check of the SBOM against OSV: applies the register of the
dismissed flaws (``security/vex.json``, OpenVEX) to the result of OSV-Scanner
and writes the text of the GitHub issue « Faille dans un composant ».

Usage (workflow ``sbom-nightly.yml``):

    python tool/security/osv_report.py --osv osv.json --vex security/vex.json \
        --markdown report.md --ids ids.txt [--source "main @ abc123"]
    python tool/security/osv_report.py --check-vex security/vex.json

Register format: docs/security-vex-v0.1.md. Each statement needs a reason
(``impact_statement``) and a review date written in ``status_notes``
(« réexamen AAAA-MM-JJ »); after that date the flaw is reported again.
Exit 0 when the report is written (flaws or not), 2 when an input is invalid.
Standard library only (runs on the bare GitHub runner).
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

DISMISSING = {"not_affected", "fixed"}
STATUSES = DISMISSING | {"affected", "under_investigation"}
JUSTIFICATIONS = {
    "component_not_present", "vulnerable_code_not_present", "vulnerable_code_not_in_execute_path",
    "vulnerable_code_cannot_be_controlled_by_adversary", "inline_mitigations_already_exist",
}
REVIEW_RE = re.compile(r"(?:réexamen|reexamen|review)\D{0,5}(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
MARKER = "sarcade-osv-ids:"
# OSV ecosystem (before any ":release") → purl type.
ECOSYSTEM_TYPES = {"pypi": "pypi", "debian": "deb", "ubuntu": "deb", "alpine": "apk", "npm": "npm",
                   "go": "golang", "pub": "pub", "maven": "maven", "crates.io": "cargo", "nuget": "nuget"}


class InvalidInput(Exception):
    pass


# --- register (OpenVEX) -----------------------------------------------------

def review_date(statement: dict) -> dt.date | None:
    match = REVIEW_RE.search(statement.get("status_notes") or "")
    if not match:
        return None
    try:
        return dt.date.fromisoformat(match.group(1))
    except ValueError:
        return None


def load_vex(path: str | Path) -> list[dict]:
    """Reads and checks the register; raises InvalidInput with every problem."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidInput(f"{path}: illisible ({exc})") from exc
    problems = []
    if not str(doc.get("@context", "")).startswith("https://openvex.dev/ns"):
        problems.append("@context OpenVEX manquant")
    statements = doc.get("statements")
    if not isinstance(statements, list):
        raise InvalidInput(f"{path}: 'statements' doit être une liste")
    for i, s in enumerate(statements):
        where = f"statements[{i}]"
        name = (s.get("vulnerability") or {}).get("name")
        if not name:
            problems.append(f"{where}: vulnerability.name manquant")
        status = s.get("status")
        if status not in STATUSES:
            problems.append(f"{where} ({name}): status invalide {status!r}")
        if status == "not_affected" and s.get("justification") not in JUSTIFICATIONS:
            problems.append(f"{where} ({name}): justification OpenVEX manquante ou invalide")
        if not (s.get("impact_statement") or "").strip():
            problems.append(f"{where} ({name}): raison (impact_statement) obligatoire")
        if review_date(s) is None:
            problems.append(f"{where} ({name}): date de réexamen obligatoire dans status_notes (« réexamen AAAA-MM-JJ »)")
        for product in s.get("products") or []:
            if not str(product.get("@id", "")).startswith("pkg:"):
                problems.append(f"{where} ({name}): produit sans purl (@id « pkg:… »)")
    if problems:
        raise InvalidInput(f"{path}:\n- " + "\n- ".join(problems))
    return statements


def parse_purl(purl: str) -> tuple[str, str, str | None]:
    """pkg:type/namespace/name@version → (type, name, version)."""
    body = purl.removeprefix("pkg:").split("?")[0].split("#")[0]
    body, _, version = body.partition("@")
    parts = body.split("/")
    return parts[0].lower(), unquote(parts[-1]).lower(), unquote(version) or None


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.lower())


def statement_applies(statement: dict, finding: dict) -> bool:
    names = {statement["vulnerability"]["name"], *(statement["vulnerability"].get("aliases") or [])}
    if not names & set(finding["ids"]):
        return False
    products = statement.get("products") or []
    if not products:
        return True
    for product in products:
        kind, name, version = parse_purl(product["@id"])
        if (kind == finding["purl_type"] and _norm(name) == _norm(finding["package"])
                and (version is None or version == finding["version"])):
            return True
    return False


# --- OSV-Scanner result -----------------------------------------------------

def _fixed_versions(vuln: dict, package: str) -> set[str]:
    fixed = set()
    for affected in vuln.get("affected") or []:
        if _norm((affected.get("package") or {}).get("name", "")) != _norm(package):
            continue
        for rng in affected.get("ranges") or []:
            fixed |= {e["fixed"] for e in rng.get("events") or [] if e.get("fixed")}
    return fixed


def _severity(group: dict, vulns: list[dict]) -> str:
    if group.get("max_severity"):
        return str(group["max_severity"])
    for v in vulns:
        sev = (v.get("database_specific") or {}).get("severity")
        if sev:
            return str(sev)
    return ""


def findings(osv: dict) -> list[dict]:
    """One finding per (package, flaw group); groups merge the aliases."""
    out = []
    for result in osv.get("results") or []:
        for entry in result.get("packages") or []:
            pkg = entry.get("package") or {}
            vulns = {v["id"]: v for v in entry.get("vulnerabilities") or [] if v.get("id")}
            groups = entry.get("groups") or [{"ids": [i]} for i in vulns]
            ecosystem = str(pkg.get("ecosystem", ""))
            for group in groups:
                members = [vulns[i] for i in group.get("ids") or [] if i in vulns]
                ids = list(dict.fromkeys([*(group.get("ids") or []), *(group.get("aliases") or []),
                                          *(a for v in members for a in v.get("aliases") or [])]))
                if not ids:
                    continue
                fixed = set().union(*(_fixed_versions(v, pkg.get("name", "")) for v in members)) if members else set()
                out.append({
                    "ids": ids, "id": ids[0], "package": pkg.get("name", ""), "version": pkg.get("version", ""),
                    "ecosystem": ecosystem,
                    "purl_type": ECOSYSTEM_TYPES.get(ecosystem.split(":")[0].lower(), ecosystem.lower()),
                    "severity": _severity(group, members),
                    "fixed": sorted(fixed),
                    "summary": next((v.get("summary") for v in members if v.get("summary")), ""),
                })
    unique = {}
    for f in out:
        unique.setdefault((f["id"], f["package"], f["version"]), f)
    return sorted(unique.values(), key=lambda f: (f["package"].lower(), f["id"]))


def triage(found: list[dict], statements: list[dict], today: dt.date) -> tuple[list[dict], list[dict]]:
    """→ (open, dismissed). A dismissal past its review date opens again."""
    open_, dismissed = [], []
    for f in found:
        match = next((s for s in statements if statement_applies(s, f)), None)
        if match is None:
            open_.append(f)
            continue
        due = review_date(match)
        item = {**f, "vex_status": match["status"], "reason": match["impact_statement"].strip(), "review": due}
        if match["status"] in DISMISSING and due is not None and due >= today:
            dismissed.append(item)
        else:
            item["note"] = ("réexamen dépassé le " + due.isoformat()) if match["status"] in DISMISSING \
                else f"registre : {match['status']}"
            open_.append(item)
    return open_, dismissed


# --- issue text ---------------------------------------------------------------

def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _link(vid: str) -> str:
    return f"[{vid}](https://osv.dev/vulnerability/{vid})"


def markdown(open_: list[dict], dismissed: list[dict], source: str, today: dt.date) -> str:
    lines = [f"Contrôle de nuit du {today.isoformat()} : SBOM {source} comparé à la base OSV (OSV-Scanner).", ""]
    if open_:
        lines += [f"**{len(open_)} faille(s) non écartée(s)**", "",
                  "| Faille | Composant | Version | Gravité | Corrigée en | Résumé |",
                  "|---|---|---|---|---|---|"]
        for f in open_:
            alias = ", ".join(i for i in f["ids"][1:4])
            summary = f["summary"] + (f" ({f['note']})" if f.get("note") else "")
            lines.append(f"| {_link(f['id'])}{' (' + _cell(alias) + ')' if alias else ''} | {_cell(f['package'])} "
                         f"({_cell(f['ecosystem'])}) | {_cell(f['version'])} | {_cell(f['severity']) or '?'} | "
                         f"{_cell(', '.join(f['fixed'])) or 'pas de correctif'} | {_cell(summary)[:200]} |")
    else:
        lines.append("Aucune faille non écartée.")
    if dismissed:
        lines += ["", f"<details><summary>{len(dismissed)} faille(s) écartée(s) par le registre "
                  "<code>security/vex.json</code></summary>", "",
                  "| Faille | Composant | Statut | Raison | Réexamen |", "|---|---|---|---|---|"]
        for f in dismissed:
            lines.append(f"| {_link(f['id'])} | {_cell(f['package'])} {_cell(f['version'])} | {f['vex_status']} | "
                         f"{_cell(f['reason'])[:200]} | {f['review'].isoformat()} |")
        lines += ["", "</details>"]
    lines += ["", "Pour écarter une faille : ajouter une déclaration dans `security/vex.json` "
              "(raison et date de réexamen obligatoires, voir `docs/security-vex-v0.1.md`).", "",
              f"<!-- {MARKER} {','.join(f['id'] for f in open_)} -->"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--osv")
    parser.add_argument("--vex", default="security/vex.json")
    parser.add_argument("--markdown")
    parser.add_argument("--ids")
    parser.add_argument("--source", default="")
    parser.add_argument("--today")
    parser.add_argument("--check-vex", metavar="VEX")
    args = parser.parse_args(argv)
    try:
        if args.check_vex:
            statements = load_vex(args.check_vex)
            print(f"{args.check_vex}: {len(statements)} déclaration(s) valide(s)")
            return 0
        if not args.osv:
            parser.error("--osv requis")
        today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
        statements = load_vex(args.vex) if Path(args.vex).is_file() else []
        try:
            osv = json.loads(Path(args.osv).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InvalidInput(f"{args.osv}: résultat OSV illisible ({exc})") from exc
        open_, dismissed = triage(findings(osv), statements, today)
    except InvalidInput as exc:
        print(f"ERREUR {exc}", file=sys.stderr)
        return 2
    if args.markdown:
        Path(args.markdown).write_text(markdown(open_, dismissed, args.source, today), encoding="utf-8")
    if args.ids:
        Path(args.ids).write_text("".join(f["id"] + "\n" for f in open_), encoding="utf-8")
    print(f"{len(open_)} faille(s) non écartée(s), {len(dismissed)} écartée(s)")
    for f in open_:
        print(f"  {f['id']} {f['package']} {f['version']} {f.get('note', '')}".rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())

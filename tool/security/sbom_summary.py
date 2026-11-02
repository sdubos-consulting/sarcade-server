"""One line about a CycloneDX SBOM: components per kind of package (purl type)
and distribution, to see at a glance what the nightly check covers."""
import collections
import json
import sys
from urllib.parse import parse_qs


def summary(doc: dict) -> str:
    kinds = collections.Counter()
    distros = collections.Counter()
    for comp in doc.get("components") or []:
        purl = str(comp.get("purl") or "")
        if not purl.startswith("pkg:"):
            kinds["sans purl"] += 1
            continue
        kinds[purl[4:].split("/")[0]] += 1
        distro = parse_qs(purl.partition("?")[2]).get("distro")
        if distro:
            distros[distro[0]] += 1
    total = sum(kinds.values())
    parts = ", ".join(f"{k} {n}" for k, n in kinds.most_common())
    dist = ", ".join(f"{k} {n}" for k, n in distros.most_common()) or "aucune"
    return f"SBOM : {total} composants ({parts}) ; distribution : {dist}"


if __name__ == "__main__":
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as fh:
            print(f"{summary(json.load(fh))}\n")

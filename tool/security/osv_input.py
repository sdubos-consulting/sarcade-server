"""Copy of a CycloneDX SBOM prepared for OSV-Scanner: the Debian release of
the purls is cut to its major number (``distro=debian-13.7`` → ``debian-13``),
as the OSV ecosystem names it (``Debian:13``); otherwise point releases match
no advisory. The SBOM served by the server is left untouched.

Usage: python tool/security/osv_input.py in.cdx.json out.cdx.json
"""
import json
import re
import sys

DISTRO_RE = re.compile(r"(distro=(?:debian|ubuntu)-)(\d+)(?:\.\d+)+")


def normalize(doc: dict) -> int:
    changed = 0
    for comp in doc.get("components") or []:
        purl = comp.get("purl")
        if isinstance(purl, str) and "distro=" in purl:
            new = DISTRO_RE.sub(r"\1\2", purl)
            if new != purl:
                comp["purl"] = new
                changed += 1
    return changed


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as fh:
        document = json.load(fh)
    count = normalize(document)
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(document, fh)
    print(f"{count} purl(s) Debian ramenée(s) à la version majeure")

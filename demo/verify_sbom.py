"""Integration check of the SBOM of the image (lot 22): the image built by the
Dockerfile, as on the NAS, serves its inventory of components (CycloneDX) at
/sarcade-sbom.cdx.json, with the Python packages, the system packages and the
embedded web client, and nothing of the environment of the server.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_sbom.py
"""
import os
import sys

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
c = httpx.Client(timeout=30)


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


r = c.get(f"{BASE}/sarcade-sbom.cdx.json")
check(r.status_code == 200, f"SBOM served (HTTP {r.status_code})")
check(r.headers["content-type"].startswith("application/vnd.cyclonedx+json"), "CycloneDX media type")
doc = r.json()
check(doc.get("bomFormat") == "CycloneDX" and doc.get("specVersion"), f"CycloneDX {doc.get('specVersion')}")
check(doc["metadata"]["component"]["name"] == "sarcade-server", "image named sarcade-server")
components = doc.get("components") or []
purls = [str(comp.get("purl", "")) for comp in components]
check(any(p.startswith("pkg:pypi/fastapi@") for p in purls), "Python packages (fastapi)")
check(any(p.startswith("pkg:pypi/sqlalchemy@") for p in purls), "Python packages (sqlalchemy)")
check(any(p.startswith("pkg:deb/debian/") for p in purls), "system packages (Debian)")
web = [comp for comp in components if comp.get("name") == "sarcade-web"]
check(len(web) == 1 and web[0]["hashes"][0]["alg"] == "SHA-256", "embedded web client with its SHA-256")
served = c.get(f"{BASE}/sarcade-web.sha256").text.strip().removeprefix("sha256:")
check(web[0]["hashes"][0]["content"] == served, "same digest as /sarcade-web.sha256")
check(not any(comp.get("type") == "file" for comp in components), "no file inventory")
check("sarcade-demo" not in r.text and "DATABASE_URL" not in r.text, "nothing of the server environment")
print(f"{len(components)} components")

# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Dependency watch on the i9 (tool/veille_dependances.py; card « GitHub de
nouveau actif (10 novembre 2026) », choices 4b to 4g): one card at the top
of « À valider », updated without duplicate, nothing when all is up to date."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("veille", ROOT / "tool" / "veille_dependances.py")
veille = importlib.util.module_from_spec(spec)
spec.loader.exec_module(veille)

KANBAN = """---

kanban-plugin: board

---

## À valider

- [ ] **Autre carte** [[fiches/2026-11-09 autre|fiche]]
- [ ] **Encore une** [[fiches/2026-11-09 encore|fiche]]

## À développer

- [ ] **Veille des dépendances** [[fiches/2026-01-01 veille-des-dependances|fiche]] #securite #dependances
"""

OSV = {"results": [{"source": {"path": "uv.lock", "type": "lockfile"}, "packages": [{
    "package": {"name": "pytest", "version": "8.4.2", "ecosystem": "PyPI"},
    "vulnerabilities": [{"id": "GHSA-6w46-j5rx-g56g", "summary": "tmp dir race",
                         "affected": [{"package": {"name": "pytest", "ecosystem": "PyPI"},
                                       "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "9.0.3"}]}]}]},
                        {"id": "PYSEC-2026-1845"}],
    "groups": [{"ids": ["GHSA-6w46-j5rx-g56g", "PYSEC-2026-1845"], "aliases": ["CVE-2026-1"], "max_severity": "7.5"}]}]}]}


def setup(tmp_path, osv=OSV, mode="failles"):
    (tmp_path / "osv.json").write_text(json.dumps(osv), encoding="utf-8")
    kanban = tmp_path / "SARCADE - Kanban.md"
    kanban.write_text(KANBAN, encoding="utf-8")
    data = {"mode": mode, "date": "2026-11-10", "heure": "06:00",
            "scans": [{"depot": "sarcade-server", "fichier": "uv.lock", "osv": str(tmp_path / "osv.json")}],
            "registres": [{"depot": "sarcade-server", "fichier": "osv-scanner.toml", "code": 0}]}
    (tmp_path / "c.json").write_text(json.dumps(data), encoding="utf-8")
    return kanban


def run(tmp_path):
    return veille.rapport(tmp_path / "c.json", tmp_path / "SARCADE - Kanban.md", tmp_path / "fiches", tmp_path / "rapports")


def test_flaw_makes_one_card_on_top_of_a_valider(tmp_path):
    kanban = setup(tmp_path)
    out = run(tmp_path)
    assert out["failles"] == 1 and out["carte"] == "créée"
    text = kanban.read_text(encoding="utf-8")
    lines = text.splitlines()
    top = lines.index("## À valider") + 2
    assert lines[top] == ("- [ ] **Veille des dépendances** [[fiches/2026-11-10 veille-des-dependances|fiche]] "
                          "#securite #dependances")
    assert lines[top + 1].startswith("- [ ] **Autre carte**")
    # The card of another column is not touched, the file ends with a line break.
    assert "[[fiches/2026-01-01 veille-des-dependances|fiche]]" in text and text.endswith("\n")
    fiche = (tmp_path / "fiches" / "2026-11-10 veille-des-dependances.md").read_text(encoding="utf-8")
    assert "GHSA-6w46-j5rx-g56g" in fiche and "HIGH" in fiche and "passer de 8.4.2 à 9.0.3" in fiche
    assert "Toutes les failles" in Path(out["rapport"]).read_text(encoding="utf-8") and "Toutes les failles" not in fiche
    assert fiche.index("veille:failles:debut") < fiche.index("veille:versions:debut")
    assert Path(out["rapport"]).is_file()


def test_second_run_updates_without_duplicate_and_keeps_notes(tmp_path):
    kanban = setup(tmp_path)
    run(tmp_path)
    fiche = tmp_path / "fiches" / "2026-11-10 veille-des-dependances.md"
    fiche.write_text(fiche.read_text(encoding="utf-8") + "Note du porteur : on attend pytest 9.\n", encoding="utf-8")
    # Moved down by hand meanwhile: put back on top, once.
    text = kanban.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines(keepends=True) if "2026-11-10 veille" in l)
    text = text.replace(line, "").replace("- [ ] **Encore une**", line + "- [ ] **Encore une**")
    kanban.write_text(text, encoding="utf-8")
    out = run(tmp_path)
    assert out["carte"] == "mise à jour"
    text = kanban.read_text(encoding="utf-8")
    assert text.count("2026-11-10 veille-des-dependances") == 1
    assert text.splitlines()[text.splitlines().index("## À valider") + 2].startswith("- [ ] **Veille des dépendances**")
    body = fiche.read_text(encoding="utf-8")
    assert body.count("veille:failles:debut") == 1 and "Note du porteur : on attend pytest 9." in body


def test_nothing_when_everything_is_up_to_date(tmp_path):
    kanban = setup(tmp_path, osv={"results": []})
    out = run(tmp_path)
    assert out["failles"] == 0 and out["carte"].startswith("aucune")
    assert kanban.read_text(encoding="utf-8") == KANBAN
    assert not (tmp_path / "fiches").exists()


def test_versions_section(tmp_path):
    pub = {"packages": [
        {"package": "xml", "kind": "direct", "current": {"version": "6.6.1"}, "upgradable": {"version": "6.6.1"},
         "resolvable": {"version": "7.1.0"}, "latest": {"version": "7.1.0"}},
        {"package": "hive_ce", "kind": "direct", "current": {"version": "2.23.2"}, "upgradable": {"version": "2.23.3"},
         "resolvable": {"version": "2.23.3"}, "latest": {"version": "2.23.3"}},
        {"package": "bluez", "kind": "transitive", "current": {"version": "0.8.2"}, "latest": {"version": "0.8.3"}}]}
    (tmp_path / "pub.json").write_text(json.dumps(pub), encoding="utf-8")
    data = {"date": "2026-11-16", "heure": "07:00", "pub": str(tmp_path / "pub.json"),
            "uv_maj": "Resolved 74 packages\nUpdate fastapi v0.162.0 -> v0.162.2\n",
            "uv_arbre": "sarcade-server v0.1.0\n├── fastapi v0.162.0 (latest: v0.162.2)\n"
                        "├── psycopg[binary] v3.3.6 (latest: v3.3.8)\n└── pytest v8.4.2 (extra: dev) (latest: v9.2.0)\n",
            "flutter": {"installe": "3.47.9", "stable": "3.50.0", "date": "2026-11-12"},
            "images": [{"image": "python:3.15-slim", "fichier": "Dockerfile", "plus_recente": None,
                        "reconstruite": "2026-11-07", "erreur": None}]}
    text, count = veille.versions_section(data)
    assert count == 3 + 3 + 1 + 1
    assert "`xml` | 6.6.1 | 6.6.1 | 7.1.0 | 7.1.0" in text and "1 dépendances indirectes" in text
    assert "`pytest` (dev) | 8.4.2 | - | 9.2.0" in text and "`psycopg` | 3.3.6 | - | 3.3.8" in text
    assert "Flutter 3.50.0" in text and "docker build --pull" in text
    assert "versions majeures (`xml` 7.1.0)" in text
    empty, n = veille.versions_section({"date": "2026-11-16", "flutter": {"installe": "3.47.9", "stable": "3.47.9"}})
    assert n == 0 and "Tout est à jour" in empty


def test_base_images(tmp_path):
    df = tmp_path / "Dockerfile"
    df.write_text("FROM scratch AS web\nFROM anchore/syft:v1.57.0 AS sbom\nFROM web-${X} AS w\nFROM python:3.15-slim\n",
                  encoding="utf-8")
    assert veille.images_in(df) == ["anchore/syft:v1.57.0", "python:3.15-slim"]
    assert veille.newer_tag("3.15-slim", ["3.16-slim", "3.16.1-slim", "3.17-slim-trixie", "3.14-slim"]) == "3.16-slim"
    assert veille.newer_tag("v1.57.0", ["v1.58.1", "v1.58.0", "latest", "1.60.0"]) == "v1.58.1"
    assert veille.newer_tag("2.10", ["2.10-builder", "2.9"]) is None

    def hub(url):
        if "/tags/" in url:
            return {"digest": "sha256:new", "last_updated": "2026-11-07T23:08:05Z"}
        return {"results": [{"name": "3.15-slim"}, {"name": "3.16-slim"}], "next": None}
    got = veille.check_image("python:3.15-slim", "Dockerfile", "sha256:old", hub=hub)
    assert got["plus_recente"] == "3.16-slim" and got["reconstruite"] == "2026-11-07"
    assert veille.check_image("python:3.15-slim", "Dockerfile", None, hub=lambda u: 1 / 0)["erreur"] == "ZeroDivisionError"


def test_scripts_are_utf8_with_bom_and_never_touch_github():
    for name in ("veille-dependances.ps1", "veille-installer.ps1", "publish-github.ps1"):
        raw = (ROOT / "tool" / name).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), name
    installer = (ROOT / "tool" / "veille-installer.ps1").read_text(encoding="utf-8-sig")
    assert "-RunLevel Limited" in installer and "StartWhenAvailable" in installer
    assert "-Essai" in (ROOT / "tool" / "veille-dependances.ps1").read_text(encoding="utf-8-sig")
    publish = (ROOT / "tool" / "publish-github.ps1").read_text(encoding="utf-8-sig")
    assert "[Parameter(Mandatory" in publish and "$Essai" in publish and "push -f" not in publish


def test_github_showcase_snapshot_never_pushes_the_vault_nor_automations():
    """GitHub, vitrine des grandes versions (décision B du 11/11/2026) : un commit par version, sans historique."""
    publish = (ROOT / "tool" / "publish-github.ps1").read_text(encoding="utf-8-sig")
    code = "\n".join(l for l in publish.splitlines() if not l.lstrip().startswith("#"))
    repos = code[code.index("$repos = [ordered]@{"):code.index("}", code.index("Pages = $false"))]
    assert "'sarcade-app'" in repos and "'sarcade-server'" in repos and "vault" not in repos
    assert "le coffre ne doit jamais être publié sur GitHub" in code
    assert "$exclus = @('.github/workflows', '.github/dependabot.yml')" in code
    assert "commit-tree" in code and "--force" not in code and "push -f" not in code and "+main:refs/heads" not in code
    assert "'^SARCADE v\\d+\\.\\d+\\.\\d+$'" in code  # refus d'un main de GitHub qui n'est pas un instantané
    assert "enabled=false" in code and "allowed_actions=selected" in code and "github_owned_allowed=true" in code
    assert "refs/heads/gh-pages" in code and "build_type=legacy" in code and "confidentialite/" in code
    # Site et instantané poussés avant la construction des fichiers (site coupé le moins longtemps possible).
    assert code.index("Step '2. Dépôts, instantanés et site sur GitHub'") < code.index("Step '3. Fichiers de la version'")


def test_compose_no_longer_uses_the_github_image():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "ghcr.io/sdubos-consulting" not in compose
    assert "image: sarcade-server:${SARCADE_IMAGE_TAG:-local}" in compose and "build: ." in compose


def test_dev_tools_and_system_packages(tmp_path):
    setup(tmp_path)
    (tmp_path / "image.json").write_text(json.dumps({"components": [{"purl": "pkg:pypi/fastapi@0.162.0"}]}), encoding="utf-8")
    debian = {"results": [{"packages": [{"package": {"name": "tar", "version": "1.35", "ecosystem": "Debian:13"},
                                          "vulnerabilities": [{"id": "DEBIAN-CVE-2026-5704"}],
                                          "groups": [{"ids": ["DEBIAN-CVE-2026-5704"], "max_severity": "5.5"}]}]}]}
    (tmp_path / "osv-image.json").write_text(json.dumps(debian), encoding="utf-8")
    data = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    data["scans"].append({"depot": "sarcade-server", "fichier": "image du NAS", "osv": str(tmp_path / "osv-image.json")})
    data["image_sbom"] = str(tmp_path / "image.json")
    text, count = veille.failles_section(data, veille.findings(data["scans"]))
    assert count == 2
    assert "`pytest` (absent de l'image : outil de développement)" in text
    assert "paquets du système de l'image (`tar`)" in text and "docker build --pull" in text

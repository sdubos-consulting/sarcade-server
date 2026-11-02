"""Nightly SBOM check: register of the dismissed flaws (security/vex.json,
OpenVEX) applied to the result of OSV-Scanner (tool/security/osv_report.py)."""
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 11, 2)


def load():
    spec = importlib.util.spec_from_file_location("osv_report", ROOT / "tool" / "security" / "osv_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OSV = {"results": [{
    "source": {"path": "/work/sbom/sarcade-server.cdx.json", "type": "sbom"},
    "packages": [
        {"package": {"name": "Jinja2", "version": "2.10", "ecosystem": "PyPI"},
         "vulnerabilities": [
             {"id": "GHSA-462w-v97r-4m45", "aliases": ["CVE-2019-10906"], "summary": "Sandbox escape",
              "affected": [{"package": {"name": "jinja2", "ecosystem": "PyPI"},
                            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.10.1"}]}]}]},
             {"id": "PYSEC-2019-217", "aliases": ["CVE-2019-10906"], "summary": ""},
         ],
         "groups": [{"ids": ["GHSA-462w-v97r-4m45", "PYSEC-2019-217"], "aliases": ["CVE-2019-10906"],
                     "max_severity": "8.6"}]},
        {"package": {"name": "libexpat1", "version": "2.6.2-2", "ecosystem": "Debian:13"},
         "vulnerabilities": [{"id": "DEBIAN-CVE-2024-8176", "aliases": ["CVE-2024-8176"],
                              "summary": "Stack overflow | recursive"}]},
    ],
}]}


def vex(*statements):
    return {"@context": "https://openvex.dev/ns/v0.2.0", "@id": "x", "author": "SDUBOS Consulting",
            "timestamp": "2026-11-02T00:00:00Z", "version": 1, "statements": list(statements)}


def statement(name, review="2027-01-31", **extra):
    s = {"vulnerability": {"name": name}, "status": "not_affected",
         "justification": "vulnerable_code_not_in_execute_path",
         "impact_statement": "Jinja2 n'est jamais utilisé avec des gabarits venant d'un utilisateur",
         "status_notes": f"réexamen {review}"}
    s.update(extra)
    return s


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_register_of_the_repository_is_valid():
    load().load_vex(ROOT / "security" / "vex.json")


def test_test_sbom_holds_a_known_flaw():
    doc = json.loads((ROOT / "security" / "test-sbom.cdx.json").read_text(encoding="utf-8"))
    assert doc["bomFormat"] == "CycloneDX"
    assert any(c["purl"] == "pkg:pypi/jinja2@2.10" for c in doc["components"])


def test_reason_and_review_date_are_required(tmp_path):
    mod = load()
    bad = statement("CVE-1", impact_statement=" ")
    bad.pop("status_notes")
    path = write(tmp_path, "vex.json", vex(bad, statement("CVE-2", justification="because")))
    with pytest.raises(mod.InvalidInput) as err:
        mod.load_vex(path)
    text = str(err.value)
    assert "raison (impact_statement) obligatoire" in text
    assert "date de réexamen obligatoire" in text
    assert "justification OpenVEX" in text
    assert mod.main(["--check-vex", str(path)]) == 2


def test_findings_merge_aliases_per_package():
    found = load().findings(OSV)
    assert [f["id"] for f in found] == ["GHSA-462w-v97r-4m45", "DEBIAN-CVE-2024-8176"]
    jinja = found[0]
    assert set(jinja["ids"]) == {"GHSA-462w-v97r-4m45", "PYSEC-2019-217", "CVE-2019-10906"}
    assert jinja["fixed"] == ["2.10.1"] and jinja["severity"] == "8.6" and jinja["purl_type"] == "pypi"
    assert found[1]["purl_type"] == "deb"


def test_dismissed_by_alias_and_reopened_after_review():
    mod = load()
    found = mod.findings(OSV)
    open_, dismissed = mod.triage(found, [statement("CVE-2019-10906")], TODAY)
    assert [f["id"] for f in open_] == ["DEBIAN-CVE-2024-8176"]
    assert dismissed[0]["reason"].startswith("Jinja2")
    open_, dismissed = mod.triage(found, [statement("CVE-2019-10906", review="2026-11-01")], TODAY)
    assert not dismissed
    assert open_[0]["note"] == "réexamen dépassé le 2026-11-01"


def test_products_restrict_the_statement():
    mod = load()
    found = mod.findings(OSV)
    other = statement("CVE-2019-10906", products=[{"@id": "pkg:pypi/markupsafe"}])
    assert len(mod.triage(found, [other], TODAY)[1]) == 0
    same = statement("CVE-2019-10906", products=[{"@id": "pkg:pypi/jinja2@2.10"}])
    assert len(mod.triage(found, [same], TODAY)[1]) == 1
    old = statement("CVE-2019-10906", products=[{"@id": "pkg:pypi/jinja2@2.9"}])
    assert len(mod.triage(found, [old], TODAY)[1]) == 0


def test_under_investigation_stays_open():
    mod = load()
    s = statement("CVE-2024-8176", status="under_investigation")
    s.pop("justification")
    open_, _ = mod.triage(mod.findings(OSV), [s], TODAY)
    assert {f["id"] for f in open_} == {"GHSA-462w-v97r-4m45", "DEBIAN-CVE-2024-8176"}
    assert open_[1]["note"] == "registre : under_investigation"


def test_report_and_ids(tmp_path):
    mod = load()
    osv = write(tmp_path, "osv.json", OSV)
    register = write(tmp_path, "vex.json", vex(statement("CVE-2024-8176")))
    report, ids = tmp_path / "report.md", tmp_path / "ids.txt"
    assert mod.main(["--osv", str(osv), "--vex", str(register), "--markdown", str(report), "--ids", str(ids),
                     "--source", "main @ abc", "--today", "2026-11-02"]) == 0
    assert ids.read_text(encoding="utf-8") == "GHSA-462w-v97r-4m45\n"
    text = report.read_text(encoding="utf-8")
    assert "**1 faille(s) non écartée(s)**" in text
    assert "[GHSA-462w-v97r-4m45](https://osv.dev/vulnerability/GHSA-462w-v97r-4m45)" in text
    assert "DEBIAN-CVE-2024-8176" not in text.split("<details>")[0]
    assert "1 faille(s) écartée(s)" in text
    assert text.rstrip().endswith("<!-- sarcade-osv-ids: GHSA-462w-v97r-4m45 -->")


def test_nothing_found(tmp_path):
    mod = load()
    osv = write(tmp_path, "osv.json", {"results": []})
    report, ids = tmp_path / "report.md", tmp_path / "ids.txt"
    assert mod.main(["--osv", str(osv), "--vex", str(tmp_path / "absent.json"), "--markdown", str(report),
                     "--ids", str(ids)]) == 0
    assert ids.read_text(encoding="utf-8") == ""
    assert "Aucune faille non écartée." in report.read_text(encoding="utf-8")


def test_unreadable_osv_result_fails(tmp_path):
    bad = tmp_path / "osv.json"
    bad.write_text("Scanning…", encoding="utf-8")
    assert load().main(["--osv", str(bad), "--vex", str(tmp_path / "none.json")]) == 2

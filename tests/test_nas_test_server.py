"""Serveur de test distinct de l'exploitation (tool/nas, lot 28).

sarcade-switch et sarcade-autoupdate tournent ici pour de vrai (bash, git),
avec des doublures de docker et curl : version de référence (étiquette) pour
l'exploitation, branches réservées au serveur de test, mise à jour
automatique limitée au test.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAS = ROOT / "tool" / "nas"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None,
                                reason="bash et git nécessaires")

DOCKER_STUB = """#!/bin/bash
echo "docker $*" >> "$STUB_LOG"
case "$*" in
  *"select version_num"*) echo "${STUB_DB_VERSION:-0001}" ;;
esac
exit 0
"""

CURL_STUB = """#!/bin/bash
echo "curl $*" >> "$STUB_LOG"
for a in "$@"; do
  case "$a" in
    */health) echo '{"status":"ok"}'; exit 0 ;;
    */releases/tags/web-dev) echo '{"assets":[{"updated_at":"2026-11-04T10:00:00Z"}]}'; exit 0 ;;
    */check-runs) echo '{"check_runs":[{"status":"completed","conclusion":"success"}]}'; exit 0 ;;
  esac
done
exit 22
"""

SWITCH_STUB = """#!/bin/bash
echo "switch SARCADE_ROOT=$SARCADE_ROOT $*" >> "$STUB_LOG"
git -C "$SARCADE_ROOT/sarcade-server" -c safe.directory='*' checkout -q -B main origin/main
"""


def run(cmd, cwd=None, env=None, check=True):
    return subprocess.run(cmd, cwd=cwd, env=env, check=check, capture_output=True, text=True)


def git(*args, cwd):
    return run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *args], cwd=cwd)


@pytest.fixture()
def nas(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", cwd=origin)
    (origin / "migrations" / "versions").mkdir(parents=True)
    (origin / "migrations" / "versions" / "0001_init.py").write_text("# 1\n")
    (origin / "tool" / "nas").mkdir(parents=True)
    for f in NAS.glob("sarcade-*.sh"):
        shutil.copy(f, origin / "tool" / "nas" / f.name)
    git("add", "-A", cwd=origin)
    git("commit", "-qm", "v1", cwd=origin)
    git("tag", "v0.1.0-exercice1", cwd=origin)
    (origin / "migrations" / "versions" / "0002_next.py").write_text("# 2\n")
    git("add", "-A", cwd=origin)
    git("commit", "-qm", "suite", cwd=origin)
    git("branch", "feat/essai", cwd=origin)

    prod = tmp_path / "volume1" / "docker" / "sarcade"
    prod.mkdir(parents=True)
    git("clone", "-q", str(origin), str(prod / "sarcade-server"), cwd=tmp_path)

    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, text in {"docker": DOCKER_STUB, "curl": CURL_STUB, "fake-switch": SWITCH_STUB}.items():
        (stubs / name).write_text(text)
        (stubs / name).chmod(0o755)
    installed = tmp_path / "installed"
    installed.mkdir()
    log = tmp_path / "stub.log"
    log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "SARCADE_ALLOW_NONROOT": "1",
        "SARCADE_PROD_ROOT": str(prod),
        "SARCADE_TEST_ROOT": str(tmp_path / "volume1" / "docker" / "sarcade-test"),
        "SARCADE_BIN": str(installed),
        "SARCADE_WAIT_S": "4",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    env.pop("SARCADE_ROOT", None)
    return {"tmp": tmp_path, "origin": origin, "prod": prod, "env": env, "log": log,
            "installed": installed, "stubs": stubs}


def switch(nas, *args, root=None, check=False):
    env = dict(nas["env"], SARCADE_ROOT=str(root or nas["prod"]))
    return run(["bash", str(NAS / "sarcade-switch.sh"), *args], env=env, check=check)


def autoupdate(nas, *args, env_extra=None):
    env = dict(nas["env"], SARCADE_SWITCH=str(nas["stubs"] / "fake-switch"), **(env_extra or {}))
    return run(["bash", str(NAS / "sarcade-autoupdate.sh"), *args], env=env, check=False)


def head_of(repo):
    return run(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()


def make_test_instance(nas):
    test = Path(nas["env"]["SARCADE_TEST_ROOT"])
    test.mkdir(parents=True)
    git("clone", "-q", str(nas["origin"]), str(test / "sarcade-server"), cwd=nas["tmp"])
    (test / "instance.env").write_text("SARCADE_INSTANCE=test\nSARCADE_HEALTH=http://localhost:8010/health\n")
    return test


def test_production_goes_to_reference_tag(nas):
    r = switch(nas, "v0.1.0-exercice1")
    assert r.returncode == 0, r.stderr
    repo = nas["prod"] / "sarcade-server"
    tag_sha = run(["git", "-C", str(nas["origin"]), "rev-parse", "v0.1.0-exercice1^{commit}"]).stdout.strip()
    assert head_of(repo) == tag_sha
    assert "Version  : v0.1.0-exercice1" in r.stdout
    assert "Instance : exploitation" in r.stdout
    assert "compose build" in nas["log"].read_text()


def test_production_refuses_a_branch(nas):
    before = head_of(nas["prod"] / "sarcade-server")
    r = switch(nas, "feat/essai")
    assert r.returncode != 0
    assert "serveur d'exploitation" in r.stderr and "sarcade-test" in r.stderr
    assert head_of(nas["prod"] / "sarcade-server") == before
    assert "compose build" not in nas["log"].read_text()


def test_production_branch_only_with_explicit_option(nas):
    r = switch(nas, "feat/essai", "--branche")
    assert r.returncode == 0, r.stderr
    assert "Version  : feat/essai" in r.stdout


def test_unknown_target_changes_nothing(nas):
    r = switch(nas, "v9.9.9")
    assert r.returncode != 0 and "inconnue" in r.stderr
    assert "compose build" not in nas["log"].read_text()


def test_test_instance_takes_branches_and_its_own_health(nas):
    test = make_test_instance(nas)
    r = switch(nas, "feat/essai", root=test)
    assert r.returncode == 0, r.stderr
    assert "Instance : test" in r.stdout
    assert "localhost:8010/health" in nas["log"].read_text()


def test_switch_list_shows_tags(nas):
    r = switch(nas, "--list", check=True)
    assert "v0.1.0-exercice1" in r.stdout and "feat/essai" in r.stdout


def test_autoupdate_without_test_server_leaves_production_alone(nas):
    before = head_of(nas["prod"] / "sarcade-server")
    r = autoupdate(nas)
    assert r.returncode == 0, r.stderr
    assert "serveur de test non installé" in (nas["prod"] / "autoupdate.log").read_text()
    assert "switch" not in nas["log"].read_text()
    assert head_of(nas["prod"] / "sarcade-server") == before
    assert not Path(nas["env"]["SARCADE_TEST_ROOT"]).exists()
    # Une seule ligne par jour.
    autoupdate(nas)
    assert (nas["prod"] / "autoupdate.log").read_text().count("non installé") == 1


def test_autoupdate_never_targets_production(nas):
    r = autoupdate(nas, env_extra={"SARCADE_ROOT": str(nas["prod"])})
    assert r.returncode == 0
    assert "serveur d'exploitation" in (nas["prod"] / "autoupdate.log").read_text()
    assert "switch" not in nas["log"].read_text()


def test_autoupdate_follows_main_on_test_server(nas):
    test = make_test_instance(nas)
    repo = test / "sarcade-server"
    run(["git", "-C", str(repo), "reset", "-q", "--hard", "HEAD~1"])
    r = autoupdate(nas, "--status")
    assert "Instance       : test" in r.stdout
    r = autoupdate(nas)
    assert r.returncode == 0, r.stderr
    log = nas["log"].read_text()
    assert f"switch SARCADE_ROOT={test} main" in log
    assert "--branche" not in log
    assert head_of(repo) == head_of(nas["origin"])
    # Tous les outils tool/nas/sarcade-*.sh sont réinstallés, sans « .sh ».
    names = sorted(p.name for p in nas["installed"].iterdir())
    assert "sarcade-switch" in names and "sarcade-autoupdate" in names
    assert all(not n.endswith(".sh") for n in names)
    assert "terminé" in (test / "autoupdate.log").read_text()


def test_autoupdate_pauses_when_test_is_on_a_pr_branch(nas):
    test = make_test_instance(nas)
    run(["git", "-C", str(test / "sarcade-server"), "checkout", "-q", "-B", "feat/essai", "origin/feat/essai"])
    r = autoupdate(nas)
    assert r.returncode == 0
    assert "switch" not in nas["log"].read_text()
    assert "basculé à la main sur feat/essai" in (test / "autoupdate.log").read_text()


def test_test_compose_template_is_a_separate_project():
    text = (NAS / "test" / "compose.yaml").read_text()
    assert "name: sarcade-test" in text
    assert "SARCADE_TEST_PORT:-8010" in text
    assert "context: ./sarcade-server" in text
    assert "POSTGRES_PASSWORD:?" in text  # jamais de mot de passe par défaut

"""Sauvegarde chiffrée et restauration sur le serveur de test (tool/nas, lot 28).

sarcade-backup et sarcade-restore tournent ici pour de vrai (bash, openssl,
tar, gzip) ; docker est une doublure : la « base » d'une instance est le
fichier db.sql de son dossier, ses « fichiers envoyés » le dossier files/.
L'essai de bout en bout avec de vrais conteneurs est dans la CI
(tests/nas/e2e_backup_restore.sh).
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAS = ROOT / "tool" / "nas"

pytestmark = pytest.mark.skipif(
    any(shutil.which(t) is None for t in ("bash", "openssl", "sha256sum", "tar", "gzip")),
    reason="bash, openssl, sha256sum, tar et gzip nécessaires")

DOCKER_STUB = r"""#!/bin/bash
echo "docker [$(basename "$PWD")] $*" >> "$STUB_LOG"
[ "$1" = compose ] || exit 0
shift
case "$1" in
  exec|run)
    args=("$@")
    # dernier « sh -c CMD » : exécuté ici, avec les fichiers de l'instance
    for i in "${!args[@]}"; do
      if [ "${args[$i]}" = "-c" ] && [ "${args[$((i-1))]}" = sh ]; then
        SARCADE_FILE_ROOT=$PWD/files exec sh -c "${args[$((i+1))]}"
      fi
    done
    case "$*" in
      *pg_dump*) cat "$PWD/db.sql"; exit 0 ;;
      *pg_isready*) exit 0 ;;
      *"select version_num"*) echo 0031; exit 0 ;;
      *"select count(*)"*) echo 3; exit 0 ;;
      *"DROP DATABASE"*) rm -f "$PWD/db.sql"; exit 0 ;;
      *"CREATE DATABASE"*) : > "$PWD/db.sql"; exit 0 ;;
      *psql*) cat > "$PWD/db.sql"; exit 0 ;;
    esac ;;
esac
exit 0
"""

CURL_STUB = """#!/bin/bash
echo "curl $*" >> "$STUB_LOG"
echo '{"status":"ok"}'
"""


def run(cmd, env, check=False, input=None):
    return subprocess.run(cmd, env=env, check=check, capture_output=True, text=True, input=input)


@pytest.fixture()
def nas(tmp_path):
    prod = tmp_path / "docker" / "sarcade"
    test = tmp_path / "docker" / "sarcade-test"
    dest = tmp_path / "sarcade-sauvegardes"
    for d in (prod / "files" / "personal" / "alice", test / "files", dest):
        d.mkdir(parents=True)
    # « Base » de l'exploitation : assez de contenu pour dépasser 1 Ko chiffré.
    (prod / "db.sql").write_text("-- SARCADE\nCREATE TABLE events (id int);\n"
                                 + "".join(f"INSERT INTO events VALUES ({i}, '{os.urandom(8).hex()}');\n"
                                           for i in range(200)))
    (prod / "files" / "personal" / "alice" / "note.txt").write_text("bonjour\n")
    (prod / "files" / "licence.json").write_text("{}\n")
    (test / "files" / "ancien.txt").write_text("à effacer\n")
    (test / "instance.env").write_text("SARCADE_INSTANCE=test\nSARCADE_HEALTH=http://localhost:8010/health\n")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, text in {"docker": DOCKER_STUB, "curl": CURL_STUB}.items():
        (stubs / name).write_text(text)
        (stubs / name).chmod(0o755)
    passfile = tmp_path / "pass"
    passfile.write_text("phrase de passe de test\n")
    log = tmp_path / "stub.log"
    log.write_text("")
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}", "STUB_LOG": str(log),
           "SARCADE_ALLOW_NONROOT": "1", "SARCADE_ROOT": str(prod), "SARCADE_PROD_ROOT": str(prod),
           "SARCADE_RESTORE_ROOT": str(test), "SARCADE_BACKUP_DEST": str(dest),
           "SARCADE_BACKUP_KEY_BITS": "2048", "SARCADE_WAIT_S": "6",
           "SARCADE_BACKUP_PASSOUT": f"file:{passfile}", "SARCADE_BACKUP_PASSIN": f"file:{passfile}"}
    return {"prod": prod, "test": test, "dest": dest, "env": env, "log": log, "tmp": tmp_path,
            "state": prod / "backups" / "last.json"}


def backup(nas, *args, **extra):
    return run(["bash", str(NAS / "sarcade-backup.sh"), *args], dict(nas["env"], **extra))


def restore(nas, *args, **extra):
    return run(["bash", str(NAS / "sarcade-restore.sh"), *args], dict(nas["env"], **extra))


def init(nas):
    r = backup(nas, "--init-key")
    assert r.returncode == 0, r.stderr
    return r


def test_init_key_once_private_key_protected(nas):
    r = init(nas)
    key = nas["prod"] / "secrets" / "backup-key.pem"
    assert "Empreinte" in r.stdout
    assert "ENCRYPTED PRIVATE KEY" in key.read_text()
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    again = backup(nas, "--init-key")
    assert again.returncode != 0 and "déjà présente" in again.stderr


def test_backup_is_encrypted_and_state_is_written(nas):
    init(nas)
    r = backup(nas)
    assert r.returncode == 0, r.stderr
    [b] = [p for p in nas["dest"].iterdir() if p.name.startswith("sarcade-")]
    assert sorted(p.name for p in b.iterdir()) == ["SHA256SUMS", "db.sql.gz.enc", "files.tar.gz.enc",
                                                   "key.enc", "manifest.json"]
    for f in ("db.sql.gz.enc", "files.tar.gz.enc"):
        data = (b / f).read_bytes()
        assert data.startswith(b"Salted__")  # openssl enc, pas de gzip en clair
        assert b"INSERT INTO" not in data and b"bonjour" not in data
    state = json.loads(nas["state"].read_text())
    assert state["format"] == 1 and state["status"] == "ok" and state["error"] is None
    assert state["last_success_name"] == b.name
    assert state["last_success_at"].endswith("Z")
    assert state["alembic"] == "0031"
    assert state["files_count"] == 2
    assert state["retention_days"] == 14 and state["kept"] == 1
    assert state["db_bytes"] > 1024
    assert state["destination"] == str(nas["dest"])
    assert len(state["key_fingerprint"]) == 16
    manifest = json.loads((b / "manifest.json").read_text())
    assert manifest["key_fingerprint"] == state["key_fingerprint"]
    assert backup(nas, "--check").returncode == 0
    assert "Dernière OK" in backup(nas, "--status").stdout


def test_restore_on_test_server(nas):
    init(nas)
    assert backup(nas).returncode == 0
    r = restore(nas)
    assert r.returncode == 0, r.stderr + r.stdout
    assert (nas["test"] / "db.sql").read_text() == (nas["prod"] / "db.sql").read_text()
    assert (nas["test"] / "files" / "personal" / "alice" / "note.txt").read_text() == "bonjour\n"
    assert not (nas["test"] / "files" / "ancien.txt").exists()
    log = nas["log"].read_text()
    assert "[sarcade-test] compose stop server" in log
    assert "[sarcade-test] compose up -d server" in log
    assert "[sarcade] compose stop" not in log  # l'exploitation n'est pas touchée
    assert "localhost:8010/health" in log
    state = json.loads((nas["prod"] / "backups" / "last-restore.json").read_text())
    assert state["status"] == "ok" and state["target"] == str(nas["test"])
    assert state["counts"]["events"] == 3 and state["alembic"] == "0031"
    assert "Contenu" in r.stdout


def test_restore_refuses_production(nas):
    init(nas)
    assert backup(nas).returncode == 0
    r = restore(nas, SARCADE_RESTORE_ROOT=str(nas["prod"]))
    assert r.returncode != 0 and "exploitation" in r.stderr
    assert "DROP DATABASE" not in nas["log"].read_text()
    r = restore(nas, "--exploitation", SARCADE_RESTORE_ROOT=str(nas["prod"]), SARCADE_RESTORE_CONFIRM="oui")
    assert r.returncode != 0 and "non confirmé" in r.stderr
    assert "DROP DATABASE" not in nas["log"].read_text()


def test_restore_wrong_passphrase_changes_nothing(nas, tmp_path):
    init(nas)
    assert backup(nas).returncode == 0
    bad = tmp_path / "bad"
    bad.write_text("pas le bon\n")
    r = restore(nas, SARCADE_BACKUP_PASSIN=f"file:{bad}")
    assert r.returncode != 0 and "mot de passe" in r.stderr
    assert "DROP DATABASE" not in nas["log"].read_text()
    assert (nas["test"] / "files" / "ancien.txt").exists()
    state = json.loads((nas["prod"] / "backups" / "last-restore.json").read_text())
    assert state["status"] == "error"


def test_restore_refuses_a_damaged_backup(nas):
    init(nas)
    assert backup(nas).returncode == 0
    [b] = [p for p in nas["dest"].iterdir() if p.name.startswith("sarcade-")]
    data = bytearray((b / "db.sql.gz.enc").read_bytes())
    data[-1] ^= 1
    (b / "db.sql.gz.enc").write_bytes(bytes(data))
    r = restore(nas, b.name)
    assert r.returncode != 0 and "abîmée" in r.stderr
    assert "DROP DATABASE" not in nas["log"].read_text()


def test_retention_keeps_14_days_and_the_latest(nas):
    init(nas)
    old = time.time() - 20 * 86400
    for name, age in (("sarcade-20261001-024700", old), ("sarcade-20261030-024700", time.time() - 5 * 86400)):
        d = nas["dest"] / name
        d.mkdir()
        os.utime(d, (age, age))
    assert backup(nas).returncode == 0
    names = sorted(p.name for p in nas["dest"].iterdir())
    assert "sarcade-20261001-024700" not in names
    assert "sarcade-20261030-024700" in names
    assert json.loads(nas["state"].read_text())["kept"] == 2


def test_failure_is_recorded_and_previous_success_kept(nas):
    init(nas)
    assert backup(nas).returncode == 0
    first = json.loads(nas["state"].read_text())
    shutil.rmtree(nas["dest"])
    r = backup(nas)
    assert r.returncode != 0
    state = json.loads(nas["state"].read_text())
    assert state["status"] == "error" and "dossier de destination absent" in state["error"]
    assert state["last_success_at"] == first["last_success_at"]
    assert state["last_success_name"] == first["last_success_name"]


def test_check_alerts_when_a_backup_is_missing(nas):
    r = backup(nas, "--check")
    assert r.returncode == 1 and "aucune sauvegarde" in r.stderr
    nas["state"].parent.mkdir(parents=True)
    nas["state"].write_text(json.dumps({"format": 1, "status": "ok", "last_success_at": "2026-11-01T02:47:00Z",
                                        "last_success_name": "sarcade-20261101-034700"}))
    r = backup(nas, "--check")
    assert r.returncode == 1 and "plus de 26 h" in r.stderr


def test_no_key_no_backup(nas):
    r = backup(nas)
    assert r.returncode != 0 and "--init-key" in r.stderr
    assert json.loads(nas["state"].read_text())["status"] == "error"

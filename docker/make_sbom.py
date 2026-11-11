"""Inventory of the components of the image (SBOM, CycloneDX JSON).

Docker build step (Dockerfile), run last, once the Python packages, the
system packages and the web client are in place: syft (from its official
image, mounted only for this step) scans the root of the image being built,
then this script keeps only the packages, adds the embedded web client and
writes the result where the server serves it (``/sarcade-sbom.cdx.json``,
``src/sarcade/api/sbom.py``).

Never fatal: without syft (``SARCADE_SBOM=none``), or when the scan fails,
the image builds normally, the reason is printed in the build log and the
server answers 404 at ``/sarcade-sbom.cdx.json``.

Nothing sensitive goes into the file: syft scans the image only (no
environment, no secret: the ``.env`` and the secrets of the NAS are mounted at
run time, never at build time) and the file components are dropped.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL_CANDIDATES = ("syft", "usr/local/bin/syft", "bin/syft")
# Built on the i9 (SARCADE_WEB=local); the validated major releases of
# sarcade-app are published on GitHub (card « GitHub de nouveau actif
# (10 novembre 2026) »: no more web-dev pre-release).
WEB_RELEASE_URL = "https://github.com/sdubos-consulting/sarcade-app/releases"
# Mounts, temporary files and the output itself are not part of the image.
EXCLUDES = ("./proc/**", "./sys/**", "./dev/**", "./tmp/**", "./run/**", "./app/sbom/**")
TIMEOUT_S = 900


def find_tool(tool_dir: str) -> Path | None:
    if not tool_dir:
        return None
    for candidate in TOOL_CANDIDATES:
        path = Path(tool_dir) / candidate
        if path.is_file():
            return path
    return None


def syft_command(tool: Path, root: str, output: Path, version: str) -> list[str]:
    command = [str(tool), "scan", f"dir:{root}", "-q",
               "-o", f"cyclonedx-json={output}",
               # Installed packages only (as for an image), not the declared
               # files such as uv.lock, which also lists the dev tools.
               "--override-default-catalogers", "image",
               "--source-name", "sarcade-server", "--source-version", version or "dev"]
    for pattern in EXCLUDES:
        command += ["--exclude", pattern]
    return command


def syft_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        # No network: no update check, no remote licence lookup.
        "SYFT_CHECK_FOR_APP_UPDATE": "false",
        "SYFT_PYTHON_SEARCH_REMOTE_LICENSES": "false",
        "SYFT_GOLANG_SEARCH_REMOTE_LICENSES": "false",
        "SYFT_JAVASCRIPT_SEARCH_REMOTE_LICENSES": "false",
        "SYFT_JAVA_USE_NETWORK": "false",
        # Packages only, no inventory of every file.
        "SYFT_FILE_METADATA_SELECTION": "none",
        "SYFT_FILE_EXECUTABLE_SELECTION": "none",
    })
    return env


def web_client_component(web_root: str) -> dict | None:
    """The web client as one component: its Dart packages are compiled into
    main.dart.js and listed by the SBOM of the application itself."""
    root = Path(web_root) if web_root else None
    if root is None or not (root / "sarcade-web.sha256").is_file():
        return None
    digest = (root / "sarcade-web.sha256").read_text(encoding="utf-8").strip().removeprefix("sha256:")
    version = "local"
    try:
        info = json.loads((root / "version.json").read_text(encoding="utf-8"))
        if info.get("version"):
            version = str(info["version"]) + (f"+{info['build_number']}" if info.get("build_number") else "")
    except (OSError, ValueError):
        pass
    return {
        "bom-ref": "sarcade-web",
        "type": "application",
        "name": "sarcade-web",
        "version": version,
        "description": "Client web SARCADE embarqué (sarcade-app, construit sur le poste de développement)",
        "hashes": [{"alg": "SHA-256", "content": digest}],
        "externalReferences": [{"type": "distribution", "url": WEB_RELEASE_URL}],
    }


def finalize(raw: dict, version: str, web_root: str) -> dict:
    """Keeps the packages, names the image and adds the web client."""
    if raw.get("bomFormat") != "CycloneDX":
        raise ValueError("not a CycloneDX document")
    components = [c for c in raw.get("components") or [] if c.get("type") != "file"]
    web = web_client_component(web_root)
    if web:
        components.append(web)
    raw["components"] = components
    kept = {c.get("bom-ref") for c in components}
    if raw.get("dependencies"):
        raw["dependencies"] = [d for d in raw["dependencies"] if d.get("ref") in kept]
    metadata = raw.setdefault("metadata", {})
    metadata["component"] = {
        "bom-ref": "sarcade-server-image",
        "type": "container",
        "name": "sarcade-server",
        "version": version or "dev",
        "description": "Image du serveur SARCADE construite par le Dockerfile",
    }
    return raw


def main(tool_dir: str, output: str, root: str = "/", web_root: str = "/app/web",
         version: str = "") -> int:
    dest = Path(output)
    tool = find_tool(tool_dir)
    if tool is None:
        print("sbom: no syft, skipped (/sarcade-sbom.cdx.json will answer 404)")
        return 0
    try:
        with tempfile.TemporaryDirectory() as work:
            raw_path = Path(work) / "raw.cdx.json"
            run = subprocess.run(syft_command(tool, root, raw_path, version), env=syft_env(),
                                 capture_output=True, text=True, timeout=TIMEOUT_S)
            if run.returncode != 0 or not raw_path.is_file():
                tail = (run.stderr or run.stdout or "").strip().splitlines()[-5:]
                raise RuntimeError(f"syft exit {run.returncode}: {' | '.join(tail)}")
            document = finalize(json.loads(raw_path.read_text(encoding="utf-8")), version, web_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - the image must build anyway
        dest.unlink(missing_ok=True)
        print(f"sbom: not generated ({exc})")
        return 0
    print(f"sbom: {len(document['components'])} components in {dest}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main(args[0] if args else "", args[1] if len(args) > 1 else "/app/sbom/sarcade-image.json",
                  version=os.getenv("SARCADE_VERSION", "")))

#!/usr/bin/env python3
"""Grail vault: verified, append-only copies of the grail, kept outside the grail.

The grail (kody-w/rapp-installer) is the kernel. This repo never changes it; it only reads it and keeps copies,
so that a bad push to the grail (or the grail being unreachable) never takes the last good kernel with it.

    python3 vault.py snapshot [--out docs]   copy the grail's current main, gate it, record it
    python3 vault.py verify   [--out docs]   re-check every stored copy against its manifest

A snapshot is three files under docs/snapshots/<version>-<commit7>/:
    grail.bundle    the full git history up to that commit (clone it like a repo)
    grail.tar.gz    the plain file tree at that commit
    manifest.json   commit, version, time, sha256 of both files, and the gate results

docs/ledger.json records every commit ever seen, passing or not. docs/latest.json points at the newest
snapshot that passed every gate: the last known good. A failing commit is recorded but never becomes latest.
Standard library only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

GRAIL = os.getenv("GRAIL_REPO_URL", "https://github.com/kody-w/rapp-installer.git")
BRANCH = "main"
# Only mirrors that are live. Add https://kodyw.com/grail when its copy step is switched on.
MIRRORS = ["https://kody-w.github.io/grail-vault"]


def write_beacon(out: Path, manifest: dict) -> None:
    """One machine-readable answer to "where is the last known good kernel, and how do I check it?"
    Trust comes from the hashes, not from whichever host served the bytes, so any agent can fetch
    from any copy and verify it without a person in the loop."""
    name = manifest["name"]
    beacon = {
        "schema": "rapp-grail-beacon/1",
        "what": "The last known good RAPP Brainstem kernel (the grail), with every place to get it and how to verify it.",
        "kernel": {"version": manifest["version"], "commit": manifest["commit"], "snapshot": name,
                   "taken_at": manifest["taken_at"],
                   "sha256": {f: facts["sha256"] for f, facts in manifest["files"].items()}},
        "get": {
            "mirrors": [f"{m}/snapshots/{name}/{f}" for m in MIRRORS for f in ("grail.bundle", "grail.tar.gz")],
            "git": [manifest["source"]],
            "archives": {"software_heritage": f"swh:1:rev:{manifest['commit']}",
                         "software_heritage_browse": f"https://archive.softwareheritage.org/swh:1:rev:{manifest['commit']}"},
        },
        "verify": "sha256 of the downloaded file must equal kernel.sha256 for that file; the bundle's HEAD must equal kernel.commit",
        "install": {"command": f"curl -fsSL {MIRRORS[0]}/install.sh | bash",
                    "pinned": f"curl -fsSL {MIRRORS[0]}/install.sh | VAULT_SNAPSHOT={name} bash",
                    "notes": "Non-interactive. Runs the snapshot's own installer unchanged after checking its SHA-256."},
        "gates": [g["detail"] for g in manifest["gates"]],
    }
    save(out / "beacon.json", beacon)
    save(out / ".well-known" / "rapp-grail.json", beacon)
    (out / ".nojekyll").touch()


def run(*cmd, cwd=None, env=None, timeout=900, check=True) -> subprocess.CompletedProcess:
    result = subprocess.run(list(map(str, cmd)), cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{' '.join(map(str, cmd))} failed:\n{result.stdout[-2000:]}{result.stderr[-2000:]}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


# ---------- gates: what "known good" means ----------

def gate_installer_parses(tree: Path) -> str:
    run("bash", "-n", tree / "install.sh")
    return "install.sh parses"


def gate_kernel_tests(tree: Path, python: str) -> str:
    kernel = tree / "rapp_brainstem"
    out = run(python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests", cwd=kernel,
              env={**os.environ, "GITHUB_TOKEN": ""}).stdout.strip().splitlines()
    return out[-1] if out else "tests passed"


def gate_kernel_boots(tree: Path, python: str, port: int = 7197) -> str:
    kernel = tree / "rapp_brainstem"
    env = {**os.environ, "PORT": str(port), "GITHUB_TOKEN": ""}
    log = open(tree / "kernel.log", "w")
    process = subprocess.Popen([python, "brainstem.py"], cwd=kernel, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        health = None
        for _ in range(40):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as reply:
                    health = json.loads(reply.read())
                break
            except (urllib.error.URLError, OSError, ValueError):
                if process.poll() is not None:
                    break
        if not isinstance(health, dict):
            raise RuntimeError("kernel did not answer /health:\n" + (tree / "kernel.log").read_text()[-2000:])
        expected = (kernel / "VERSION").read_text().strip()
        if health.get("version") != expected:
            raise RuntimeError(f"/health says version {health.get('version')}, VERSION file says {expected}")
        request = urllib.request.Request(f"http://127.0.0.1:{port}/chat", data=b"{}", method="POST",
                                         headers={"content-type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=10)
            raise RuntimeError("/chat accepted an empty message")
        except urllib.error.HTTPError as error:
            if error.code != 400 or "error" not in json.loads(error.read() or b"{}"):
                raise RuntimeError(f"/chat on an empty message answered HTTP {error.code}, expected a JSON 400")
        return f"kernel {expected} boots; /health and /chat answer"
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


def kernel_python(tree: Path, work: Path) -> str:
    """A throwaway environment with the kernel's own requirements."""
    env_dir = work / "venv"
    run(sys.executable, "-m", "venv", env_dir)
    python = str(env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    requirements = tree / "rapp_brainstem" / "requirements-dev.txt"
    base = [python, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", requirements]
    if run(*base, cwd=requirements.parent, check=False).returncode:
        run(*base, "--no-cache-dir", cwd=requirements.parent)
    return python


# ---------- snapshot ----------

def snapshot(out: Path) -> int:
    ledger = load(out / "ledger.json", {"snapshots": []})
    seen = {entry["commit"] for entry in ledger["snapshots"]}
    work = Path(tempfile.mkdtemp(prefix="grail-vault-"))
    try:
        mirror = work / "grail.git"
        run("git", "clone", "--quiet", "--mirror", GRAIL, mirror)
        commit = run("git", "-C", mirror, "rev-parse", BRANCH).stdout.strip()
        if commit in seen:
            print(f"grail {commit[:7]} already in the vault; nothing to do")
            return 0
        tree = work / "tree"
        run("git", "clone", "--quiet", mirror, tree)
        run("git", "-C", tree, "checkout", "--quiet", commit)
        version = (tree / "rapp_brainstem" / "VERSION").read_text().strip()
        name = f"{version}-{commit[:7]}"
        folder = out / "snapshots" / name
        if folder.exists():
            raise RuntimeError(f"{folder} exists but is not in the ledger; refusing to overwrite a stored copy")
        folder.mkdir(parents=True)
        run("git", "-C", mirror, "bundle", "create", (folder / "grail.bundle").resolve(), BRANCH)
        run("git", "-C", mirror, "archive", "--format=tar.gz", "-o", (folder / "grail.tar.gz").resolve(), commit)
        run("git", "bundle", "verify", (folder / "grail.bundle").resolve(), cwd=mirror)

        gates, passed = [], True
        try:
            gates.append({"gate": "installer", "ok": True, "detail": gate_installer_parses(tree)})
            python = kernel_python(tree, work)
            gates.append({"gate": "kernel tests", "ok": True, "detail": gate_kernel_tests(tree, python)})
            gates.append({"gate": "kernel boots", "ok": True, "detail": gate_kernel_boots(tree, python)})
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
            passed = False
            gates.append({"gate": "failed", "ok": False, "detail": str(error)[-1500:]})

        manifest = {
            "schema": "grail-vault/1", "name": name, "commit": commit, "version": version,
            "source": GRAIL, "taken_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "known_good": passed, "gates": gates,
            "files": {f: {"sha256": sha256(folder / f), "bytes": (folder / f).stat().st_size}
                      for f in ("grail.bundle", "grail.tar.gz")},
        }
        save(folder / "manifest.json", manifest)
        ledger["snapshots"].append({key: manifest[key] for key in ("name", "commit", "version", "taken_at", "known_good")})
        save(out / "ledger.json", ledger)
        if passed:
            write_beacon(out, manifest)
            save(out / "latest.json", {"name": name, "commit": commit, "version": version,
                                       "manifest": f"snapshots/{name}/manifest.json"})
        print(json.dumps({"name": name, "known_good": passed, "gates": gates}, indent=2))
        if not passed and os.getenv("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
                handle.write(f"failed={name}\n")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def verify(out: Path) -> int:
    problems = 0
    for manifest_path in sorted((out / "snapshots").glob("*/manifest.json")):
        manifest = load(manifest_path, {})
        for name, facts in manifest.get("files", {}).items():
            actual = sha256(manifest_path.parent / name)
            if actual != facts["sha256"]:
                problems += 1
                print(f"MISMATCH {manifest_path.parent.name}/{name}")
    latest = load(out / "latest.json", None)
    if latest and not (out / latest["manifest"]).is_file():
        problems += 1
        print("latest.json points at a missing snapshot")
    beacon = load(out / "beacon.json", None)
    if latest and (not beacon or beacon["kernel"]["snapshot"] != latest["name"]):
        problems += 1
        print("beacon.json does not name the last known good")
    print(f"{'ok' if not problems else 'PROBLEMS'}: {problems} problem(s)")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["snapshot", "verify", "beacon"])
    parser.add_argument("--out", default="docs")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if args.command == "beacon":  # rebuild the beacon from the last known good
        write_beacon(out, load(out / load(out / "latest.json", {})["manifest"], {}))
        return verify(out)
    return snapshot(out) if args.command == "snapshot" else verify(out)


if __name__ == "__main__":
    sys.exit(main())

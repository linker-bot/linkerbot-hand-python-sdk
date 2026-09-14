#!/usr/bin/env python3
"""Release gate and publisher for linkerbot-hand.

Releasing this package means six things staying consistent: the version in
``pyproject.toml``, a dated section in ``CHANGELOG.md``, the pinned version in
both READMEs, a signed git tag, the artifacts in the package registry, and a
Gitea Release carrying those artifacts. The 2.0.0 release got the first two
right and silently missed the next three, which produced a version nobody could
install; after those were repaired it still had no Release, which made it the
only tag in this repository without one. This script exists so that ordering and
consistency are checked mechanically instead of remembered.

Usage::

    python scripts/release.py check      # read-only; changes nothing
    python scripts/release.py tag        # check, then sign and push the tag
    python scripts/release.py publish    # build, verify, upload, create Release

``tag`` is the human action; ``publish`` is what CI runs on a ``v*`` tag push.
Until a runner is online both are run locally. ``publish`` is idempotent: it
skips the upload when the registry already has this version and skips the
Release when one already exists, so running it locally and from CI cannot
double-publish.

``publish`` requires ``v<version>`` to exist and to point at HEAD. That is the
inverse of the gate ``tag`` uses, and it is deliberate: the tag's whole purpose
is to record which source produced the artifact in the registry, so building from
anything other than the tagged commit destroys the only thing it is for. In CI
this holds automatically (the workflow checks out the tag); locally it forces a
``git checkout v<version>`` first.

Credentials come from the environment, never from a file in the repo:

    GITEA_USERNAME, GITEA_TOKEN     (or TWINE_USERNAME, TWINE_PASSWORD)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "linkerbot-hand"
WHEEL_STEM = "linkerbot_hand"
HOST = "https://gitea.linkerhub.work"
OWNER = "zhuzhixiang"
REGISTRY = f"{HOST}/api/packages/{OWNER}/pypi"
SIMPLE_INDEX = f"{REGISTRY}/simple/{PACKAGE}/"
API = f"{HOST}/api/v1/repos/{OWNER}/{PACKAGE}"
READMES = ("README.md", "README.en.md")


class Failure(Exception):
    """A gate rejected the release. The message says which one and why."""


def run(*args: str, capture: bool = True, env: dict[str, str] | None = None) -> str:
    """Run one command from the repository root, raising Failure on non-zero."""

    completed = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=capture,
        text=True,
        env=None if env is None else {**os.environ, **env},
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise Failure(f"command failed: {' '.join(args)}\n{detail}")
    return (completed.stdout or "").strip()


def credentials() -> tuple[str, str]:
    user = os.environ.get("GITEA_USERNAME") or os.environ.get("TWINE_USERNAME")
    token = os.environ.get("GITEA_TOKEN") or os.environ.get("TWINE_PASSWORD")
    if not user or not token:
        raise Failure(
            "set GITEA_USERNAME and GITEA_TOKEN (or TWINE_USERNAME and TWINE_PASSWORD)"
        )
    return user, token


def api_request(
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> tuple[int, object]:
    """Call the Gitea API, returning the status and decoded body.

    A 404 is returned rather than raised: callers use it to decide whether a
    Release already exists, which is how ``publish`` stays idempotent.
    """

    _, token = credentials()
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(f"{API}{path}", data=body, method=method)
    request.add_header("Authorization", f"token {token}")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8", "replace")
            return response.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", "replace")
        if error.code == 404:
            return 404, None
        raise Failure(f"{method} {path} returned {error.code}: {text[:300]}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise Failure(f"{method} {path} failed: {error}") from error


def declared_version() -> str:
    """Return the version pyproject.toml declares."""

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise Failure("pyproject.toml declares no project.version")
    return version


def registry_versions() -> set[str] | None:
    """Return versions already in the registry, or None when unreachable.

    Unreachable is distinguished from empty on purpose: a network failure must
    not read as "nothing is published" and let a re-upload through.
    """

    user = os.environ.get("GITEA_USERNAME") or os.environ.get("TWINE_USERNAME")
    token = os.environ.get("GITEA_TOKEN") or os.environ.get("TWINE_PASSWORD")
    request = urllib.request.Request(SIMPLE_INDEX)
    if token:
        request.add_header("Authorization", f"token {token}")
    elif user:
        raise Failure("a username was given without a token")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"  ! registry unreachable ({error}); version reuse not checked")
        return None
    pattern = rf"{WHEEL_STEM}-([0-9][^-]*)-py3"
    return set(re.findall(pattern, body))


def check_working_tree(*, require_main: bool) -> None:
    """Reject a dirty tree, and on the tagging path an unsynced branch.

    ``require_main`` is False when publishing: that runs from a detached tag
    checkout, so "on main" cannot hold. The tracked-file half still applies —
    building from a modified tree would upload source that exists in no commit.
    Untracked scratch files are normal in a working checkout; what actually
    matters is that none of them reach the artifact, which
    ``check_sdist_is_committed`` enforces later.
    """

    if run("git", "status", "--porcelain", "--untracked-files=no"):
        raise Failure("tracked files are modified; commit or stash first")
    untracked = run("git", "ls-files", "--others", "--exclude-standard")
    if untracked:
        count = len(untracked.splitlines())
        print(
            f"  note {count} untracked path(s) present; sdist contents verified later"
        )
    if not require_main:
        return
    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        raise Failure(f"releases are cut from main, not {branch!r}")
    run("git", "fetch", "origin", "--quiet")
    local = run("git", "rev-parse", "HEAD")
    remote = run("git", "rev-parse", "origin/main")
    if local != remote:
        raise Failure("main differs from origin/main; pull or push first")


def check_changelog(version: str) -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(
        rf"^## {re.escape(version)} - \d{{4}}-\d{{2}}-\d{{2}}$", text, re.MULTILINE
    ):
        raise Failure(f"CHANGELOG.md has no dated '## {version} - YYYY-MM-DD' section")
    match = re.search(r"^## 未发布$(.*?)^## ", text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise Failure("CHANGELOG.md has no '## 未发布' section")
    if match.group(1).strip():
        raise Failure(
            "CHANGELOG.md still has entries under 未发布; move them into the "
            f"{version} section before releasing"
        )


def check_readmes(version: str) -> None:
    """Both READMEs must pin exactly this version and no other.

    The install snippet ships inside the wheel via ``readme``, so a stale pin is
    not only wrong on the web page: it is baked into the published artifact.
    """

    for name in READMES:
        text = (ROOT / name).read_text(encoding="utf-8")
        pinned = set(re.findall(rf"{PACKAGE}==([0-9][^'\"\s]*)", text))
        if version not in pinned:
            raise Failure(f"{name} does not pin {PACKAGE}=={version}")
        stale = pinned - {version}
        if stale:
            raise Failure(f"{name} still pins {PACKAGE}=={', '.join(sorted(stale))}")


def check_tag_absent(version: str) -> None:
    tag = f"v{version}"
    if run("git", "tag", "--list", tag):
        raise Failure(f"tag {tag} already exists locally")
    if run("git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"):
        raise Failure(f"tag {tag} already exists on origin")


def check_tag_points_at_head(version: str) -> None:
    """Require ``v<version>`` to exist and to be the commit being built.

    Without this, ``publish`` uploads whatever happens to be checked out. The
    registry cannot be overwritten, so a version whose artifact does not match
    its tag is permanent: anyone checking out the tag later to reproduce or audit
    that artifact gets different source. That is the one thing the tag exists to
    record, so it is the last place the check should have been missing.
    """

    tag = f"v{version}"
    if not run("git", "tag", "--list", tag):
        raise Failure(f"tag {tag} does not exist; run 'release.py tag' first")
    tagged = run("git", "rev-list", "-n1", tag)
    head = run("git", "rev-parse", "HEAD")
    if tagged != head:
        raise Failure(
            f"HEAD is {head[:9]} but {tag} points at {tagged[:9]}; publish must "
            f"build the tagged source — run 'git checkout {tag}' first"
        )


def check_quality() -> None:
    if shutil.which("ruff") is None:
        raise Failure("ruff is not on PATH; install the dev dependency group")
    run("ruff", "check", "src", "tests", "scripts")
    run("ruff", "format", "--check", "src", "tests", "scripts")
    run(sys.executable, "-m", "pytest", "tests/hand", "-q")


def command_check(version: str, *, for_publish: bool = False) -> set[str] | None:
    """Run every read-only gate. Returns the registry versions when known."""

    print(f"{PACKAGE} {version}")
    steps: list[tuple[str, object]] = [
        ("working tree", lambda: check_working_tree(require_main=not for_publish)),
        ("changelog", lambda: check_changelog(version)),
        ("readmes", lambda: check_readmes(version)),
        ("quality", check_quality),
    ]
    # Before tagging the tag must not exist; when publishing it must exist and be
    # exactly what is checked out.
    steps.append(
        ("tag points at HEAD", lambda: check_tag_points_at_head(version))
        if for_publish
        else ("tag unused", lambda: check_tag_absent(version))
    )
    for label, step in steps:
        step()  # type: ignore[operator]
        print(f"  ok   {label}")
    published = registry_versions()
    if published is not None:
        print(f"  ok   registry has {len(published)} version(s)")
    return published


def build() -> list[Path]:
    dist = ROOT / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    run(sys.executable, "-m", "build")
    artifacts = sorted(dist.iterdir())
    if len(artifacts) != 2:
        raise Failure(
            f"expected a wheel and an sdist, got {[p.name for p in artifacts]}"
        )
    return artifacts


def verify_artifacts(version: str, artifacts: list[Path]) -> None:
    """Check the built metadata, not just that the build succeeded.

    The wheel embeds README as its long description, so this is where a stale
    install snippet would otherwise slip into a permanent artifact.
    """

    run(sys.executable, "-m", "twine", "check", *[str(p) for p in artifacts])
    wheel = next(p for p in artifacts if p.suffix == ".whl")
    with zipfile.ZipFile(wheel) as archive:
        name = next(n for n in archive.namelist() if n.endswith("METADATA"))
        metadata = archive.read(name).decode("utf-8", "replace")
    found = re.search(r"^Version: (.+)$", metadata, re.MULTILINE)
    if found is None or found.group(1).strip() != version:
        raise Failure(f"wheel metadata version is {found and found.group(1)!r}")
    pinned = set(re.findall(rf"{PACKAGE}==([0-9][^'\"\s]*)", metadata))
    if pinned != {version}:
        raise Failure(
            f"wheel long description pins {PACKAGE}=={pinned or 'nothing'}; "
            f"expected only {version}"
        )
    check_sdist_is_committed(
        next(p for p in artifacts if p.suffixes[-2:] == [".tar", ".gz"])
    )
    print(f"  ok   artifacts ({', '.join(p.name for p in artifacts)})")


def check_sdist_is_committed(sdist: Path) -> None:
    """Every file in the sdist must be tracked by git.

    A release is meant to contain the committed tree and nothing else. Scratch
    directories left in the working copy are easy to miss by eye and impossible
    to remove from the registry once uploaded.
    """

    tracked = set(run("git", "ls-files").splitlines())
    with tarfile.open(sdist) as archive:
        # Filter by member type, not by a trailing slash: setuptools writes
        # directory entries without one, which would otherwise all look untracked.
        members = [member.name for member in archive.getmembers() if member.isfile()]
    prefix = f"{sdist.name[: -len('.tar.gz')]}/"
    generated = {"PKG-INFO", "setup.cfg"}
    stray = sorted(
        relative
        for name in members
        if (relative := name.removeprefix(prefix))
        and relative not in tracked
        and Path(relative).name not in generated
        and ".egg-info/" not in relative
    )
    if stray:
        raise Failure(
            "sdist contains files git does not track: " + ", ".join(stray[:10])
        )


def upload(artifacts: list[Path]) -> None:
    user, token = credentials()
    run(
        sys.executable,
        "-m",
        "twine",
        "upload",
        "--repository-url",
        REGISTRY,
        *[str(p) for p in artifacts],
        capture=False,
        env={"TWINE_USERNAME": user, "TWINE_PASSWORD": token},
    )


def release_notes(version: str) -> str:
    """Return this version's CHANGELOG section as the Release body."""

    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(
        rf"^## {re.escape(version)} - \d{{4}}-\d{{2}}-\d{{2}}$\n(.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise Failure(f"CHANGELOG.md has no section for {version}")
    return match.group(1).strip()


def create_release(version: str, artifacts: list[Path]) -> None:
    """Create the Gitea Release and attach the artifacts.

    Every prior version of this package has one with both artifacts attached.
    This is the step the 2.0.0 release missed even after everything else was
    repaired, because no other gate makes it visible: the tag, the registry and
    the changelog were all correct while the Release simply did not exist.
    """

    tag = f"v{version}"
    status, existing = api_request(f"/releases/tags/{tag}")
    if status == 200 and isinstance(existing, dict):
        assets = existing.get("assets") or []
        print(f"  skip Release {tag} already exists with {len(assets)} asset(s)")
        return
    _, created = api_request(
        "/releases",
        method="POST",
        payload={
            "tag_name": tag,
            "name": f"{PACKAGE} {version}",
            "body": release_notes(version),
            "draft": False,
            "prerelease": False,
        },
    )
    if not isinstance(created, dict) or "id" not in created:
        raise Failure(f"creating Release {tag} returned no id: {created!r}")
    identifier = created["id"]
    user, token = credentials()
    for artifact in artifacts:
        # Attachments are multipart, which urllib makes awkward; curl is used for
        # this one call only.
        run(
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--user",
            f"{user}:{token}",
            "-X",
            "POST",
            f"{API}/releases/{identifier}/assets?name={artifact.name}",
            "-F",
            f"attachment=@{artifact}",
        )
    print(f"  ok   Release {tag} created with {len(artifacts)} asset(s)")


def command_tag(version: str) -> None:
    command_check(version)
    tag = f"v{version}"
    run("git", "tag", "-s", tag, "-m", f"{PACKAGE} {version}")
    run("git", "verify-tag", tag)
    run("git", "push", "origin", tag)
    print(f"  ok   pushed signed tag {tag}")
    print(f"  next 'release.py publish' from a checkout of {tag}")


def command_publish(version: str) -> None:
    published = command_check(version, for_publish=True)
    artifacts = build()
    verify_artifacts(version, artifacts)
    if published is not None and version in published:
        print(f"  skip {version} is already in the registry; nothing to upload")
    else:
        upload(artifacts)
        print(f"  ok   uploaded {version}")
    create_release(version, artifacts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "tag", "publish"))
    arguments = parser.parse_args()
    version = declared_version()
    handlers = {
        "check": lambda: command_check(version),
        "tag": lambda: command_tag(version),
        "publish": lambda: command_publish(version),
    }
    try:
        handlers[arguments.command]()
    except Failure as error:
        print(f"\nrelease {arguments.command} refused:\n  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

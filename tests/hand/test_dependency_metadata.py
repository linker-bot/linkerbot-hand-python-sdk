"""Distribution boundary regression tests."""

from pathlib import Path
from typing import Any

import tomli
from packaging.requirements import Requirement
from packaging.version import Version


def _project_metadata() -> dict[str, Any]:
    return tomli.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_runtime_dependency_surface_is_transport_only() -> None:
    project = _project_metadata()
    requirements = tuple(Requirement(value) for value in project["dependencies"])

    assert tuple(requirement.name for requirement in requirements) == (
        "linkerbot-transport",
    )
    assert all(requirement.name != "numpy" for requirement in requirements)


def test_transport_dependency_requests_only_can_backends() -> None:
    project = _project_metadata()
    requirement = Requirement(project["dependencies"][0])

    assert requirement.extras == {"can", "canfd"}
    assert requirement.marker is None
    assert Version("0.1.4") in requirement.specifier
    assert Version("0.1.3") not in requirement.specifier
    assert Version("0.2.0") not in requirement.specifier


def test_distribution_discovers_only_the_linkerbot_hand_namespace() -> None:
    metadata = tomli.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    package_find = metadata["tool"]["setuptools"]["packages"]["find"]

    assert package_find["include"] == ["linkerbot_hand*"]
    assert package_find["namespaces"] is False


def test_source_distribution_excludes_internal_tests() -> None:
    manifest = Path("MANIFEST.in").read_text(encoding="utf-8").splitlines()

    assert "prune tests" in manifest


def test_removed_implementation_namespaces_have_no_python_sources() -> None:
    removed_roots = (
        Path("src/linkerbot/hand"),
        Path("src/linkerbot_hand/backends"),
        Path("src/linkerbot_hand/commands"),
        Path("src/linkerbot_hand/contracts"),
    )

    remaining = tuple(
        source
        for root in removed_roots
        for source in root.rglob("*.py")
        if source.is_file()
    )

    assert remaining == ()

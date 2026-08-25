"""Distribution dependency compatibility tests."""

from pathlib import Path

import pytest
import tomli
from packaging.requirements import Requirement
from packaging.version import Version


def _numpy_requirement(python_version: str) -> Requirement:
    project = tomli.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = [
        Requirement(value)
        for value in project["dependencies"]
        if Requirement(value).name == "numpy"
    ]
    active = [
        requirement
        for requirement in requirements
        if requirement.marker is None
        or requirement.marker.evaluate({"python_version": python_version})
    ]
    assert len(active) == 1
    return active[0]


@pytest.mark.parametrize(
    ("python_version", "supported", "unsupported"),
    (
        ("3.10", ("1.21.6", "1.26.4"), ("1.21.5", "2.0.0")),
        ("3.12", ("1.26.4",), ("1.21.5", "2.0.0")),
        ("3.13", ("2.1.0", "2.5.2"), ("1.26.4", "3.0.0")),
    ),
)
def test_numpy_requirement_matches_supported_python_runtime(
    python_version: str,
    supported: tuple[str, ...],
    unsupported: tuple[str, ...],
) -> None:
    requirement = _numpy_requirement(python_version)

    assert all(Version(version) in requirement.specifier for version in supported)
    assert all(Version(version) not in requirement.specifier for version in unsupported)

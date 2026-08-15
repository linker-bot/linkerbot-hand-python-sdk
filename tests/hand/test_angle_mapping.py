"""Unit tests for shared hand angle mapping helpers."""

import multiprocessing
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from linkerbot.exceptions import ValidationError
from linkerbot.hand import angle_mapping as angle_mapping_module
from linkerbot.hand.angle_mapping import AngleMappingManager, default_angle_mapping


def _reverse_mapping(joint_count: int) -> list[list[int]]:
    return [list(reversed(range(256))) for _ in range(joint_count)]


def _persist_mapping_in_process(
    mapping_path: str,
    side: str,
    fill_value: int,
    load_barrier: Any,
) -> None:
    manager = AngleMappingManager(
        "l6",
        ["joint"],
        mapping_path,
        side=side,
        interface_name="can0",
    )
    original_load = manager._load_file

    def coordinated_load() -> dict[str, Any]:
        data = original_load()
        try:
            load_barrier.wait(timeout=1.0)
        except threading.BrokenBarrierError:
            pass
        return data

    setattr(manager, "_load_file", coordinated_load)
    manager.set_mapping([[fill_value] * 256])


def test_default_angle_mapping_is_linear() -> None:
    mapping = default_angle_mapping(6)

    assert len(mapping) == 6
    assert all(len(row) == 256 for row in mapping)
    assert mapping[0] == list(range(256))
    assert mapping[5] == list(range(256))


def test_mapping_manager_creates_default_toml(tmp_path: Path) -> None:
    path = tmp_path / "mappings.toml"

    manager = AngleMappingManager("l6", ["a", "b"], path)

    assert path.exists()
    assert manager.map_values([0, 255]) == [0, 255]
    assert manager.get_mapping() == [list(range(256)), list(range(256))]


def test_mapping_manager_preserves_other_model_sections(tmp_path: Path) -> None:
    path = tmp_path / "mappings.toml"
    l6 = AngleMappingManager("l6", ["a", "b"], path)
    custom = _reverse_mapping(2)
    l6.set_mapping(custom)

    l20 = AngleMappingManager("l20lite", ["c", "d", "e"], path)

    assert l20.map_values([1, 2, 3]) == [1, 2, 3]
    assert AngleMappingManager("l6", ["a", "b"], path).get_mapping() == custom


def test_mapping_manager_distinguishes_side_and_interface(tmp_path: Path) -> None:
    path = tmp_path / "mappings.toml"
    left_can0 = AngleMappingManager(
        "l6", ["a", "b"], path, side="left", interface_name="can0"
    )
    left_can0.set_mapping(_reverse_mapping(2))

    right_can0 = AngleMappingManager(
        "l6", ["a", "b"], path, side="right", interface_name="can0"
    )
    left_can1 = AngleMappingManager(
        "l6", ["a", "b"], path, side="left", interface_name="can1"
    )

    assert left_can0.map_values([0, 128]) == [255, 127]
    assert right_can0.map_values([0, 128]) == [0, 128]
    assert left_can1.map_values([0, 128]) == [0, 128]


def test_mapping_manager_reloads_saved_mapping(tmp_path: Path) -> None:
    path = tmp_path / "mappings.toml"
    manager = AngleMappingManager("l6", ["a", "b"], path)
    custom = _reverse_mapping(2)

    manager.set_mapping(custom)
    reloaded = AngleMappingManager("l6", ["a", "b"], path)

    assert reloaded.map_values([0, 128]) == [255, 127]


def test_mapping_manager_deterministically_unmaps_hardware_values(
    tmp_path: Path,
) -> None:
    manager = AngleMappingManager("l6", ["a", "b"], tmp_path / "mappings.toml")
    mapping = _reverse_mapping(2)
    mapping[1] = [0 if value < 128 else 10 for value in range(256)]
    manager.set_mapping(mapping)

    assert manager.unmap_values([254, 5]) == [1, 0]


def test_mapping_manager_publishes_forward_and_inverse_as_one_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = AngleMappingManager("l6", ["a", "b"], tmp_path / "mappings.toml")
    replacement = _reverse_mapping(2)
    build_started = threading.Event()
    allow_build = threading.Event()
    original_build = angle_mapping_module._build_inverse_mapping

    def blocking_build(mapping: list[list[int]]) -> list[list[int]]:
        if mapping == replacement:
            build_started.set()
            if not allow_build.wait(timeout=2.0):
                raise AssertionError("inverse mapping build was not released")
        return original_build(mapping)

    monkeypatch.setattr(angle_mapping_module, "_build_inverse_mapping", blocking_build)
    with ThreadPoolExecutor(max_workers=1) as executor:
        update = executor.submit(manager.set_mapping, replacement)
        assert build_started.wait(timeout=2.0)
        try:
            assert manager.map_values([0, 255]) == [0, 255]
            assert manager.unmap_values([0, 255]) == [0, 255]
        finally:
            allow_build.set()
        update.result(timeout=2.0)

    assert manager.map_values([0, 255]) == [255, 0]
    assert manager.unmap_values([255, 0]) == [0, 255]


def test_concurrent_manager_updates_preserve_both_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "mappings.toml"
    left = AngleMappingManager(
        "l6", ["joint"], path, side="left", interface_name="can0"
    )
    right = AngleMappingManager(
        "l6", ["joint"], path, side="right", interface_name="can0"
    )
    left_mapping = [[17] * 256]
    right_mapping = [[231] * 256]
    start_barrier = threading.Barrier(3)
    load_barrier = threading.Barrier(2)
    original_load = AngleMappingManager._load_file

    def coordinated_load(self: AngleMappingManager) -> dict[str, Any]:
        data = original_load(self)
        try:
            load_barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        return data

    def update(manager: AngleMappingManager, mapping: list[list[int]]) -> None:
        start_barrier.wait(timeout=2.0)
        manager.set_mapping(mapping)

    monkeypatch.setattr(AngleMappingManager, "_load_file", coordinated_load)
    with ThreadPoolExecutor(max_workers=2) as executor:
        updates = (
            executor.submit(update, left, left_mapping),
            executor.submit(update, right, right_mapping),
        )
        start_barrier.wait(timeout=2.0)
        for future in updates:
            future.result(timeout=3.0)

    assert (
        AngleMappingManager(
            "l6", ["joint"], path, side="left", interface_name="can0"
        ).get_mapping()
        == left_mapping
    )
    assert (
        AngleMappingManager(
            "l6", ["joint"], path, side="right", interface_name="can0"
        ).get_mapping()
        == right_mapping
    )
    assert list(tmp_path.glob(".mappings.toml.*.tmp")) == []


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux flock semantics")
def test_process_updates_preserve_both_sections(tmp_path: Path) -> None:
    path = tmp_path / "mappings.toml"
    for side in ("left", "right"):
        AngleMappingManager("l6", ["joint"], path, side=side, interface_name="can0")

    context = multiprocessing.get_context("fork")
    load_barrier = context.Barrier(2)
    processes = [
        context.Process(
            target=_persist_mapping_in_process,
            args=(str(path), "left", 17, load_barrier),
        ),
        context.Process(
            target=_persist_mapping_in_process,
            args=(str(path), "right", 231, load_barrier),
        ),
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=5.0)
        assert all(not process.is_alive() for process in processes)
        assert [process.exitcode for process in processes] == [0, 0]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.0)

    assert AngleMappingManager(
        "l6", ["joint"], path, side="left", interface_name="can0"
    ).get_mapping() == [[17] * 256]
    assert AngleMappingManager(
        "l6", ["joint"], path, side="right", interface_name="can0"
    ).get_mapping() == [[231] * 256]
    assert list(tmp_path.glob(".mappings.toml.*.tmp")) == []


def test_mapping_manager_rejects_mismatched_joint_names(tmp_path: Path) -> None:
    path = tmp_path / "mappings.toml"
    AngleMappingManager("l6", ["a", "b"], path)

    with pytest.raises(ValidationError):
        AngleMappingManager("l6", ["b", "a"], path)


def test_mapping_manager_rejects_invalid_mapping(tmp_path: Path) -> None:
    manager = AngleMappingManager("l6", ["a", "b"], tmp_path / "mappings.toml")

    with pytest.raises(ValidationError):
        manager.set_mapping([[0] * 256])

    with pytest.raises(ValidationError):
        manager.set_mapping([[0] * 255, [0] * 256])

    invalid = default_angle_mapping(2)
    invalid[0][0] = 256
    with pytest.raises(ValidationError):
        manager.set_mapping(invalid)

    invalid = default_angle_mapping(2)
    invalid[0][0] = 1.0  # ty: ignore[invalid-assignment]
    with pytest.raises(ValidationError):
        manager.set_mapping(invalid)


def test_mapping_manager_rejects_invalid_raw_values(tmp_path: Path) -> None:
    manager = AngleMappingManager("l6", ["a", "b"], tmp_path / "mappings.toml")

    with pytest.raises(ValidationError):
        manager.map_values([0])

    with pytest.raises(ValidationError):
        manager.map_values([0, 256])

    with pytest.raises(ValidationError):
        manager.map_values(
            [0, 1.0]  # ty: ignore[invalid-argument-type]
        )

"""Fast immutable aging matches validated construction for every sample type."""

from dataclasses import FrozenInstanceError, dataclass, fields, replace

import pytest

from linkerbot_hand.api.device_info import DeviceInfo
from linkerbot_hand.api.enums import DataOrigin, HandSide
from linkerbot_hand.api.errors import ValidationError
from linkerbot_hand.api.readings import (
    AngleSample,
    ForceFieldSample,
    ForceSample,
    SampleMeta,
    TactileMatrix,
)
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import TelemetrySemantic
from linkerbot_hand.runtime._snapshot_samples import (
    _VECTOR_TYPES,
    age_device_info,
    age_sample,
)


def valid_meta():
    return SampleMeta(
        sequence=2,
        collected_at=100,
        age_s=0,
        valid=True,
        stale=False,
        connection_generation=3,
        origin=DataOrigin.DEVICE_RESPONSE,
        within_declared_range=False,
    )


def samples():
    meta = valid_meta()
    result = [
        (semantic, cls(raw=(1, 2), meta=meta))
        for semantic, cls in _VECTOR_TYPES.items()
    ]
    result.extend(
        [
            (
                TelemetrySemantic.ANGLES,
                AngleSample(
                    raw=(10, 20),
                    percent=(1.0, None),
                    physical_rad=(None, 0.2),
                    meta=meta,
                ),
            ),
            (TelemetrySemantic.FORCE, ForceSample(raw=(1, 2), tactile=(), meta=meta)),
            (
                TelemetrySemantic.FORCE,
                ForceSample(
                    raw=(),
                    meta=meta,
                    tactile=(
                        TactileMatrix(
                            name="finger", rows=2, columns=2, raw=((1, 2), (3, 4))
                        ),
                    ),
                ),
            ),
        ]
    )
    return result


@pytest.mark.parametrize("semantic,sample", samples())
@pytest.mark.parametrize("collected", [10.0, None])
def test_all_builtin_samples_match_replace_and_share_only_immutable_data(
    semantic, sample, collected
):
    caps = get_declared_capabilities("l6", side="left")
    aged = age_sample(
        semantic,
        sample,
        monotonic_now=20,
        collected_monotonic=collected,
        capabilities=caps,
        generation=3,
    )
    meta = (
        replace(sample.meta, age_s=10, stale=True)
        if collected is not None
        else SampleMeta.unavailable(connection_generation=3)
    )
    assert aged == replace(sample, meta=meta)
    assert aged is not sample and type(aged) is type(sample)
    for field in fields(sample):
        if field.name != "meta":
            if (
                isinstance(sample, ForceSample)
                and field.name == "fields"
                and not meta.valid
            ):
                # Invalidation rebuilds child metadata; empty tuple identity is
                # an implementation detail that differs between Python versions.
                assert aged.fields == sample.fields
                continue
            assert getattr(aged, field.name) is getattr(sample, field.name)
    assert sample.meta.age_s == 0 and sample.meta.valid
    with pytest.raises(FrozenInstanceError):
        aged.meta = sample.meta


def test_force_sample_invalidation_rebuilds_child_meta_and_shares_raw_data():
    original_meta = valid_meta()
    original_field = ForceFieldSample(
        name="fn",
        sensor_names=("finger",),
        raw=((1, 2),),
        units=(None,),
        encodings=(None,),
        valid_values=((True, True),),
        meta=original_meta,
    )
    sample = ForceSample(
        raw=(1, 2), tactile=(), meta=original_meta, fields=(original_field,)
    )
    aged = age_sample(
        TelemetrySemantic.FORCE,
        sample,
        monotonic_now=20,
        collected_monotonic=None,
        capabilities=get_declared_capabilities("l6", side="left"),
        generation=3,
    )
    unavailable = SampleMeta.unavailable(connection_generation=3)
    assert aged.meta == unavailable
    assert len(aged.fields) == 1
    assert aged.fields[0] == replace(original_field, meta=unavailable)
    assert aged.fields[0] is not original_field
    assert aged.raw is sample.raw and aged.tactile is sample.tactile
    for field in fields(original_field):
        if field.name != "meta":
            assert getattr(aged.fields[0], field.name) is getattr(
                original_field, field.name
            )
    assert sample.meta is original_meta and original_field.meta is original_meta
    assert sample.meta.valid and original_field.meta.valid
    assert sample.status("fn") == "available"
    assert aged.status("fn") == "invalidated"


def test_device_info_preserves_all_optional_identity_fields():
    original = DeviceInfo(
        model="l6",
        serial_number="SN",
        pcb_version=None,
        firmware_version=None,
        mechanical_version=None,
        product_code="P",
        sensor_variant=None,
        side=HandSide.LEFT,
        raw_identity=b"id",
        meta=valid_meta(),
        protocol_name="test",
        microcontroller_uid="uid",
        build_time="today",
    )
    for collected in (None, 10):
        aged = age_device_info(
            original, monotonic_now=20, collected_monotonic=collected, generation=3
        )
        meta = (
            SampleMeta.unavailable(connection_generation=3)
            if collected is None
            else replace(original.meta, age_s=10, stale=False)
        )
        assert aged == replace(original, meta=meta)


def test_custom_sample_subclass_still_runs_its_validator_on_aging():
    calls = []

    @dataclass(frozen=True)
    class CustomAngle(AngleSample):
        def __post_init__(self):
            super().__post_init__()
            calls.append(self.meta.age_s)

    sample = CustomAngle(
        raw=(1,), percent=(1.0,), physical_rad=(None,), meta=valid_meta()
    )
    aged = age_sample(
        TelemetrySemantic.ANGLES,
        sample,
        monotonic_now=11,
        collected_monotonic=10,
        capabilities=get_declared_capabilities("l6", side="left"),
        generation=3,
    )
    assert type(aged) is CustomAngle and calls == [0, 1]


def test_public_constructors_and_replace_still_reject_invalid_data():
    sample = AngleSample(
        raw=(1,), percent=(1.0,), physical_rad=(None,), meta=valid_meta()
    )
    with pytest.raises(ValidationError):
        replace(sample, raw=(float("nan"),))
    with pytest.raises(ValidationError):
        AngleSample(raw=(1,), percent=(), physical_rad=(), meta=valid_meta())
    with pytest.raises(ValidationError):
        replace(sample.meta, age_s=-1)

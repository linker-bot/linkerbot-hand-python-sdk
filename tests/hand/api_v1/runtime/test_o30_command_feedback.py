"""O30 write feedback must preserve actual state instead of echoing targets."""

import time

import pytest

from linkerbot_hand import (
    AngleFeedbackPolicy,
    CommandSemantic,
    CommandStage,
    DataOrigin,
    HandDevice,
    HandSide,
    PollingPlan,
    TelemetrySemantic,
    get_declared_capabilities,
)
from linkerbot_hand.protocols.o30 import O30ProtocolBackend
from linkerbot_hand.protocols.o30.codec import MI_POSITION, encode_v25

from .o30_canfd_fake import O30DeviceScript, ScriptedO30Transport


class MovingHandScript(O30DeviceScript):
    """Keep measured position different from a newly accepted target."""

    def _write(self, main_index, sub_index, length, payload, *, rts=False):
        before = self.actual[MI_POSITION]
        replies = super()._write(main_index, sub_index, length, payload, rts=rts)
        if main_index == MI_POSITION:
            self.actual[MI_POSITION] = before
            return (self._response(main_index, sub_index, before, rts=rts),)
        return replies


def test_fragmented_write_publishes_only_complete_actual_position():
    capabilities = get_declared_capabilities("O30", side="right")
    transaction = O30ProtocolBackend(HandSide.RIGHT).command_transaction(
        CommandSemantic.ANGLES, (116,) * 20, capabilities
    )
    script = O30DeviceScript()
    actual = encode_v25((99,) * 20)
    wrong_view = transaction.consume(script._response(1, 0, actual, rts=True))
    assert not wrong_view.matched
    first = transaction.consume(script._response(1, 0, actual[:10]))
    assert first.matched and not first.complete
    assert not first.publications and first.result is None
    last = transaction.consume(script._response(1, 10, actual[10:]))
    assert last.acknowledged and last.complete
    assert last.result.raw == (99,) * 20
    assert last.result.semantic is TelemetrySemantic.ANGLES
    assert last.result.origin is DataOrigin.DEVICE_COMMAND_FEEDBACK


@pytest.mark.parametrize("policy", list(AngleFeedbackPolicy))
def test_runtime_uses_current_position_feedback_for_each_policy(policy):
    script = MovingHandScript()
    transport = ScriptedO30Transport(script)
    hand = HandDevice(model="O30", side="right", transport=transport)
    hand.open()
    try:
        initial = hand.get_snapshot().angles
        assert (
            hand.get_capabilities()
            .command(CommandSemantic.ANGLES)
            .returns_current_state
        )
        handle = hand.set_angles((116,) * 20)
        assert handle.wait(timeout=1) is CommandStage.ACKNOWLEDGED
        sample = hand.get_snapshot().angles
        assert sample.raw == tuple(range(20))
        assert sample.meta.sequence > initial.meta.sequence
        assert sample.meta.collected_at >= initial.meta.collected_at
        assert sample.meta.origin is DataOrigin.DEVICE_COMMAND_FEEDBACK
        query_count = script.read_counts[(MI_POSITION, False)]
        hand.start_polling(PollingPlan(angles_hz=20, angle_feedback=policy))
        # Continue writing through several polling deadlines; no forced motion
        # convergence in this fake can disguise target echo as current state.
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            assert (
                hand.set_angles((84,) * 20).wait(timeout=1) is CommandStage.ACKNOWLEDGED
            )
            time.sleep(0.005)
        hand.stop_polling()
        queries = script.read_counts[(MI_POSITION, False)] - query_count
        if policy is AngleFeedbackPolicy.COMMAND_FEEDBACK_ONLY:
            assert queries == 0
        elif policy is AngleFeedbackPolicy.QUERY_ONLY:
            assert queries >= 3
        else:
            assert queries <= 2  # Startup may precede the first command reply.
        assert hand.get_snapshot().angles.raw == tuple(range(20))
    finally:
        hand.close()

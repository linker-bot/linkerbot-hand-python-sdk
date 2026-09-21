"""L6 C6/C7 report device quantization rather than exact host byte echoes."""

from contextlib import closing

import pytest

from linkerbot_hand import CommandStage, HandDevice, HandSide

from .classic_can_fake import ScriptedClassicCANTransport, make_script


@pytest.mark.parametrize(
    "opcode,setter,channel",
    [
        (0xC6, "set_stall_thresholds", "stall_thresholds"),
        (0xC7, "set_stall_hold_currents", "stall_hold_currents"),
    ],
)
def test_quantized_stall_responses_publish_observed_values(opcode, setter, channel):
    script = make_script("L6", HandSide.LEFT)
    script.bad_command_echoes[opcode] = (90,) * 6
    hand = HandDevice(
        model="L6", side="left", transport=ScriptedClassicCANTransport(script)
    )
    with closing(hand.open()):
        assert getattr(hand, setter)((91,) * 6).wait(1) is CommandStage.ACKNOWLEDGED
        assert getattr(hand.get_snapshot(), channel).raw == (90,) * 6

from src.app.hardware_plug_runtime import HardwarePlugRuntime
from src.app.mock_api import MockHunterAPI


def test_hardware_plug_runtime_runs_one_integrated_tick():
    runtime = HardwarePlugRuntime(MockHunterAPI())

    result = runtime.tick(play_action="wand_fast")

    assert result["contract_ready"] is True
    assert result["activity"]["level"] in {"low", "medium", "high"}
    assert result["audio_emotion"]["emotion"] in {"hungry", "clingy", "alert", "playful", "calm"}
    assert result["play"]["ok"] is True
    assert "water" in result


def test_hardware_plug_runtime_blocks_when_contract_missing():
    class EmptyAPI:
        pass

    result = HardwarePlugRuntime(EmptyAPI()).tick()

    assert result["contract_ready"] is False
    assert "snapshot" in result["missing"]

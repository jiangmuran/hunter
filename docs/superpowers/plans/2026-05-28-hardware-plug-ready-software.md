# Hardware-Plug-Ready Software Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete all pure-software PRD gaps so Hunter's robot-side software is hardware-plug-ready: hardware teams only need to provide conforming drivers/endpoints and calibration data.

**Architecture:** Add a thin hardware capability adapter layer above `HunterAPI`, then route perception, audio, activity, play execution, reward, care, report, memory, and remote takeover through one runtime orchestrator. Keep current demo/mock flows working while making readiness distinguish `hardware_plug_ready` from true `real_product_ready`.

**Tech Stack:** Python 3.12, stdlib dataclasses/typing/sqlite/json, existing pytest suite, existing app/software package layout.

---

## File Structure

- Create `src/app/hardware_contract.py` — typed software contract for robot-side hardware capabilities: camera frame, audio features, activity sample, play actuator, reward actuator, water sensor, remote command gate.
- Create `src/app/runtime_events.py` — small event record helpers shared by orchestrator/report/readiness.
- Create `src/app/play_executor.py` — maps strategy arms (`wand_fast`, `laser_escape`, etc.) into hardware capability commands with safety metadata.
- Create `src/app/reward_executor.py` — wraps `treat_reward` policy and dispatches reward actuator calls when policy allows.
- Create `src/app/activity_sensing.py` — hardware-plug-ready activity scoring from target visibility/motion plus optional sensor samples.
- Create `src/app/remote_takeover.py` — CLI-safe remote command authorization and command routing without Web UI.
- Create `src/app/hardware_plug_runtime.py` — integrated runtime wiring perception/audio/activity/play/reward/care/report/memory.
- Modify `src/app/mock_api.py` — implement mock methods for new hardware capability contract.
- Modify `src/software/api_client.py` — add generic endpoint wrappers for play actuator, reward actuator, sensor state, and remote command dispatch.
- Modify `src/app/demo.py` — expose CLI previews/checks for hardware-plug runtime and remote takeover.
- Modify `src/app/prd_readiness.py` — promote pure-software items to `hardware_plug_ready` when covered by interface + mock contract + tests, leave true product readiness hardware-dependent.
- Add tests:
  - `tests/test_app_hardware_contract.py`
  - `tests/test_app_activity_sensing.py`
  - `tests/test_app_play_executor.py`
  - `tests/test_app_reward_executor.py`
  - `tests/test_app_remote_takeover.py`
  - `tests/test_app_hardware_plug_runtime.py`
  - update `tests/test_app_prd_readiness.py`
  - update `tests/test_app_mock_api.py`
  - update `tests/test_app_demo.py`

---

## Task 1: Hardware Capability Contract

**Files:**
- Create: `src/app/hardware_contract.py`
- Modify: `src/app/mock_api.py`
- Test: `tests/test_app_hardware_contract.py`

- [ ] **Step 1: Write failing tests**

```python
from src.app.hardware_contract import HardwareCapabilityStatus, build_hardware_contract_report
from src.app.mock_api import MockHunterAPI


def test_mock_api_satisfies_robot_side_hardware_contract():
    api = MockHunterAPI()
    report = build_hardware_contract_report(api)

    assert report["ready"] is True
    assert report["missing"] == []
    assert {item["capability"] for item in report["capabilities"]} == {
        "camera_snapshot",
        "audio_features",
        "activity_sample",
        "play_actuator",
        "reward_actuator",
        "water_sensor",
        "remote_command",
    }


def test_contract_report_lists_missing_methods():
    class EmptyAPI:
        pass

    report = build_hardware_contract_report(EmptyAPI())

    assert report["ready"] is False
    assert "capture_audio_features" in report["missing"]
    assert "execute_play_action" in report["missing"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_hardware_contract.py -q`
Expected: FAIL because `src.app.hardware_contract` does not exist.

- [ ] **Step 3: Implement contract report**

Create `src/app/hardware_contract.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HardwareCapabilityStatus:
    capability: str
    method: str
    ready: bool

    def to_dict(self) -> dict[str, Any]:
        return {"capability": self.capability, "method": self.method, "ready": self.ready}


REQUIRED_HARDWARE_METHODS = {
    "camera_snapshot": "snapshot",
    "audio_features": "capture_audio_features",
    "activity_sample": "activity_sample",
    "play_actuator": "execute_play_action",
    "reward_actuator": "dispense_treat",
    "water_sensor": "water_state",
    "remote_command": "remote_command",
}


def build_hardware_contract_report(api: Any) -> dict[str, Any]:
    capabilities = [
        HardwareCapabilityStatus(capability, method, callable(getattr(api, method, None)))
        for capability, method in REQUIRED_HARDWARE_METHODS.items()
    ]
    missing = [item.method for item in capabilities if not item.ready]
    return {
        "ready": not missing,
        "missing": missing,
        "capabilities": [item.to_dict() for item in capabilities],
    }
```

- [ ] **Step 4: Extend mock API**

Add to `MockHunterAPI` in `src/app/mock_api.py`:

```python
    def capture_audio_features(self) -> dict[str, Any]:
        return {"pitch_hz": 650, "energy": 0.62, "duration_ms": 900, "repetition": 2}

    def activity_sample(self) -> dict[str, Any]:
        return {"motion_score": 0.55, "visible_ratio": 0.75, "window_seconds": 10}

    def execute_play_action(self, action: str, intensity: str = "medium", duration_ms: int = 1200):
        return self.cmd(f"play:{action}:{intensity}:{duration_ms}")

    def dispense_treat(self, grams: float = 1.0, reason: str = "reward"):
        entry = self.cmd(f"treat:{grams}:{reason}")
        entry["dispensed_grams"] = grams
        return entry

    def water_state(self) -> dict[str, Any]:
        return {"level_mm": 42, "last_drink_minutes_ago": 90, "sensor_ok": True}

    def remote_command(self, command: str, **params: Any):
        return self.cmd(f"remote:{command}:{params}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_app_hardware_contract.py -q`
Expected: PASS.

---

## Task 2: Hardware API Endpoint Wrappers

**Files:**
- Modify: `src/software/api_client.py`
- Test: `tests/test_app_hardware_contract.py`

- [ ] **Step 1: Add failing test for real API surface**

Append to `tests/test_app_hardware_contract.py`:

```python
from src.software.api_client import HunterAPI


class RecordingSession:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout=2):
        self.calls.append(("GET", url, None, timeout))
        return Response({"ok": True, "level_mm": 40})

    def post(self, url, json=None, timeout=2):
        self.calls.append(("POST", url, json, timeout))
        return Response({"ok": True, "payload": json})


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_hunter_api_exposes_hardware_plug_endpoints():
    session = RecordingSession()
    api = HunterAPI("http://hunter.local", session=session)

    api.capture_audio_features()
    api.activity_sample()
    api.execute_play_action("wand_fast", intensity="high", duration_ms=900)
    api.dispense_treat(grams=1.5, reason="catch")
    api.water_state()
    api.remote_command("stop")

    assert ("GET", "http://hunter.local/audio/features", None, 2) in session.calls
    assert ("GET", "http://hunter.local/activity/sample", None, 2) in session.calls
    assert ("POST", "http://hunter.local/play/action", {"action": "wand_fast", "intensity": "high", "duration_ms": 900}, 2) in session.calls
    assert ("POST", "http://hunter.local/reward/treat", {"grams": 1.5, "reason": "catch"}, 2) in session.calls
    assert ("GET", "http://hunter.local/water/state", None, 2) in session.calls
    assert ("POST", "http://hunter.local/remote/command", {"command": "stop"}, 2) in session.calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_hardware_contract.py::test_hunter_api_exposes_hardware_plug_endpoints -q`
Expected: FAIL because methods are missing.

- [ ] **Step 3: Implement endpoint wrappers**

Add methods to `HunterAPI` in `src/software/api_client.py`:

```python
    def capture_audio_features(self):
        return self.session.get(f"{self.base_url}/audio/features", timeout=2).json()

    def activity_sample(self):
        return self.session.get(f"{self.base_url}/activity/sample", timeout=2).json()

    def execute_play_action(self, action: str, intensity: str = "medium", duration_ms: int = 1200):
        payload = {"action": action, "intensity": intensity, "duration_ms": duration_ms}
        return self.session.post(f"{self.base_url}/play/action", json=payload, timeout=2).json()

    def dispense_treat(self, grams: float = 1.0, reason: str = "reward"):
        payload = {"grams": grams, "reason": reason}
        return self.session.post(f"{self.base_url}/reward/treat", json=payload, timeout=2).json()

    def water_state(self):
        return self.session.get(f"{self.base_url}/water/state", timeout=2).json()

    def remote_command(self, command: str, **params):
        payload = {"command": command, **params}
        return self.session.post(f"{self.base_url}/remote/command", json=payload, timeout=2).json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_app_hardware_contract.py -q`
Expected: PASS.

---

## Task 3: Activity Sensing

**Files:**
- Create: `src/app/activity_sensing.py`
- Test: `tests/test_app_activity_sensing.py`

- [ ] **Step 1: Write failing tests**

```python
from src.app.activity_sensing import build_activity_score


def test_activity_score_uses_visible_target_and_motion_sample():
    score = build_activity_score(
        target={"visible": True, "w": 180, "h": 180},
        sample={"motion_score": 0.8, "visible_ratio": 0.75, "window_seconds": 10},
    )

    assert score["level"] == "high"
    assert score["score"] >= 0.7
    assert score["window_seconds"] == 10


def test_activity_score_handles_no_target_with_low_motion():
    score = build_activity_score(target=None, sample={"motion_score": 0.1, "visible_ratio": 0.0})

    assert score["level"] == "low"
    assert score["score"] <= 0.25
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_activity_sensing.py -q`
Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement activity scoring**

Create `src/app/activity_sensing.py`:

```python
from __future__ import annotations

from typing import Any


def build_activity_score(target: dict[str, Any] | None, sample: dict[str, Any] | None = None) -> dict[str, Any]:
    sample = sample or {}
    motion = float(sample.get("motion_score", 0.0) or 0.0)
    visible_ratio = float(sample.get("visible_ratio", 1.0 if target else 0.0) or 0.0)
    target_bonus = 0.2 if target else 0.0
    score = max(0.0, min(1.0, motion * 0.55 + visible_ratio * 0.25 + target_bonus))
    if score >= 0.7:
        level = "high"
    elif score >= 0.35:
        level = "medium"
    else:
        level = "low"
    return {
        "score": round(score, 3),
        "level": level,
        "window_seconds": int(sample.get("window_seconds", 10) or 10),
        "source": "hardware_sample" if sample else "target_visibility",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_app_activity_sensing.py -q`
Expected: PASS.

---

## Task 4: Play Executor

**Files:**
- Create: `src/app/play_executor.py`
- Test: `tests/test_app_play_executor.py`

- [ ] **Step 1: Write failing tests**

```python
from src.app.mock_api import MockHunterAPI
from src.app.play_executor import PlayExecutor, build_play_command


def test_build_play_command_maps_known_action_to_safe_duration():
    command = build_play_command("wand_fast", activity_level="high")

    assert command["action"] == "wand_fast"
    assert command["intensity"] == "medium"
    assert command["duration_ms"] <= 1500
    assert command["safety"] == "bounded"


def test_play_executor_dispatches_to_hardware_contract():
    api = MockHunterAPI()
    result = PlayExecutor(api).execute("laser_escape", activity_level="medium")

    assert result["ok"] is True
    assert api.command_history[-1]["action"].startswith("play:laser_escape")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_play_executor.py -q`
Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement play executor**

Create `src/app/play_executor.py`:

```python
from __future__ import annotations

from typing import Any


ACTION_DEFAULTS = {
    "wand_fast": {"intensity": "medium", "duration_ms": 1200},
    "wand_hover": {"intensity": "low", "duration_ms": 1400},
    "laser_escape": {"intensity": "medium", "duration_ms": 1000},
    "laser_zigzag": {"intensity": "medium", "duration_ms": 1100},
    "sound_tease": {"intensity": "low", "duration_ms": 800},
}


def build_play_command(action: str, activity_level: str = "medium") -> dict[str, Any]:
    base = dict(ACTION_DEFAULTS.get(action, {"intensity": "low", "duration_ms": 800}))
    if activity_level == "low":
        base["intensity"] = "low"
    if activity_level == "high" and base["duration_ms"] > 1200:
        base["duration_ms"] = 1200
    return {"action": action, **base, "safety": "bounded"}


class PlayExecutor:
    def __init__(self, api: Any):
        self.api = api

    def execute(self, action: str, activity_level: str = "medium") -> dict[str, Any]:
        command = build_play_command(action, activity_level=activity_level)
        return self.api.execute_play_action(
            command["action"],
            intensity=command["intensity"],
            duration_ms=command["duration_ms"],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_app_play_executor.py -q`
Expected: PASS.

---

## Task 5: Reward Executor

**Files:**
- Create: `src/app/reward_executor.py`
- Test: `tests/test_app_reward_executor.py`

- [ ] **Step 1: Write failing tests**

```python
from src.app.mock_api import MockHunterAPI
from src.app.reward_executor import RewardExecutor


def test_reward_executor_dispenses_when_policy_allows():
    api = MockHunterAPI()
    result = RewardExecutor(api).maybe_reward({"outcome": "caught", "catch_success": True})

    assert result["dispensed"] is True
    assert api.command_history[-1]["action"].startswith("treat:")


def test_reward_executor_does_not_dispense_for_lost_target():
    api = MockHunterAPI()
    result = RewardExecutor(api).maybe_reward({"outcome": "lost_target", "catch_success": False})

    assert result["dispensed"] is False
    assert api.command_history == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_reward_executor.py -q`
Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement reward executor**

Create `src/app/reward_executor.py`:

```python
from __future__ import annotations

from typing import Any

from src.app.treat_reward import build_treat_reward_decision


class RewardExecutor:
    def __init__(self, api: Any, daily_limit: int = 8):
        self.api = api
        self.daily_limit = daily_limit
        self.dispensed_today = 0

    def maybe_reward(self, session_summary: dict[str, Any]) -> dict[str, Any]:
        decision = build_treat_reward_decision(
            caught=bool(session_summary.get("catch_success") or session_summary.get("outcome") == "caught"),
            lost_target=bool(session_summary.get("outcome") == "lost_target"),
            dispensed_today=self.dispensed_today,
            daily_limit=self.daily_limit,
            treats_remaining=int(session_summary.get("treats_remaining", 10) or 10),
        )
        if not decision["dispense"]:
            return {"dispensed": False, "decision": decision}
        response = self.api.dispense_treat(grams=1.0, reason=decision["reason"])
        self.dispensed_today += 1
        return {"dispensed": True, "decision": decision, "response": response}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_app_reward_executor.py -q`
Expected: PASS.

---

## Task 6: Remote Takeover CLI-Safe Router

**Files:**
- Create: `src/app/remote_takeover.py`
- Test: `tests/test_app_remote_takeover.py`

- [ ] **Step 1: Write failing tests**

```python
from src.app.mock_api import MockHunterAPI
from src.app.remote_takeover import RemoteTakeover


def test_remote_takeover_requires_operator_token():
    api = MockHunterAPI()
    result = RemoteTakeover(api, operator_token="demo").dispatch("forward", token="wrong")

    assert result["ok"] is False
    assert result["reason"] == "unauthorized"
    assert api.command_history == []


def test_remote_takeover_dispatches_allowed_command():
    api = MockHunterAPI()
    result = RemoteTakeover(api, operator_token="demo").dispatch("stop", token="demo")

    assert result["ok"] is True
    assert api.command_history[-1]["action"].startswith("remote:stop")


def test_remote_takeover_rejects_unknown_command():
    api = MockHunterAPI()
    result = RemoteTakeover(api, operator_token="demo").dispatch("delete_everything", token="demo")

    assert result["ok"] is False
    assert result["reason"] == "unsupported_command"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_remote_takeover.py -q`
Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement remote takeover**

Create `src/app/remote_takeover.py`:

```python
from __future__ import annotations

from typing import Any


ALLOWED_REMOTE_COMMANDS = {"forward", "rotate_cw", "rotate_ccw", "stop", "emergency", "play_sound"}


class RemoteTakeover:
    def __init__(self, api: Any, operator_token: str):
        self.api = api
        self.operator_token = operator_token

    def dispatch(self, command: str, token: str, **params: Any) -> dict[str, Any]:
        if token != self.operator_token:
            return {"ok": False, "reason": "unauthorized", "command": command}
        if command not in ALLOWED_REMOTE_COMMANDS:
            return {"ok": False, "reason": "unsupported_command", "command": command}
        response = self.api.remote_command(command, **params)
        return {"ok": bool(response.get("ok", True)), "command": command, "response": response}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_app_remote_takeover.py -q`
Expected: PASS.

---

## Task 7: Integrated Hardware-Plug Runtime

**Files:**
- Create: `src/app/hardware_plug_runtime.py`
- Test: `tests/test_app_hardware_plug_runtime.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_hardware_plug_runtime.py -q`
Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement integrated runtime**

Create `src/app/hardware_plug_runtime.py`:

```python
from __future__ import annotations

from typing import Any

from src.app.activity_sensing import build_activity_score
from src.app.audio_emotion import classify_audio_emotion
from src.app.hardware_contract import build_hardware_contract_report
from src.app.play_executor import PlayExecutor


class HardwarePlugRuntime:
    def __init__(self, api: Any):
        self.api = api
        self.play_executor = PlayExecutor(api)

    def tick(self, play_action: str = "wand_hover") -> dict[str, Any]:
        contract = build_hardware_contract_report(self.api)
        if not contract["ready"]:
            return {"contract_ready": False, "missing": contract["missing"], "contract": contract}
        frame = self.api.snapshot()
        audio_features = self.api.capture_audio_features()
        activity_sample = self.api.activity_sample()
        activity = build_activity_score(target={"visible": frame is not None}, sample=activity_sample)
        audio_emotion = classify_audio_emotion(audio_features)
        play = self.play_executor.execute(play_action, activity_level=activity["level"])
        water = self.api.water_state()
        return {
            "contract_ready": True,
            "activity": activity,
            "audio_emotion": audio_emotion,
            "play": play,
            "water": water,
            "contract": contract,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_app_hardware_plug_runtime.py -q`
Expected: PASS.

---

## Task 8: CLI Surface

**Files:**
- Modify: `src/app/demo.py`
- Test: `tests/test_app_demo.py`

- [ ] **Step 1: Add failing CLI tests**

Add tests:

```python
def test_hardware_plug_check_cli_reports_contract_ready(self):
    result = demo.run_demo_entry(["--hardware-plug-check"], verbose=False)

    self.assertTrue(result["contract_ready"])
    self.assertIn("activity", result)


def test_remote_takeover_cli_dispatches_stop(self):
    result = demo.run_demo_entry([
        "--remote-takeover-command", "stop",
        "--remote-token", "demo",
        "--remote-operator-token", "demo",
    ], verbose=False)

    self.assertTrue(result["ok"])
    self.assertEqual(result["command"], "stop")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_demo.py::DemoTest::test_hardware_plug_check_cli_reports_contract_ready tests/test_app_demo.py::DemoTest::test_remote_takeover_cli_dispatches_stop -q`
Expected: FAIL because parser flags and dispatch paths are missing.

- [ ] **Step 3: Add imports and parser args**

Modify `src/app/demo.py` imports:

```python
from src.app.hardware_plug_runtime import HardwarePlugRuntime
from src.app.remote_takeover import RemoteTakeover
```

Add parser args:

```python
    parser.add_argument("--hardware-plug-check", action="store_true")
    parser.add_argument("--remote-takeover-command")
    parser.add_argument("--remote-token", default="")
    parser.add_argument("--remote-operator-token", default="demo")
```

- [ ] **Step 4: Add CLI handlers**

Add functions:

```python
def run_hardware_plug_check(verbose: bool = True) -> dict:
    result = HardwarePlugRuntime(MockHunterAPI()).tick(play_action="wand_fast")
    if verbose:
        print({"hardware_plug_check": result})
    return result


def run_remote_takeover_command(args: argparse.Namespace, verbose: bool = True) -> dict:
    api = build_api(mode=args.mode, base_url=args.base_url)
    result = RemoteTakeover(api, operator_token=args.remote_operator_token).dispatch(
        args.remote_takeover_command,
        token=args.remote_token,
    )
    if verbose:
        print({"remote_takeover": result})
    return result
```

In `run_demo_entry`, before default session dispatch:

```python
    if args.hardware_plug_check:
        return run_hardware_plug_check(verbose=verbose)
    if args.remote_takeover_command:
        return run_remote_takeover_command(args, verbose=verbose)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_app_demo.py -q`
Expected: PASS.

---

## Task 9: PRD Readiness Semantics

**Files:**
- Modify: `src/app/prd_readiness.py`
- Test: `tests/test_app_prd_readiness.py`

- [ ] **Step 1: Add failing readiness tests**

Add tests:

```python
def test_prd_coverage_reports_hardware_plug_ready_statuses(self):
    coverage = build_prd_software_coverage()
    statuses = {feature["id"]: feature["status"] for feature in coverage["features"]}

    self.assertEqual(statuses["wand_play"], "hardware_plug_ready")
    self.assertEqual(statuses["laser_chase"], "hardware_plug_ready")
    self.assertEqual(statuses["treat_reward"], "hardware_plug_ready")
    self.assertEqual(statuses["remote_app_control"], "hardware_plug_ready")
    self.assertTrue(coverage["hardware_plug_ready"])
    self.assertFalse(coverage["real_product_ready"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_prd_readiness.py::PrdReadinessTest::test_prd_coverage_reports_hardware_plug_ready_statuses -q`
Expected: FAIL because statuses are still `strategy_only`, `mock_usable`, or `out_of_scope`.

- [ ] **Step 3: Update readiness statuses and summary**

In `src/app/prd_readiness.py`:

```python
READY_STATUSES = {"implemented", "mock_usable", "hardware_plug_ready"}
```

Update pure-software-covered features:

```python
"vision_tracking": "hardware_plug_ready"
"audio_emotion": "hardware_plug_ready"
"activity_sensing": "hardware_plug_ready"
"wand_play": "hardware_plug_ready"
"laser_chase": "hardware_plug_ready"
"treat_reward": "hardware_plug_ready"
"water_monitoring": "hardware_plug_ready"
"meme_generator": "hardware_plug_ready"
"daily_diary": "hardware_plug_ready"
"preference_model": "implemented"
"remote_app_control": "hardware_plug_ready"
"surprise_entropy": "hardware_plug_ready"
```

Return fields:

```python
"hardware_plug_ready": len(blockers) == 0,
"real_product_ready": False,
"real_product_summary": "机器人端纯软件已达到 hardware-plug-ready；真实产品仍需要硬件实现、现场校准和长时间验证。",
```

- [ ] **Step 4: Update old tests expecting out-of-scope**

Change remote test to assert hardware-plug-ready and mention CLI/robot-side interface, not WebUI completion.

- [ ] **Step 5: Run readiness tests**

Run: `python -m pytest tests/test_app_prd_readiness.py tests/test_app_demo.py -q`
Expected: PASS.

---

## Task 10: Full Verification

**Files:**
- No code changes unless verification exposes failures.

- [ ] **Step 1: Run focused PRD suite**

Run:

```bash
python -m pytest \
  tests/test_app_hardware_contract.py \
  tests/test_app_activity_sensing.py \
  tests/test_app_play_executor.py \
  tests/test_app_reward_executor.py \
  tests/test_app_remote_takeover.py \
  tests/test_app_hardware_plug_runtime.py \
  tests/test_app_prd_readiness.py \
  tests/test_app_demo.py \
  tests/test_app_mock_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run complete test suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 3: Run CLI checks**

Run:

```bash
python -m src.app.demo --hardware-plug-check
python -m src.app.demo --prd-software-coverage
python -m src.app.demo --remote-takeover-command stop --remote-token demo --remote-operator-token demo
```

Expected: each command exits 0 and prints a successful result.

---

## Self-Review

- Spec coverage: hardware contract, endpoint wrappers, activity sensing, play execution, reward execution, remote takeover, integrated runtime, CLI surface, and readiness semantics are each covered by a task.
- Placeholder scan: no TBD/TODO/implement-later placeholders are present.
- Type consistency: `capture_audio_features`, `activity_sample`, `execute_play_action`, `dispense_treat`, `water_state`, and `remote_command` are defined consistently across contract, mock API, real API wrapper, and runtime.

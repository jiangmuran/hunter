from __future__ import annotations

from typing import Any


PRD_SOFTWARE_FEATURES = [
    {
        "id": "vision_tracking",
        "feature": "视觉追踪",
        "status": "hardware_plug_ready",
        "real_use_gap": "已有检测器接口、追踪器、mock 场景、底盘动作闭环和硬件契约；真实产品仍需要现场摄像头模型、帧率和距离标定。",
        "evidence": [
            "src/software/perception/tracker.py",
            "src/app/orchestrator.py",
            "src/app/state_machine.py",
            "tests/test_perception_tracker.py",
        ],
    },
    {
        "id": "audio_emotion",
        "feature": "叫声识别",
        "status": "hardware_plug_ready",
        "real_use_gap": "已有软件抽象层、四分类特征管线和麦克风特征硬件契约；真实产品仍需要硬件侧采样、模型替换和现场阈值校准。",
        "evidence": [
            "src/app/audio_emotion.py",
            "tests/test_app_audio_emotion.py",
        ],
    },
    {
        "id": "activity_sensing",
        "feature": "活跃度感知",
        "status": "hardware_plug_ready",
        "real_use_gap": "已有 10 秒活跃度评分、目标可见率融合和 activity_sample 硬件契约；真实产品仍需要光流/IMU 等硬件侧数据校准。",
        "evidence": [
            "src/app/session_summary.py",
            "src/app/dashboard_preview.py",
            "tests/test_app_session_summary.py",
        ],
    },
    {
        "id": "wand_play",
        "feature": "逗猫棒挥舞",
        "status": "hardware_plug_ready",
        "real_use_gap": "已有动作推荐、偏好选择、惊喜熵候选动作和 bounded play actuator 执行契约；真实产品仍需要机械臂轨迹硬件实现与幅度标定。",
        "evidence": [
            "src/app/next_session_plan.py",
            "src/app/surprise_entropy.py",
            "src/software/hunt/motion_generator.py",
        ],
    },
    {
        "id": "laser_chase",
        "feature": "激光点追逐",
        "status": "hardware_plug_ready",
        "real_use_gap": "已有 laser_escape 软件推荐、新鲜度控制和 bounded play actuator 执行契约；真实产品仍需要激光云台、安全角度和路径硬件实现。",
        "evidence": [
            "src/app/personalization_policy.py",
            "src/app/surprise_entropy.py",
            "tests/test_app_surprise_entropy.py",
        ],
    },
    {
        "id": "treat_reward",
        "feature": "零食投喂奖励",
        "status": "hardware_plug_ready",
        "real_use_gap": "已有扑抓成功、每日上限、余量策略和 dispense_treat 执行契约；真实产品仍需要零食机构、重量/余量传感器和落点标定。",
        "evidence": [
            "src/app/treat_reward.py",
            "tests/test_app_treat_reward.py",
        ],
    },
    {
        "id": "water_monitoring",
        "feature": "饮水监测",
        "status": "hardware_plug_ready",
        "real_use_gap": "software/care 有 WaterMonitor 服务，硬件契约提供 water_state；真实产品仍需要液位传感器实现、校准、推送通道和长时间运行验证。",
        "evidence": [
            "src/software/care/__init__.py",
        ],
    },
    {
        "id": "meme_generator",
        "feature": "表情包生成器",
        "status": "hardware_plug_ready",
        "real_use_gap": "software/report 有 meme_generator 管线，硬件契约提供摄像头帧入口；真实产品仍需要现场视频流、YOLO/姿态模型、字体/输出目录和端到端生成验证。",
        "evidence": [
            "src/software/report/meme_generator.py",
            "src/software/report/__init__.py",
        ],
    },
    {
        "id": "daily_diary",
        "feature": "猫咪每日日报",
        "status": "hardware_plug_ready",
        "real_use_gap": "已有会话报告、dashboard、daily diary、增强报告和运行时事件入口；真实产品仍需要全天事件数据库、定时任务和推送通道部署。",
        "evidence": [
            "src/app/daily_diary.py",
            "src/app/enhanced_report.py",
            "src/software/report/__init__.py",
            "tests/test_app_daily_diary.py",
        ],
    },
    {
        "id": "preference_model",
        "feature": "猫咪个性偏好",
        "status": "implemented",
        "real_use_gap": "已有 MemoryBox Beta-Bandit、session memory adapter、profile 和 personalization preview；真实可用还需要长期数据积累和跨天评估。",
        "evidence": [
            "src/software/memory/__init__.py",
            "src/app/session_memory.py",
            "src/app/cat_profile.py",
            "src/app/personalization_policy.py",
        ],
    },
    {
        "id": "remote_app_control",
        "feature": "远程 APP 控制",
        "status": "hardware_plug_ready",
        "real_use_gap": "已有机器人端 CLI 远程接管、安全 token 门控和 remote_command 硬件契约；真实产品仍需要移动端、视频流、MQTT/WebRTC 和权限体系部署。",
        "evidence": [
            "src/app/remote_takeover.py",
            "src/app/demo.py",
            "tests/test_app_remote_takeover.py",
        ],
    },
    {
        "id": "surprise_entropy",
        "feature": "惊喜熵引擎",
        "status": "hardware_plug_ready",
        "real_use_gap": "已有可解释候选动作评分、安全门控、偏好匹配、CLI preview 和 play actuator 执行契约；真实产品仍需要真实动作执行日志与效果反馈校准。",
        "evidence": [
            "src/app/surprise_entropy.py",
            "tests/test_app_surprise_entropy.py",
        ],
    },
]

READY_STATUSES = {"implemented", "mock_usable", "hardware_plug_ready"}
BLOCKING_STATUSES = {"missing"}


def build_prd_software_coverage() -> dict[str, Any]:
    features = [dict(feature) for feature in PRD_SOFTWARE_FEATURES]
    counts = {}
    blockers = []
    for feature in features:
        status = feature["status"]
        counts[status] = counts.get(status, 0) + 1
        if status in BLOCKING_STATUSES:
            blockers.append({
                "id": feature["id"],
                "feature": feature["feature"],
                "gap": feature["real_use_gap"],
            })
    return {
        "scope": "non_webui_software_prd_coverage",
        "features": features,
        "counts": counts,
        "blockers": blockers,
        "software_demo_ready": len(blockers) == 0,
        "hardware_plug_ready": len(blockers) == 0,
        "real_product_ready": False,
        "real_product_summary": "机器人端纯软件已达到 hardware-plug-ready；真实产品仍需要硬件实现、现场校准和长时间验证。",
    }


def build_onsite_demo_check(product_suite: dict[str, Any], intelligence_brief: dict[str, Any], entropy_preview: dict[str, Any]) -> dict[str, Any]:
    coverage = build_prd_software_coverage()
    consistency = _consistency_checks(product_suite, intelligence_brief, entropy_preview)
    software_demo_ready = len([check for check in consistency if not check["passed"]]) == 0
    blockers = [*coverage["blockers"], *[check for check in consistency if not check["passed"]]]
    return {
        "ready": coverage["real_product_ready"] and software_demo_ready,
        "software_abstraction_ready": len(blockers) == 0,
        "hardware_plug_ready": coverage["hardware_plug_ready"],
        "software_demo_ready": software_demo_ready,
        "real_product_ready": coverage["real_product_ready"],
        "coverage": coverage,
        "consistency_checks": consistency,
        "blockers": blockers,
        "demo_commands": [
            "python -m src.app.demo --software-mvp-acceptance",
            "python -m src.app.demo --software-intelligence-brief",
            "python -m src.app.demo --surprise-entropy-preview",
            "python -m src.app.demo --audio-emotion-preview",
            "python -m src.app.demo --treat-reward-preview",
            "python -m src.app.demo --hardware-plug-check",
            "python -m src.app.demo --remote-takeover-command stop --remote-token demo --remote-operator-token demo",
            "python -m src.app.demo --product-suite",
            "python -m src.app.demo --mode mock --scenario all --include-memory-update",
        ],
        "real_use_gap_summary": coverage["real_product_summary"],
    }


def _consistency_checks(product_suite: dict[str, Any], intelligence_brief: dict[str, Any], entropy_preview: dict[str, Any]) -> list[dict[str, Any]]:
    outcome_counts = product_suite.get("outcome_counts", {})
    strategy = intelligence_brief.get("strategy", {})
    entropy = intelligence_brief.get("surprise_entropy", {})
    selected = entropy.get("selected_action", {}) if isinstance(entropy, dict) else {}
    candidates = entropy_preview.get("candidates", [])
    return [
        _check("mock suite includes success scenario", outcome_counts.get("success", 0) >= 1),
        _check("mock suite includes error scenario", outcome_counts.get("error", 0) >= 1),
        _check("intelligence brief has entropy engine", "surprise_entropy_engine" in intelligence_brief.get("capabilities", [])),
        _check(
            "safe strategy does not select high intensity action",
            strategy.get("decision") not in {"safe_pause", "recovery_check"} or selected.get("intensity") != "high",
        ),
        _check("entropy preview uses recent action novelty", any(candidate.get("novelty") != 1.0 for candidate in candidates)),
        _check("representative report is not error-first", "看到了猫，并安全靠近到制动距离" in intelligence_brief.get("enhanced_report", {}).get("text", "")),
    ]


def _check(name: str, passed: bool) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed)}

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

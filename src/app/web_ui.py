from __future__ import annotations

import html
from typing import Any

from src.app.demo import run_product_demo_suite, run_software_mvp_acceptance


def build_web_ui_model() -> dict[str, Any]:
    product_suite = run_product_demo_suite(verbose=False)
    acceptance = run_software_mvp_acceptance(verbose=False)
    dashboard = product_suite["dashboard_preview"]
    sessions = [dict(artifact) for artifact in product_suite["artifacts"].values()]
    highlights = dashboard.get("highlights", [])
    for session, highlight in zip(sessions, highlights):
        session["highlight"] = highlight
    return {
        "title": "Hunter Software MVP",
        "subtitle": "无硬件软件闭环预览：mock 场景、产品日报、个性化推荐和硬件接入状态。",
        "dashboard": dashboard,
        "daily_diary": product_suite["daily_diary"],
        "personalization": product_suite["personalization_preview"],
        "acceptance": acceptance,
        "sessions": sessions,
    }


def render_web_ui_html(model: dict[str, Any]) -> str:
    dashboard = model["dashboard"]
    acceptance = model["acceptance"]
    diary = model["daily_diary"]
    personalization = model["personalization"]
    status_text = "Ready for hardware integration" if acceptance["ready_for_hardware_integration"] else "Not ready"
    scenario_buttons = _scenario_buttons(model["sessions"])
    timeline = _timeline(dashboard.get("state_timeline", []))
    trajectory = _trajectory_card(dashboard.get("trajectory", {}), dashboard.get("activity", {}))
    highlights = _highlight_cards(dashboard.get("highlights", []))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(model["title"])}</title>
  <style>{_CSS}</style>
</head>
<body>
  <main>
    <section class="hero">
      <p class="eyebrow">HUNTER WEB UI</p>
      <h1>{_e(model["title"])}</h1>
      <p>{_e(model["subtitle"])}</p>
      <div class="status">{status_text}</div>
    </section>
    <section class="card console">
      <h2>Scenario Console</h2>
      <p>用 mock 场景模拟真实交互结果，适合演示软件闭环。</p>
      <div class="scenario-buttons">{scenario_buttons}</div>
    </section>
    <section class="grid">
      <article class="card"><h2>State Timeline</h2>{timeline}</article>
      <article class="card"><h2>Trajectory</h2>{trajectory}</article>
    </section>
    <section class="card"><h2>Highlights</h2><div class="highlight-grid">{highlights}</div></section>
    <section class="grid">
      <article class="card">
        <h2>Dashboard</h2>
        <div class="metric"><span>{dashboard["total_sessions"]}</span><label>sessions</label></div>
        {_list("Outcomes", dashboard.get("outcome_counts", {}))}
        {_list("Commands", dashboard.get("command_totals", {}))}
      </article>
      <article class="card">
        <h2>Daily Diary</h2>
        <p class="diary">{_e(diary["text"])}</p>
        <small>mode: {_e(diary["mode"])}</small>
      </article>
      <article class="card">
        <h2>Personalization</h2>
        <div class="metric"><span>{_e(personalization["recommended_arm"])}</span><label>recommended arm</label></div>
        <p>{_e(personalization["summary"])}</p>
        <small>source: {_e(personalization["source"])}</small>
      </article>
      <article class="card">
        <h2>Acceptance</h2>
        <p>{status_text}</p>
        {_items(acceptance.get("remaining_for_real_mvp", []))}
      </article>
    </section>
    <section class="card sessions">
      <h2>Recent mock sessions</h2>
      <table>
        <thead><tr><th>Scenario</th><th>Outcome</th><th>Summary</th></tr></thead>
        <tbody>{_session_rows(model["sessions"])}</tbody>
      </table>
    </section>
  </main>
  <script>
    document.querySelectorAll('[data-scenario]').forEach((button) => {{
      button.addEventListener('click', () => {{
        document.querySelectorAll('[data-scenario]').forEach((item) => item.classList.remove('active'));
        button.classList.add('active');
      }});
    }});
  </script>
</body>
</html>"""


def run_web_ui_preview(verbose: bool = True) -> str:
    html_text = render_web_ui_html(build_web_ui_model())
    if verbose:
        print(html_text)
    return html_text


def _scenario_buttons(sessions: list[dict[str, Any]]) -> str:
    return "".join(
        f'<button type="button" data-scenario="{_e(session.get("scenario", "unknown"))}">{_e(session.get("scenario", "unknown"))}</button>'
        for session in sessions
    )


def _timeline(states: list[str]) -> str:
    return '<ol class="timeline">' + ''.join(f'<li>{_e(state)}</li>' for state in states) + '</ol>'


def _trajectory_card(trajectory: dict[str, Any], activity: dict[str, Any]) -> str:
    return (
        f'<div class="metric"><span>{_e(trajectory.get("total_path_length", 0))}</span><label>total path length</label></div>'
        f'<div class="metric"><span>{_e(activity.get("average_engagement_score", 0))}%</span><label>avg engagement</label></div>'
    )


def _highlight_cards(highlights: list[dict[str, Any]]) -> str:
    return "".join(
        '<article class="highlight" data-tone="{tone}"><h3>{title}</h3><p>{story}</p><small>{detail}</small></article>'.format(
            tone=_e(highlight.get("tone", "calm")),
            title=_e(highlight.get("title", "")),
            story=_e(highlight.get("story", "")),
            detail=_e(highlight.get("detail", "")),
        )
        for highlight in highlights
    )


def _session_rows(sessions: list[dict[str, Any]]) -> str:
    rows = []
    for artifact in sessions:
        rows.append(
            "<tr>"
            f"<td>{_e(artifact.get('scenario', 'unknown'))}</td>"
            f"<td>{_e(artifact.get('report', {}).get('outcome', 'unknown'))}</td>"
            f"<td>{_e(artifact.get('report', {}).get('title', ''))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _list(title: str, values: dict[str, Any]) -> str:
    body = "".join(f"<li><span>{_e(k)}</span><b>{_e(v)}</b></li>" for k, v in values.items()) or "<li><span>none</span><b>0</b></li>"
    return f"<h3>{_e(title)}</h3><ul>{body}</ul>"


def _items(values: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{_e(value)}</li>" for value in values) + "</ul>"


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


_CSS = """
:root { color-scheme: dark; --bg: #050712; --card: #101624; --line: #24314a; --text: #edf4ff; --muted: #8fa2c2; --cyan: #22d3ee; --green: #34d399; --amber: #fbbf24; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: radial-gradient(circle at top left, #12304a, var(--bg) 35%); color: var(--text); }
main { width: min(1180px, calc(100vw - 32px)); margin: 0 auto; padding: 40px 0; }
.hero { padding: 40px; border: 1px solid var(--line); background: rgba(16, 22, 36, 0.82); border-radius: 28px; box-shadow: 0 24px 80px rgba(0,0,0,.35); }
.eyebrow, small, label { color: var(--muted); letter-spacing: .14em; text-transform: uppercase; font-size: 12px; }
h1 { margin: 8px 0; font-size: clamp(40px, 6vw, 72px); line-height: 1; }
h2 { margin: 0 0 18px; }
h3 { margin: 22px 0 8px; color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: .12em; }
.status { display: inline-flex; margin-top: 18px; padding: 10px 14px; border-radius: 999px; background: rgba(52, 211, 153, .12); border: 1px solid rgba(52, 211, 153, .35); color: var(--green); font-weight: 700; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin: 18px 0; }
.card { padding: 24px; border: 1px solid var(--line); background: rgba(16, 22, 36, .78); border-radius: 22px; }
.console { margin: 18px 0; }
.scenario-buttons { display: flex; flex-wrap: wrap; gap: 12px; }
.scenario-buttons button { padding: 12px 16px; border-radius: 999px; border: 1px solid rgba(143, 162, 194, .28); background: rgba(9, 14, 24, .9); color: var(--text); cursor: pointer; }
.scenario-buttons button.active { border-color: rgba(34, 211, 238, .8); background: rgba(34, 211, 238, .16); }
.metric span { display: block; color: var(--cyan); font-size: 34px; font-weight: 800; }
ul, ol { list-style: none; padding: 0; margin: 0; }
li { display: flex; justify-content: space-between; gap: 16px; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,.06); }
.timeline li { justify-content: flex-start; }
.highlight-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.highlight { padding: 16px; border-radius: 18px; background: rgba(9, 14, 24, .72); border: 1px solid rgba(143, 162, 194, .2); }
.highlight[data-tone="success"] { border-color: rgba(52, 211, 153, .42); }
.highlight[data-tone="danger"] { border-color: rgba(248, 113, 113, .42); }
.highlight[data-tone="warning"] { border-color: rgba(251, 191, 36, .42); }
.highlight p { color: #d7e4f7; line-height: 1.7; }
.diary { white-space: pre-wrap; color: #d7e4f7; line-height: 1.8; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 12px; border-bottom: 1px solid rgba(255,255,255,.08); text-align: left; }
th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .12em; }
@media (max-width: 760px) { .grid, .highlight-grid { grid-template-columns: 1fr; } .hero { padding: 28px; } }
"""

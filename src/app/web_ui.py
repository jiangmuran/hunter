from __future__ import annotations

import html
from typing import Any

from src.app.demo import run_product_demo_suite, run_software_mvp_acceptance


def build_web_ui_model() -> dict[str, Any]:
    product_suite = run_product_demo_suite(verbose=False)
    acceptance = run_software_mvp_acceptance(verbose=False)
    dashboard = dict(product_suite["dashboard_preview"])
    sessions = []
    for artifact in product_suite["artifacts"].values():
        session = dict(artifact)
        session["dashboard_highlights"] = _build_session_highlights(session)
        sessions.append(session)
    dashboard["highlights"] = _build_dashboard_highlights(dashboard, sessions)
    for session in sessions:
        session["dashboard_highlights"] = dashboard["highlights"]
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
    sessions = model["sessions"]
    featured_session = _featured_session(sessions)
    status_text = "Ready for hardware integration" if acceptance["ready_for_hardware_integration"] else "Not ready"
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
      <div class="hero-actions">
        <div class="status">{status_text}</div>
        <div class="hero-chip">{_e(dashboard["milestone"]["headline"])}</div>
      </div>
    </section>
    <section class="grid overview-grid">
      <article class="card">
        <h2>Dashboard</h2>
        <div class="metric"><span>{dashboard["total_sessions"]}</span><label>sessions</label></div>
        {_list("Outcomes", dashboard.get("outcome_counts", {}))}
        {_list("Commands", dashboard.get("command_totals", {}))}
      </article>
      <article class="card highlights-card">
        <h2>Highlights</h2>
        <p class="section-copy">从 mock 数据中抽取的产品亮点，可直接映射到演示讲解口径。</p>
        {_bullets(dashboard.get("highlights", []), class_name="highlights-list")}
      </article>
      <article class="card">
        <h2>Daily Diary</h2>
        <p class="diary">{_e(diary["text"])}</p>
        <small>mode: {_e(diary["mode"])} </small>
      </article>
      <article class="card">
        <h2>Personalization</h2>
        <div class="metric"><span>{_e(personalization["recommended_arm"])}</span><label>recommended arm</label></div>
        <p>{_e(personalization["summary"])}</p>
        <small>source: {_e(personalization["source"])} </small>
      </article>
      <article class="card acceptance-card">
        <h2>Acceptance</h2>
        <p>{status_text}</p>
        {_bullets(acceptance.get("remaining_for_real_mvp", []))}
      </article>
    </section>
    <section class="card interactive-console">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Interactive Preview</p>
          <h2>Scenario Console</h2>
        </div>
        <p class="section-copy">完全由服务端渲染，按钮仅使用内联脚本切换 active 状态，保持当前静态 HTML 风格。</p>
      </div>
      <div class="scenario-button-row">{_scenario_buttons(sessions, featured_session)}</div>
      <div class="interactive-grid">
        <article class="console-panel">
          <h3>State Timeline</h3>
          {_timeline(featured_session)}
        </article>
        <article class="console-panel trajectory-panel">
          <h3>Trajectory</h3>
          {_trajectory(featured_session)}
        </article>
        <article class="console-panel">
          <h3>Highlights</h3>
          {_bullets(featured_session.get("dashboard_highlights", []), class_name="highlights-list")}
          <div class="session-note">
            <strong>{_e(featured_session.get("report", {}).get("title", ""))}</strong>
            <p>{_e(featured_session.get("summary", ""))}</p>
          </div>
        </article>
      </div>
    </section>
    <section class="card sessions">
      <h2>Recent mock sessions</h2>
      <table>
        <thead><tr><th>Scenario</th><th>Outcome</th><th>Summary</th></tr></thead>
        <tbody>{_session_rows(sessions)}</tbody>
      </table>
    </section>
  </main>
  <script>{_SCRIPT}</script>
</body>
</html>"""


def run_web_ui_preview(verbose: bool = True) -> str:
    html_text = render_web_ui_html(build_web_ui_model())
    if verbose:
        print(html_text)
    return html_text


def _featured_session(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    for session in sessions:
        if session.get("scenario") == "approach":
            return session
    return sessions[0] if sessions else {}


def _build_dashboard_highlights(dashboard: dict[str, Any], sessions: list[dict[str, Any]]) -> list[str]:
    milestone = dashboard.get("milestone", {})
    latest = dashboard.get("latest_session", {})
    return [
        milestone.get("headline", "无硬件 MVP 已形成演示闭环。"),
        f"覆盖 {dashboard.get('total_sessions', 0)} 个 mock session，包含 {len(dashboard.get('outcome_counts', {}))} 类结果。",
        f"最近一次场景为 {latest.get('scenario', 'unknown')}，结论：{latest.get('title', '已生成摘要')}。",
        f"当前可展示 {len(sessions)} 条状态轨迹与命令回放。",
    ]


def _build_session_highlights(session: dict[str, Any]) -> list[str]:
    report = session.get("report", {})
    states = session.get("states", [])
    return [
        f"{session.get('scenario', 'unknown')} 场景结果：{report.get('outcome', 'unknown')}。",
        f"状态推进：{' → '.join(_state_names(states[:4])) or '暂无状态数据'}。",
        f"报告标题：{report.get('title', session.get('summary', ''))}",
    ]


def _state_names(states: list[dict[str, Any]]) -> list[str]:
    return [str(state.get("state", "unknown")) for state in states]


def _scenario_buttons(sessions: list[dict[str, Any]], active_session: dict[str, Any]) -> str:
    buttons = []
    active_id = active_session.get("id")
    for session in sessions:
        class_name = "scenario-button is-active" if session.get("id") == active_id else "scenario-button"
        buttons.append(
            f"<button type=\"button\" class=\"{class_name}\" data-scenario-button data-session-id=\"{_e(session.get('id', ''))}\">"
            f"<span>{_e(session.get('scenario', 'unknown'))}</span>"
            f"<small>{_e(session.get('report', {}).get('outcome', 'unknown'))}</small>"
            "</button>"
        )
    return "".join(buttons)


def _timeline(session: dict[str, Any]) -> str:
    items = []
    for event in session.get("events", [])[:6]:
        items.append(
            "<li>"
            f"<span class=\"timeline-tick\">T{_e(event.get('tick', '?'))}</span>"
            f"<strong>{_e(event.get('kind', 'event'))}</strong>"
            f"<p>{_e(event.get('message', ''))}</p>"
            "</li>"
        )
    return f"<ol class=\"timeline\">{''.join(items)}</ol>"


def _trajectory(session: dict[str, Any]) -> str:
    items = []
    for index, state in enumerate(session.get("states", [])[:5], start=1):
        target = state.get("target") or {}
        items.append(
            "<li>"
            f"<span class=\"trajectory-step\">{index:02d}</span>"
            f"<div><strong>{_e(state.get('state', 'unknown'))}</strong><p>offset {target.get('center_offset_x', 'n/a')} · size {target.get('size_ratio', 'n/a')}</p></div>"
            "</li>"
        )
    return f"<ul class=\"trajectory\">{''.join(items)}</ul>"


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


def _bullets(values: list[str], class_name: str = "") -> str:
    class_attr = f' class="{class_name}"' if class_name else ""
    return f"<ul{class_attr}>" + "".join(f"<li>{_e(value)}</li>" for value in values) + "</ul>"


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


_CSS = """
:root { color-scheme: dark; --bg: #050712; --card: #101624; --line: #24314a; --text: #edf4ff; --muted: #8fa2c2; --cyan: #22d3ee; --green: #34d399; --amber: #fbbf24; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; background: radial-gradient(circle at top left, #12304a, var(--bg) 35%); color: var(--text); }
main { width: min(1180px, calc(100vw - 32px)); margin: 0 auto; padding: 40px 0 72px; }
.hero { padding: 40px; border: 1px solid var(--line); background: rgba(16, 22, 36, 0.82); border-radius: 28px; box-shadow: 0 24px 80px rgba(0,0,0,.35); }
.hero-actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 18px; }
.hero-chip { display: inline-flex; align-items: center; padding: 10px 14px; border-radius: 999px; border: 1px solid rgba(34, 211, 238, .25); background: rgba(34, 211, 238, .08); color: #c7f7ff; }
.eyebrow, small, label { color: var(--muted); letter-spacing: .14em; text-transform: uppercase; font-size: 12px; }
h1 { margin: 8px 0; font-size: clamp(40px, 6vw, 72px); line-height: 1; }
h2 { margin: 0 0 18px; }
h3 { margin: 0 0 14px; color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: .12em; }
.status { display: inline-flex; padding: 10px 14px; border-radius: 999px; background: rgba(52, 211, 153, .12); border: 1px solid rgba(52, 211, 153, .35); color: var(--green); font-weight: 700; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin: 18px 0; }
.overview-grid { align-items: start; }
.card, .console-panel { padding: 24px; border: 1px solid var(--line); background: rgba(16, 22, 36, .78); border-radius: 22px; }
.metric span { display: block; color: var(--cyan); font-size: 34px; font-weight: 800; }
ul, ol { list-style: none; padding: 0; margin: 0; }
li { display: flex; justify-content: space-between; gap: 16px; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,.06); }
.diary { white-space: pre-wrap; color: #d7e4f7; line-height: 1.8; }
.section-heading { display: flex; justify-content: space-between; gap: 20px; align-items: end; margin-bottom: 20px; }
.section-copy { margin: 0; color: #c4d2e7; max-width: 520px; line-height: 1.7; }
.highlights-list li { display: block; padding-left: 18px; position: relative; }
.highlights-list li::before { content: \"•\"; position: absolute; left: 0; color: var(--amber); }
.interactive-console { margin-bottom: 18px; }
.scenario-button-row { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 18px; }
.scenario-button { min-width: 160px; padding: 14px 16px; border-radius: 18px; border: 1px solid rgba(143, 162, 194, .28); background: rgba(9, 14, 24, .9); color: var(--text); text-align: left; cursor: pointer; }
.scenario-button span, .scenario-button small { display: block; }
.scenario-button.is-active { border-color: rgba(34, 211, 238, .8); box-shadow: 0 0 0 1px rgba(34, 211, 238, .3) inset; background: linear-gradient(180deg, rgba(34, 211, 238, .14), rgba(9, 14, 24, .94)); }
.interactive-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.timeline li, .trajectory li { display: flex; justify-content: flex-start; align-items: flex-start; gap: 14px; }
.timeline-tick, .trajectory-step { min-width: 44px; padding: 6px 10px; border-radius: 999px; background: rgba(34, 211, 238, .1); color: var(--cyan); font-size: 12px; text-align: center; }
.timeline p, .trajectory p, .session-note p { margin: 6px 0 0; color: #c4d2e7; line-height: 1.6; }
.session-note { margin-top: 18px; padding-top: 18px; border-top: 1px solid rgba(255,255,255,.08); }
.acceptance-card { grid-column: span 2; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 12px; border-bottom: 1px solid rgba(255,255,255,.08); text-align: left; }
th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .12em; }
@media (max-width: 980px) { .interactive-grid { grid-template-columns: 1fr; } .acceptance-card { grid-column: span 1; } }
@media (max-width: 760px) { .grid { grid-template-columns: 1fr; } .hero { padding: 28px; } .section-heading { flex-direction: column; align-items: flex-start; } }
"""


_SCRIPT = """
const scenarioButtons = document.querySelectorAll('[data-scenario-button]');
scenarioButtons.forEach((button) => {
  button.addEventListener('click', () => {
    scenarioButtons.forEach((candidate) => candidate.classList.remove('is-active'));
    button.classList.add('is-active');
  });
});
"""

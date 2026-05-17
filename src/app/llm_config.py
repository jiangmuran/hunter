from __future__ import annotations

import os
from typing import Any, Mapping


def load_llm_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = env or os.environ
    provider = source.get("HUNTER_LLM_PROVIDER", "none").strip() or "none"
    model = source.get("HUNTER_LLM_MODEL")
    api_key_env = source.get("HUNTER_LLM_API_KEY_ENV")
    api_key_configured = bool(api_key_env and source.get(api_key_env))
    return {
        "enabled": provider != "none" and api_key_configured,
        "provider": provider,
        "model": model,
        "api_key_env": api_key_env,
        "api_key_configured": api_key_configured,
    }


def describe_llm_config(config: dict[str, Any] | None = None) -> str:
    current = config or load_llm_config()
    if current["enabled"]:
        state = f"当前 LLM 已启用：provider={current['provider']}，model={current['model'] or '未指定'}。"
    else:
        state = "当前 LLM 未启用；daily diary 会使用本地模板输出。"
    return "\n".join([
        state,
        "配置位置是环境变量，不把密钥写进仓库：",
        "- HUNTER_LLM_PROVIDER：例如 anthropic，未设置时为 none。",
        "- HUNTER_LLM_MODEL：例如 claude-sonnet-4-6。",
        "- HUNTER_LLM_API_KEY_ENV：保存真实 key 的环境变量名，例如 ANTHROPIC_API_KEY。",
        "- ANTHROPIC_API_KEY：真实密钥放在本机 shell/部署环境里。",
        "代码入口仍是 daily_diary 的 llm_fn(prompt) hook；配置模块只负责告诉运行层该用哪个 provider/model/key。",
    ])

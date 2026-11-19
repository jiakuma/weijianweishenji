"""DeepSeek-V4.1-Flash 官方 API 统一适配层。

官方接入方式（2026-11 核实，见 https://api-docs.deepseek.com/）：
    base_url : https://api.deepseek.com          # OpenAI 兼容，官方明确不需要 /v1
    endpoint : {base_url}/chat/completions
    model    : deepseek-flash                    # 当前由 DeepSeek-V4.1-Flash-1017 提供服务
    auth     : Authorization: Bearer <DeepSeek 平台 Key>

全项目的大模型调用（问答 / Agent / 自动分类 / 检索评测 / 制度互斥 / 连通性测试）
统一从本模块取配置与端点。换模型或换供应商时，只改 OFFICIAL_* 常量与系统设置，
不需要逐个业务模块改代码。

两个刻意不做的事（踩坑记录）：
1. 不全局开启 response_format={"type":"json_object"}：官方 JSON mode 只保证根节点是
   「对象」，而制度互斥的条款抽取 / 预筛提示词要求输出「数组」，开启会直接破坏解析。
   继续沿用提示词约束 + 各模块已有的容错解析（_extract_json / _parse_items 等）。
2. 思考模式必须显式声明：V4.1-Flash 即使不传 thinking 也默认开启推理，token 先被
   reasoning_content 消耗，max_tokens 小的请求（如自动分类 40）会返回空 content。
   因此结构化任务（条款抽取、比对、分类、评测）一律显式传 {"type":"disabled"}；
   仅知识问答按系统设置里的 llmThinking 开关启用 {"type":"enabled"}。

兼容：填写其它网关地址（如内网 LiteLLM 的 /litellm/v1）时保留原路径，
官方 host 才会自动去掉多余的 /v1，因此可以随时在设置页改回原代理。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .db import SessionLocal, get_raw_settings

# ------------------------------------------------------------------ 官方常量

OFFICIAL_BASE_URL = "https://api.deepseek.com"
OFFICIAL_MODEL = "deepseek-flash"       # DeepSeek-V4.1-Flash-1017
OFFICIAL_MODEL_PRO = "deepseek-v4-pro"  # 需要更强推理时可在设置页手动切换
LEGACY_BASE_URL = "https://dmxwg.intra.yiducloud.cn/litellm/v1"  # 原内网代理，仅作回退备忘

_OFFICIAL_HOSTS = {"api.deepseek.com"}
_RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class LLMConfig:
    base: str
    key: str
    model: str
    temperature: float
    max_tokens: int
    thinking: bool

    @property
    def ready(self) -> bool:
        """三项齐全才算可用；Key 为空时各模块按原逻辑降级。"""
        return bool(self.base and self.key and self.model)


# ------------------------------------------------------------------ 配置读取

def normalize_base_url(base: str | None) -> str:
    """规范化成「不含 /chat/completions」的 base，供各调用点统一拼接端点。

    - 官方地址去掉多余的 /v1：https://api.deepseek.com/v1 -> https://api.deepseek.com
    - 其它网关保留原路径（内网 LiteLLM 依赖 /litellm/v1）
    - 允许直接粘贴完整端点 https://api.deepseek.com/chat/completions
    """
    value = str(base or "").strip().rstrip("/")
    if not value:
        return OFFICIAL_BASE_URL
    if value.endswith("/chat/completions"):
        value = value[: -len("/chat/completions")].rstrip("/")
    host = (urlparse(value).hostname or "").lower()
    if host in _OFFICIAL_HOSTS and value.endswith("/v1"):
        value = value[: -len("/v1")].rstrip("/")
    return value


def _env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def llm_config(session=None) -> LLMConfig:
    """配置优先级：系统设置(DB) > 环境变量 > 官方默认值。

    Key 不写进代码也不写进默认配置：可在系统设置填写，或用
    LLM_API_KEY / DEEPSEEK_API_KEY 环境变量注入（后者是官方惯例变量名）。
    """
    if session is None:
        with SessionLocal() as s:
            st = get_raw_settings(s)
    else:
        st = get_raw_settings(session)

    base = normalize_base_url(
        str(st.get("llmBaseUrl") or "").strip()
        or _env("LLM_BASE_URL", "DEEPSEEK_BASE_URL")
        or OFFICIAL_BASE_URL
    )
    key = str(st.get("llmApiKey") or "").strip() or _env("LLM_API_KEY", "DEEPSEEK_API_KEY")
    model = str(st.get("llmModel") or "").strip() or _env("LLM_MODEL") or OFFICIAL_MODEL
    try:
        temperature = float(st.get("temperature", 0.2) or 0.2)
    except (TypeError, ValueError):
        temperature = 0.2
    try:
        max_tokens = int(st.get("maxTokens", 2048) or 2048)
    except (TypeError, ValueError):
        max_tokens = 2048
    return LLMConfig(
        base=base,
        key=key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        thinking=_as_bool(st.get("llmThinking", False)),
    )


# ------------------------------------------------------------------ 请求构造

def chat_url(base: str | None) -> str:
    """由 base 得到 /chat/completions 完整端点，避免重复拼接。"""
    value = normalize_base_url(base)
    return value if value.endswith("/chat/completions") else f"{value}/chat/completions"


def is_official(base: str | None) -> bool:
    """是否指向 DeepSeek 官方端点（决定能否安全注入官方专属参数）。"""
    return (urlparse(normalize_base_url(base)).hostname or "").lower() in _OFFICIAL_HOSTS


def auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def apply_official_params(payload: dict, thinking: bool = False, official: bool = True) -> dict:
    """按官方规范整理请求体。

    - thinking=True  -> 显式启用思考（知识问答可选）
    - thinking=False -> 官方端点显式 disabled，避免默认推理吃光 token 预算；
                        非官方网关只移除该字段，保证回退代理时不被未知参数打挂
    - 移除官方忽略的 frequency_penalty / presence_penalty
    """
    body = dict(payload)
    if thinking:
        body["thinking"] = {"type": "enabled"}
        body.setdefault("reasoning_effort", "medium")
    elif official:
        body["thinking"] = {"type": "disabled"}
        body.pop("reasoning_effort", None)
    else:
        body.pop("thinking", None)
        body.pop("reasoning_effort", None)
    body.pop("frequency_penalty", None)
    body.pop("presence_penalty", None)
    return body


def post_chat(payload: dict, timeout: float = 30.0, *, config: LLMConfig | None = None,
              thinking: bool = False, retries: int = 2, client: httpx.Client | None = None) -> dict:
    """统一发起 /chat/completions，带 429 / 5xx / 网络抖动退避重试。

    业务模块原有 `except Exception -> 降级` 逻辑保持不变；这里把 HTTP 错误统一成
    RuntimeError，并在消息里带上状态码，方便「测试模型连接」给出可操作的提示。
    client 可传入调用方自持的连接池（制度互斥并发解析场景复用连接）。
    """
    cfg = config or llm_config()
    if not cfg.ready:
        raise RuntimeError("未配置大模型 API Key，请在系统设置填写，或设置 DEEPSEEK_API_KEY 环境变量")
    body = apply_official_params(payload, thinking, official=is_official(cfg.base))
    url = chat_url(cfg.base)
    poster = client.post if client is not None else httpx.post
    last: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            resp = poster(url, headers=auth_headers(cfg.key), json=body, timeout=timeout)
            if resp.status_code in _RETRY_STATUS and attempt < retries:
                time.sleep(min(2 ** attempt, 4))
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}：{(resp.text or '')[:200]}")
            return resp.json()
        except httpx.TimeoutException as exc:
            last = exc
        except httpx.NetworkError as exc:
            last = exc
        if attempt < retries:
            time.sleep(min(2 ** attempt, 4))
        else:
            break
    raise RuntimeError(f"大模型请求失败：{type(last).__name__ if last else 'HTTPError'} {str(last)[:160]}")

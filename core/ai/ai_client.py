# -*- coding: utf-8 -*-
"""
AI 服务客户端
AI service client.
封装对内网 LLM API（OpenAI 兼容接口）的调用。

配置优先级（高→低）：
  1. 构造函数显式传参
  2. user_data/ai_config.json（用户在 GUI 中保存的配置）
  3. 环境变量（CHECKLISTTOOL_AI_*）
  4. 硬编码默认值

支持超时、重试、结构化 JSON 输出。
"""

import json
import os
import re
import time
from typing import Optional, Dict, Any, List

import requests

# ------------------------------------------------------------------
# 默认值（最低优先级）
# ------------------------------------------------------------------
try:
    from config.app_config import (
        AI_API_BASE_URL as _ENV_BASE_URL,
        AI_API_KEY as _ENV_API_KEY,
        AI_MODEL_NAME as _ENV_MODEL,
        AI_TIMEOUT_SECONDS as _ENV_TIMEOUT,
        AI_MAX_RETRIES as _ENV_MAX_RETRIES,
        AI_MAX_TOKENS as _ENV_MAX_TOKENS,
        AI_REASONING_EFFORT as _ENV_REASONING_EFFORT,
        USER_DATA_DIR,
    )
except ImportError:
    _ENV_BASE_URL = "http://127.0.0.1:8000/v1"
    _ENV_API_KEY = ""
    _ENV_MODEL = "default"
    _ENV_TIMEOUT = 30
    _ENV_MAX_RETRIES = 2
    _ENV_MAX_TOKENS = 2048
    _ENV_REASONING_EFFORT = None
    # 回退路径与 config.app_config 保持一致：冻结后以 EXE 目录为根
    if getattr(sys, "frozen", False):
        _root = os.path.dirname(os.path.abspath(sys.executable))
    else:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    USER_DATA_DIR = os.path.join(_root, "user_data")

AI_CONFIG_FILE = os.path.join(USER_DATA_DIR, "ai_config.json")

# 硬编码默认值
_HARD_DEFAULTS = {
    "base_url": "http://127.0.0.1:8000/v1",
    "api_key": "",
    "model": "default",
    "timeout": 30,
    "max_retries": 2,
    "max_tokens": 2048,
}


class AIClientError(Exception):
    """AI 服务调用失败。"""


class AIClient:
    """
    内网 LLM API 客户端（OpenAI chat/completions 兼容协议）。

    用法:
        client = AIClient()
        reply = client.chat([
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ])
        # 或期望返回 JSON：
        obj = client.chat_json([...])
    """

    # ------------------------------------------------------------------
    # 静态：配置文件读写
    # ------------------------------------------------------------------
    @staticmethod
    def load_config() -> Dict[str, Any]:
        """
        从 user_data/ai_config.json 读取用户保存的配置。
        文件不存在或损坏时返回空 dict。
        """
        try:
            if os.path.isfile(AI_CONFIG_FILE):
                with open(AI_CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
        return {}

    @staticmethod
    def save_config(config: Dict[str, Any]) -> bool:
        """
        将配置保存到 user_data/ai_config.json。
        返回 True 表示成功。
        """
        try:
            os.makedirs(os.path.dirname(AI_CONFIG_FILE) or ".", exist_ok=True)
            with open(AI_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            return True
        except (IOError, TypeError):
            return False

    @staticmethod
    def has_saved_config() -> bool:
        """检查是否存在用户保存的 AI 配置。"""
        cfg = AIClient.load_config()
        return bool(cfg.get("base_url") or cfg.get("api_key"))

    # ------------------------------------------------------------------
    # 实例
    # ------------------------------------------------------------------
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        max_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ):
        """
        参数显式传入时优先级最高；
        否则依次读取：JSON 配置文件 → 环境变量 → 硬编码默认值。
        """
        # 按优先级解析各项
        self.base_url = (
            base_url or self._resolve("base_url", _ENV_BASE_URL)
        ).rstrip("/")
        self.api_key = api_key or self._resolve("api_key", _ENV_API_KEY)
        self.model = model or self._resolve("model", _ENV_MODEL)
        self.timeout = timeout if timeout is not None else int(
            self._resolve("timeout", str(_ENV_TIMEOUT))
        )
        self.max_retries = max_retries if max_retries is not None else int(
            self._resolve("max_retries", str(_ENV_MAX_RETRIES))
        )
        self.max_tokens = max_tokens if max_tokens is not None else int(
            self._resolve("max_tokens", str(_ENV_MAX_TOKENS))
        )
        # reasoning_effort: None 表示不传此参数（让模型自行决定）
        _env_re = _ENV_REASONING_EFFORT or ""
        _re_val = reasoning_effort if reasoning_effort is not None else self._resolve("reasoning_effort", _env_re)
        self.reasoning_effort = _re_val if _re_val else None

    @staticmethod
    def _resolve(key: str, env_value: str) -> str:
        """
        配置解析链：JSON 配置文件 → 环境变量 → 硬编码默认值。

        返回值始终为字符串；调用方负责类型转换。
        """
        # 1) JSON 配置文件
        saved = AIClient.load_config()
        if key in saved and saved[key] is not None and str(saved[key]).strip() != "":
            return str(saved[key]).strip()

        # 2) 环境变量
        if env_value and str(env_value).strip() != "":
            return str(env_value).strip()

        # 3) 硬编码默认值
        return str(_HARD_DEFAULTS.get(key, ""))

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------
    def _build_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _build_payload(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2048,
        response_format: Optional[Dict[str, str]] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format
        # "none" 表示用户想关闭推理。部分服务（DeepSeek 等）不接受此值，
        # 传了会直接 400。跳过该参数的效果等同于不启用推理。
        if reasoning_effort and reasoning_effort != "none":
            payload["reasoning_effort"] = reasoning_effort
        return payload

    def _request(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2048,
        response_format: Optional[Dict[str, str]] = None,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        """
        发送请求并返回模型文本回复。失败时按重试次数自动重试。
        """
        # None 表示使用实例默认配置
        if reasoning_effort is None:
            reasoning_effort = self.reasoning_effort
        url = f"{self.base_url}/chat/completions"
        payload = self._build_payload(
            messages, temperature, max_tokens, response_format, reasoning_effort
        )
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(
                    url,
                    headers=self._build_headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    body = resp.json()
                    choices = body.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        content = (msg.get("content") or "").strip()
                        if content:
                            return content
                        # content 为空：检查是否是 thinking 模型 token 不足
                        # DeepSeek 用 reasoning_content，其他服务可能用 reasoning
                        reasoning = (
                            msg.get("reasoning_content") or
                            msg.get("reasoning") or ""
                        ).strip()
                        finish_reason = choices[0].get("finish_reason", "")
                        usage_info = body.get("usage", {})
                        completion_tokens = usage_info.get("completion_tokens", 0)
                        if reasoning and finish_reason == "length":
                            raise AIClientError(
                                f"Thinking 模型思考过程消耗了全部 {completion_tokens} 个 token，"
                                f"未能生成回复内容。\n"
                                f"当前 max_tokens 设置为 {max_tokens}，不足以容纳思考+回复。\n"
                                f"建议：在 AI 配置中增大 max_tokens（如 8192~16384），"
                                f"或关闭推理力度以避免思考消耗 token。\n"
                                f"思考内容摘要：{reasoning[:200]}..."
                            )
                        elif reasoning:
                            raise AIClientError(
                                f"Thinking 模型返回了思考内容但回复为空。\n"
                                f"finish_reason={finish_reason}，completion_tokens={completion_tokens}\n"
                                f"思考内容摘要：{reasoning[:200]}..."
                            )
                    # HTTP 200 但没有 choices 或 content 为空 → 记录原始响应以便调试
                    snippet = resp.text[:600]
                    raise AIClientError(
                        f"AI 服务返回 200 但无有效内容。\n"
                        f"请求 URL：{url}\n"
                        f"原始响应：{snippet}"
                    )
                # 非 200：尝试提取 JSON 中的 message，否则截取文本
                detail = resp.text[:800]
                try:
                    import json as _json
                    err_body = _json.loads(detail)
                    err_msg = err_body.get("error", {}).get("message", "") or str(err_body)
                    detail = err_msg[:300]
                except Exception:
                    detail = detail[:300]
                last_error = AIClientError(
                    f"AI 服务返回错误 (HTTP {resp.status_code})\n{detail}"
                )
            except requests.exceptions.Timeout as e:
                last_error = AIClientError(f"AI API 超时 ({self.timeout}s)")
            except requests.exceptions.ConnectionError as e:
                last_error = AIClientError(f"AI API 连接失败: {e}")
            except requests.exceptions.RequestException as e:
                last_error = AIClientError(f"AI API 请求异常: {e}")

            if attempt < self.max_retries:
                time.sleep(1.0 * (attempt + 1))

        raise last_error or AIClientError("AI API 未知错误")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        """
        普通对话：返回模型文本回复。
        max_tokens=None 时使用实例配置的默认值。
        """
        if max_tokens is None:
            max_tokens = self.max_tokens
        return self._request(messages, temperature=temperature, max_tokens=max_tokens,
                             reasoning_effort=reasoning_effort)

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        expect_json: bool = True,
        reasoning_effort: Optional[str] = None,
    ) -> Any:
        """
        期望返回 JSON 对象。

        参数:
            expect_json: True 时优先使用 response_format={"type": "json_object"}
                         （需要模型支持）；同时会在 prompt 中追加 JSON 输出指令。
            max_tokens: None 时使用实例配置的默认值。
            reasoning_effort: 覆盖实例配置。None 时使用实例默认值。
        返回:
            解析后的 dict/list；解析失败返回 None。
        """
        if max_tokens is None:
            max_tokens = self.max_tokens
        rf = {"type": "json_object"} if expect_json else None
        msgs = list(messages)
        if msgs and msgs[-1].get("role") == "user":
            msgs[-1] = dict(msgs[-1])
            msgs[-1]["content"] = (
                str(msgs[-1]["content"])
                + "\n\n请**只输出**合法的 JSON，不要包含 markdown 代码块标记或其他文字。"
            )

        text = self._request(
            msgs, temperature=temperature, max_tokens=max_tokens, response_format=rf,
            reasoning_effort=reasoning_effort
        )

        if not text:
            return None

        text = text.strip()

        # ── 容错 1：用正则提取 markdown 代码块中的 JSON ──
        # 匹配 ```json ... ``` 或 ``` ... ``` 代码块
        fence_patterns = [
            r"```(?:json)?\s*\n?(.*?)\n?```",   # ```json\n...\n```
        ]
        for pat in fence_patterns:
            m = re.search(pat, text, re.DOTALL)
            if m:
                text = m.group(1).strip()
                break

        # ── 容错 2：如果仍有前缀/后缀干扰，尝试定位 JSON 边界 ──
        if not text.startswith("{") and not text.startswith("["):
            # 寻找第一个 { 或 [，截取到最后一个 } 或 ]
            for opener, closer in (("{", "}"), ("[", "]")):
                start = text.find(opener)
                end = text.rfind(closer)
                if start != -1 and end > start:
                    text = text[start:end + 1]
                    break

        # ── 容错 3：去除残留的 markdown 标记 ──
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def check_connectivity(self) -> bool:
        """
        检测 AI 服务是否可达：发送一条轻量聊天请求，确保模型能正常加载和响应。
        thinking 模型（如 deepseek-v4-pro、qwen3.5）需要大量 token 用于思考过程，
        这里给足 token（4096），保证思考+简短回复都有空间。
        """
        try:
            self._request(
                [{"role": "user", "content": "hi"}],
                temperature=0,
                max_tokens=4096,
            )
            return True
        except AIClientError:
            return False
        except requests.exceptions.RequestException:
            return False


# ------------------------------------------------------------------
# 模块级便捷函数（供 feature 页直接使用）
# ------------------------------------------------------------------
_default_client: Optional[AIClient] = None


def get_client(refresh: bool = False) -> AIClient:
    """
    获取或创建默认 AI 客户端（单例）。

    参数:
        refresh: True 时丢弃缓存的客户端，重新按当前配置创建。
                 用于用户在 AI 配置页保存新配置后刷新。
    """
    global _default_client
    if refresh or _default_client is None:
        _default_client = AIClient()
    return _default_client


def invalidate_client() -> None:
    """使缓存的客户端失效，下次 get_client() 将重建。"""
    global _default_client
    _default_client = None

"""OpenAI 兼容 AI 客户端（流式 / 非流式），httpx 实现

对齐 PHP 版 AiClient 的行为：默认 Provider/Model、SSE 解析、usage 提取、日志落库。
"""
import json
import time

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.helpers import now
from app.models import AiLog, AiModel, AiProvider, SystemConfig


def config_value(db: Session, key: str, default):
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if not row or row.value is None or str(row.value) == "":
        return default
    return str(row.value)


def default_target(db: Session) -> tuple[AiProvider, AiModel] | None:
    provider = (
        db.query(AiProvider)
        .filter(AiProvider.status == 1)
        .order_by(AiProvider.is_default.desc(), AiProvider.id.asc())
        .first()
    )
    model = (
        db.query(AiModel)
        .filter(AiModel.status == 1)
        .order_by(AiModel.is_default.desc(), AiModel.id.asc())
        .first()
    )
    if not provider or not model:
        return None
    return provider, model


def endpoint(provider: AiProvider) -> str:
    base = (provider.base_url or "").rstrip("/")
    if not base:
        raise RuntimeError("AI Provider base_url 为空")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def log_call(db: Session, task_type: str, provider: AiProvider, model: AiModel,
             usage: dict, duration_ms: int, status: str, error: str = "") -> None:
    row = AiLog(
        provider=provider.name,
        model=model.name,
        task_type=task_type,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        duration=duration_ms,
        status=status,
        error_message=error[:900],
        created_at=now(),
    )
    db.add(row)
    db.commit()


def _headers(provider: AiProvider) -> dict:
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = "Bearer " + provider.api_key
    return headers


class AiClient:
    def __init__(self, db: Session):
        self.db = db

    def _resolve(self):
        target = default_target(self.db)
        if not target:
            raise RuntimeError("没有可用的 AI Provider/Model，请先在 AI 配置中添加并启用")
        return target

    def _build_payload(self, messages: list[dict], model: AiModel, options: dict, stream: bool) -> dict:
        temperature = options.get("temperature")
        if temperature is None:
            temperature = model.temperature
            if temperature is None:
                try:
                    temperature = float(config_value(self.db, "ai_temperature", settings.ai_temperature))
                except (TypeError, ValueError):
                    temperature = settings.ai_temperature
        payload = {
            "model": model.name,
            "messages": messages,
            "temperature": float(temperature),
            "stream": stream,
        }
        if options.get("max_tokens"):
            payload["max_tokens"] = int(options["max_tokens"])
        return payload

    def chat(self, messages: list[dict], options: dict | None = None) -> dict:
        """非流式对话 → {text, usage, provider, model, duration}"""
        options = options or {}
        provider, model = self._resolve()
        task_type = str(options.get("task_type", "chat"))
        timeout = int(options.get("timeout") or 0) or int(
            float(config_value(self.db, "ai_http_timeout", settings.ai_http_timeout))
        )

        payload = self._build_payload(messages, model, options, stream=False)
        start = time.time()
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=20), follow_redirects=True) as client:
                resp = client.post(endpoint(provider), json=payload, headers=_headers(provider))
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            log_call(self.db, task_type, provider, model, {}, duration, "failed", str(e))
            raise RuntimeError(f"AI 请求失败: {e}") from e
        duration = int((time.time() - start) * 1000)

        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code != 200 or not isinstance(body, dict) or "choices" not in body:
            err = (body or {}).get("error", {}).get("message", "") if isinstance(body, dict) else ""
            err = err or f"HTTP {resp.status_code}"
            log_call(self.db, task_type, provider, model, {}, duration, "failed", str(err))
            raise RuntimeError(f"AI 调用失败: {err}")

        text = str(body["choices"][0]["message"]["content"])
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        log_call(self.db, task_type, provider, model, usage, duration, "success")
        return {
            "text": text,
            "usage": usage,
            "provider": provider.name,
            "model": model.name,
            "duration": duration,
        }

    def chat_stream(self, messages: list[dict], options: dict | None = None,
                    on_delta=None, on_stage=None) -> dict:
        """流式对话（SSE 解析），增量回调 on_delta(text)"""
        options = options or {}
        provider, model = self._resolve()
        task_type = str(options.get("task_type", "chat"))

        payload = self._build_payload(messages, model, options, stream=True)
        start = time.time()
        try:
            # 流式：不设总超时，只设连接超时（长时间生成）
            client = httpx.Client(timeout=httpx.Timeout(None, connect=20))
            with client.stream("POST", endpoint(provider), json=payload, headers=_headers(provider)) as resp:
                duration = int((time.time() - start) * 1000)
                if resp.status_code != 200:
                    err = f"HTTP {resp.status_code}"
                    try:
                        err_body = resp.read().decode("utf-8", "ignore")
                        body = json.loads(err_body)
                        if isinstance(body, dict):
                            err = str(body.get("error", {}).get("message", err))
                    except (ValueError, UnicodeDecodeError):
                        pass
                    log_call(self.db, task_type, provider, model, {}, duration, "failed", err)
                    raise RuntimeError(f"AI 调用失败: {err}")

                on_stage and on_stage("stream_start")
                text = ""
                usage: dict = {}
                finished = False
                read_error: str | None = None
                try:
                    for line in resp.iter_lines():
                        line = (line or "").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            finished = True
                            break
                        try:
                            event = json.loads(data)
                        except ValueError:
                            continue
                        if not isinstance(event, dict):
                            continue
                        if isinstance(event.get("usage"), dict):
                            usage = event["usage"]
                        choices = event.get("choices") or []
                        delta = ""
                        if choices and isinstance(choices[0], dict):
                            delta = (
                                choices[0].get("delta", {}).get("content")
                                or choices[0].get("message", {}).get("content")
                                or choices[0].get("text")
                                or ""
                            )
                        if isinstance(delta, str) and delta:
                            text += delta
                            on_delta and on_delta(delta)
                except Exception as e:
                    read_error = str(e)
                on_stage and on_stage("stream_end")

                if text == "" and read_error:
                    log_call(self.db, task_type, provider, model, usage, duration,
                             "failed", "流式读取中断: " + read_error)
                    raise RuntimeError(f"AI 流式输出中断: {read_error}")

                log_call(self.db, task_type, provider, model, usage, duration, "success")
                return {
                    "text": text,
                    "usage": usage,
                    "provider": provider.name,
                    "model": model.name,
                    "duration": duration,
                }
        except RuntimeError:
            raise
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            log_call(self.db, task_type, provider, model, {}, duration, "failed", str(e))
            raise RuntimeError(f"AI 请求失败: {e}") from e

"""OpenAI 兼容 AI 客户端（流式 / 非流式），httpx 实现

对齐 PHP 版 AiClient 的行为：默认 Provider/Model、SSE 解析、usage 提取、日志落库。
"""
import json
import queue
import threading
import time

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.helpers import now
from app.models import AiLog, AiModel, AiProvider, SystemConfig

# 这些 HTTP 状态说明是配置/账号问题（Key 错误、余额不足、无权限、模型或地址不存在），
# 重试不会好转；连续续写遇到时应停止，而不是继续逐章失败
FATAL_HTTP_STATUS = {401, 402, 403, 404}


class CallbackAbort(Exception):
    """回调（on_delta/on_stage）主动中止调用，例如用户取消任务；不能被当作网络错误吞掉"""


class AiFatalError(RuntimeError):
    """不可恢复的 AI 调用错误（配置缺失 / 鉴权失败 / 额度不足等）"""


def is_fatal(exc: BaseException | None) -> bool:
    """沿异常链（raise ... from e）判断是否为不可恢复错误"""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, AiFatalError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def _http_error(status_code: int, message: str) -> RuntimeError:
    cls = AiFatalError if status_code in FATAL_HTTP_STATUS else RuntimeError
    return cls(f"AI 调用失败: {message}")


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


# 辅助类调用（摘要、记忆、审校、质量检查）可以走更便宜的模型；其余（正文、设定、大纲、点子聊天）走写作模型
AUXILIARY_TASK_TYPES = {"generate_summary", "update_memory", "rebuild_memory", "consistency_check", "quality_check"}


def resolve_target(db: Session, task_type: str = "") -> tuple[AiProvider, AiModel] | None:
    """按任务类型选模型：系统设置里指定了「写作模型 / 辅助模型」就用它（连同它所属的 Provider），否则用默认"""
    key = "model_auxiliary_id" if task_type in AUXILIARY_TASK_TYPES else "model_writing_id"
    try:
        model_id = int(float(config_value(db, key, 0)))
    except (TypeError, ValueError):
        model_id = 0
    if model_id > 0:
        model = db.query(AiModel).filter(AiModel.id == model_id, AiModel.status == 1).first()
        if model:
            provider = db.query(AiProvider).filter(
                AiProvider.id == model.provider_id, AiProvider.status == 1).first()
            if provider:
                return provider, model
    return default_target(db)


def endpoint(provider: AiProvider) -> str:
    base = (provider.base_url or "").rstrip("/")
    if not base:
        raise AiFatalError("AI Provider base_url 为空")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def estimate_usage(messages: list[dict], text: str) -> dict:
    """接口没返回 usage 时按字数粗略估算（中文约 1 字 ≈ 1 token，偏保守）"""
    prompt_tokens = sum(len(str(m.get("content") or "")) for m in messages)
    completion_tokens = len(text or "")
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens, "_estimated": True}


def log_call(db: Session, task_type: str, provider: AiProvider, model: AiModel,
             usage: dict, duration_ms: int, status: str, error: str = "", prompt: str = "",
             novel_id: int = 0) -> None:
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0) or prompt_tokens + completion_tokens
    cost = (prompt_tokens * float(model.price_input or 0)
            + completion_tokens * float(model.price_output or 0)) / 1_000_000
    row = AiLog(
        novel_id=int(novel_id or 0),
        provider=provider.name,
        model=model.name,
        task_type=task_type,
        prompt=prompt,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost=round(cost, 6),
        usage_estimated=1 if usage.get("_estimated") else 0,
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


def _system_prompt(messages: list[dict]) -> str:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            return str(m.get("content", ""))
    return ""


class AiClient:
    def __init__(self, db: Session, novel_id: int = 0):
        self.db = db
        self.novel_id = novel_id  # 记入 AI 日志，用于按小说统计用量
        # 可选：等待非流式响应期间定期调用，抛 CallbackAbort 即中止（用于取消任务）
        self.cancel_check = None

    def _post_cancellable(self, client: httpx.Client, url: str, payload: dict, headers: dict):
        """非流式请求：没有取消检查时直接请求；有则在线程里请求、每 0.5 秒检查一次是否被取消"""
        if self.cancel_check is None:
            return client.post(url, json=payload, headers=headers)
        import concurrent.futures as cf
        executor = cf.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(client.post, url, json=payload, headers=headers)
        try:
            while True:
                try:
                    return future.result(timeout=0.5)
                except cf.TimeoutError:
                    self.cancel_check()  # 被取消时抛 CallbackAbort
        except CallbackAbort:
            client.close()  # 关闭连接，让后台请求尽快结束
            raise
        finally:
            executor.shutdown(wait=False)

    def _resolve(self, task_type: str = ""):
        target = resolve_target(self.db, task_type)
        if not target:
            raise AiFatalError("没有可用的 AI Provider/Model，请先在 AI 配置中添加并启用")
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
        if stream and options.get("include_usage", True):
            # OpenAI 兼容接口在流式最后一个事件里返回 usage；不支持的服务会报 400，chat_stream 会去掉后重试
            payload["stream_options"] = {"include_usage": True}
        return payload

    def chat(self, messages: list[dict], options: dict | None = None) -> dict:
        """非流式对话 → {text, usage, provider, model, duration}"""
        options = options or {}
        task_type = str(options.get("task_type", "chat"))
        provider, model = self._resolve(task_type)
        prompt = _system_prompt(messages)
        timeout = int(options.get("timeout") or 0) or int(
            float(config_value(self.db, "ai_http_timeout", settings.ai_http_timeout))
        )

        payload = self._build_payload(messages, model, options, stream=False)
        start = time.time()
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=20), follow_redirects=True) as client:
                resp = self._post_cancellable(client, endpoint(provider), payload, _headers(provider))
        except CallbackAbort:
            duration = int((time.time() - start) * 1000)
            log_call(self.db, task_type, provider, model, {}, duration, "failed", "已取消", prompt, self.novel_id)
            raise
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            log_call(self.db, task_type, provider, model, {}, duration, "failed", str(e), prompt, self.novel_id)
            raise RuntimeError(f"AI 请求失败: {e}") from e
        duration = int((time.time() - start) * 1000)

        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code != 200 or not isinstance(body, dict) or "choices" not in body:
            err = (body or {}).get("error", {}).get("message", "") if isinstance(body, dict) else ""
            err = err or f"HTTP {resp.status_code}"
            log_call(self.db, task_type, provider, model, {}, duration, "failed", str(err), prompt, self.novel_id)
            raise _http_error(resp.status_code, str(err))

        text = str(body["choices"][0]["message"]["content"])
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        if not usage.get("total_tokens") and not usage.get("prompt_tokens"):
            usage = estimate_usage(messages, text)
        log_call(self.db, task_type, provider, model, usage, duration, "success", "", prompt, self.novel_id)
        return {
            "text": text,
            "usage": usage,
            "provider": provider.name,
            "model": model.name,
            "duration": duration,
        }

    def chat_stream(self, messages: list[dict], options: dict | None = None,
                    on_delta=None, on_stage=None) -> dict:
        """流式对话（SSE 解析），增量回调 on_delta(text)

        网络读取放在后台线程，主线程每 0.5 秒检查一次 cancel_check：
        即使模型还没吐出第一个字（排队、长思考），取消也能立即生效。
        返回 {text, usage, provider, model, duration, finish_reason, done}
        """
        options = options or {}
        task_type = str(options.get("task_type", "chat"))
        provider, model = self._resolve(task_type)
        prompt = _system_prompt(messages)
        payload = self._build_payload(messages, model, options, stream=True)
        start = time.time()

        events: queue.Queue = queue.Queue()
        stop = threading.Event()
        holder: dict = {}

        def produce() -> None:
            try:
                # 不设总超时，但设连接超时 + 读超时（长时间生成，但防止流挂起永久卡住 worker）
                with httpx.Client(timeout=httpx.Timeout(None, connect=20, read=300)) as client:
                    holder["client"] = client
                    with client.stream("POST", endpoint(provider), json=payload, headers=_headers(provider)) as resp:
                        if resp.status_code != 200:
                            err = f"HTTP {resp.status_code}"
                            try:
                                body = json.loads(resp.read().decode("utf-8", "ignore"))
                                if isinstance(body, dict):
                                    err = str(body.get("error", {}).get("message", err))
                            except (ValueError, UnicodeDecodeError):
                                pass
                            events.put(("http_error", (resp.status_code, err)))
                            return
                        events.put(("start", None))
                        for line in resp.iter_lines():
                            if stop.is_set():
                                return
                            events.put(("line", line))
                events.put(("end", None))
            except Exception as e:  # noqa: BLE001
                if not stop.is_set():
                    events.put(("error", e))

        def abort() -> None:
            stop.set()
            client = holder.get("client")
            if client is not None:
                try:
                    client.close()  # 让后台线程的网络读取尽快结束
                except Exception:  # noqa: BLE001
                    pass

        def next_event():
            while True:
                try:
                    return events.get(timeout=0.5)
                except queue.Empty:
                    if self.cancel_check is not None:
                        self.cancel_check()  # 被取消时抛 CallbackAbort

        threading.Thread(target=produce, daemon=True).start()
        text = ""
        usage: dict = {}
        finish_reason = ""
        done = False
        started = False
        read_error: str | None = None
        duration = 0
        try:
            while True:
                kind, value = next_event()
                if kind == "http_error":
                    status_code, err = value
                    duration = int((time.time() - start) * 1000)
                    if status_code in (400, 422) and "stream_options" in payload:
                        # 服务不支持 stream_options：去掉后重试一次
                        return self.chat_stream(messages, {**options, "include_usage": False}, on_delta, on_stage)
                    log_call(self.db, task_type, provider, model, {}, duration, "failed", err, prompt, self.novel_id)
                    raise _http_error(status_code, err)
                if kind == "start":
                    started = True
                    duration = int((time.time() - start) * 1000)
                    on_stage and on_stage("stream_start")
                    continue
                if kind == "error":
                    if not started:
                        raise value
                    read_error = str(value)
                    break
                if kind == "end":
                    break
                line = (value or "").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    done = True
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
                    finish_reason = choices[0].get("finish_reason") or finish_reason
                    delta = (
                        (choices[0].get("delta") or {}).get("content")
                        or (choices[0].get("message") or {}).get("content")
                        or choices[0].get("text")
                        or ""
                    )
                if isinstance(delta, str) and delta:
                    text += delta
                    on_delta and on_delta(delta)
        except RuntimeError:
            abort()
            raise
        except CallbackAbort:
            abort()
            log_call(self.db, task_type, provider, model, {}, int((time.time() - start) * 1000),
                     "failed", "已取消", prompt, self.novel_id)
            raise
        except Exception as e:
            abort()
            log_call(self.db, task_type, provider, model, {}, int((time.time() - start) * 1000),
                     "failed", str(e), prompt, self.novel_id)
            raise RuntimeError(f"AI 请求失败: {e}") from e
        abort()
        on_stage and on_stage("stream_end")

        if text == "" and read_error:
            log_call(self.db, task_type, provider, model, usage, duration,
                     "failed", "流式读取中断: " + read_error, prompt, self.novel_id)
            raise RuntimeError(f"AI 流式输出中断: {read_error}")
        if read_error and not finish_reason:
            finish_reason = "interrupted"

        if not usage.get("total_tokens") and not usage.get("prompt_tokens"):
            usage = estimate_usage(messages, text)
        log_call(self.db, task_type, provider, model, usage, duration, "success", "", prompt, self.novel_id)
        return {
            "text": text,
            "usage": usage,
            "provider": provider.name,
            "model": model.name,
            "duration": duration,
            "finish_reason": finish_reason,
            "done": done,
        }

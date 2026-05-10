import json
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from starlette.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CONFIG_FILE = Path(__file__).parent / "configs.json"

# Shared HTTP client — stays alive for the process lifetime
_http_client = None


async def get_client():
    global _http_client
    if _http_client is None or _http_client.is_closed:
        from httpx import AsyncClient
        _http_client = AsyncClient(timeout=180, follow_redirects=True)
    return _http_client


def load_configs():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def find_config(model_name: str):
    clean = model_name.replace(":latest", "")
    configs = load_configs()
    for cfg in configs:
        if cfg.get("name") == clean or cfg.get("model") == clean:
            return cfg
    if configs:
        return configs[0]
    return None


def openai_to_anthropic(data: dict, cfg: dict) -> dict:
    messages_in = data.get("messages", [])
    system_text = ""
    anthropic_msgs = []
    for m in messages_in:
        if m.get("role") == "system":
            system_text = m.get("content", "")
        else:
            content = m.get("content", "")
            if isinstance(content, str):
                anthropic_msgs.append({"role": m["role"], "content": [{"type": "text", "text": content}]})
            else:
                anthropic_msgs.append({"role": m["role"], "content": content})
    if not anthropic_msgs:
        anthropic_msgs = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    max_tok = data.get("max_tokens") or data.get("max_completion_tokens") or 4096
    req = {
        "model": cfg.get("model", data.get("model", "")),
        "max_tokens": max_tok,
        "messages": anthropic_msgs,
    }
    if system_text:
        req["system"] = system_text
    if "temperature" in data:
        req["temperature"] = data["temperature"]
    return req


def anthropic_to_openai(upstream: dict, model_name: str) -> dict:
    content_blocks = upstream.get("content", [])
    text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
    content = "\n".join(text_parts)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": upstream.get("usage", {}).get("input_tokens", 0),
            "completion_tokens": upstream.get("usage", {}).get("output_tokens", 0),
            "total_tokens": upstream.get("usage", {}).get("input_tokens", 0) + upstream.get("usage", {}).get("output_tokens", 0),
        }
    }


def make_ollama_chat_chunk(model_name, content, done=False, usage=None, tool_calls=None):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    chunk = {
        "model": model_name,
        "created_at": now,
        "message": msg,
        "done": done,
    }
    if done:
        chunk["total_duration"] = 0
        chunk["load_duration"] = 0
        chunk["prompt_eval_count"] = (usage or {}).get("prompt_tokens", 0)
        chunk["eval_count"] = (usage or {}).get("completion_tokens", 0)
        chunk["eval_duration"] = 0
    return json.dumps(chunk, ensure_ascii=False)


def parse_sse_line(line):
    line = line.strip()
    if not line or line.startswith(":"):
        return None
    if line.startswith("data: "):
        data = line[6:]
        if data == "[DONE]":
            return {"done": True}
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    return None


@app.options("/{path:path}")
async def options_handler():
    return JSONResponse({})


@app.get("/api/version")
async def ollama_version():
    return {"version": "0.12.10"}


@app.get("/api/tags")
async def api_tags():
    configs = load_configs()
    models = []
    for cfg in configs:
        name = cfg.get("name", cfg.get("model", ""))
        if not name:
            continue
        models.append({
            "name": name + ":latest",
            "model": name + ":latest",
            "modified_at": "2025-01-01T00:00:00Z",
            "size": 4000000000,
            "digest": "",
            "details": {
                "parent_model": "",
                "format": "gguf",
                "family": "llama",
                "families": ["llama"],
                "parameter_size": "7B",
                "quantization_level": "Q4_0"
            }
        })
    if not models:
        models.append({"name": "placeholder:latest", "model": "placeholder:latest"})
    return {"models": models}


@app.post("/api/show")
async def api_show(request: Request):
    body = await request.json()
    name = body.get("name", "").replace(":latest", "")
    cfg = find_config(name)
    model_name = cfg.get("name", cfg.get("model", name)) if cfg else name
    caps = ["completion", "tools"]
    if cfg and cfg.get("protocol") == "openai":
        caps.append("vision")
    return {
        "modelfile": "FROM relay\nPARAMETER temperature 0.7\nPARAMETER num_ctx 1048576\n",
        "parameters": "temperature 0.7\nnum_ctx 1048576\n",
        "template": "{{ .System }}\n{{ .Prompt }}",
        "details": {
            "parent_model": "",
            "format": "gguf",
            "family": "llama",
            "families": ["llama"],
            "parameter_size": "7B",
            "quantization_level": "Q4_0"
        },
        "model_info": {
            "general.architecture": "CausalLM",
            "general.name": model_name,
            "llama.context_length": 1048576,
        },
        "capabilities": caps,
    }


@app.get("/api/ps")
async def api_ps():
    return {"models": []}


@app.get("/api/running")
async def api_running():
    return {"models": []}


@app.post("/api/embeddings")
async def api_embeddings(request: Request):
    data = await request.json()
    model_name = data.get("model", "").replace(":latest", "")
    cfg = find_config(model_name)
    if cfg is None:
        return JSONResponse({"error": "no config found"}, status_code=404)

    url = cfg["url"].rstrip("/")
    if not url.endswith("/embeddings"):
        url = url + "/embeddings"

    req_body = {
        "model": cfg.get("model", ""),
        "input": data.get("input", ""),
    }

    headers = {}
    api_key = cfg.get("apiKey", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        client = await get_client()
        resp = await client.post(url, json=req_body, headers=headers)
        upstream = resp.json()
        embeddings = []
        if "data" in upstream:
            for item in upstream["data"]:
                embeddings.append(item.get("embedding", []))
        elif "embedding" in upstream:
            embeddings.append(upstream["embedding"])
        return {"embedding": embeddings[0] if embeddings else []}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/v1/models")
async def v1_models():
    configs = load_configs()
    data = []
    for cfg in configs:
        name = cfg.get("name", cfg.get("model", ""))
        if name:
            data.append({"id": name, "object": "model", "created": int(time.time()), "owned_by": "ollama-relay"})
    if not data:
        data.append({"id": "placeholder", "object": "model", "created": int(time.time()), "owned_by": "ollama-relay"})
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def v1_chat_completions(request: Request):
    data = await request.json()
    model_name = data.get("model", "").replace(":latest", "")
    cfg = find_config(model_name)
    if cfg is None:
        return JSONResponse({"error": "no config found"}, status_code=404)

    protocol = cfg.get("protocol", "openai")
    model_label = cfg.get("name", cfg.get("model", model_name))
    is_stream = data.get("stream", False)

    if protocol == "anthropic":
        return await _proxy_anthropic(cfg, data, model_label, is_stream)
    else:
        return await _proxy_openai(cfg, data, model_label, is_stream)


async def _proxy_openai(cfg, data, model_label, is_stream):
    url = cfg["url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"

    req_body = {
        "model": cfg.get("model", data.get("model", "")),
        "messages": data.get("messages", []),
        "stream": is_stream,
    }
    # Pass through tool-related and other parameters
    for key in ("tools", "tool_choice", "temperature", "max_tokens", "max_completion_tokens",
                "top_p", "frequency_penalty", "presence_penalty", "stop", "response_format"):
        if key in data:
            req_body[key] = data[key]

    headers = {}
    api_key = cfg.get("apiKey", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if is_stream:
        async def stream():
            try:
                client = await get_client()
                async with client.stream("POST", url, json=req_body, headers=headers) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        error_msg = error_body.decode("utf-8", errors="replace")
                        error_chunk = {
                            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model_label,
                            "choices": [{"index": 0, "delta": {"content": f"[上游错误 {resp.status_code}] {error_msg}"}, "finish_reason": "stop"}],
                        }
                        yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    async for chunk in resp.aiter_bytes():
                        yield chunk
            except Exception as e:
                error_chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_label,
                    "choices": [{"index": 0, "delta": {"content": f"[连接错误] {str(e)}"}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream")
    else:
        try:
            client = await get_client()
            resp = await client.post(url, json=req_body, headers=headers)
            try:
                result = resp.json()
            except Exception:
                raw = resp.text[:500]
                return {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model_label,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": f"[上游响应非JSON {resp.status_code}] {raw}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            if "error" in result:
                error_msg = result["error"]
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
                return {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model_label,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": f"[上游错误] {error_msg}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            if "choices" not in result:
                return {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model_label,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": f"[上游响应格式异常] {json.dumps(result, ensure_ascii=False)[:500]}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            if "model" in result:
                result["model"] = model_label
            return result
        except Exception as e:
            return {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model_label,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": f"[连接错误] {str(e)}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }


async def _proxy_anthropic(cfg, data, model_label, is_stream):
    url = cfg["url"].rstrip("/")
    if not url.endswith("/messages"):
        url = url + "/messages"

    req_body = openai_to_anthropic(data, cfg)

    headers = {"anthropic-version": "2023-06-01"}
    api_key = cfg.get("apiKey", "")
    if api_key:
        headers["x-api-key"] = api_key

    if is_stream:
        async def stream():
            try:
                client = await get_client()
                async with client.stream("POST", url, json=req_body, headers=headers) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        error_msg = error_body.decode("utf-8", errors="replace")
                        error_chunk = {
                            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model_label,
                            "choices": [{"index": 0, "delta": {"content": f"[上游错误 {resp.status_code}] {error_msg}"}, "finish_reason": "stop"}],
                        }
                        yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    async for chunk in resp.aiter_bytes():
                        yield chunk
            except Exception as e:
                error_chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_label,
                    "choices": [{"index": 0, "delta": {"content": f"[连接错误] {str(e)}"}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream")
    else:
        try:
            client = await get_client()
            resp = await client.post(url, json=req_body, headers=headers)
            try:
                upstream = resp.json()
            except Exception:
                raw = resp.text[:500]
                return {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model_label,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": f"[上游响应非JSON {resp.status_code}] {raw}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            if "error" in upstream:
                error_msg = upstream["error"]
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
                return {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model_label,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": f"[上游错误] {error_msg}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            return anthropic_to_openai(upstream, model_label)
        except Exception as e:
            return {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model_label,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": f"[连接错误] {str(e)}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }


@app.post("/api/chat")
async def api_chat(request: Request):
    data = await request.json()
    model_name = data.get("model", "").replace(":latest", "")
    cfg = find_config(model_name)
    if cfg is None:
        return JSONResponse({"error": "no config found"}, status_code=404)

    model_label = cfg.get("name", cfg.get("model", model_name))
    is_stream = data.get("stream", False)

    if is_stream:
        return StreamingResponse(
            _ollama_chat_stream(cfg, data, model_label),
            media_type="application/x-ndjson",
        )
    else:
        result = await _ollama_chat_non_stream(cfg, data, model_label)
        return result


async def _ollama_chat_stream(cfg, data, model_label):
    protocol = cfg.get("protocol", "openai")
    messages = data.get("messages", [])
    tools = data.get("tools")
    tool_choice = data.get("tool_choice")

    try:
        if protocol == "anthropic":
            async for chunk in _ollama_stream_anthropic(cfg, messages, model_label, tools=tools):
                yield chunk
        else:
            async for chunk in _ollama_stream_openai(cfg, messages, model_label, tools=tools, tool_choice=tool_choice):
                yield chunk
    except Exception:
        yield make_ollama_chat_chunk(model_label, "\n[连接中断]", done=True) + "\n"


async def _ollama_stream_openai(cfg, messages, model_label, tools=None, tool_choice=None, **kwargs):
    url = cfg["url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"

    req_body = {
        "model": cfg.get("model", ""),
        "messages": messages,
        "stream": True,
    }
    if tools:
        req_body["tools"] = tools
    if tool_choice:
        req_body["tool_choice"] = tool_choice

    headers = {}
    api_key = cfg.get("apiKey", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    client = await get_client()
    async with client.stream("POST", url, json=req_body, headers=headers) as resp:
        if resp.status_code != 200:
            error_body = await resp.aread()
            error_msg = error_body.decode("utf-8", errors="replace")[:300]
            yield make_ollama_chat_chunk(model_label, f"[上游错误 {resp.status_code}] {error_msg}", done=True) + "\n"
            return

        buffer = ""
        tool_calls_acc = {}  # Accumulate tool call chunks by index
        async for raw_chunk in resp.aiter_bytes():
            buffer += raw_chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                parsed = parse_sse_line(line)
                if parsed is None:
                    continue
                if parsed.get("done"):
                    final_tools = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())] if tool_calls_acc else None
                    yield make_ollama_chat_chunk(model_label, "", done=True, tool_calls=final_tools) + "\n"
                    return
                choices = parsed.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    # Handle tool_calls chunks
                    tc = delta.get("tool_calls", [])
                    if tc:
                        for call in tc:
                            idx = call.get("index", 0)
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                            acc = tool_calls_acc[idx]
                            if call.get("id"):
                                acc["id"] = call["id"]
                            if call.get("type"):
                                acc["type"] = call["type"]
                            fn = call.get("function", {})
                            if fn.get("name"):
                                acc["function"]["name"] = fn["name"]
                            if fn.get("arguments"):
                                acc["function"]["arguments"] += fn["arguments"]
                    if content:
                        yield make_ollama_chat_chunk(model_label, content, done=False) + "\n"

        # Handle any remaining buffer content
        if buffer.strip():
            parsed = parse_sse_line(buffer)
            if parsed and not parsed.get("done"):
                choices = parsed.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", choices[0].get("message", {}))
                    content = delta.get("content", "")
                    tc = delta.get("tool_calls", [])
                    if tc:
                        for call in tc:
                            idx = call.get("index", 0)
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                            acc = tool_calls_acc[idx]
                            if call.get("id"):
                                acc["id"] = call["id"]
                            fn = call.get("function", {})
                            if fn.get("name"):
                                acc["function"]["name"] = fn["name"]
                            if fn.get("arguments"):
                                acc["function"]["arguments"] += fn["arguments"]
                    if content:
                        yield make_ollama_chat_chunk(model_label, content, done=False) + "\n"

    # Final done chunk — include accumulated tool_calls here (Ollama native behavior)
    final_tools = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())] if tool_calls_acc else None
    yield make_ollama_chat_chunk(model_label, "", done=True, tool_calls=final_tools) + "\n"


async def _ollama_stream_anthropic(cfg, messages, model_label, tools=None):
    url = cfg["url"].rstrip("/")
    if not url.endswith("/messages"):
        url = url + "/messages"

    openai_data = {"model": cfg.get("model", ""), "messages": messages}
    req_body = openai_to_anthropic(openai_data, cfg)
    req_body["stream"] = True
    # Convert OpenAI tools format to Anthropic format if present
    if tools:
        anthropic_tools = []
        for t in tools:
            func = t.get("function", {})
            anthropic_tools.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object"}),
            })
        req_body["tools"] = anthropic_tools

    headers = {"anthropic-version": "2023-06-01"}
    api_key = cfg.get("apiKey", "")
    if api_key:
        headers["x-api-key"] = api_key

    client = await get_client()
    async with client.stream("POST", url, json=req_body, headers=headers) as resp:
        if resp.status_code != 200:
            error_body = await resp.aread()
            error_msg = error_body.decode("utf-8", errors="replace")[:300]
            yield make_ollama_chat_chunk(model_label, f"[上游错误 {resp.status_code}] {error_msg}", done=True) + "\n"
            return

        buffer = ""
        async for raw_chunk in resp.aiter_bytes():
            buffer += raw_chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type", "")
                    if etype == "content_block_delta":
                        delta = event.get("delta", {})
                        text = delta.get("text", "")
                        if text:
                            yield make_ollama_chat_chunk(model_label, text, done=False) + "\n"
                    elif etype == "message_stop":
                        yield make_ollama_chat_chunk(model_label, "", done=True) + "\n"
                        return

    yield make_ollama_chat_chunk(model_label, "", done=True) + "\n"


async def _ollama_chat_non_stream(cfg, data, model_label):
    protocol = cfg.get("protocol", "openai")
    messages = data.get("messages", [])
    tools = data.get("tools")
    tool_choice = data.get("tool_choice")

    try:
        if protocol == "anthropic":
            url = cfg["url"].rstrip("/")
            if not url.endswith("/messages"):
                url = url + "/messages"
            openai_data = {"model": cfg.get("model", ""), "messages": messages}
            req_body = openai_to_anthropic(openai_data, cfg)
            headers = {"anthropic-version": "2023-06-01"}
            api_key = cfg.get("apiKey", "")
            if api_key:
                headers["x-api-key"] = api_key
            client = await get_client()
            resp = await client.post(url, json=req_body, headers=headers)
            upstream = resp.json()
            if "error" in upstream:
                return JSONResponse(upstream, status_code=resp.status_code)
            content_blocks = upstream.get("content", [])
            text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
            content = "\n".join(text_parts)
            usage = upstream.get("usage", {})
            return {
                "model": model_label,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "message": {"role": "assistant", "content": content},
                "done": True,
                "total_duration": 0,
                "load_duration": 0,
                "prompt_eval_count": usage.get("input_tokens", 0),
                "eval_count": usage.get("output_tokens", 0),
                "eval_duration": 0,
            }
        else:
            url = cfg["url"].rstrip("/")
            if not url.endswith("/chat/completions"):
                url = url + "/chat/completions"
            req_body = {
                "model": cfg.get("model", ""),
                "messages": messages,
                "stream": False,
            }
            if tools:
                req_body["tools"] = tools
            if tool_choice:
                req_body["tool_choice"] = tool_choice
            headers = {}
            api_key = cfg.get("apiKey", "")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            client = await get_client()
            resp = await client.post(url, json=req_body, headers=headers)
            upstream = resp.json()
            if "error" in upstream:
                return JSONResponse(upstream, status_code=resp.status_code)
            choice = upstream.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content", "")
            reasoning = message.get("reasoning_content", "")
            if reasoning:
                content = f"[思考过程]\n{reasoning}\n\n[回答]\n{content}"
            usage = upstream.get("usage", {})
            resp_msg = {"role": "assistant", "content": content}
            if message.get("tool_calls"):
                resp_msg["tool_calls"] = message["tool_calls"]
            return {
                "model": model_label,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "message": resp_msg,
                "done": True,
                "total_duration": 0,
                "load_duration": 0,
                "prompt_eval_count": usage.get("prompt_tokens", 0),
                "eval_count": usage.get("completion_tokens", 0),
                "eval_duration": 0,
            }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/generate")
async def api_generate(request: Request):
    data = await request.json()
    model_name = data.get("model", "").replace(":latest", "")
    cfg = find_config(model_name)
    if cfg is None:
        return JSONResponse({"error": "no config found"}, status_code=404)

    model_label = cfg.get("name", cfg.get("model", model_name))
    is_stream = data.get("stream", False)
    prompt = data.get("prompt", "")

    chat_data = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "stream": is_stream,
    }
    if "system" in data:
        chat_data["messages"].insert(0, {"role": "system", "content": data["system"]})

    if is_stream:
        return StreamingResponse(
            _ollama_generate_stream(cfg, chat_data, model_label),
            media_type="application/x-ndjson",
        )
    else:
        result = await _ollama_chat_non_stream(cfg, chat_data, model_label)
        if isinstance(result, dict) and "message" in result:
            content = result["message"]["content"]
            usage = result
            return {
                "model": model_label,
                "created_at": result.get("created_at", ""),
                "response": content,
                "done": True,
                "total_duration": 0,
                "load_duration": 0,
                "prompt_eval_count": usage.get("prompt_eval_count", 0),
                "eval_count": usage.get("eval_count", 0),
                "eval_duration": 0,
            }
        return result


async def _ollama_generate_stream(cfg, data, model_label):
    async for chunk in _ollama_chat_stream(cfg, data, model_label):
        parsed = json.loads(chunk)
        content = parsed.get("message", {}).get("content", "")
        done = parsed.get("done", False)
        gen_chunk = {
            "model": model_label,
            "created_at": parsed.get("created_at", ""),
            "response": content,
            "done": done,
        }
        if done:
            gen_chunk["total_duration"] = parsed.get("total_duration", 0)
            gen_chunk["load_duration"] = parsed.get("load_duration", 0)
            gen_chunk["prompt_eval_count"] = parsed.get("prompt_eval_count", 0)
            gen_chunk["eval_count"] = parsed.get("eval_count", 0)
            gen_chunk["eval_duration"] = parsed.get("eval_duration", 0)
        yield json.dumps(gen_chunk, ensure_ascii=False) + "\n"

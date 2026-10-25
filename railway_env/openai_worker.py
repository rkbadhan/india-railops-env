"""JSON worker for OpenAI-compatible Chat Completions, including Fireworks.

Only the supplied observation/history and tool schemas go to the provider.
No suite, plan, baseline or answer-key files are read by this adapter.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from .env_settings import load_env


def failure(kind, message, **details):
    return {"error": {"type": kind, "message": message}, **details}


def positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def completion(request, base_url, api_key=None, model=None, timeout=90, temperature=None,
               max_output_tokens=None, token_parameter="max_completion_tokens", opener=urllib.request.urlopen,
               reasoning_effort=None):
    try:
        url = urllib.parse.urlsplit(base_url or "")
        if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise ValueError("configure an HTTP(S) base URL without credentials, query or fragment")
        if url.hostname == "api.fireworks.ai" and not api_key:
            raise ValueError("configure OPENAI_API_KEY or FIREWORKS_API_KEY locally")
        if api_key and (not isinstance(api_key, str) or not api_key.isascii() or "\r" in api_key or "\n" in api_key):
            raise ValueError("API key must be a single ASCII line")
        model = model or request["model_id"]
        if not isinstance(model, str) or not model or "REPLACE" in model:
            raise ValueError("configure an exact provider model id")
        declared_output = request.get("max_output_tokens")
        if declared_output is not None and max_output_tokens is not None and declared_output != max_output_tokens:
            raise ValueError("adapter output allowance disagrees with the shared harness declaration")
        output = positive_int(declared_output if declared_output is not None else
                              max_output_tokens if max_output_tokens is not None else 4096, "max_output_tokens")
        window = request.get("context_window_tokens")
        if window is not None and positive_int(window, "context_window_tokens") <= output:
            raise ValueError("declared model context has no input allowance")
        tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                  "parameters": t["input_schema"]}} for t in request["tools"]]
        messages = [{"role": m["role"], "content": m["content"]} for m in request["messages"]]
        messages.append({"role": "user", "content": f"Trials remaining: {request['trials_remaining']}. Call exactly one available tool."})
        body = {"model": model, "messages": messages, "tools": tools, "tool_choice": "required",
                "parallel_tool_calls": False, "stream": False, token_parameter: output}
        if url.hostname == "api.fireworks.ai":
            # Fireworks otherwise may lower the requested completion allowance.
            body["context_length_exceeded_behavior"] = "error"
        if temperature is not None:
            if not 0 <= temperature <= 2:
                raise ValueError("temperature must be in [0, 2]")
            body["temperature"] = temperature
        if reasoning_effort is not None:
            if reasoning_effort not in ("none", "low", "medium", "high", "xhigh", "max"):
                raise ValueError("unsupported reasoning_effort setting")
            body["reasoning_effort"] = reasoning_effort
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        http = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        with opener(http, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        kind = "configuration_error" if error.code in (400, 401, 403, 404, 413, 422) else "provider_error"
        error.close()
        # Do not echo provider error bodies: authentication failures may contain credentials.
        return failure(kind, f"Chat Completions request rejected (HTTP {error.code}); check endpoint, model, credentials and context settings",
                       provider_http_status=error.code)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return failure("provider_error", f"Chat Completions transport failed: {type(error).__name__}")
    except (ValueError, TypeError, KeyError) as error:
        return failure("configuration_error", str(error))

    if not isinstance(data, dict):
        return failure("provider_error", "provider response must be an object")
    provider_usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    usage = {"input_tokens": provider_usage.get("prompt_tokens"),
             "output_tokens": provider_usage.get("completion_tokens"), "cost_usd": None}
    details = {"usage": usage, "provider_model": data.get("model"), "provider_usage": provider_usage,
               "raw_provider_response": data}
    prompt = usage["input_tokens"]
    if window is not None and type(prompt) is int and prompt + output > window:
        return failure("configuration_error", "reported prompt tokens leave less than the declared output allowance", **details)
    if (not isinstance(data.get("choices"), list) or len(data["choices"]) != 1 or
            not isinstance(data["choices"][0], dict) or not isinstance(data["choices"][0].get("message"), dict)):
        return failure("provider_error", "provider must return one completion with a message object", **details)
    try:
        choices = data["choices"]
        choice = choices[0]
        details["provider_finish_reason"] = choice.get("finish_reason")
        if choice.get("finish_reason") == "length":
            return failure("model_error", "completion output limit reached before a complete tool action",
                           failure_reason="output_limit", **details)
        calls = choice["message"].get("tool_calls", [])
        if len(calls) != 1:
            raise ValueError("model must return exactly one tool call; no action was executed")
        function = calls[0]["function"]
        name = function["name"]
        if name not in {t["name"] for t in request["tools"]}:
            raise ValueError("model chose an unavailable tool")
        arguments = json.loads(function["arguments"])
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        return {"tool": name, "arguments": arguments, **details}
    except (ValueError, TypeError, KeyError, IndexError) as error:
        return failure("model_error", str(error), **details)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--env-file", help="optional environment file; defaults to local .env when present")
    parser.add_argument("--model")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--token-parameter", choices=("max_completion_tokens", "max_tokens"), default="max_completion_tokens")
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--reasoning-effort", choices=("none", "low", "medium", "high", "xhigh", "max"),
                        help="explicit provider reasoning setting; omitted preserves the provider default")
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    try:
        load_env(args.env_file or ".env", required=args.env_file is not None)
        base_url = args.base_url or os.environ.get("OPENAI_BASE_URL")
        key = os.environ.get(args.api_key_env)
        if not key and args.api_key_env == "OPENAI_API_KEY" and urllib.parse.urlsplit(base_url or "").hostname == "api.fireworks.ai":
            key = os.environ.get("FIREWORKS_API_KEY")
        # Pipes can default to cp1252 on Windows, independently of the parent.
        # The worker protocol is UTF-8 in both directions, including reasoning.
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(request, dict):
            raise ValueError("worker request must be an object")
        selected_model = args.model or request.get("model_id") or os.environ.get("OPENAI_MODEL")
        result = completion(request, base_url, key, selected_model, args.timeout, args.temperature,
                            args.max_output_tokens, args.token_parameter, reasoning_effort=args.reasoning_effort)
    except (ValueError, TypeError, KeyError) as error:
        result = failure("configuration_error", str(error))
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
OpenAI client — thin wrapper over the `openai` SDK for Forge.

Drop-in replacement for VertexClient: exposes the same generate() contract
(messages in our internal dict format → {"text", "function_calls", ...}),
so react_loop.py and forge_agent.py don't need to know which provider is
behind the agent.

Auth: just an OpenAI API key (OPENAI_API_KEY) — no GCP project, no service
account, no Workload Identity. This intentionally has no code_execution or
context-caching support (OpenAI Chat Completions has no equivalent to
Gemini's server-side code execution or explicit context cache) — those
fields are always empty/no-ops so callers can treat both clients the same.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential


class OpenAIClient:
    """OpenAI Chat Completions client for Forge agent."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gpt-4o-mini",
        fallback_model: str | None = "gpt-4o",
    ) -> None:
        self.model_name = model_name
        self.fallback_model = fallback_model
        self._consecutive_failures = 0

        # Interface parity with VertexClient (unused here — no Gemini-style
        # server-side code execution or File Search on OpenAI).
        self.code_execution = False
        self.file_search_store_name: str | None = None

        self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY", ""))

    # ── Core generation ─────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        reraise=True,
    )
    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system_instruction: str | None = None,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """
        Send a chat.completions request with function calling.

        Returns a dict in the same shape VertexClient.generate() returns:
          - "text": any text response
          - "function_calls": list of {name, args, id} if the model wants tools
          - "executable_code" / "code_results": always [] (no OpenAI equivalent)
          - "grounding_metadata": always None (no OpenAI equivalent)
          - "finish_reason": why generation stopped
          - "usage": token counts
        """
        oa_messages = self._build_messages(messages, system_instruction)
        oa_tools = self._build_tools(tools)

        model = self.model_name
        if self._consecutive_failures >= 2 and self.fallback_model:
            model = self.fallback_model
            print(f"  🔄 Escalating to {model} after {self._consecutive_failures} failures")

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": oa_messages,
            "temperature": temperature,
            "max_tokens": 8192,
        }
        if oa_tools:
            kwargs["tools"] = oa_tools

        response = self.client.chat.completions.create(**kwargs)
        parsed = self._parse_response(response)

        if not parsed["function_calls"] and not parsed["text"]:
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0

        return parsed

    # ── Interface parity no-ops (Gemini-only features) ────────────────

    def create_context_cache(self, *args: Any, **kwargs: Any) -> bool:
        return False

    def cleanup_cache(self) -> None:
        return None

    # ── Message building ────────────────────────────────────────────

    def _build_messages(
        self, messages: list[dict[str, Any]], system_instruction: str | None
    ) -> list[dict[str, Any]]:
        """Convert our internal message format → OpenAI chat messages."""
        out: list[dict[str, Any]] = []
        if system_instruction:
            out.append({"role": "system", "content": system_instruction})

        last_call_ids: list[str] = []

        for msg in messages:
            role = msg["role"]

            if role in ("user", "tool"):
                if msg.get("text"):
                    out.append({"role": "user", "content": msg["text"]})

                frs = msg.get("function_responses")
                if frs is None and "function_response" in msg:
                    frs = [msg["function_response"]]
                if frs:
                    for i, fr in enumerate(frs):
                        call_id = last_call_ids[i] if i < len(last_call_ids) else f"call_{i}"
                        content = fr["response"]
                        if not isinstance(content, str):
                            content = json.dumps(content)
                        out.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": content,
                        })

            elif role == "assistant":
                content = msg.get("text") or None
                tool_calls = None
                fcs = msg.get("function_calls")
                if fcs:
                    tool_calls = []
                    last_call_ids = []
                    for i, fc in enumerate(fcs):
                        call_id = fc.get("id") or f"call_{i}"
                        last_call_ids.append(call_id)
                        tool_calls.append({
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": fc["name"],
                                "arguments": json.dumps(fc.get("args", {})),
                            },
                        })
                assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                out.append(assistant_msg)

        return out

    def _build_tools(
        self, tool_declarations: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]] | None:
        """Build OpenAI tool schemas from our simple dict format."""
        if not tool_declarations:
            return None

        oa_tools = []
        for td in tool_declarations:
            params = td.get("parameters") or {"type": "object", "properties": {}}
            if "type" not in params:
                params["type"] = "object"
            oa_tools.append({
                "type": "function",
                "function": {
                    "name": td["name"],
                    "description": td.get("description", ""),
                    "parameters": params,
                },
            })
        return oa_tools

    # ── Response parsing ────────────────────────────────────────────

    def _parse_response(self, response: Any) -> dict[str, Any]:
        result: dict[str, Any] = {
            "text": "",
            "function_calls": [],
            "executable_code": [],
            "code_results": [],
            "grounding_metadata": None,
            "parts": [],
            "finish_reason": None,
            "usage": {},
        }

        if getattr(response, "usage", None):
            u = response.usage
            result["usage"] = {
                "input_tokens": getattr(u, "prompt_tokens", 0),
                "output_tokens": getattr(u, "completion_tokens", 0),
                "total_tokens": getattr(u, "total_tokens", 0),
            }

        if not response.choices:
            return result

        choice = response.choices[0]
        result["finish_reason"] = choice.finish_reason
        msg = choice.message

        if msg.content:
            result["text"] = msg.content
            result["parts"].append({"type": "text", "text": msg.content})

        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                fc_dict = {"name": tc.function.name, "args": args, "id": tc.id}
                result["function_calls"].append(fc_dict)
                result["parts"].append({"type": "function_call", **fc_dict})

        return result

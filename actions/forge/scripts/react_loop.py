"""
ReAct Loop — core reasoning engine for Forge.

Implements the ReAct (Reasoning + Acting) pattern:
  while not done and within limits:
      response = model.generate(system + trajectory, tools)
      if response has function_calls:
          for each call: observation = tools.execute(call)
          trajectory.append(call + observation)
      elif response has text (final answer):
          return text

Provider-agnostic: works against any client implementing the same
generate() contract as OpenAIClient (text, function_calls, usage, ...).

Design inspired by:
  - DSPy: trajectory-as-list, overflow truncation (drop oldest)
  - LangChain: max_iterations + wall-clock timeout + handle_parsing_errors
  - Open Deep Research: progressive token limit retry
  - DeepAgents: think tool for explicit planning
"""

from __future__ import annotations

import json
import platform
import time
from typing import Any

from tools import ToolRegistry
from openai_client import OpenAIClient

# OS detection — used for logging only
_PLATFORM = platform.system()  # "Linux" on CI, "Windows" on local dev


class ReActLoop:
    """ReAct agent loop powered by LLM function calling."""

    def __init__(
        self,
        llm: OpenAIClient,
        tools: ToolRegistry,
        max_iterations: int = 15,
        max_wall_clock: int = 600,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.max_iterations = max_iterations
        self.max_wall_clock = max_wall_clock

    def run(
        self,
        system_prompt: str,
        user_prompt: str,
        tool_filter: list[str] | None = None,
    ) -> ReActResult:
        """
        Execute the ReAct loop.

        Args:
            system_prompt: System instruction for Gemini.
            user_prompt: The task description.
            tool_filter: Optional list of tool names to expose. None = all tools.

        Returns:
            ReActResult with final text, trajectory, and stats.
        """
        start_time = time.time()
        trajectory: list[TrajectoryStep] = []
        messages: list[dict[str, Any]] = [
            {"role": "user", "text": user_prompt},
        ]

        # Filter tools if requested
        declarations = self.tools.declarations()
        if tool_filter:
            declarations = [d for d in declarations if d["name"] in tool_filter]

        total_input_tokens = 0
        total_output_tokens = 0

        for iteration in range(1, self.max_iterations + 1):
            # Wall-clock guard
            elapsed = time.time() - start_time
            if elapsed > self.max_wall_clock:
                print(f"\n⏰ Wall-clock timeout after {elapsed:.0f}s")
                break

            print(f"\n{'─' * 50}")
            print(f"  Iteration {iteration}/{self.max_iterations}  ({elapsed:.0f}s elapsed)")
            print(f"{'─' * 50}")

            # Call Gemini — semantic retry on empty responses
            response = None
            for _attempt in range(3):
                response = self.llm.generate(
                    messages=messages,
                    tools=declarations,
                    system_instruction=system_prompt,
                    temperature=0.2,
                )
                if response["function_calls"] or response["text"]:
                    break
                print(f"  ⚠️ Empty response (attempt {_attempt + 1}/3), retrying...")
                time.sleep(2 ** _attempt)

            # Track tokens
            usage = response.get("usage", {})
            total_input_tokens += usage.get("input_tokens", 0)
            total_output_tokens += usage.get("output_tokens", 0)

            # ── Log File Search grounding (if Gemini searched the store) ──
            gm = response.get("grounding_metadata")
            if gm:
                chunks = getattr(gm, "grounding_chunks", None)
                if chunks:
                    print(f"\n  📚 File Search retrieved {len(chunks)} chunk(s):")
                    for i, chunk in enumerate(chunks[:5]):
                        src = getattr(chunk, "retrieved_context", None)
                        if src:
                            uri = getattr(src, "uri", "")
                            title = getattr(src, "title", "")
                            print(f"     [{i+1}] {title or uri}")

            # ── Log code execution (server-side, already complete) ──
            code_executions = response.get("executable_code", [])
            code_results = response.get("code_results", [])
            if code_executions:
                print(f"\n  🐍 Gemini Code Execution ({len(code_executions)} block(s)):")
                for i, ec in enumerate(code_executions):
                    lang = ec.get("language", "PYTHON")
                    code = ec["code"]
                    print(f"     [{i + 1}] ({lang}) {code[:200]}{'...' if len(code) > 200 else ''}")
                for cr in code_results:
                    outcome = cr.get("outcome", "")
                    output = cr.get("output", "")
                    status = "✅" if "OK" in outcome else "⚠️"
                    print(f"     {status} Result: {output[:300]}")

            # ── Handle function calls (our tool use) ────────────────
            if response["function_calls"]:
                # Build the assistant message (one message with ALL function calls)
                assistant_msg: dict[str, Any] = {"role": "assistant"}
                if response["function_calls"]:
                    assistant_msg["function_calls"] = response["function_calls"]
                if response["text"]:
                    assistant_msg["text"] = response["text"]
                if code_executions:
                    assistant_msg["executable_code"] = code_executions
                if code_results:
                    assistant_msg["code_results"] = code_results

                # Execute ALL function calls and collect responses
                # Vertex AI requires: 1 model msg (N calls) → 1 user msg (N responses)
                all_responses: list[dict[str, Any]] = []

                for fc in response["function_calls"]:
                    tool_name = fc.get("name") or ""
                    tool_args = fc.get("args") if isinstance(fc.get("args"), dict) else {}
                    fc["name"] = tool_name  # normalise for history
                    fc["args"] = tool_args

                    # ── Malformed FC guard ───────────────────────
                    available = {d["name"] for d in declarations}
                    if not tool_name or tool_name not in available:
                        observation = (
                            f"⚠️ Unknown tool '{tool_name}'. "
                            f"Available: {', '.join(sorted(available))}"
                        )
                        print(f"  ⚠️ {observation}")
                        trajectory.append(TrajectoryStep(
                            iteration=iteration, tool_name=tool_name or "?",
                            tool_args=tool_args, observation=observation,
                        ))
                        all_responses.append({
                            "name": tool_name or "unknown",
                            "response": {"result": observation},
                        })
                        continue

                    # ── Stuck-loop detection (3 identical calls) ─
                    if len(trajectory) >= 2:
                        _sig = (tool_name, json.dumps(tool_args, sort_keys=True))
                        _prev = [
                            (s.tool_name, json.dumps(s.tool_args, sort_keys=True))
                            for s in trajectory[-2:]
                        ]
                        if all(p == _sig for p in _prev):
                            observation = (
                                f"⚠️ You've called {tool_name} with identical arguments "
                                f"3 times. The result won't change. Use `think` to "
                                f"re-plan your approach."
                            )
                            print(f"  🔁 Stuck loop detected — nudging")
                            trajectory.append(TrajectoryStep(
                                iteration=iteration, tool_name=tool_name,
                                tool_args=tool_args, observation=observation,
                            ))
                            all_responses.append({
                                "name": tool_name,
                                "response": {"result": observation},
                            })
                            continue

                    # Print thought if think tool
                    if tool_name == "think":
                        reasoning = tool_args.get("reasoning", "")
                        print(f"\n  💭 Thought: {reasoning[:300]}")
                    else:
                        args_str = ", ".join(f"{k}={repr(v)[:80]}" for k, v in tool_args.items())
                        print(f"\n  ⚡ {tool_name}({args_str})")

                    # Execute the tool — errors become observations (DSPy pattern)
                    try:
                        observation = self.tools.execute(tool_name, tool_args)
                    except Exception as tool_err:
                        observation = f"⚠️ Tool error in {tool_name}: {type(tool_err).__name__}: {tool_err}"

                    # Smart output preview — head/tail for large outputs
                    if len(observation) > 500:
                        obs_preview = observation[:300] + "\n...\n" + observation[-150:]
                    else:
                        obs_preview = observation
                    print(f"  👁️ {obs_preview}")

                    # Record in trajectory
                    trajectory.append(TrajectoryStep(
                        iteration=iteration,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        observation=observation,
                    ))

                    all_responses.append({
                        "name": tool_name,
                        "response": {"result": observation},
                    })

                # Append ONE assistant message + ONE user message with ALL responses
                # This satisfies Vertex AI's requirement that function response
                # count must match function call count in the turn.
                messages.append(assistant_msg)
                messages.append({
                    "role": "user",
                    "function_responses": all_responses,
                })

                # Context overflow protection — DSPy-style bounded truncation
                # Drop oldest tool cycles (4 msgs each: assistant+response ×2)
                # up to 3 trims per loop iteration to stay within context
                if len(messages) > 50:
                    trimmed = 0
                    while len(messages) > 40 and trimmed < 3:
                        # Keep first user message, drop 4 oldest interactions
                        if len(messages) > 5:
                            messages = messages[:1] + messages[5:]
                            trimmed += 1
                    if trimmed:
                        print(f"\n  📦 Compacted trajectory (dropped {trimmed} oldest tool cycles)")

            elif response["text"]:
                # Model returned text without function calls → final answer
                # (may include code execution — that's fine, it's the answer)
                final_text = response["text"]
                print(f"\n  ✅ Final response ({len(final_text)} chars)")
                if code_executions:
                    print(f"     (included {len(code_executions)} code execution block(s))")
                return ReActResult(
                    text=final_text,
                    trajectory=trajectory,
                    iterations=iteration,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    elapsed=time.time() - start_time,
                )
            else:
                # Empty response — push the model to continue
                print("\n  ⚠️ Empty response, nudging model...")
                messages.append({
                    "role": "user",
                    "text": "Please continue. If you're done exploring, provide your final response.",
                })

        # Exhausted iterations — do a final extraction
        print(f"\n  ⏱️ Reached max iterations ({self.max_iterations}). Extracting final answer...")
        messages.append({
            "role": "user",
            "text": (
                "You have reached the maximum number of exploration iterations. "
                "Based on everything you've observed, provide your complete final response now."
            ),
        })

        response = self.llm.generate(
            messages=messages,
            tools=None,  # No tools — force text response
            system_instruction=system_prompt,
            temperature=0.2,
        )

        total_input_tokens += response.get("usage", {}).get("input_tokens", 0)
        total_output_tokens += response.get("usage", {}).get("output_tokens", 0)

        return ReActResult(
            text=response.get("text", "(no response)"),
            trajectory=trajectory,
            iterations=self.max_iterations,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            elapsed=time.time() - start_time,
        )


# ──────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────

class TrajectoryStep:
    """One step in the ReAct trajectory."""

    def __init__(
        self,
        iteration: int,
        tool_name: str,
        tool_args: dict[str, Any],
        observation: str,
    ) -> None:
        self.iteration = iteration
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.observation = observation

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "tool": self.tool_name,
            "args": self.tool_args,
            "observation": self.observation[:500],
        }


class ReActResult:
    """Result of a complete ReAct loop execution."""

    def __init__(
        self,
        text: str,
        trajectory: list[TrajectoryStep],
        iterations: int,
        input_tokens: int,
        output_tokens: int,
        elapsed: float,
    ) -> None:
        self.text = text
        self.trajectory = trajectory
        self.iterations = iterations
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.elapsed = elapsed

    def stats_summary(self) -> str:
        tools_used = {}
        for step in self.trajectory:
            tools_used[step.tool_name] = tools_used.get(step.tool_name, 0) + 1
        tools_str = ", ".join(f"{k}×{v}" for k, v in sorted(tools_used.items()))
        return (
            f"Iterations: {self.iterations} | "
            f"Tools: {tools_str} | "
            f"Tokens: {self.input_tokens:,} in / {self.output_tokens:,} out | "
            f"Time: {self.elapsed:.1f}s"
        )

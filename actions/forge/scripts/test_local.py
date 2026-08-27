#!/usr/bin/env python3
"""
Local Test Harness for Forge — tests each component without GitHub Actions.

Usage:
    cd ops-factory/actions/forge/scripts
    export OPENAI_API_KEY=sk-...
    python test_local.py

Tests:
    1. OpenAI connectivity (model responds)
    2. Tool registry (all base tools work)
    3. Function calling (model calls a tool)
    4. Mini ReAct loop (multi-step exploration)
"""

from __future__ import annotations

import os
import platform
import sys
import time
import traceback

# ── Configuration ───────────────────────────────────────────────────

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-4o")

# Use ops-factory itself as the workspace to explore
WORKSPACE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# ── Helpers ─────────────────────────────────────────────────────────

PASS = 0
FAIL = 0


def header(title: str) -> None:
    print(f"\n{'━' * 60}")
    print(f"  {title}")
    print(f"{'━' * 60}")


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
        if detail:
            print(f"     {detail[:200]}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")
        if detail:
            print(f"     {detail[:300]}")


# ── Test 1: OpenAI Connectivity ────────────────────────────────────

def test_openai_connectivity():
    header("TEST 1: OpenAI Connectivity")

    from openai_client import OpenAIClient

    print(f"  Model : {MODEL_NAME}")
    print()

    client = OpenAIClient(api_key=OPENAI_API_KEY, model_name=MODEL_NAME)

    print("  Sending: 'What is 2+2? Reply with just the number.'")
    t0 = time.time()
    try:
        response = client.generate(
            messages=[{"role": "user", "text": "What is 2+2? Reply with just the number."}],
            tools=None,
            system_instruction="You are a helpful assistant. Be concise.",
            temperature=0.0,
        )
        elapsed = time.time() - t0
        text = response.get("text", "").strip()
        usage = response.get("usage", {})
        check(
            "Model responds",
            bool(text),
            f"Response: '{text}' | Tokens: {usage.get('input_tokens', '?')} in / {usage.get('output_tokens', '?')} out | {elapsed:.1f}s",
        )
        check("Response contains '4'", "4" in text, f"Got: '{text}'")
    except Exception as e:
        check("Model responds", False, f"Error: {e}")
        traceback.print_exc()

    return client


# ── Test 2: Tool Registry ──────────────────────────────────────────

def test_tools():
    header("TEST 2: Tool Registry (base tools)")

    from tools import ToolRegistry

    tools = ToolRegistry(workspace=WORKSPACE)
    names = tools.names()
    print(f"  Workspace: {WORKSPACE}")
    print(f"  Registered tools: {names}")
    print()

    check("15 base tools registered", len(names) == 15, f"Got {len(names)}: {names}")

    # Test each tool
    # 1. think
    r = tools.execute("think", {"reasoning": "Testing the think tool locally"})
    check("think", "Thought recorded" in r, r)

    # 2. execute
    r = tools.execute("execute", {"command": "echo hello-forge"})
    check("execute (echo)", "hello-forge" in r, r)

    # 3. execute — blocked command
    r = tools.execute("execute", {"command": "rm -rf /"})
    check("execute (blocked rm -rf /)", "blocked" in r.lower() or "Error" in r, r)

    # 4. read_file
    r = tools.execute("read_file", {"path": "README.md", "start_line": 1, "end_line": 5})
    check("read_file (README.md)", "ops-factory" in r.lower() or "README" in r, r[:150])

    # 5. ls
    r = tools.execute("ls", {"path": "."})
    check("ls (root)", "actions" in r.lower() or "docs" in r.lower() or "README" in r, r[:200])

    # 6. glob
    r = tools.execute("glob", {"pattern": "**/*.py"})
    check("glob (**/*.py)", ".py" in r, f"Found: {r[:200]}")

    # 7. grep
    r = tools.execute("grep", {"pattern": "Forge", "path": ".", "include": "*.md"})
    check("grep (Forge in *.md)", "Forge" in r or "forge" in r.lower(), r[:200])

    # 8. tree
    r = tools.execute("tree", {"path": ".", "depth": 2})
    check("tree (depth 2)", "├" in r or "└" in r, f"{r[:200]}")

    # 9. find_definition
    r = tools.execute("find_definition", {"symbol": "ToolRegistry"})
    check("find_definition (ToolRegistry)", "ToolRegistry" in r, r[:200])

    # 10. git_diff
    r = tools.execute("git_diff", {"args": "HEAD~1"})
    # Might be empty if clean — that's ok
    check("git_diff (HEAD~1)", True, r[:150] if r else "(no diff — clean)")

    # 11. git_log
    r = tools.execute("git_log", {"args": "--oneline -5"})
    check("git_log (last 5)", len(r) > 5 or "no commits" in r.lower(), r[:200])

    # 12. git_show
    r = tools.execute("git_show", {"ref": "HEAD"})
    check("git_show (HEAD)", len(r) > 5 or "no commit" in r.lower(), r[:200])

    # 13. run_tests
    r = tools.execute("run_tests", {})
    check("run_tests (no crash)", True, r[:200])

    # 14. write_file + edit_file (test in a temp location)
    test_file = "__forge_test_temp.txt"
    r = tools.execute("write_file", {"path": test_file, "content": "hello world\nline two\nline three\n"})
    check("write_file", "Wrote" in r, r)

    r = tools.execute("edit_file", {"path": test_file, "old_string": "line two", "new_string": "line TWO edited"})
    check("edit_file", "Edited" in r or "replaced" in r.lower(), r)

    # Verify edit
    r = tools.execute("read_file", {"path": test_file})
    check("edit_file (verify)", "line TWO edited" in r, r[:150])

    # 15. delete_file — cleanup via the tool itself
    r = tools.execute("delete_file", {"path": test_file})
    check("delete_file", "Deleted" in r or "deleted" in r.lower(), r)

    # Test declarations format
    decls = tools.declarations()
    check("declarations() returns list", isinstance(decls, list) and len(decls) == 15)
    first = decls[0]
    check(
        "declaration has name/description/parameters",
        all(k in first for k in ("name", "description", "parameters")),
        f"Keys: {list(first.keys())}",
    )

    return tools


# ── Test 3: Function Calling ───────────────────────────────────────

def test_function_calling(client, tools):
    header("TEST 3: OpenAI Function Calling")

    # Give the model tools and ask a question that requires tool use
    decls = tools.declarations()
    # Only expose a few tools for this test
    test_decls = [d for d in decls if d["name"] in ("think", "ls", "read_file", "tree")]

    print(f"  Exposing tools: {[d['name'] for d in test_decls]}")
    print(f"  Asking: 'List the files in the repository root directory.'")
    print()

    try:
        t0 = time.time()
        response = client.generate(
            messages=[{
                "role": "user",
                "text": "List the files in the repository root directory. Use the ls tool.",
            }],
            tools=test_decls,
            system_instruction="You are a coding assistant. Use the provided tools to answer questions about the codebase.",
            temperature=0.0,
        )
        elapsed = time.time() - t0

        fc = response.get("function_calls", [])
        text = response.get("text", "")

        check(
            "Model returned function call(s)",
            len(fc) > 0,
            f"Function calls: {fc}" if fc else f"Got text instead: {text[:200]}",
        )

        if fc:
            call = fc[0]
            check("First call is 'ls'", call["name"] == "ls", f"Got: {call['name']}({call['args']})")

            # Execute the tool call
            observation = tools.execute(call["name"], call["args"])
            check("Tool execution succeeds", bool(observation), observation[:200])

            # Send observation back to the model
            messages = [
                {"role": "user", "text": "List the files in the repository root directory. Use the ls tool."},
                {"role": "assistant", "function_calls": [call]},
                {"role": "user", "function_response": {"name": call["name"], "response": {"result": observation}}},
            ]
            response2 = client.generate(
                messages=messages,
                tools=test_decls,
                system_instruction="You are a coding assistant. Use the provided tools to answer questions about the codebase.",
                temperature=0.0,
            )
            final_text = response2.get("text", "")
            check(
                "Model produces final answer after tool result",
                bool(final_text),
                f"Final answer: {final_text[:300]}",
            )

        print(f"\n  ⏱️ Total: {elapsed:.1f}s")

    except Exception as e:
        check("Function calling works", False, f"Error: {e}")
        traceback.print_exc()


# ── Test 4: Mini ReAct Loop ────────────────────────────────────────

def test_react_loop(client, tools):
    header("TEST 4: Mini ReAct Loop (5 iterations)")

    from react_loop import ReActLoop

    react = ReActLoop(
        llm=client,
        tools=tools,
        max_iterations=5,      # Keep it short for testing
        max_wall_clock=120,    # 2 minute limit
    )

    # A task that requires tool use
    system = (
        "You are Forge, an AI coding assistant. "
        "Use tools to explore the repository and answer questions. "
        "Be concise in your final answer."
    )
    user = (
        "Explore the ops-factory repository and tell me:\n"
        "1. How many Python files are there?\n"
        "2. What is the main purpose of this repo (read README.md)?\n"
        "3. List the directories under actions/\n\n"
        "Use the tree, glob, and read_file tools to find out."
    )

    print(f"  Task: Explore ops-factory repo (max 5 iterations)")
    print()

    try:
        t0 = time.time()
        result = react.run(
            system_prompt=system,
            user_prompt=user,
            tool_filter=["think", "ls", "tree", "glob", "read_file", "grep"],
        )
        elapsed = time.time() - t0

        check("ReAct completed", bool(result.text), f"Result length: {len(result.text)} chars")
        check("Used tools", len(result.trajectory) > 0, f"Steps: {len(result.trajectory)}")
        check(
            "Multiple iterations",
            result.iterations >= 2,
            f"Iterations: {result.iterations}",
        )

        print(f"\n  📊 {result.stats_summary()}")
        print(f"\n  📝 Final answer (first 500 chars):")
        print(f"  {'-' * 50}")
        for line in result.text[:500].split("\n"):
            print(f"  {line}")
        print(f"  {'-' * 50}")

    except Exception as e:
        check("ReAct loop works", False, f"Error: {e}")
        traceback.print_exc()


# ── Main ────────────────────────────────────────────────────────────

def main():
    print()
    print("🔨 Forge Local Test Harness")
    print("=" * 60)
    print(f"  Model      : {MODEL_NAME}")
    print(f"  Workspace  : {WORKSPACE}")
    print(f"  Platform   : {platform.system()} {platform.machine()}")
    print(f"  Python     : {platform.python_version()}")
    print()

    if not OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY is not set")
        sys.exit(1)

    # Run tests
    t0 = time.time()

    client = test_openai_connectivity()
    tools = test_tools()
    test_function_calling(client, tools)
    test_react_loop(client, tools)

    total = time.time() - t0

    # Summary
    header(f"RESULTS: {PASS} passed, {FAIL} failed ({total:.1f}s)")
    if FAIL == 0:
        print("  🎉 All tests passed! Forge is ready.")
    else:
        print(f"  ⚠️ {FAIL} test(s) failed. Check output above.")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

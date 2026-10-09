from __future__ import annotations
import threading
import argparse
import json
import os
from typing import Any, Callable
import ollama
from dotenv import load_dotenv
from status import Status
from tools import (
    analyze_file,
    find_references,
    get_call_graph,
    get_dependencies,
    get_entry_points,
    get_function_info,
    get_project_summary,
    get_project_tree,
    read_file,
    search_code,
    set_project_root,
)

from prompts import (
    PROJECT_AGENT_SYSTEM_PROMPT,
    TOOL_SCHEMAS,
    build_user_prompt,
)

load_dotenv()

DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:31b")
DEFAULT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "20"))

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")

if OLLAMA_API_KEY:
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "https://ollama.com")
else:
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")


def get_ollama_client() -> ollama.Client:
    if OLLAMA_API_KEY:
        return ollama.Client(
            host=OLLAMA_HOST,
            headers={
                "Authorization": f"Bearer {OLLAMA_API_KEY}"
            },
        )

    return ollama.Client(
        host=OLLAMA_HOST,
    )


TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "get_project_summary": get_project_summary,
    "get_project_tree": get_project_tree,
    "get_entry_points": get_entry_points,
    "search_code": search_code,
    "read_file": read_file,
    "analyze_file": analyze_file,
    "get_dependencies": get_dependencies,
    "get_function_info": get_function_info,
    "find_references": find_references,
    "get_call_graph": get_call_graph,
}


TOOL_STATUS_TEXT = {
    "get_project_summary": "Inspecting project summary",
    "get_project_tree": "Inspecting project structure",
    "get_entry_points": "Finding project entry points",
    "search_code": "Searching project source code",
    "read_file": "Reading project file",
    "analyze_file": "Analyzing source file",
    "get_dependencies": "Analyzing project dependencies",
    "get_function_info": "Inspecting function",
    "find_references": "Finding symbol references",
    "get_call_graph": "Building function call graph",
}


def serialize_result(result: Any) -> str:
    try:
        return json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    except Exception:
        return json.dumps(
            {"result": str(result)},
            ensure_ascii=False,
        )


def parse_tool_arguments(arguments: Any) -> dict:
    if arguments is None:
        return {}

    if isinstance(arguments, dict):
        return arguments

    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)

            if isinstance(parsed, dict):
                return parsed

        except json.JSONDecodeError:
            pass

    return {}


def execute_tool(
    tool_name: str,
    arguments: dict,
    status: Status | None = None,
) -> dict:
    tool = TOOL_REGISTRY.get(tool_name)

    if tool is None:
        if status:
            status.fail(
                f"Unknown tool: {tool_name}",
                step=f"Tool: {tool_name}",
            )

        return {
            "success": False,
            "error": f"Unknown tool: {tool_name}",
        }

    status_text = TOOL_STATUS_TEXT.get(
        tool_name,
        f"Executing {tool_name}",
    )

    if status:
        status.update(
            step=f"Tool: {tool_name}",
            process=status_text,
            state="running",
        )

    try:
        result = tool(**arguments)

        if status:
            status.done(
                step=f"Tool: {tool_name}",
                process=f"{status_text} — completed",
            )

        return {
            "success": True,
            "tool": tool_name,
            "result": result,
        }

    except Exception as exc:
        if status:
            status.fail(
                error=str(exc),
                step=f"Tool: {tool_name}",
            )

        return {
            "success": False,
            "tool": tool_name,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def run_agent(
    user_request: str,
    project_root: str,
    model: str | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    status: Status | None = None,
) -> str:

    if not user_request.strip():
        raise ValueError("user_request cannot be empty.")

    if not project_root.strip():
        raise ValueError("project_root cannot be empty.")

    model = model or DEFAULT_MODEL

    if status is None:
        status = Status()

    client = get_ollama_client()

    try:
        status.update(
            step="Project setup",
            process="Setting project root",
            state="running",
        )

        set_project_root(project_root)

        status.done(
            step="Project setup",
            process="Project root ready",
        )

        messages: list[Any] = [
            {
                "role": "system",
                "content": PROJECT_AGENT_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": build_user_prompt(user_request),
            },
        ]

        for step in range(1, max_steps + 1):
            status.update(
                step=f"Agent step {step}",
                process="Reasoning about the next action",
                state="running",
            )

            response = client.chat(
                model=model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                options={
                    "temperature": 0.1,
                },
            )

            assistant_message = response.message

            if not assistant_message.tool_calls:
                answer = assistant_message.content or ""

                if not answer.strip():
                    status.fail(
                        error="Agent returned an empty response.",
                        step=f"Agent step {step}",
                    )

                    return "The agent finished without producing a response."

                status.update(
                    step="Final response",
                    process="Preparing final answer",
                    state="running",
                )

                status.done(
                    step="Final response",
                    process="Analysis completed",
                )

                return answer.strip()

            messages.append(assistant_message)

            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name

                arguments = parse_tool_arguments(
                    tool_call.function.arguments
                )

                tool_result = execute_tool(
                    tool_name=tool_name,
                    arguments=arguments,
                    status=status,
                )

                messages.append(
                    {
                        "role": "tool",
                        "content": serialize_result(tool_result),
                        "tool_name": tool_name,
                    }
                )

        status.fail(
            error=f"Maximum agent steps reached ({max_steps}).",
            step="Agent",
        )

        return (
            f"The agent reached the maximum tool-call limit "
            f"({max_steps}) before producing a final answer."
        )

    except Exception as exc:
        status.fail(
            error=str(exc),
            step="Agent",
        )
        raise


def ask_agent(
    question: str,
    project_root: str,
    model: str | None = None,
    status: Status | None = None,
) -> str:
    return run_agent(
        user_request=question,
        project_root=project_root,
        model=model,
        status=status,
    )

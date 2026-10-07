import os
import sys
import json
import time
import subprocess
from datetime import datetime, timezone
import anthropic

MAX_ITERATIONS = 10
WORKSPACE = os.path.expanduser("~/agentgate/workspace")
TRACE_FILE = os.path.expanduser("~/agentgate/trace.jsonl")
TIMEOUT_SECONDS = 10
ALLOWED_COMMANDS = {"cat", "ls", "wc", "head"}


class PolicyDenied(Exception):
    pass


def check_path(path):
    if not isinstance(path, str):
        raise PolicyDenied(f"path must be a string, got {type(path).__name__}")

    if path.startswith("/"):
        raise PolicyDenied("absolute paths are not allowed")

    resolved = os.path.realpath(os.path.join(WORKSPACE, path))
    workspace_real = os.path.realpath(WORKSPACE)

    if not resolved.startswith(workspace_real + os.sep) and resolved != workspace_real:
        raise PolicyDenied(f"path escapes workspace: {path}")

    return os.path.relpath(resolved, workspace_real)


def check_command(name):
    if name not in ALLOWED_COMMANDS:
        raise PolicyDenied(f"command not on allowlist: {name}")


def log_trace(entry):
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    with open(TRACE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def run_in_container(argv):
    cmd = [
        "docker", "run", "--rm",
        "--network=none",
        "--user", "1000:1000",
        "--memory=256m",
        "-v", f"{WORKSPACE}:/workspace:ro",
        "alpine",
    ] + argv

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: tool timed out after {TIMEOUT_SECONDS}s"

    if proc.returncode != 0:
        return f"ERROR (exit {proc.returncode}): {proc.stderr.strip()}"

    return proc.stdout


def read_file(args):
    if "path" not in args:
        raise PolicyDenied("missing required argument: path")
    safe = check_path(args["path"])
    check_command("cat")
    return run_in_container(["cat", f"/workspace/{safe}"])


def list_dir(args):
    safe = check_path(args.get("path", "."))
    check_command("ls")
    return run_in_container(["ls", "-la", f"/workspace/{safe}"])


HANDLERS = {
    "read_file": read_file,
    "list_dir": list_dir,
}

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to the file"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List the files in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path, defaults to current"}
            },
            "required": [],
        },
    },
]


def run_agent(question, client):
    messages = [{"role": "user", "content": question}]

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"--- iteration {iteration} ---")

        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=1024,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            for block in response.content:
                if block.type == "text":
                    print("\nANSWER:", block.text)
            return

        messages.append({"role": "assistant", "content": response.content})

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            print(f"  tool: {block.name}  args: {block.input}")

            handler = HANDLERS.get(block.name)
            start = time.monotonic()

            if handler is None:
                output = f"ERROR: unknown tool {block.name}"
                decision = "unknown_tool"
            else:
                try:
                    output = handler(block.input)
                    decision = "allowed"
                except PolicyDenied as e:
                    output = f"DENIED: {e}"
                    decision = "denied"

            duration_ms = int((time.monotonic() - start) * 1000)

            log_trace({
                "iteration": iteration,
                "tool": block.name,
                "args": block.input,
                "decision": decision,
                "duration_ms": duration_ms,
                "output_preview": output[:120],
            })

            print(f"  [{decision}] {duration_ms}ms")
            print(f"  result: {output[:80]}")

            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })

        messages.append({"role": "user", "content": results})

    print("\nHit iteration cap.")


if __name__ == "__main__":
    run_agent(sys.argv[1], anthropic.Anthropic())

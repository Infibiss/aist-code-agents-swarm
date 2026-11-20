"""A scripted OpenAI-compatible "model" for end-to-end tests without a GPU.

It plays an oracle agent in mini-SWE-agent's text format: apply the task's gold patch, write patch.txt, submit.
If the judge accepts the result, the proxy, the agent adapter, the sandbox and the judge work together.
"""

from __future__ import annotations

import json
import time

from aiohttp import web

from aist_swarm.tasks import Task


def script(task: Task) -> list[str]:
    files = " ".join(task.patch_files())
    return [
        "THOUGHT: apply the fix.\n\n```mswea_bash_command\ncat > /tmp/fix.diff <<'AIST_EOF'\n"
        + task.patch + "AIST_EOF\ngit apply /tmp/fix.diff && echo applied\n```",
        f"THOUGHT: make the patch file.\n\n```mswea_bash_command\ngit diff -- {files} > patch.txt\n```",
        "THOUGHT: submit.\n\n```mswea_bash_command\necho COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt\n```",
    ]


def app(tasks: dict[str, Task]) -> web.Application:
    async def chat(request: web.Request) -> web.Response:
        body = await request.json()
        messages = body["messages"]
        prompt = next(m["content"] for m in messages if m["role"] == "user")
        task = next((t for t in tasks.values() if t.problem_statement[:200] in prompt), None)
        step = sum(1 for m in messages if m["role"] == "assistant")
        steps = script(task) if task else []
        content = steps[step] if step < len(steps) else steps[-1] if steps else "no task"
        usage = {"prompt_tokens": sum(len(str(m["content"])) // 4 for m in messages),
                 "completion_tokens": len(content) // 4}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return web.json_response({
            "id": f"fake-{time.time_ns()}", "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model", "fake"), "usage": usage,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        })

    async def models(_):
        return web.json_response({"object": "list", "data": [{"id": "fake", "object": "model"}]})

    a = web.Application(client_max_size=64 * 1024 * 1024)
    a.router.add_post("/v1/chat/completions", chat)
    a.router.add_get("/v1/models", models)
    return a


def serve(tasks: list[Task], port: int = 8099) -> None:
    print(json.dumps({"fake_llm": port, "tasks": len(tasks)}))
    web.run_app(app({t.instance_id: t for t in tasks}), host="127.0.0.1", port=port, print=None)

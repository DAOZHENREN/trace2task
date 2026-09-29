"""Read remote Codex capabilities without invoking a model or exporting credentials."""
from __future__ import annotations

import argparse
import json
import shlex
import time

from trace2task.codex_app_server import StdioJsonTransport


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--smoke-model", help="Explicitly spend one subscription model turn")
    parser.add_argument("--remote-command", nargs=argparse.REMAINDER,
                        default=["codex", "app-server"],
                        help="Trusted server-side argv; quoted before SSH transport")
    args = parser.parse_args()
    # Do not accept arbitrary SSH options or shell commands as a host.
    if not args.host or args.host.startswith("-") or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@.-_:"
        for c in args.host
    ):
        parser.error("Invalid SSH host")
    transport = StdioJsonTransport("ssh", command=[
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        args.host, shlex.join(args.remote_command),
    ])
    request_id = 0

    def request(method, params):
        nonlocal request_id
        request_id += 1
        transport.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + 30
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timed out reading {method}")
            message = transport.receive(remaining)
            if "method" in message and "id" in message:
                transport.send({"id": message["id"], "error": {
                    "code": -32601, "message": "Read-only capability probe",
                }})
            elif message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"{method} rejected: {message['error']}")
                return message["result"]

    try:
        request("initialize", {"clientInfo": {"name": "trace2task-rsi-probe",
                                             "version": "0.1"}})
        transport.send({"method": "initialized", "params": {}})
        account = request("account/read", {"refreshToken": False}).get("account") or {}
        models = request("model/list", {})
        smoke = None
        if args.smoke_model:
            if not any(model.get("model") == args.smoke_model
                       for model in models.get("data", [])):
                raise ValueError("Smoke model was not returned by this server")
            started = time.monotonic()
            thread = request("thread/start", {
                "model": args.smoke_model, "cwd": "/rsi/runtime", "ephemeral": True,
                "approvalPolicy": "never", "sandbox": "read-only",
                "baseInstructions": "You are a text-only connectivity check. Do not use tools.",
            })["thread"]["id"]
            turn = request("turn/start", {
                "threadId": thread, "effort": "low", "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                "input": [{"type": "text", "text": "Reply with exactly RSI_MODEL_OK."}],
            })["turn"]["id"]
            deadline = time.monotonic() + 120
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Subscription smoke response timed out")
                message = transport.receive(remaining)
                if "id" in message and "method" in message:
                    transport.send({"id": message["id"], "error": {
                        "code": -32601, "message": "Tools forbidden in model-only smoke",
                    }})
                    raise RuntimeError("Model-only smoke attempted a server tool request")
                params = message.get("params") or {}
                if message.get("method") == "item/started":
                    kind = (params.get("item") or {}).get("type")
                    if kind not in {"userMessage", "agentMessage", "reasoning"}:
                        raise RuntimeError(f"Unexpected model-only item: {kind}")
                if message.get("method") != "turn/completed" or params.get("threadId") != thread:
                    continue
                completed = params.get("turn") or {}
                if completed.get("id") != turn:
                    continue
                if completed.get("status") != "completed":
                    raise RuntimeError(f"Smoke turn failed: {completed.get('error')}")
                texts = [item["text"] for item in completed.get("items", [])
                         if item.get("type") == "agentMessage"
                         and item.get("phase") != "commentary"]
                if not texts or texts[-1].strip() != "RSI_MODEL_OK":
                    raise RuntimeError("Unexpected smoke response")
                smoke = {"response": texts[-1], "elapsed_seconds": round(
                    time.monotonic() - started, 2), "model": args.smoke_model}
                break
        # Explicit whitelist: omit email, account IDs and any authentication fields.
        print(json.dumps({
            "authentication": account.get("type"),
            "plan": account.get("planType"),
            "models": [{key: model.get(key) for key in (
                "id", "model", "isDefault", "supportedReasoningEfforts",
            )} for model in models.get("data", [])],
            "next_cursor": models.get("nextCursor"),
            "model_calls": 1 if args.smoke_model else 0,
            "smoke": smoke,
        }, ensure_ascii=False, indent=2))
    finally:
        transport.close()


if __name__ == "__main__":
    main()

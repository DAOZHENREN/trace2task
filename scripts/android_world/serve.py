"""Reuse the pinned official AndroidWorld server and add scoped GUI primitives."""

import asyncio
import contextlib
import hmac
import json
import os
import subprocess
import uuid
from pathlib import Path

import fastapi
import uvicorn
from android_world import registry, suite_utils
from android_world.env import env_launcher
from fastapi.responses import JSONResponse
from server import android_server as official

from trace2task.android_world_benchmark import SCOPE, execute_gui, validate_gui

root = Path(os.environ["TRACE2TASK_ANDROIDWORLD_RUNTIME"])
adb = Path(os.environ.get("TRACE2TASK_ANDROIDWORLD_ADB", str(root / "sdk/platform-tools/adb")))
expected_avd = os.environ.get("TRACE2TASK_ANDROIDWORLD_AVD", "AndroidWorldAvd")
token = (root / "service.token").read_text().strip()
instance = uuid.uuid4().hex


@contextlib.asynccontextmanager
async def lifespan(app):
    avd = await asyncio.to_thread(
        subprocess.check_output, [str(adb), "-s", "emulator-5554", "emu", "avd", "name"], text=True
    )
    if avd.splitlines()[0].strip() != expected_avd:
        raise RuntimeError("Refusing to control an unowned Android device")
    qemu = (
        await asyncio.to_thread(
            subprocess.check_output,
            [str(adb), "-s", "emulator-5554", "shell", "getprop", "ro.kernel.qemu"],
            text=True,
        )
    ).strip()
    if qemu != "1":
        raise RuntimeError("AndroidWorld requires an emulator")
    marker = root / "official-setup-complete.json"
    env = env_launcher.load_and_setup_env(
        console_port=5554,
        grpc_port=8554,
        adb_path=str(adb),
        emulator_setup=not marker.exists(),
        freeze_datetime=True,
    )
    app.state.app_android_env = env
    task_registry = registry.TaskRegistry()
    app.state.task_registry = task_registry
    app.state.suite = suite_utils.create_suite(
        task_registry=task_registry.get_registry(task_registry.ANDROID_WORLD_FAMILY),
        n_task_combinations=2,
        seed=42,
    )
    # Restore explicitly frozen parameters; upstream uses wall-clock folder names.
    frozen = root / "frozen-task-params.json"
    if frozen.exists():
        for item in json.loads(frozen.read_text()):
            task = app.state.suite[item["task_type"]][item["task_index"]]
            params = dict(task.params)
            params.update(item["params"])
            app.state.suite[item["task_type"]][item["task_index"]] = type(task)(params)
    marker.write_text(
        json.dumps(
            {"revision": "55ddaf5edc6f6c25673aed343907d5b9dca66f84", "avd": expected_avd}
        )
    )
    (root / "service-ready.json").write_text(
        json.dumps({"scope": SCOPE, "instance": instance, "port": 8876})
    )
    try:
        yield
    finally:
        env.close()


app = official.app
app.router.lifespan_context = lifespan


@app.middleware("http")
async def authenticate(request, call_next):
    if not hmac.compare_digest(request.headers.get("X-Trace2Task-Token", ""), token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/trace2task/identity")
def identity():
    return {"scope": SCOPE, "instance": instance}


@app.get("/trace2task/screenshot.png")
async def screenshot_png(request: fastapi.Request, wait_to_stabilize: bool = True):
    # Same official state retrieval; only the transport representation changes.
    import io
    import hashlib
    from PIL import Image
    from fastapi.responses import Response
    state = request.app.state.app_android_env.get_state(wait_to_stabilize=wait_to_stabilize)
    pixels = state.pixels
    if str(pixels.dtype) != 'uint8' or len(pixels.shape) != 3 or pixels.shape[2] != 3:
        raise RuntimeError('Unexpected screenshot pixel format')
    output = io.BytesIO()
    Image.fromarray(pixels).save(output, format='PNG', compress_level=1)
    return Response(output.getvalue(), media_type='image/png', headers={
        'X-Pixels-SHA256': hashlib.sha256(pixels.tobytes()).hexdigest(),
        'X-Screenshot-Instance': instance,
        'Cache-Control': 'no-store'})


@app.post("/trace2task/validate-action")
def validate_action_request(body: dict):
    if body.get("instance") != instance:
        raise fastapi.HTTPException(409, "Emulator instance changed")
    try:
        validate_gui(body.get("action"), scope=SCOPE)
    except ValueError as error:
        return {"valid": False, "executed": False, "reason": str(error), "instance": instance}
    return {"valid": True, "executed": False, "instance": instance}


@app.post("/trace2task/action")
def action(body: dict):
    if body.get("instance") != instance:
        raise fastapi.HTTPException(409, "Emulator instance changed")
    return execute_gui(app.state.app_android_env, body["action"], scope=SCOPE)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("TRACE2TASK_ANDROIDWORLD_PORT", "8876")))

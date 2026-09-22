"""Local-only D/5970 screenshot preview. Intentionally has NO desktop executor."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import io
import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path

CHECKPOINT_SHA = "4c07d912b881ccc69a28477aedf6c2b349f91965dc5dca89ebba8995987d02d3"
REVISION = "89644892e4d85e24eaac8bacfd4f463576704203"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def verify(bundle):
    config = json.loads((bundle / "frozen-launch.json").read_text(encoding="utf-8"))
    if config["model_revision"] != REVISION or config["optimizer"]["optimizer_steps"] != 5970:
        raise ValueError("Wrong model revision or checkpoint step")
    expected = {
        "step-00005970.pt": CHECKPOINT_SHA,
        "weights/model.safetensors": config["weight_sha256"],
        "source-manifest.json": config["source_manifest_sha256"],
    }
    expected.update({"processor/" + k: v for k, v in config["processor_files_sha256"].items()})
    checked = {}
    for name, digest in expected.items():
        path = bundle / name
        actual = sha(path)
        if path.is_symlink() or actual != digest:
            raise ValueError("Hash mismatch or symlink: " + name)
        checked[name] = actual
    manifest = json.loads((bundle / "source-manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest.items():
        path = (bundle / "source" / name).resolve()
        if not path.is_relative_to((bundle / "source").resolve()) or sha(path) != digest:
            raise ValueError("Frozen source mismatch: " + name)
        checked["source/" + name] = digest
    return config, checked


def capture_primary():
    import ctypes

    from PIL import ImageGrab

    user32 = ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if not previous:
        raise ctypes.WinError()
    try:
        width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        image = ImageGrab.grab(bbox=(0, 0, width, height), all_screens=False).convert("RGB")
        if image.size != (width, height):
            raise RuntimeError("Primary screen pixel dimensions changed; capture again")
        return image
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(previous)


def annotate(image, actions):
    from PIL import ImageDraw

    result = image.copy()
    draw = ImageDraw.Draw(result)
    w, h = result.size
    for index, action in enumerate(actions, 1):
        args = action.get("args", {})
        if "x" in args and "y" in args:
            x, y = min(w - 1, int(args["x"] * w)), min(h - 1, int(args["y"] * h))
            draw.ellipse((x - 12, y - 12, x + 12, y + 12), outline="red", width=4)
            draw.text(
                (max(0, min(x + 14, w - 150)), min(y, max(0, h - 20))),
                f"{index}: {action['skill']}",
                fill="red",
                stroke_width=1,
            )
        if all(k in args for k in ("start_x", "start_y", "end_x", "end_y")):
            start = (min(w - 1, int(args["start_x"] * w)), min(h - 1, int(args["start_y"] * h)))
            end = (min(w - 1, int(args["end_x"] * w)), min(h - 1, int(args["end_y"] * h)))
            draw.line((start, end), fill="red", width=4)
            draw.text(start, f"{index}: drag start", fill="red")
            draw.ellipse((end[0] - 8, end[1] - 8, end[0] + 8, end[1] + 8), outline="red", width=3)
    return result


class PreviewModel:
    def __init__(self, bundle, output):
        self.output = output
        output.mkdir(parents=True, exist_ok=True)
        config, hashes = verify(bundle)
        write_json(output / "hash-verification.json", hashes)
        print(f"Verified {len(hashes)} model/source files", flush=True)
        # Separate process: imports exclusively from the verified training source.
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(bundle / "source" / "src"))
        os.environ.update(
            HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1"
        )
        import torch
        from trace2task.visual_nanojev_backbone import VisualNanoJevBackbone
        from trace2task.visual_nanojev_lora import attach_language_lora
        from transformers import AutoConfig, AutoProcessor, Qwen3VLForConditionalGeneration

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable")
        torch.cuda.reset_peak_memory_stats()
        x = torch.ones((64, 64), device="cuda", dtype=torch.bfloat16)
        if not bool(((x @ x) == 64).all()):
            raise RuntimeError("GPU tensor test failed")
        torch.cuda.synchronize()
        del x
        self.torch = torch
        self.config = config
        versions = {
            p: importlib.metadata.version(p)
            for p in (
                "torch",
                "torchvision",
                "transformers",
                "tokenizers",
                "peft",
                "accelerate",
                "safetensors",
            )
        }
        write_json(
            output / "cuda-check.json",
            {
                "device": torch.cuda.get_device_name(),
                "capability": torch.cuda.get_device_capability(),
                "cuda": torch.version.cuda,
                "tensor_matmul_passed": True,
                "versions": versions,
            },
        )
        print("CUDA BF16 matrix multiplication passed; loading fixed base model on CPU", flush=True)
        model = Qwen3VLForConditionalGeneration.from_pretrained(
            str(bundle / "weights"),
            config=AutoConfig.from_pretrained(bundle / "processor", local_files_only=True),
            local_files_only=True,
            use_safetensors=True,
            torch_dtype=torch.bfloat16,
            attn_implementation="sdpa",
        )
        adapted, lora = attach_language_lora(model, rank=16, alpha=32)
        bridge = VisualNanoJevBackbone(adapted, head_size=256)
        # Only this pinned, verified, user-owned artifact may use pickle loading.
        checkpoint = torch.load(bundle / "step-00005970.pt", map_location="cpu", weights_only=False)
        optimizer_sha = hashlib.sha256(
            json.dumps(
                config["optimizer"], sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        if (
            checkpoint.get("completed_optimizer_steps") != 5970
            or checkpoint.get("config_sha256") != optimizer_sha
            or checkpoint.get("schema")
            not in {
                "visual_nanojev_training_checkpoint_v1",
                "visual_nanojev_training_checkpoint_v2",
            }
        ):
            raise ValueError("Checkpoint identity/config mismatch")
        trainable = {n: p for n, p in bridge.named_parameters() if p.requires_grad}
        saved = checkpoint["trainable_tensors"]
        if set(saved) != set(trainable):
            raise ValueError("Trainable parameter names differ")
        parameter_report = {}
        with torch.no_grad():
            for name, tensor in saved.items():
                parameter = trainable[name]
                if tensor.shape != parameter.shape or tensor.dtype != parameter.dtype:
                    raise ValueError("Parameter shape/dtype mismatch: " + name)
                if not bool(torch.isfinite(tensor).all()):
                    raise ValueError("Nonfinite tensor: " + name)
                parameter.copy_(tensor)
                if not torch.equal(parameter, tensor):
                    raise ValueError("Reload mismatch: " + name)
                parameter_report[name] = {"shape": list(tensor.shape), "dtype": str(tensor.dtype)}
        del checkpoint, saved
        print(
            f"Strictly restored {len(trainable)} LoRA/head tensors; moving bridge to GPU",
            flush=True,
        )
        self.bridge = bridge.to("cuda").eval()  # Do NOT change trainable parameter dtypes.
        self.processor = AutoProcessor.from_pretrained(
            bundle / "processor",
            local_files_only=True,
            trust_remote_code=False,
            min_pixels=65536,
            max_pixels=1048576,
        )
        write_json(
            output / "load-report.json",
            {
                "status": "loaded",
                "revision": REVISION,
                "checkpoint_sha256": CHECKPOINT_SHA,
                "lora": lora,
                "trainable_tensors": parameter_report,
                "versions": versions,
                "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                "configuration": {
                    "min_pixels": 65536,
                    "max_pixels": 1048576,
                    "max_sequence_tokens": 4096,
                    "max_actions": 8,
                    "max_text_tokens": 128,
                },
            },
        )

    def predict(self, task, image, *, history=None, step_index=0):
        from trace2task.visual_nanojev_collator import VisualNanoJevCollator
        from trace2task.visual_nanojev_data import build_record
        from trace2task.visual_nanojev_inference import VisualNanoJevPredictor

        if not isinstance(task, str) or not task.strip() or len(task) > 10000:
            raise ValueError("Enter a task (1-10000 characters)")
        if image.width * image.height > 32_000_000:
            raise ValueError("Screenshot too large")
        root = self.output / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
        root.mkdir()
        image.save(root / "screenshot.png")
        record = build_record(
            record_id=root.name,
            trajectory_id=root.name,
            duplicate_group=root.name,
            step_index=step_index,
            task=task,
            screenshot={
                "asset_id": "screenshot.png",
                "sha256": sha(root / "screenshot.png"),
                "width": image.width,
                "height": image.height,
                "timing": "before_target_action",
            },
            history=history or [],
            target={"skill": "end_step", "args": {}},
            audit_only={"local_preview": True, "target_is_schema_placeholder": True},
        )
        write_json(root / "record.json", record)
        collator = VisualNanoJevCollator(self.processor, images_root=root, max_sequence_tokens=4096)
        predictor = VisualNanoJevPredictor(
            self.bridge, collator, max_actions=8, max_text_tokens=128
        )
        emitted = []
        original = predictor.predict_action

        def predict_action(value, prior):
            action = original(value, prior)
            emitted.append(action)
            return action

        predictor.predict_action = predict_action
        torch = self.torch
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        result = {
            "task": task,
            "history": history or [],
            "executed": False,
            "task_success_verified": False,
            "output_directory": str(root),
            "emitted_actions": emitted,
        }
        try:
            with torch.inference_mode():
                prediction = predictor.predict_step(record)
            result.update(status="predicted", prediction=prediction)
            annotated = annotate(image, prediction["actions"])
            annotated.save(root / "annotated.png")
        except Exception as exc:  # noqa: BLE001 - archive failures, never execute partial output
            result.update(status="error", error=f"{type(exc).__name__}: {exc}")
            (root / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
            if emitted:
                annotate(image, emitted).save(root / "annotated.png")
                result["annotation_scope"] = "diagnostic_partial_output_not_an_executable_plan"
        finally:
            torch.cuda.synchronize()
            result.update(
                elapsed_seconds=time.perf_counter() - started,
                peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                peak_reserved_bytes=torch.cuda.max_memory_reserved(),
            )
            write_json(root / "prediction.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path("D:/Models/Trace2Task-D-5970"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "runs/trained-model-preview",
    )
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--task")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.verify_only:
        _, hashes = verify(args.bundle)
        write_json(args.output / "hash-verification.json", hashes)
        print(f"Verified {len(hashes)} files", flush=True)
        return
    server = create_server() if args.serve else None
    try:
        model = PreviewModel(args.bundle, args.output)
        print("D/5970 loaded; preview ONLY; no desktop inputs enabled", flush=True)
        if args.serve:
            server.model = model
            print("http://127.0.0.1:8767 — local-only screenshot preview", flush=True)
            server.serve_forever()
        else:
            from PIL import Image

            if args.capture == bool(args.image) or not args.task:
                parser.error("Choose --capture or --image, and supply --task")
            image = capture_primary() if args.capture else Image.open(args.image).convert("RGB")
            print(json.dumps(model.predict(args.task, image), ensure_ascii=False), flush=True)
    except Exception:
        (args.output / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_initialized():
            write_json(
                args.output / "failure-memory.json",
                {
                    "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                    "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
                    "error_location": "see startup-error.log; no automatic budget reduction",
                },
            )
        raise
    finally:
        if server is not None:
            server.server_close()


def create_server(port=8767):
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from PIL import Image

    token = uuid.uuid4().hex
    origin = f"http://127.0.0.1:{port}"
    prediction_lock = threading.Lock()

    class Server(ThreadingHTTPServer):
        allow_reuse_address = False

        def server_bind(self):
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health" and self.headers.get("Host") == f"127.0.0.1:{port}":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                status = "ready" if getattr(self.server, "model", None) is not None else "loading"
                self.wfile.write(
                    json.dumps(
                        {"protocol": 2, "model": "D-5970", "history": True, "status": status}
                    ).encode()
                )
                return
            if self.headers.get("Host") != f"127.0.0.1:{port}" or self.path != "/":
                self.send_error(404)
                return
            html = (
                Path(__file__)
                .with_name("preview.html")
                .read_text(encoding="utf-8")
                .replace("__TOKEN__", token)
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(html.encode())

        def do_POST(self):
            if (
                self.path != "/predict"
                or self.headers.get("Host") != f"127.0.0.1:{port}"
                or self.headers.get("Origin") != origin
                or self.headers.get("X-Preview-Token") != token
            ):
                self.send_error(403)
                return
            if not prediction_lock.acquire(blocking=False):
                self.send_error(409, "A prediction is already running")
                return
            try:
                if getattr(self.server, "model", None) is None:
                    self.send_error(503, "The D model is still loading")
                    return
                self.connection.settimeout(30)
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 20 * 1024 * 1024:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(size))
                if body.get("capture") is True:
                    time.sleep(3)  # User-selected capture: time to switch away from the browser.
                    image = capture_primary()
                else:
                    image = Image.open(
                        io.BytesIO(base64.b64decode(body["image"], validate=True))
                    ).convert("RGB")
                history = body.get("history", [])
                if not isinstance(history, list) or len(history) > 4:
                    raise ValueError("At most four executed history steps are allowed")
                result = self.server.model.predict(body["task"], image,
                                                  history=history, step_index=body.get("step_index", 0))
                annotated = Path(result["output_directory"]) / "annotated.png"
                if annotated.exists():
                    result["annotated_image"] = (
                        "data:image/png;base64," + base64.b64encode(annotated.read_bytes()).decode()
                    )
            except Exception as exc:  # noqa: BLE001 - report local preview failures to UI
                result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            finally:
                prediction_lock.release()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode())

    return Server(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    main()

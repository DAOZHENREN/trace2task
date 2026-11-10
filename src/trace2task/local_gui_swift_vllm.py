"""Desktop131 serving over pinned vLLM; shares the published input/codec contract.

The 105-slot predecessor is adapted only at the table/pooler boundary. This is
prefill slot readout, not autoregressive generation of the JSON action string.
"""

import importlib.util
import json
import sys
import time
from pathlib import Path


class VLLMReader:
    def __init__(self, model):
        import torch
        import trace2task
        from tokenizers import Tokenizer
        from transformers import AutoProcessor
        from trace2task.local_gui_swift import deployment_paths
        from trace2task.desktop131_vllm import make_engine

        release, base, upstream = deployment_paths()
        spec = importlib.util.spec_from_file_location(
            "swift131_release_verify", release / "infer.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = json.loads((release / "gui_swift_config.json").read_text())
        module.verify_files(release, json.loads((release / "SHA256SUMS.json").read_text()))
        module.verify_files(base, config["base_files"])
        module.verify_files(upstream, json.loads((release / "upstream.json").read_text())["files"])
        provenance = Path(model).parent / "provenance.json"
        evidence = json.loads(provenance.read_text())
        module.verify_files(provenance.parent, evidence["files"])
        if evidence["sources"] != list(map(str, (release, base, upstream))):
            raise ValueError("Merged derivative does not match configured release")
        sys.path[:0] = [str(release / "frozen"), str(upstream)]
        trace2task.__path__.append(str(release / "frozen/trace2task"))
        from gui_owl_desktop131 import Desktop131Codec
        from runtime_parts import prepare_inputs

        self.c = Desktop131Codec(Tokenizer.from_file(str(base / "tokenizer.json")))
        if self.c.codebook_sha256 != config["codebook_sha256"]:
            raise ValueError("Wrong coordinate codebook")
        self.proc = self.processor = AutoProcessor.from_pretrained(base, local_files_only=True)
        self.keys = config["key_sequences"]
        self._prepare_inputs = prepare_inputs
        self.engine = self.model = make_engine(
            model, self.c, max_images=getattr(self, "max_images", 3)
        )
        self.last_logits = None
        torch.set_num_threads(4)

    def inputs(self, request):
        return self._prepare_inputs(self, request)

    def predict(self, admitted):
        import torch
        from PIL import Image
        from vllm import PoolingParams

        started = time.perf_counter()
        inputs, row, budget = self.inputs(admitted)
        expected = inputs["input_ids"][0].tolist()
        image_id = self.c.tokenizer.token_to_id("<|image_pad|>")
        # vLLM's maintained processor performs expansion. Collapse only known
        # image runs, preserving every text/history-bin/table token verbatim.
        ids = []
        for token in expected:
            if token == image_id and ids and ids[-1] == image_id:
                continue
            ids.append(token)
        images = []
        for im in row["images"]:
            with Image.open(im["path"]) as f:
                images.append(f.convert("RGB"))
        if ids.count(image_id) != len(images):
            raise ValueError("Image placeholder mismatch")
        request = {
            "prompt_token_ids": ids,
            "multi_modal_data": {"image": images},
            "mm_processor_kwargs": {"min_pixels": 65536, "max_pixels": budget},
        }
        t = time.perf_counter()
        output = self.engine.encode(
            request,
            pooling_params=PoolingParams(
                extra_kwargs={"swift131_table_ids": list(self.c.input_ids)}
            ),
            pooling_task="encode",
            use_tqdm=False,
        )[0]
        model_seconds = time.perf_counter() - t
        if output.prompt_token_ids != expected:
            raise ValueError("vLLM differs from published input token sequence")
        logits = torch.as_tensor(output.outputs.data).float()
        if logits.shape[0] != 131 or not torch.isfinite(logits).all():
            raise ValueError("Invalid Desktop131 logits")
        self.last_logits = logits
        try:
            action, error = self.c.decode(logits, key_sequences=self.keys), None
        except (ValueError, KeyError) as exc:
            action, error = None, str(exc)
        return dict(
            action=action,
            parse_error=error,
            raw_ids=logits.argmax(-1).tolist(),
            forward_calls=1,
            output_slots=131,
            model_seconds=model_seconds,
            total_seconds=time.perf_counter() - started,
            input_tokens=len(expected),
            image_budget=budget,
            inference_engine="vllm-0.11.0-pooling",
            input_token_parity=True,
        )

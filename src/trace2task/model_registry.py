"""Model identity and inference capabilities, independent of action delivery.

Profiles describe compatibility, not benchmark quality or installed readiness.
Native prompts/decoders remain in local_gui_protocol; ExecutionBackend remains
the contract in execution_core. No backend choice grants additional actions.
"""
from dataclasses import asdict, dataclass

GUI_CONTEXT_TOKENS = 32768
SUMMARY_TRIGGER_TOKENS = 24576
SUMMARY_KEEP_TURNS = 4


@dataclass(frozen=True)
class PrebuiltGGUF:
    """Official, pinned files; existing weights can be reused without conversion."""
    directory: str
    directory_env: str
    files: tuple[tuple[str, str], ...]
    quantization: str
    cache_type: str = "q8_0"


@dataclass(frozen=True)
class ModelProfile:
    id: str
    label: str
    repository: str
    revision: str
    adapter: str
    platforms: tuple[str, ...]
    engines: tuple[str, ...]
    maturity: str = "supported"
    note: str = ""
    prebuilt_gguf: PrebuiltGGUF | None = None

    @property
    def alias(self):
        return "trace2task-" + self.id


@dataclass(frozen=True)
class InferenceBackend:
    id: str
    label: str
    context_policy: str
    context_description: str
    context_window: int | None = None


INFERENCE_BACKENDS = {
    "transformers": InferenceBackend("transformers", "Transformers", "vram_images_only",
        "按显存清理历史图片；任务、经验与文字历史保留。"),
    "llama-server": InferenceBackend("llama-server", "llama.cpp", "langchain_summary_then_native_image_eviction",
        f"{GUI_CONTEXT_TOKENS // 1024}K 上下文；接近 {SUMMARY_TRIGGER_TOKENS // 1024}K 时摘要早期对话，保留最近 {SUMMARY_KEEP_TURNS} 轮、原始任务和完整经验；必要时清理历史图片。", GUI_CONTEXT_TOKENS),
    "chat-completions": InferenceBackend("chat-completions", "Chat Completions", "budget_image_eviction",
        "按配置预算清理历史图片；文字保留。预算值不是模型能力检测结果。"),
    "codex": InferenceBackend("codex", "Codex", "provider_managed",
        "上下文由 Codex 管理；截图与经验交由所选订阅服务处理。"),
    "frozen-d": InferenceBackend("frozen-d", "D 冻结推理", "frozen_checkpoint",
        "冻结预测协议；不支持精简序列经验。"),
}

MODEL_PROFILES = {
    p.id: p for p in (
        ModelProfile("qwen3-vl-2b", "Qwen3-VL · 2B", "Qwen/Qwen3-VL-2B-Instruct",
            "89644892e4d85e24eaac8bacfd4f463576704203", "qwen-normalized-actions",
            ("windows",), ("llama-server", "transformers"),
            note="llama.cpp 接入已实现；需本机转换并校验 GGUF，真实任务效果需单独验收。"),
        ModelProfile("qwen3-vl-8b-instruct", "Qwen3-VL · 8B", "Qwen/Qwen3-VL-8B-Instruct-GGUF",
            "f982a07559d4a2f6c8744d840bf6fccab30eea96", "qwen-normalized-actions",
            ("windows",), ("llama-server",),
            note="官方 Q4_K_M + F16 视觉投影；复用已有 GGUF，统一任务会话、摘要、停止和运行记录。",
            prebuilt_gguf=PrebuiltGGUF("D:/Models/Qwen3-VL-8B-Instruct", "TRACE2TASK_QWEN8B_GGUF_DIR", (
                ("Qwen3VL-8B-Instruct-Q4_K_M.gguf", "67d1659bfe71b89d50b45a4ad1a9e5b997e5bb16ce5da66a6a6167abd569e9e2"),
                ("mmproj-Qwen3VL-8B-Instruct-F16.gguf", "ca524100ebf825c9a870db1c580d03879e0da0ab2541697e2458e64891cf9d38"),
            ), "Q4_K_M text / F16 vision")),
        ModelProfile("gui-owl-2b", "GUI-Owl 1.5 · 2B", "mPLUG/GUI-Owl-1.5-2B-Instruct",
            "528ceaec795bbfbe6103bd79e03db849feadfb24", "owl-computer-use",
            ("windows",), ("llama-server", "transformers")),
        ModelProfile("mai-ui-2b", "MAI-UI · 2B", "Tongyi-MAI/MAI-UI-2B",
            "503050934809558c8dfd2ddedaf9621fa74ac2de", "mai-mobile-desktop-subset",
            ("android", "windows-experimental"), ("transformers",), "experimental",
            "手机模型的有限桌面适配；不支持的动作明确拒绝。"),
    )
}
GUI_MODELS = tuple(MODEL_PROFILES)


def profile_for(key, backend=None):
    try:
        profile = MODEL_PROFILES[key]
    except KeyError:
        raise ValueError("Unknown GUI model profile: " + str(key)) from None
    if backend is not None and backend not in profile.engines:
        raise ValueError(f"{profile.label} 不支持 {backend}；可选：{', '.join(profile.engines)}")
    return profile


def public_catalog():
    return {"models": [asdict(p) for p in MODEL_PROFILES.values()],
            "inference_backends": [asdict(b) for b in INFERENCE_BACKENDS.values()],
            "execution_backends": [
                {"id": "win32", "label": "Win32", "scopes": ["desktop"], "input_mode": "foreground"},
                {"id": "cua", "label": "Cua", "scopes": ["desktop", "selected_windows"],
                 "input_mode": "scope_dependent"}]}

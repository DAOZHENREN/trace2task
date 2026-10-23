"""Linux-only guarded launch plan for the pinned upstream GRPO trainer.

Default is a JSON command preview. No dependency installation, downloads, device
actions, process termination, or training occur without --execute.
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

COMMIT = 'a4168aa2709a6a2350811591054d3579884dd357'


def preflight(upstream, model, train, val):
    issues = []
    if sys.platform != 'linux':
        issues.append('Training requires an isolated Linux environment')
    for name in ('torch', 'vllm', 'ray', 'peft', 'omegaconf', 'tensordict', 'torchdata', 'transformers'):
        if importlib.util.find_spec(name) is None:
            issues.append('Missing dependency: ' + name)
    if not (model / 'config.json').is_file() or not list(model.glob('*.safetensors')):
        issues.append('Local model weights missing; no automatic download')
    for label, path in [('train', train), ('val', val)]:
        if not path.is_dir() or not list(path.rglob('evaluation_summary.json')):
            issues.append(label + ' must be a nonempty official rollout directory')
        for provenance in path.rglob('trace2task_provenance.json'):
            value = json.loads(provenance.read_text())
            if value.get('synthetic_test_only') or not value.get('training_eligible'):
                issues.append('Synthetic or ineligible trajectory: ' + str(provenance))
        for summary in path.rglob('evaluation_summary.json'):
            if not (summary.parent / 'trace2task_provenance.json').is_file():
                issues.append('Missing reviewed provenance: ' + str(summary))
    if train == val or train.is_relative_to(val) or val.is_relative_to(train):
        issues.append('Train/validation directories must be disjoint')
    train_ids = {p.parent.parent.parent.name for p in train.rglob('evaluation_summary.json')}
    val_ids = {p.parent.parent.parent.name for p in val.rglob('evaluation_summary.json')}
    if train_ids & val_ids:
        issues.append('Task identity overlaps train and validation')
    if shutil.disk_usage(upstream).free < 60 * 1024**3:
        issues.append('Less than 60 GiB free disk for smoke run; preserve existing data')
    head = subprocess.run(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=False)
    dirty = subprocess.run(['git', '-C', str(upstream), 'status', '--porcelain'], capture_output=True, text=True, check=False)
    if head.returncode or head.stdout.strip() != COMMIT or dirty.returncode or dirty.stdout.strip():
        issues.append('Upstream must be clean and pinned')
    try:
        gpu = subprocess.run(['nvidia-smi', '--query-gpu=memory.free,utilization.gpu', '--format=csv,noheader,nounits'],
                             capture_output=True, text=True, check=True, timeout=10)
        rows = [[int(v.strip()) for v in row.split(',')] for row in gpu.stdout.splitlines()]
        processes = subprocess.run(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits'],
                                   capture_output=True, text=True, check=True, timeout=10)
        if len(rows) != 4 or any(free < 20000 or util > 5 for free, util in rows) or processes.stdout.strip():
            issues.append('Require four idle GPUs with >=20000 MiB free and no compute processes')
    except (OSError, subprocess.SubprocessError, ValueError):
        issues.append('Cannot verify exclusive idle GPU availability')
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('upstream', 'model', 'train', 'val'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    upstream, model, train, val = [getattr(args, name).resolve() for name in ('upstream', 'model', 'train', 'val')]
    issues = preflight(upstream, model, train, val)
    command = [sys.executable, '-m', 'verl.trainer.main', 'config=examples/config.yaml',
               f'data.train_files={train}', f'data.val_files={val}', 'data.use_mobileforge_format=true',
               'data.system_prompt=examples/format_prompt/r1gui_qwen3vl_system.jinja',
               'data.format_prompt=examples/format_prompt/r1gui_qwen3vl_user.jinja',
               f'worker.actor.model.model_path={model}', 'algorithm.adv_estimator=grpo',
               'worker.reward.reward_function=examples/reward_function/adaptive_gui.py:compute_score',
               'worker.rollout.tensor_parallel_size=1', 'worker.rollout.enable_chunked_prefill=false',
               'worker.rollout.n=2', 'worker.rollout.gpu_memory_utilization=0.4',
               'worker.actor.global_batch_size=4', 'worker.actor.micro_batch_size_per_device_for_experience=1',
               'worker.actor.model.freeze_vision_tower=true', 'data.rollout_batch_size=4', 'data.val_batch_size=4',
               'data.min_pixels=65536', 'data.max_pixels=262144', 'data.max_prompt_length=4096',
               'data.max_response_length=512', 'trainer.n_gpus_per_node=4', 'trainer.total_epochs=1',
               'trainer.max_steps=1', 'trainer.save_freq=-1', 'trainer.val_freq=-1',
               'trainer.val_before_train=false', 'trainer.find_last_checkpoint=false',
               'trainer.experiment_name=trace2task_mobileforge_smoke']
    print(json.dumps({'ready': not issues, 'blockers': issues, 'command_template': command,
                      'conversion': 'On execute, native conversion replaces raw train/val paths with generated JSON files',
                      'scope': 'one-step smoke, not paper reproduction or completed training'}, indent=2))
    if args.execute:
        if issues:
            raise SystemExit('Refusing training: preflight blockers')
        # The trainer consumes converted JSON, not raw task/agent/attempt folders.
        conversion_root = Path(tempfile.mkdtemp(prefix='conversion-', dir=upstream.parent))
        for split, source in [('train', train), ('val', val)]:
            output = conversion_root / split
            subprocess.run([sys.executable, 'data_process_verl/mobileforge_data_processor.py',
                            '--rollout_dir', str(source), '--output_dir', str(output),
                            '--format', 'grpo', '--filter_sr_max', '1.0'],
                           cwd=upstream / 'rollout', check=True)
            files = [p for p in output.rglob('mobileforge_grpo_*.json') if '_stats_' not in p.name]
            if len(files) != 1 or not json.loads(files[0].read_text()):
                raise SystemExit('Missing or empty converted ' + split + ' data')
            key = 'data.train_files=' if split == 'train' else 'data.val_files='
            command = [key + str(files[0]) if arg.startswith(key) else arg for arg in command]
        # Resource availability may have changed while converting data.
        if preflight(upstream, model, train, val):
            raise SystemExit('Resources changed during conversion; refusing training')
        env = {**os.environ, 'HF_HUB_OFFLINE': '1', 'WANDB_DISABLED': 'true',
               'CUDA_VISIBLE_DEVICES': '0,1,2,3'}
        subprocess.run(command, cwd=upstream / 'training', env=env, check=True)


if __name__ == '__main__':
    main()

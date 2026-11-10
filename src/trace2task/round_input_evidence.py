"""Read archived local-model input for display without rewriting run evidence."""
import json
from pathlib import Path


def with_local_input(entry, runs_root):
    if entry.get('model_role') != 'executor' or not entry.get('output_directory'):
        return entry
    folder = Path(entry['output_directory']).resolve()
    root = Path(runs_root).resolve()
    path = (folder / 'input.json').resolve()
    if not folder.is_relative_to(root) or not path.is_relative_to(folder):
        return entry
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return entry
    if not isinstance(record, dict) or not isinstance(record.get('messages'), list):
        return entry
    # messages is the native adapter's actual inference input. new_messages and
    # transport_messages may instead describe the pre-adapter service request.
    actual = {key: record[key] for key in ('messages', 'input_stage', 'configuration',
              'image_sources', 'image_order', 'context_management') if key in record}
    actual['evidence_path'] = str(path)
    return {**entry, 'input': actual, 'provider': 'local',
            'system_managed_externally': False}

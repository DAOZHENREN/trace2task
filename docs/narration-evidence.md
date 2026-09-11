# Narration evidence during semantic compilation

When a recording contains both a full `transcript` and timestamped `segments`,
the compiler receives both. A corrected full transcript must not disappear just
because timed speech-recognition segments are also available.

The prompt distinguishes:

- Original full transcript, treated as task-level advisory text without timestamps.
- Original segments, retaining their text, timestamps and additional fields.
- The existing normalized, heuristically aligned segment view.

A SHA-256 receipt binds the narration file bytes used during preparation. Changes
detected while loading stop compilation before the model is called. Hashes show
content identity, not truth, human review or protection against hostile concurrent
filesystem modification. Text disagreement does not establish which version is
correct; neither channel overrides the task or observed evidence.

This changes only the offline semantic compiler's narration input. It does not
rewrite the recording, change the executor, or establish improved task success.
The raw text and segment metadata enter the compiler prompt, so review recordings
for sensitive content before using a remote model.

Run the offline regression tests from the repository root:

```bash
PYTHONPATH=src python -m pytest tests/test_windows_experience.py tests/test_narration.py
```

These tests use synthetic recordings and fake model sessions; no API credentials
or live Windows VM are needed.

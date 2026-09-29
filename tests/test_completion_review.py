import json

import pytest

from trace2task.local_gui_protocol import decode_completion_review


@pytest.mark.parametrize('raw', [
    '{}', 'null', '{"verdict":"complete","verdict":"unknown","evidence":"x","missing":""}',
    '{"verdict":"complete","evidence":"","missing":""}',
    '{"verdict":"complete","evidence":"visible","missing":"not sent"}',
    '{"verdict":"incomplete","evidence":"visible","missing":""}',
    '<tool_call>{"action":"type","text":"test"}</tool_call>',
    '{"verdict":"complete","evidence":"visible","missing":"","actions":[]}',
])
def test_verifier_rejects_actions_missing_evidence_and_duplicate_keys(raw):
    with pytest.raises(ValueError):
        decode_completion_review(raw)


def test_verifier_accepts_only_explicit_read_only_evidence():
    value = {'verdict': 'incomplete', 'evidence': 'Input is empty', 'missing': 'Requested text'}
    assert decode_completion_review(json.dumps(value)) == value

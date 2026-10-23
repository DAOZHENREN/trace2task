"""Compact model-facing JSON without rewriting human text or saved evidence."""
import json


def model_json(value, *, sort_keys=False):
    """Serialize newly constructed model input; audit-file layout is independent."""
    return json.dumps(value, ensure_ascii=False, sort_keys=sort_keys,
                      separators=(',', ':'), allow_nan=False)


def compact_json_suffix(text):
    """Compact the JSON after an A/D preamble, preserving every non-layout byte.

    Do not parse and re-encode old numbers, escapes or duplicate keys. Unknown
    layouts stay verbatim; callers select only structured A/D evidence, never B
    Markdown (C callers pass only its separately verified A suffix).
    """
    prefix, separator, document = text.partition('\n\n')
    if not separator:
        return text
    try:
        if not isinstance(json.loads(document), (dict, list)):
            return text
    except (ValueError, RecursionError):
        return text
    output, quoted, escaped = [], False, False
    for char in document:
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            output.append(char)
        elif char not in ' \t\r\n':
            output.append(char)
    # Keep the preamble and the document's trailing layout as originally saved.
    trailing = document[len(document.rstrip(' \t\r\n')):]
    return prefix + separator + ''.join(output) + trailing

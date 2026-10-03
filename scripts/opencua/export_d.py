"""CLI compatibility entry for the D projection generator."""
import argparse
import json

from trace2task.trace_projection import export, project_actions

__all__ = ['export', 'project_actions']

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("recording", "derived", "output"):
        parser.add_argument("--" + option, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.recording, args.derived, args.output), ensure_ascii=False))

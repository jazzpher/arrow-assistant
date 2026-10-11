"""Export a whitelist of non-private status metadata, never logs or responses."""
import json
import sys
from pathlib import Path


def summarize(data):
    safe = {k: data.get(k) for k in ('sha', 'config', 'passed', 'failed', 'skipped')}
    safe['checks'] = [{'check': c['check'], 'status': c['status']} for c in data['checks']]
    safe['capability'] = 'inconclusive' if data.get('skipped') else 'complete'
    return safe


if __name__ == '__main__':
    source, target = map(Path, sys.argv[1:3])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summarize(json.loads(source.read_text())), indent=2))

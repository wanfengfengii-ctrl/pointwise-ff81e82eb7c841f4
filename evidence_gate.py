import importlib.util
import json
from pathlib import Path
from lab import pair_multiset, run_case

ROOT = Path(__file__).resolve().parent
SOURCES = ('storage.py', 'layout.py', 'table.py', 'planner.py', 'join.py', 'lab.py')


def verify():
    cases = json.loads((ROOT / 'review_cases.json').read_text(encoding='utf-8'))
    evidence = json.loads((ROOT / 'evidence.json').read_text(encoding='utf-8'))
    spec = importlib.util.spec_from_file_location('review_oracle', ROOT / 'review_evidence.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(cases, list) and cases
    names = [c['name'] for c in cases]
    assert len(set(names)) == len(names), 'duplicate case names'
    assert [c['name'] for c in evidence['cases']] == names
    review = (ROOT / 'REVIEW.md').read_text(encoding='utf-8')
    assert (ROOT / 'docs/bucket-join.md').read_text(encoding='utf-8').strip()
    summaries = []
    for case, saved in zip(cases, evidence['cases']):
        actual = run_case(case)
        expected = module.expected(case)
        assert saved['observed'] == actual, 'stale or invented observation: ' + case['name']
        assert pair_multiset(saved['expected']) == pair_multiset(expected), 'stale oracle'
        assert case['name'] in review, 'report must cite the case'
        summaries.append({
            'name': case['name'], 'mode': actual['trace']['plan']['mode'],
            'expected': expected, 'actual': actual['pairs'],
            'equal': pair_multiset(expected) == pair_multiset(actual['pairs'])
        })
    assert evidence['locations'], 'missing source locations'
    for loc in evidence['locations']:
        assert loc['path'] in SOURCES
        lines = (ROOT / loc['path']).read_text(encoding='utf-8').splitlines()
        assert type(loc['line']) is int and 1 <= loc['line'] <= len(lines)
        assert lines[loc['line'] - 1] == loc['text'], 'source location drift'
        assert loc['path'] + ':' + str(loc['line']) in review
    print(json.dumps({'replayed': summaries, 'locations_checked': len(evidence['locations'])},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    verify()

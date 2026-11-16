import argparse
import json
from pathlib import Path
from join import execute
from storage import MemoryObjects, DirectoryObjects
from table import write_table


def load_samples():
    return json.loads(Path(__file__).with_name('samples.json').read_text(encoding='utf-8'))


def build_case(case, objects=None):
    if objects is None:
        objects = MemoryObjects()
    return (write_table(objects, 'left', case['left']),
            write_table(objects, 'right', case['right']))


def run_case(case, objects=None):
    left, right = build_case(case, objects)
    result = execute(left, right, case['how'])
    result['tables'] = {
        side: {
            'manifest': table.manifest,
            'placements': [[r['id'] for r in table.read_bucket(i)]
                           for i in range(len(table.manifest['files']))]
        }
        for side, table in (('left', left), ('right', right))
    }
    return result


def pair_multiset(pairs):
    # Sorting JSON representations handles an unmatched right ID (null).
    return sorted(json.dumps(pair, ensure_ascii=False, separators=(',', ':'))
                  for pair in pairs)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('case', nargs='?', default='incident')
    parser.add_argument('--directory')
    args = parser.parse_args()
    case = load_samples()[args.case]
    objects = DirectoryObjects(args.directory) if args.directory else None
    print(json.dumps(run_case(case, objects), ensure_ascii=False, indent=2))

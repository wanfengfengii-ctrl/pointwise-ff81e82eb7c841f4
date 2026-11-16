import json
from storage import DirectoryObjects
from layout import ENCODINGS, bucket, coefficient, validate_schema


def pack(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def write_table(objects, prefix, spec):
    schema = dict(spec['schema'])
    validate_schema(schema)
    encoding, count = spec['encoding'], spec['buckets']
    if encoding not in ENCODINGS or type(count) is not int or count not in (2, 4):
        raise ValueError('layout')
    parts = [[] for _ in range(count)]
    seen = set()
    for row in spec['rows']:
        if set(row) != {'id', 'key', 'payload'}:
            raise ValueError('row fields')
        if type(row['id']) is not str or not row['id'] or row['id'] in seen:
            raise ValueError('row id')
        if type(row['payload']) is not str:
            raise ValueError('payload')
        coefficient(schema, row['key'])
        seen.add(row['id'])
        parts[bucket(schema, row['key'], encoding, count)].append(dict(row))
    names = [prefix + '/part-' + str(i) + '.json' for i in range(count)]
    for name, rows in zip(names, parts):
        objects.put(name, pack(rows))
    manifest = {
        'version': 1, 'schema': schema, 'row_count': len(seen),
        'layout': {'family': 'numeric', 'hash': 'sha256-v1',
                   'encoding': encoding, 'buckets': count},
        'files': names
    }
    objects.put(prefix + '/manifest.json', pack(manifest))
    return Table(objects, prefix)


class Table:
    def __init__(self, objects, prefix):
        self.objects = objects
        self.manifest = json.loads(objects.get(prefix + '/manifest.json'))

    def read_bucket(self, index):
        name = self.manifest['files'][index]
        return json.loads(self.objects.get(name))

    def read_all(self):
        # Full scan deliberately does not consult the join planner.
        return [row for i in range(len(self.manifest['files']))
                for row in self.read_bucket(i)]


if __name__ == '__main__':
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument('spec')
    parser.add_argument('directory')
    args = parser.parse_args()
    spec = json.loads(Path(args.spec).read_text(encoding='utf-8'))
    table = write_table(DirectoryObjects(args.directory), 'table', spec)
    print(json.dumps(table.manifest, ensure_ascii=False, indent=2))

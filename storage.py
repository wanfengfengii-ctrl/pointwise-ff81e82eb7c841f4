from pathlib import Path, PurePosixPath


def checked_name(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts:
        raise ValueError('unsafe object name')
    return name


class MemoryObjects:
    """Immutable object namespace; the same byte API as DirectoryObjects."""
    def __init__(self):
        self.objects = {}

    def put(self, name, data):
        checked_name(name)
        if not isinstance(data, bytes):
            raise TypeError('objects contain bytes')
        if name in self.objects:
            raise FileExistsError(name)
        self.objects[name] = data

    def get(self, name):
        return self.objects[checked_name(name)]


class DirectoryObjects:
    def __init__(self, root):
        self.root = Path(root)

    def put(self, name, data):
        p = self.root / checked_name(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('xb') as f:
            f.write(data)

    def get(self, name):
        return (self.root / checked_name(name)).read_bytes()

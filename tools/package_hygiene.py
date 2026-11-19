"""Keep filesystem metadata out of executable pfSense package directories."""


def is_metadata(name):
    return name.startswith('._') or name in ('.DS_Store','__MACOSX')


def source_ignore(directory,names):
    return {name for name in names if is_metadata(name) or name=='__pycache__' or name.endswith(('.pyc','.pyo'))}


def assert_clean_tree(root):
    bad=[str(p.relative_to(root)) for p in root.rglob('*') if is_metadata(p.name)]
    if bad:
        raise ValueError('Filesystem metadata must not be packaged: '+', '.join(bad))

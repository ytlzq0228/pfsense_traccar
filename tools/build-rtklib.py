#!/usr/bin/env python3
"""Build a pinned RTKLIB rtkrcv with a local, console-free supervisor mode.

Run on FreeBSD (the target or a compatible builder); no changes to the kernel.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

COMMIT = '180043ee24b6d2b168f98b64be15f69d50046b1a'
URL = f'https://codeload.github.com/tomojitakasu/RTKLIB/tar.gz/{COMMIT}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path)
    parser.add_argument('--prepare-only', action='store_true', help='create patched source tarball without a compiler')
    parser.add_argument('--output', type=Path, default=Path('build/native/rtkrcv'))
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pftracker-rtklib-') as td:
        work = Path(td)
        if args.source:
            source = args.source.resolve()
        else:
            archive = work/'source.tgz'
            urllib.request.urlretrieve(URL, archive)
            with tarfile.open(archive) as tf:
                # Fixed upstream archive; reject traversal and links before extraction.
                for m in tf.getmembers():
                    if m.name.startswith('/') or '..' in Path(m.name).parts or m.issym() or m.islnk():
                        raise ValueError('Unsafe source archive member')
                tf.extractall(work)
            source = work/f'RTKLIB-{COMMIT}'
        tree = work/'build'
        shutil.copytree(source/'src', tree/'src')
        shutil.copytree(source/'app/consapp/rtkrcv', tree/'app/consapp/rtkrcv')
        path = tree/'app/consapp/rtkrcv/rtkrcv.c'
        code = path.read_text()
        # No TTY, remote console, shell commands or control listener in service mode.
        old = 'int i,start=0,port=0,outstat=0,trace=0,sock=0;'
        assert code.count(old) == 1
        code = code.replace(old, old.replace('sock=0;', 'sock=0,noconsole=0;'))
        old = 'if      (!strcmp(argv[i],"-s")) start=1;'
        assert code.count(old) == 1
        code = code.replace(old, old+'\n        else if (!strcmp(argv[i],"-n")) noconsole=1;')
        old = 'else {\n        /* open device for local console */'
        assert code.count(old) == 1
        code = code.replace(old, 'else if (!noconsole) {\n        /* open device for local console */')
        path.write_text(code)
        makefile = path.parent/'gcc/makefile'
        makefile.write_text(makefile.read_text()+'\nrtkrcv:\n\t$(CC) -o rtkrcv *.o $(LDLIBS)\n')
        if args.prepare_only:
            with tarfile.open(output, 'w:gz') as tf:
                tf.add(tree, arcname='RTKLIB')
            return
        flags = '-O2 -std=gnu99 -I../../../../src -I.. -DTRACE -DENAGLO -DENAGAL -DENACMP -DENAQZS -DNFREQ=3 -DNEXOBS=3 -DSVR_REUSEADDR'
        subprocess.run(['make', '-j4', 'CC=cc', f'CFLAGS={flags}', 'LDLIBS=-lm -lpthread'],
                       cwd=path.parent/'gcc', check=True)
        shutil.copy2(path.parent/'gcc/rtkrcv', output)
        subprocess.run(['strip', str(output)], check=True)
        output.chmod(0o755)
        license_file = source/'readme.txt'
        shutil.copy2(license_file, output.parent/'RTKLIB-readme.txt')
        shutil.copy2(source/'LICENSE.txt', output.parent/'RTKLIB-LICENSE.txt')
        info = {'upstream': URL, 'commit': COMMIT, 'patch': 'add -n console-free mode',
                'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                'platform': subprocess.check_output(['uname','-sm'], text=True).strip()}
        output.with_suffix('.json').write_text(json.dumps(info, indent=2)+'\n')
        print(json.dumps(info))


if __name__ == '__main__':
    main()

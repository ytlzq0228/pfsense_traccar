#!/usr/bin/env python3
"""Build on the target pfSense release (or matching FreeBSD builder).
Usage: python3.11 tools/build-package.py [output-dir]
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import hashlib

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
from pftracker.config import DEFAULTS
from pftracker import __version__

output = Path(sys.argv[1] if len(sys.argv)>1 else root/'dist').resolve()
output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='traccar-build-') as temporary:
    work=Path(temporary)
    stage=work/'stage'; shutil.copytree(root/'package/files',stage)
    code=stage/'usr/local/share/pftracker'
    shutil.copytree(root/'src/pftracker',code/'pftracker',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (code/'defaults.json').write_text(json.dumps(DEFAULTS,indent=2)+'\n')
    (code/'LICENSE').write_text((root/'LICENSE').read_text())
    # Windows source archives do not preserve the executable bit.
    for script in (stage/'usr/local/libexec/traccar', code/'traccar.rc'):
        script.write_bytes(script.read_bytes().replace(b'\r\n',b'\n'))
    (stage/'usr/local/libexec/traccar').chmod(0o755)
    native = Path(os.environ.get('PFTRACKER_RTKLIB', str(root/'build/native/rtkrcv'))).resolve()
    if not native.is_file():
        raise SystemExit('Build RTKLIB first: python3.11 tools/build-rtklib.py; or set PFTRACKER_RTKLIB')
    subprocess.run([str(native),'-h'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
    shutil.copy2(native, stage/'usr/local/libexec/pftracker-rtkrcv')
    (stage/'usr/local/libexec/pftracker-rtkrcv').chmod(0o755)
    shutil.copy2(root/'third_party/RTKLIB-LICENSE.txt',code/'RTKLIB-LICENSE.txt')
    (code/'RTKLIB-PROVENANCE.json').write_text(json.dumps(dict(
        repository='https://github.com/tomojitakasu/RTKLIB',
        commit='180043ee24b6d2b168f98b64be15f69d50046b1a',
        patch='-n console-free service mode; explicit portable make link recipe',
        build_recipe='tools/build-rtklib.py',sha256=hashlib.sha256(native.read_bytes()).hexdigest()),indent=2))
    deps={}
    for package in ('python311','py311-sqlite3'):
        name,version,origin=subprocess.check_output(['pkg','query','%n %v %o',package],text=True).strip().split()
        deps[name]=dict(origin=origin,version=version)
    manifest=dict(name='pfSense-pkg-Traccar',version=__version__,origin='net/pfSense-pkg-Traccar',
                  comment='Reliable USB GNSS tracker for Traccar',desc='CH340 userspace GPS, persistent outbox, native pfSense configuration and status.',
                  maintainer='local@localhost',www='https://github.com/ytlzq0228/pfsense_traccar',prefix='/usr/local',
                  abi=subprocess.check_output(['pkg','config','ABI'],text=True).strip(),
                  arch=subprocess.check_output(['pkg','config','ABI'],text=True).strip(),
                  licenselogic='multi',licenses=['MIT','BSD2CLAUSE'],deps=deps,
                  scripts={'post-install':'/etc/rc.packages Traccar POST-INSTALL',
                           'pre-deinstall':'if [ -x /usr/local/etc/rc.d/traccar.sh ]; then /usr/local/etc/rc.d/traccar.sh stop; fi\n/etc/rc.packages Traccar DEINSTALL',
                           'post-deinstall':'/etc/rc.packages Traccar POST-DEINSTALL'})
    metadata=work/'meta'; metadata.mkdir()
    (metadata/'+MANIFEST').write_text(json.dumps(manifest))
    paths=sorted('/'+str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file())
    (metadata/'plist').write_text('\n'.join(p.lstrip('/') for p in paths)+'\n')
    # Explicit paths avoid dependence on ports/pkg-plist prefix expansion.
    manifest['files']={p:'-' for p in paths}
    (metadata/'+MANIFEST').write_text(json.dumps(manifest))
    subprocess.run(['pkg','create','-r',str(stage),'-m',str(metadata),'-o',str(output)],check=True)

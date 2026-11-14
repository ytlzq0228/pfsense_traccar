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
    deps={}
    for package in ('python311','py311-sqlite3'):
        name,version,origin=subprocess.check_output(['pkg','query','%n %v %o',package],text=True).strip().split()
        deps[name]=dict(origin=origin,version=version)
    manifest=dict(name='pfSense-pkg-Traccar',version=__version__,origin='net/pfSense-pkg-Traccar',
                  comment='Reliable USB GNSS tracker for Traccar',desc='CH340 userspace GPS, persistent outbox, native pfSense configuration and status.',
                  maintainer='local@localhost',www='https://github.com/ytlzq0228/pfsense_traccar',prefix='/usr/local',
                  abi=subprocess.check_output(['pkg','config','ABI'],text=True).strip(),
                  arch=subprocess.check_output(['pkg','config','ABI'],text=True).strip(),
                  licenselogic='single',licenses=['MIT'],deps=deps,
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

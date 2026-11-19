"""macOS sidecar files must never become PHP includes or widget assets."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('package_hygiene',Path(__file__).resolve().parents[1]/'tools/package_hygiene.py')
hygiene=importlib.util.module_from_spec(spec);spec.loader.exec_module(hygiene)


class PackageHygieneTests(unittest.TestCase):
    def test_copy_filters_metadata_recursively_and_keeps_real_files(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source';source.mkdir()
            for name in ['widgets/include/traccar.inc','widgets/include/._traccar.inc',
                         'widgets/widgets/traccar.widget.php','widgets/widgets/._traccar.widget.php',
                         'widgets/javascript/traccar.js','widgets/css/traccar.css',
                         'widgets/._include','.DS_Store','__MACOSX/._widgets',
                         'pftracker/__pycache__/service.pyc','pftracker/service.py']:
                p=source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('example')
            stage=Path(td)/'stage';shutil.copytree(source,stage,ignore=hygiene.source_ignore)
            hygiene.assert_clean_tree(stage)
            actual={str(p.relative_to(stage)).replace('\\','/') for p in stage.rglob('*') if p.is_file()}
            self.assertEqual(actual,{'widgets/include/traccar.inc','widgets/widgets/traccar.widget.php',
                'widgets/javascript/traccar.js','widgets/css/traccar.css','pftracker/service.py'})

    def test_manifest_guard_rejects_metadata_added_after_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);hygiene.assert_clean_tree(root)
            (root/'._widget.inc').write_bytes(bytes.fromhex('0005160700020000'))
            with self.assertRaisesRegex(ValueError,'Filesystem metadata'):
                hygiene.assert_clean_tree(root)

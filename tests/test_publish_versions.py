"""Version replacement in czdev publish PRs, without uploads or live changes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from czdev import publish


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def manifest(self, version, arch='arm64'):
        item = dict(package='sample', version=version,
                    filename=f'sample_{publish.asset_safe(version)}_{arch}.deb')
        path = self.root / (item['filename'] + '.release.json')
        path.write_text(json.dumps(item))
        return path, item

    def test_update_removes_older_versions_across_architecture_changes(self):
        older = [self.manifest(v)[0] for v in ('1.9', '1.10~rc1', '1.10-2')]
        latest, item = self.manifest('1.10-10', 'all')
        listing = self.root / 'meta.json'
        listing.write_text('{"title":"keep"}')
        publish.retire_older_manifests(self.root, item)
        self.assertTrue(latest.exists())
        self.assertTrue(listing.exists())
        self.assertFalse(any(p.exists() for p in older))

    def test_epoch_equal_version_and_newer_manifests_are_preserved(self):
        old, _ = self.manifest('9.0')
        current, item = self.manifest('1:0.1')
        equal, _ = self.manifest('1:0.1', 'all')
        newer, _ = self.manifest('2:0.1')
        publish.retire_older_manifests(self.root, item)
        self.assertFalse(old.exists())
        self.assertTrue(all(p.exists() for p in (current, equal, newer)))

    def test_missing_replacement_preserves_all_old_versions(self):
        old, item = self.manifest('1.0')
        item['filename'] = 'missing_2.0_all.deb'
        with self.assertRaisesRegex(ValueError, 'replacement'):
            publish.retire_older_manifests(self.root, item)
        self.assertTrue(old.exists())

    def test_metadata_only_branch_never_runs_version_retirement(self):
        with patch.object(publish, 'run_cmd_in'), patch.object(publish.time, 'sleep'), \
             patch.object(publish, 'retire_older_manifests') as retire:
            gh = unittest.mock.Mock()
            gh.get_ref_sha.return_value = 'upstream'
            publish.push_pool_branch(gh, 'owner', 'repo', 'metadata/sample', 'sample',
                                     str(self.root), {}, {}, {}, 'metadata only')
        retire.assert_not_called()


if __name__ == '__main__':
    unittest.main()

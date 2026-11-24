"""Stored-credential file modes, without contacting GitHub."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from czdev import auth


class CredentialModeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

        previous_home = os.environ.get('HOME')
        os.environ['HOME'] = str(self.home)
        self.addCleanup(self._restore_home, previous_home)

        # A permissive umask is the worst case: nothing masks a wide creation.
        previous_umask = os.umask(0o000)
        self.addCleanup(os.umask, previous_umask)

    def _restore_home(self, previous):
        if previous is None:
            os.environ.pop('HOME', None)
        else:
            os.environ['HOME'] = previous

    def mode(self, path):
        return stat.S_IMODE(path.stat().st_mode)

    def test_token_is_never_group_or_world_readable(self):
        auth.save_credentials({'github_token': 'token', 'username': 'someone'})
        path = auth.credentials_path()
        self.assertEqual(self.mode(path), 0o600)
        self.assertEqual(json.loads(path.read_text())['github_token'], 'token')

    def test_directory_is_owner_only(self):
        auth.save_credentials({'github_token': 'token'})
        self.assertEqual(self.mode(auth.credentials_path().parent), 0o700)

    def test_existing_wide_login_is_repaired(self):
        # An earlier version left ~/.czdev at 0755 and the file at 0644.
        directory = self.home / '.czdev'
        directory.mkdir(mode=0o755)
        legacy = directory / 'credentials'
        legacy.write_text('{}')
        os.chmod(legacy, 0o644)

        auth.save_credentials({'github_token': 'token'})

        self.assertEqual(self.mode(directory), 0o700)
        self.assertEqual(self.mode(legacy), 0o600)

    def test_file_is_created_narrow_without_relying_on_chmod(self):
        # Neutralise only the trailing chmod of the credentials file, so this
        # observes the mode the file was *created* with. The previous
        # implementation created it at the process umask -- 0666 under the
        # umask set in setUp -- and depended entirely on that chmod to narrow
        # it, leaving the token readable in between.
        real_chmod = os.chmod
        target = str(auth.credentials_path())

        def chmod_except_credentials(path, mode, *args, **kwargs):
            if str(path) == target:
                return None
            return real_chmod(path, mode, *args, **kwargs)

        with patch.object(auth.os, 'chmod', side_effect=chmod_except_credentials):
            auth.save_credentials({'github_token': 'token'})

        self.assertEqual(self.mode(auth.credentials_path()), 0o600)

    def test_round_trips_through_load(self):
        auth.save_credentials({'github_token': 'token', 'username': 'someone'})
        self.assertEqual(auth.load_token(), 'token')
        self.assertEqual(auth.load_credentials()['username'], 'someone')


if __name__ == '__main__':
    unittest.main()

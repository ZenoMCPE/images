"""Public worker admission, credential separation, and opaque local packaging checks."""

import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
import uuid
from unittest import mock
import zipfile

SPEC = importlib.util.spec_from_file_location('windows_worker', Path(__file__).parents[1] / 'windows-repair.py')
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def request():
    """Create distinct canonical request identities without real service or source data."""
    original = {'run_id': 10, 'workflow_id': 20, 'artifact_id': 30,
                'public_sha': 'd' * 40, 'request_id': str(uuid.uuid4()),
                'artifact_digest': 'sha256:' + 'e' * 64}
    env = {'SOURCE_SHA': 'a' * 40, 'RELEASE_VERSION': '0.1.2', 'REQUEST_ID': str(uuid.uuid4()),
           'TOOL_SHA': 'b' * 40, 'RUNTIME_SHA': 'c' * 40, 'INPUT_SHA': 'f' * 64,
           'ORIGINAL': json.dumps(original), 'GITHUB_REPOSITORY': 'ZenoMCPE/images'}
    return env, original


def metadata(env, original):
    """Model exactly the successful public producer named by the private coordinator."""
    repository = {'full_name': 'ZenoMCPE/images', 'id': 100}
    run = {'id': original['run_id'], 'workflow_id': original['workflow_id'],
           'head_sha': original['public_sha'], 'head_branch': 'main',
           'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
           'display_title': f"Native {original['request_id']} {env['SOURCE_SHA']} {env['RELEASE_VERSION']}",
           'repository': repository, 'head_repository': dict(repository)}
    artifact = {'id': original['artifact_id'], 'name': 'release-windows-x86_64',
                'expired': False, 'digest': original['artifact_digest'], 'size_in_bytes': 100,
                'workflow_run': {'id': original['run_id'], 'head_sha': original['public_sha'],
                                 'head_branch': 'main', 'repository_id': 100, 'head_repository_id': 100}}
    return run, artifact


class AdmissionTests(unittest.TestCase):
    """Reject ambiguous provenance before any source/tool execution."""

    def test_request_requires_exact_typed_original_and_immutable_pins(self):
        env, original = request()
        self.assertEqual(worker.validate_request(env), original)
        for key, value in [('SOURCE_SHA', 'main'), ('TOOL_SHA', 'b' * 39),
                           ('RUNTIME_SHA', 'c' * 64), ('INPUT_SHA', 'f' * 40),
                           ('GITHUB_REPOSITORY', 'fork/images')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                worker.validate_request(env | {key: value})
        for changed in [original | {'run_id': True}, original | {'extra': 'ignored'},
                        original | {'artifact_digest': 'unknown'}, original | {'request_id': '../path'}]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                worker.validate_request(env | {'ORIGINAL': json.dumps(changed)})

    def test_original_run_and_artifact_must_match_every_provenance_field(self):
        env, original = request()
        run, artifact = metadata(env, original)
        worker.verify_original(run, artifact, original, env)
        for key, value in [('id', 11), ('workflow_id', 21), ('head_sha', '0' * 40),
                           ('head_branch', 'feature'), ('event', 'pull_request'),
                           ('conclusion', 'failure'), ('display_title', 'latest')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                worker.verify_original(run | {key: value}, artifact, original, env)
        fork = copy.deepcopy(run)
        fork['head_repository']['full_name'] = 'fork/images'
        with self.assertRaises(ValueError):
            worker.verify_original(fork, artifact, original, env)
        for key, value in [('id', 31), ('expired', True), ('digest', 'sha256:' + '0' * 64),
                           ('name', 'release-macos-arm64'), ('size_in_bytes', worker.MAX_DOWNLOAD_BYTES + 1)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                worker.verify_original(run, artifact | {key: value}, original, env)
        wrong = copy.deepcopy(artifact)
        wrong['workflow_run']['id'] = 11
        with self.assertRaises(ValueError):
            worker.verify_original(run, wrong, original, env)

    def test_tool_read_uses_ephemeral_read_token_and_exact_git_blob_identity(self):
        env, _ = request()
        env['GH_TOKEN'] = 'public-read-token'
        env['CLIENT_SOURCE_TOKEN'] = 'private-read-token'
        content = b'# opaque packaging fixture\n'
        item = {'type': 'file', 'path': worker.TOOL_PATH, 'encoding': 'base64',
                'size': len(content), 'content': base64.b64encode(content).decode(),
                'sha': hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()}
        calls = []

        def api(endpoint, variables, log):
            """Record scoped credentials without issuing a request or printing source."""
            calls.append((endpoint, dict(variables)))
            return {'sha': env['TOOL_SHA']} if '/commits/' in endpoint else item

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(worker, 'api_json', side_effect=api):
            path = worker.fetch_tool(Path(directory), env, io.BytesIO())
            self.assertEqual(path.read_bytes(), content)
            self.assertTrue(all(variables['GH_TOKEN'] == 'private-read-token' for _, variables in calls))
            self.assertTrue(all(env['TOOL_SHA'] in endpoint for endpoint, _ in calls))
        item['sha'] = '0' * 40
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(worker, 'api_json', side_effect=api), self.assertRaises(ValueError):
            worker.fetch_tool(Path(directory), env, io.BytesIO())

    def test_offline_child_does_not_receive_either_api_credential(self):
        env, _ = request()
        env.update(GH_TOKEN='public-read', CLIENT_SOURCE_TOKEN='private-read',
                   GITHUB_TOKEN='public-token', SOURCE_READ_TOKEN='read-token', GH_DEBUG='api')
        child = worker.helper_environment(env)
        for key in ('GH_TOKEN', 'CLIENT_SOURCE_TOKEN', 'GITHUB_TOKEN', 'SOURCE_READ_TOKEN', 'GH_DEBUG'):
            self.assertNotIn(key, child)
        self.assertEqual(child['SOURCE_SHA'], env['SOURCE_SHA'])


class PayloadTests(unittest.TestCase):
    """Admit only four binaries/checksums without reproducing private notice policy."""

    def setUp(self):
        """Prepare tiny release-shaped public artifacts in an owned temporary directory."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env, self.original = request()
        self.files = self.root / 'files'
        self.files.mkdir()
        self.make_deliverables(self.files)
        self.env['INPUT_SHA'] = worker.file_digest(self.files / 'ZenoClient-0.1.2-windows-x86_64.zip')
        self.container = self.root / 'artifact.zip'
        with zipfile.ZipFile(self.container, 'w') as archive:
            for path in self.files.iterdir():
                archive.write(path, path.name)

    def make_deliverables(self, directory):
        """Write minimal allowed package and PE/checksum pairs for admission tests."""
        portable = directory / 'ZenoClient-0.1.2-windows-x86_64.zip'
        with zipfile.ZipFile(portable, 'w') as archive:
            archive.writestr(portable.stem + '/ZenoClient.exe', b'MZ unchanged fixture')
        setup = portable.with_name(portable.stem + '-setup.exe')
        header = bytearray(64)
        header[:2] = b'MZ'
        header[60:64] = (64).to_bytes(4, 'little')
        setup.write_bytes(header + b'PE\0\0fixture')
        for path in (portable, setup):
            path.with_name(path.name + '.sha256').write_text(f'{worker.file_digest(path)}  {path.name}\n')

    def test_outer_artifact_rejects_extra_source_aliases_and_links(self):
        extracted = self.root / 'extracted'
        extracted.mkdir()
        worker.extract_original(self.container, extracted, self.env)
        self.assertEqual({path.name for path in extracted.iterdir()}, worker.asset_names('0.1.2'))
        with zipfile.ZipFile(self.container, 'a') as archive:
            archive.writestr('private/source.py', b'never admissible')
        with self.assertRaises(ValueError):
            worker.extract_original(self.container, extracted, self.env)
        with zipfile.ZipFile(self.container, 'w') as archive:
            for path in self.files.iterdir():
                info = zipfile.ZipInfo(path.name)
                if path.suffix == '.zip':
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, path.read_bytes())
        extracted = self.root / 'links'
        extracted.mkdir()
        with self.assertRaises(ValueError):
            worker.extract_original(self.container, extracted, self.env)

    def test_download_requires_full_service_artifact_hash_and_size(self):
        body = self.container.read_bytes()
        artifact = {'size_in_bytes': len(body)}
        original = self.original | {'artifact_digest': 'sha256:' + hashlib.sha256(body).hexdigest()}

        def download(command, **kwargs):
            """Model a binary API response through the admitted destination stream."""
            kwargs['stdout'].write(body)
            return subprocess.CompletedProcess(command, 0)

        with mock.patch.object(worker.subprocess, 'run', side_effect=download):
            worker.download_original(self.root / 'download.zip', artifact, original, self.env, io.BytesIO())
            with self.assertRaises(ValueError):
                worker.download_original(self.root / 'bad.zip', artifact,
                                         original | {'artifact_digest': 'sha256:' + '0' * 64}, self.env, io.BytesIO())

    def test_output_allowlist_excludes_private_reports_and_installer_work(self):
        (self.files / 'windows-repair.json').write_text('private verification metadata')
        (self.files / 'private.log').write_text('private build output')
        output = self.root / 'public'
        worker.admit_output(self.files, output, self.env)
        self.assertEqual({path.name for path in output.iterdir()}, worker.asset_names('0.1.2'))

    def exercise_repair(self, fail=False):
        """Run the public wrapper against a fake offline helper and admitted producer."""
        source = self.root / 'private-source'
        module = source / 'mods/zeno-client'
        module.mkdir(parents=True)
        (module / 'Cargo.toml').write_text('[package]\nversion="0.1.2"\n')
        tool = source / '.native-windows-repair.py'
        tool.write_text('# fixture\n')
        env = self.env | {'GH_TOKEN': 'public-read', 'CLIENT_SOURCE_TOKEN': 'private-read'}
        run, artifact = metadata(env, self.original)
        artifact['size_in_bytes'] = self.container.stat().st_size
        self.original['artifact_digest'] = 'sha256:' + worker.file_digest(self.container)
        artifact['digest'] = self.original['artifact_digest']
        env['ORIGINAL'] = json.dumps(self.original)
        commands = []

        def child(command, **kwargs):
            """Return local package output while keeping the private marker in scratch logs."""
            commands.append(command)
            self.assertNotIn('--publish', command)
            self.assertNotIn('--repository', command)
            self.assertNotIn('GH_TOKEN', kwargs['env'])
            self.assertNotIn('CLIENT_SOURCE_TOKEN', kwargs['env'])
            corrected = Path(command[command.index('--output') + 1])
            corrected.mkdir()
            self.make_deliverables(corrected)
            kwargs['stdout'].write(b'private helper stdout\n')
            if fail:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)

        def download(destination, *args):
            """Copy the already-admitted fixture rather than contacting GitHub."""
            shutil.copy2(self.container, destination)

        captured = io.StringIO()
        encrypted = []

        def encrypt(path, context):
            """Capture scratch diagnostics to prove plaintext remains out of console output."""
            encrypted.append(path.read_bytes())
            self.assertEqual(context['RELEASE_PLATFORM'], 'windows')
            self.assertEqual(context['RELEASE_ARCH'], 'x86_64')

        if fail:
            env['PRIVATE_DEBUG_PUBLIC_KEY'] = 'fixture encryption handled by mock'
        with mock.patch.object(worker.NATIVE, 'inspect_source', return_value=env['RUNTIME_SHA']), \
                mock.patch.object(worker, 'api_json', side_effect=[run, artifact]), \
                mock.patch.object(worker, 'download_original', side_effect=download), \
                mock.patch.object(worker, 'fetch_tool', return_value=tool), \
                mock.patch.object(worker.subprocess, 'run', side_effect=child), \
                mock.patch.object(worker.NATIVE, 'encrypt_failure_log', side_effect=encrypt), \
                contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            if fail:
                with self.assertRaises(subprocess.CalledProcessError):
                    worker.repair(source, self.root / 'repaired-public', env)
            else:
                worker.repair(source, self.root / 'repaired-public', env)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], 'python')
        self.assertNotIn('private helper stdout', captured.getvalue())
        self.assertNotIn(str(source), captured.getvalue())
        return captured.getvalue(), encrypted

    def test_repair_is_local_no_publish_no_compiler_and_suppresses_child_output(self):
        self.exercise_repair()

    def test_private_failure_output_is_encrypted_and_no_payload_is_admitted(self):
        console, encrypted = self.exercise_repair(fail=True)
        self.assertEqual(len(encrypted), 1)
        self.assertIn(b'private helper stdout', encrypted[0])
        self.assertIn('Encrypted failure diagnostics prepared', console)
        self.assertFalse((self.root / 'repaired-public').exists())


if __name__ == '__main__':
    unittest.main()

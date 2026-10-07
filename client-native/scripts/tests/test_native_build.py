from pathlib import Path
import hashlib
import contextlib
import io
import subprocess
import importlib.util
import tempfile
import unittest
import uuid
import zipfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('native_builder', Path(__file__).parents[1] / 'native-build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class NativeBuilderTests(unittest.TestCase):
    def test_request_requires_full_sha_plain_version_and_canonical_uuid(self):
        valid = {'SOURCE_SHA': 'a' * 40, 'RELEASE_VERSION': '0.1.3', 'REQUEST_ID': str(uuid.uuid4())}
        builder.validate_request(valid)
        for field, value in [('SOURCE_SHA', 'main'), ('SOURCE_SHA', 'a' * 39),
                             ('RELEASE_VERSION', '0.1.3;echo nope'), ('RELEASE_VERSION', '01.2.3'),
                             ('REQUEST_ID', '../source')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                builder.validate_request(valid | {field: value})

    def archive(self, root, name):
        archive = root / 'ZenoClient-0.1.3-windows-x86_64.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr(name, b'compiled fixture')
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        archive.with_name(archive.name + '.sha256').write_text(f'{digest}  {archive.name}\n')
        return archive

    def test_artifact_admission_rejects_source_logs_and_archive_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = self.archive(root, 'ZenoClient-0.1.3-windows-x86_64/ZenoClient.exe')
            builder.verify_archive(allowed, '0.1.3', 'windows', 'x86_64')
            for name in ['../source.rs', '/absolute/ZenoClient.exe',
                         'ZenoClient-0.1.3-windows-x86_64/src/main.rs',
                         'ZenoClient-0.1.3-windows-x86_64/build.log',
                         'ZenoClient-0.1.3-windows-x86_64/runtime/core.go']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    builder.verify_archive(self.archive(root, name), '0.1.3', 'windows', 'x86_64')

    def test_nested_upstream_license_paths_are_admitted_without_source_files(self):
        prefix = 'ZenoClient-0.1.3-windows-x86_64/'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in ['licenses/regex-syntax-0.8.11/src/unicode_tables/LICENSE-UNICODE',
                         'licenses/ring-0.17.14/src/polyfill/once_cell/LICENSE-APACHE',
                         'licenses/ring-0.17.14/src/polyfill/once_cell/LICENSE-MIT',
                         'licenses/tracing-core-0.1.36/src/spin/LICENSE',
                         'licenses/ring-0.17.14/src/polyfill/once_cell/']:
                with self.subTest(path=path):
                    builder.verify_archive(self.archive(root, prefix + path), '0.1.3', 'windows', 'x86_64')
            for path in ['licenses/ring/src/main.rs', 'licenses/ring/src/LICENSE.rs',
                         'licenses/ring/src/LICENSE.cpp', 'licenses/ring/src/Cargo.toml',
                         'runtime/licenses/ring/src/LICENSE', 'src/LICENSE',
                         'licenses/ring/target/LICENSE', 'licenses/ring/.git/LICENSE',
                         'licenses/ring/src/../../../../main.rs', 'licenses\\ring\\src\\main.rs']:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    builder.verify_archive(self.archive(root, prefix + path), '0.1.3', 'windows', 'x86_64')

    def test_bad_checksum_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = self.archive(Path(directory), 'ZenoClient-0.1.3-windows-x86_64/ZenoClient.exe')
            archive.with_name(archive.name + '.sha256').write_text('wrong digest')
            with self.assertRaises(ValueError):
                builder.verify_archive(archive, '0.1.3', 'windows', 'x86_64')

    def test_setup_admission_requires_pe_and_exact_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            setup = Path(directory) / 'ZenoClient-0.1.3-windows-x86_64-setup.exe'
            header = bytearray(64)
            header[:2] = b'MZ'
            header[60:64] = (64).to_bytes(4, 'little')
            setup.write_bytes(header + b'PE\0\0fixture')
            checksum = setup.with_name(setup.name + '.sha256')
            checksum.write_text(f'{hashlib.sha256(setup.read_bytes()).hexdigest()}  {setup.name}\n')
            builder.verify_setup(setup, '0.1.3', 'x86_64')
            checksum.write_text('bad')
            with self.assertRaises(ValueError):
                builder.verify_setup(setup, '0.1.3', 'x86_64')
            setup.write_bytes(b'MZ-not-a-PE')
            with self.assertRaises(ValueError):
                builder.verify_setup(setup, '0.1.3', 'x86_64')



class RuntimeReuseTests(unittest.TestCase):
    def exercise(self, reuse_status, platform='macos', arch='arm64', mismatched_checkout=False, setup_status=0, missing_setup=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'private-source'
            runtime = source / 'build/cinnabar'
            runtime.mkdir(parents=True)
            archive = source / 'dist' / f'ZenoClient-0.1.3-{platform}-{arch}.zip'
            archive.parent.mkdir()
            with zipfile.ZipFile(archive, 'w') as packaged:
                packaged.writestr(archive.stem + '/compiled-fixture', b'compiled')
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            archive.with_name(archive.name + '.sha256').write_text(f'{digest}  {archive.name}\n')
            if platform == 'windows' and not missing_setup:
                setup = archive.with_name(archive.stem + '-setup.exe')
                header = bytearray(64)
                header[:2] = b'MZ'
                header[60:64] = (64).to_bytes(4, 'little')
                setup.write_bytes(header + b'PE\0\0fixture')
                setup.with_name(setup.name + '.sha256').write_text(f'{hashlib.sha256(setup.read_bytes()).hexdigest()}  {setup.name}\n')
            output = root / 'artifacts'
            revision = 'a' * 40
            env = {'SOURCE_SHA': 'b' * 40, 'RELEASE_VERSION': '0.1.3',
                   'REQUEST_ID': str(uuid.uuid4()), 'RELEASE_PLATFORM': platform,
                   'RELEASE_ARCH': arch, 'RELEASE_TARGET': builder.TARGETS[platform, arch]}
            commands = []
            def run(command, **kwargs):
                commands.append(command)
                status = reuse_status if command[:2] == ['python', 'scripts/reuse-runtime.py'] else 0
                if 'packaging/windows/check-setup.ps1' in command:
                    status = setup_status
                return subprocess.CompletedProcess(command, status)
            captured = io.StringIO()
            error = None
            with patch.object(builder, 'inspect_source', return_value=revision), \
                 patch.object(builder.subprocess, 'check_output', return_value='c' * 40 if mismatched_checkout else revision), \
                 patch.object(builder.subprocess, 'run', side_effect=run), \
                 contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                try:
                    builder.build(source, output, env)
                except (RuntimeError, ValueError, FileNotFoundError) as failure:
                    error = str(failure)
            self.assertNotIn(str(source), captured.getvalue())
            published = sorted(path.name for path in output.iterdir()) if output.exists() else []
            pin = (runtime / '.zeno-pinned-revision').read_text() if (runtime / '.zeno-pinned-revision').exists() else None
            return commands, error, published, pin

    def test_verified_reuse_skips_only_runtime_build_and_preserves_native_checks(self):
        for platform, arch in [('windows', 'x86_64'), ('macos', 'arm64'), ('macos', 'x86_64')]:
            with self.subTest(platform=platform, arch=arch):
                commands, error, published, pin = self.exercise(0, platform, arch)
                self.assertIsNone(error)
                self.assertEqual(len(published), 4 if platform == 'windows' else 2)
                self.assertIsNone(pin)
                probe = ['python', 'scripts/reuse-runtime.py', '--revision', 'a' * 40,
                         '--platform', platform, '--arch', arch, '--output', 'build/runtime']
                self.assertIn(probe, commands)
                self.assertFalse(any(command[0] == 'go' or 'bedrock-client' in command or 'scripts/stage-runtime.py' in command for command in commands))
                self.assertIn(['python', '-m', 'unittest', 'discover', '-s', 'scripts/tests'], commands)
                self.assertIn(['cargo', 'test', '--locked', '-p', 'zeno-client-mod'], commands)
                self.assertIn(['cargo', 'build', '--locked', '--release', '-p', 'zeno-client-mod', '--target', 'wasm32-unknown-unknown'], commands)
                self.assertTrue(any('zeno-client-pack' in command for command in commands))
                self.assertIn(['cargo', 'test', '--release', '--locked', '--target', builder.TARGETS[platform, arch]], commands)
                self.assertIn(['cargo', 'build', '--locked', '--release', '--target', builder.TARGETS[platform, arch]], commands)
                self.assertIn(['python', 'scripts/package-release.py'], commands)
                if platform != 'windows':
                    self.assertEqual(commands[-1], ['python', 'scripts/package-release.py'])
                if platform == 'windows':
                    self.assertIn(['pwsh', '-NoProfile', '-File', 'scripts/tests/install.Tests.ps1'], commands)
                    self.assertEqual(commands[-1], ['pwsh', '-NoProfile', '-File', 'packaging/windows/check-setup.ps1',
                        '-Setup', 'dist/ZenoClient-0.1.3-windows-x86_64-setup.exe',
                        '-Archive', 'dist/ZenoClient-0.1.3-windows-x86_64.zip', '-Version', '0.1.3', '-Revision', 'a' * 40])

    def test_windows_install_check_failure_prevents_every_artifact_upload(self):
        commands, error, published, pin = self.exercise(0, 'windows', 'x86_64', setup_status=1)
        self.assertEqual(error, 'Private build stage failed')
        self.assertIn('packaging/windows/check-setup.ps1', commands[-1])
        self.assertEqual(published, [])

    def test_windows_requires_setup_pair_in_addition_to_portable_archive(self):
        commands, error, published, pin = self.exercise(0, 'windows', 'x86_64', missing_setup=True)
        self.assertIsNotNone(error)
        self.assertEqual(published, [])

    def test_unavailable_exact_runtime_falls_back_to_pinned_source_build(self):
        commands, error, published, pin = self.exercise(75)
        self.assertIsNone(error)
        self.assertEqual(len(published), 2)
        self.assertEqual(pin, 'a' * 40)
        self.assertEqual(sum(command[0] == 'go' for command in commands), 2)
        self.assertTrue(any('bedrock-client' in command for command in commands))
        self.assertIn(['python', 'scripts/stage-runtime.py', '--source', 'build/cinnabar', '--output', 'build/runtime', '--platform', 'macos'], commands)
        self.assertEqual(commands[-1], ['python', 'scripts/package-release.py'])

    def test_invalid_reuse_aborts_before_build_or_artifact_admission(self):
        for status in [1, 2, -9]:
            with self.subTest(status=status):
                commands, error, published, pin = self.exercise(status)
                self.assertEqual(error, 'Private build stage failed')
                self.assertEqual(commands[-1][:2], ['python', 'scripts/reuse-runtime.py'])
                self.assertEqual(published, [])
                self.assertIsNone(pin)

    def test_runtime_checkout_validation_still_precedes_reuse(self):
        commands, error, published, pin = self.exercise(0, mismatched_checkout=True)
        self.assertEqual(error, 'Unexpected runtime revision')
        self.assertEqual(commands, [])
        self.assertEqual(published, [])


@unittest.skipUnless(importlib.util.find_spec('cryptography'), 'Diagnostics crypto library not installed')
class FailureDiagnosticsTests(unittest.TestCase):
    def test_failure_log_exports_only_bounded_authenticated_ciphertext(self):
        import base64
        import json
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from unittest.mock import patch
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / 'private.log'
            log.write_bytes(b'private source secret: compiler diagnostics')
            env = {'GITHUB_WORKSPACE': str(root), 'PRIVATE_DEBUG_PUBLIC_KEY': public,
                   'SOURCE_SHA': 'a' * 40, 'REQUEST_ID': str(uuid.uuid4()),
                   'RELEASE_PLATFORM': 'windows', 'RELEASE_ARCH': 'x86_64'}
            with patch.object(builder, 'MAX_DIAGNOSTIC_BYTES', 10):
                builder.encrypt_failure_log(log, env)
            exported = root / 'encrypted-diagnostics/native-debug-windows-x86_64.enc'
            self.assertNotIn(b'private source secret', exported.read_bytes())
            envelope = json.loads(exported.read_text())
            symmetric = key.decrypt(base64.b64decode(envelope['key']), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
            aad = json.dumps(envelope['context'], sort_keys=True, separators=(',', ':')).encode()
            plaintext = AESGCM(symmetric).decrypt(base64.b64decode(envelope['nonce']), base64.b64decode(envelope['ciphertext']), aad)
            self.assertEqual(plaintext, log.read_bytes()[-10:])
            self.assertEqual([p.suffix for p in exported.parent.iterdir()], ['.enc'])


if __name__ == '__main__':
    unittest.main()

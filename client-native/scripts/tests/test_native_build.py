from pathlib import Path
import hashlib
import importlib.util
import tempfile
import unittest
import uuid
import zipfile

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

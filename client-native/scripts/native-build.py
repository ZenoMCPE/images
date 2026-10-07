#!/usr/bin/env python3
"""Trusted native builders; private command output never enters public job logs."""
from pathlib import Path, PurePosixPath
import argparse
import base64
import json
import hashlib
import os
import re
import shutil
import subprocess
import stat
import sys
import tempfile
import time
import tomllib
import uuid
import zipfile

MAX_DIAGNOSTIC_BYTES = 8 * 1024 * 1024

TARGETS = {
    ('windows', 'x86_64'): 'x86_64-pc-windows-msvc',
    ('macos', 'arm64'): 'aarch64-apple-darwin',
    ('macos', 'x86_64'): 'x86_64-apple-darwin',
}


def validate_request(env):
    if not re.fullmatch(r'[0-9a-f]{40}', env.get('SOURCE_SHA', '')):
        raise ValueError('Invalid source revision')
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', env.get('RELEASE_VERSION', '')):
        raise ValueError('Invalid version')
    request = env.get('REQUEST_ID', '')
    if str(uuid.UUID(request)) != request:
        raise ValueError('Invalid request UUID')


def inspect_source(source, env):
    validate_request(env)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source,
                                   stderr=subprocess.DEVNULL, text=True).strip()
    if head != env['SOURCE_SHA']:
        raise ValueError('Unexpected source revision')
    manifest = tomllib.loads((source / 'Cargo.toml').read_text())
    if manifest['package']['version'] != env['RELEASE_VERSION']:
        raise ValueError('Unexpected source version')
    workflow = (source / '.github/workflows/release.yml').read_text()
    pins = re.findall(r'repository:\s*bedrock-mc/cinnabar\s+ref:\s*([0-9a-f]{40})\b', workflow)
    if len(pins) != 1:
        raise ValueError('Expected one pinned runtime revision')
    return pins[0]


def verify_archive(archive, version, platform, arch):
    expected = f'ZenoClient-{version}-{platform}-{arch}.zip'
    if archive.name != expected or not archive.is_file():
        raise ValueError('Unexpected artifact name')
    prefix = PurePosixPath(archive.stem)
    with zipfile.ZipFile(archive) as packaged:
        entries = packaged.infolist()
        if not entries:
            raise ValueError('Empty release archive')
        for entry in entries:
            name = entry.filename
            path = PurePosixPath(name)
            if '\\' in name or path.is_absolute() or '..' in path.parts or path.parts[0] != str(prefix):
                raise ValueError('Invalid release archive path')
            if any(part.lower() in ('.git', 'target') for part in path.parts):
                raise ValueError('Source/build tree in release archive')
            if any(part.lower() == 'src' for part in path.parts):
                # Crate notices retain their upstream paths, including nested src folders.
                root_licenses = len(path.parts) > 2 and path.parts[1] == 'licenses'
                license_document = re.fullmatch(
                    r'(?:LICENSE|LICENCE|UNLICENSE|COPYING|COPYRIGHT|NOTICE)(?:[-_][A-Z0-9_-]+)?(?:\.(?:txt|md|rst))?',
                    path.name, re.IGNORECASE)
                if not root_licenses or (not entry.is_dir() and not license_document):
                    raise ValueError('Source tree contains a non-license file')
            if path.suffix.lower() in ('.rs', '.go', '.py', '.log'):
                raise ValueError('Source/log file in release archive')
    checksum = archive.with_name(archive.name + '.sha256')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if checksum.read_text().strip() != f'{digest}  {archive.name}':
        raise ValueError('Unexpected artifact checksum')
    return checksum



def encrypt_failure_log(log_path, env):
    """Export bounded ciphertext only; the decryption key never enters this runner."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    public = serialization.load_pem_public_key(env['PRIVATE_DEBUG_PUBLIC_KEY'].encode())
    if not isinstance(public, rsa.RSAPublicKey) or public.key_size < 3072:
        raise ValueError('Invalid diagnostics public key')
    with log_path.open('rb') as stream:
        stream.seek(max(0, log_path.stat().st_size - MAX_DIAGNOSTIC_BYTES))
        plaintext = stream.read(MAX_DIAGNOSTIC_BYTES)
    context = {key: env[key] for key in ('SOURCE_SHA', 'REQUEST_ID', 'RELEASE_PLATFORM', 'RELEASE_ARCH')}
    aad = json.dumps(context, sort_keys=True, separators=(',', ':')).encode()
    key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    envelope = {'format': 'zeno-native-debug-v1', 'context': context,
                'key': base64.b64encode(wrapped).decode(), 'nonce': base64.b64encode(nonce).decode(),
                'ciphertext': base64.b64encode(AESGCM(key).encrypt(nonce, plaintext, aad)).decode()}
    output = Path(env['GITHUB_WORKSPACE']) / 'encrypted-diagnostics'
    output.mkdir(exist_ok=True)
    path = output / f'native-debug-{env["RELEASE_PLATFORM"]}-{env["RELEASE_ARCH"]}.enc'
    path.write_text(json.dumps(envelope, separators=(',', ':')), encoding='utf-8')


def build(source, output, env):
    runtime_sha = inspect_source(source, env)
    platform, arch, target = (env.get(key) for key in ('RELEASE_PLATFORM', 'RELEASE_ARCH', 'RELEASE_TARGET'))
    if TARGETS.get((platform, arch)) != target:
        raise ValueError('Unexpected native build target')
    runtime = source / 'build/cinnabar'
    runtime_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=runtime,
                                           stderr=subprocess.DEVNULL, text=True).strip()
    if runtime_head != runtime_sha:
        raise ValueError('Unexpected runtime revision')
    suffix = '.exe' if platform == 'windows' else ''
    log_path = Path(tempfile.gettempdir()) / f'zeno-native-{env["REQUEST_ID"]}-{platform}-{arch}.log'
    child_env = dict(env)
    try:
        with log_path.open('wb') as log:
            stage = 0
            def run(command, cwd=source, extra=None, allowed_returncodes=()):
                nonlocal stage
                stage += 1
                started = time.monotonic()
                print(f'Native build stage {stage} started.', flush=True)
                log.write(f'\nNative build stage {stage}\n'.encode())
                log.flush()
                variables = child_env | (extra or {})
                result = subprocess.run(command, cwd=cwd, env=variables, stdout=log, stderr=subprocess.STDOUT)
                elapsed = time.monotonic() - started
                if result.returncode and result.returncode not in allowed_returncodes:
                    print(f'Native build stage {stage} failed (exit {result.returncode}, {elapsed:.1f}s).', file=sys.stderr, flush=True)
                    raise RuntimeError('Private build stage failed')
                print(f'Native build stage {stage} completed ({elapsed:.1f}s).', flush=True)
                return result.returncode

            run(['python', '-m', 'unittest', 'discover', '-s', 'scripts/tests'])
            run(['cargo', 'test', '--locked', '-p', 'zeno-client-mod'], source / 'mods/zeno-client')
            run(['cargo', 'build', '--locked', '--release', '-p', 'zeno-client-mod', '--target', 'wasm32-unknown-unknown'], source / 'mods/zeno-client')
            run(['cargo', 'run', '--locked', '--release', '-p', 'zeno-client-pack', '--', 'target/wasm32-unknown-unknown/release/zeno_client_mod.wasm', '../../assets/zeno-client.component.wasm'], source / 'mods/zeno-client')
            reused = run(['python', 'scripts/reuse-runtime.py', '--revision', runtime_sha,
                          '--platform', platform, '--arch', arch, '--output', 'build/runtime'],
                         allowed_returncodes=(75,))
            if reused == 75:
                run(['cargo', 'build', '--release', '--locked', '-p', 'bedrock-client', '-p', 'asset-compiler', '--features', 'bedrock-client/local-mods', '--bin', 'bedrock-client', '--bin', 'assetc'], runtime)
                run(['go', 'build', '-trimpath', '-ldflags', '-s -w', '-o', f'target/release/bedrock-core{suffix}', './core/cmd/bedrock-core'], runtime)
                run(['go', 'build', '-trimpath', '-ldflags', '-s -w', '-o', f'../../target/release/bedrock-local-server{suffix}', '.'], runtime / 'tools/localserver', {'GOWORK': 'off'})
                (runtime / '.zeno-pinned-revision').write_text(runtime_sha)
                run(['python', 'scripts/stage-runtime.py', '--source', 'build/cinnabar', '--output', 'build/runtime', '--platform', platform])
            if platform == 'windows':
                run(['pwsh', '-NoProfile', '-File', 'scripts/tests/install.Tests.ps1'])
            run(['cargo', 'test', '--release', '--locked', '--target', target])
            run(['cargo', 'build', '--locked', '--release', '--target', target])
            run(['python', 'scripts/package-release.py'])
        archive = source / 'dist' / f'ZenoClient-{env["RELEASE_VERSION"]}-{platform}-{arch}.zip'
        checksum = verify_archive(archive, env['RELEASE_VERSION'], platform, arch)
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True)
        for path in (archive, checksum):
            shutil.copy2(path, output / path.name)
        print('Native tests, compilation and release package completed successfully.')
    except Exception:
        if env.get('PRIVATE_DEBUG_PUBLIC_KEY') and log_path.exists():
            try:
                encrypt_failure_log(log_path, env)
                print('Encrypted failure diagnostics prepared; plaintext was not published.', file=sys.stderr)
            except Exception:
                print('Could not encrypt failure diagnostics; plaintext was not published.', file=sys.stderr)
        raise
    finally:
        log_path.unlink(missing_ok=True)


def remove_tree(path):
    def retry_readonly(function, filename, error):
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
        function(filename)
    shutil.rmtree(path, onexc=retry_readonly)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['validate', 'inspect', 'build', 'cleanup'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.operation == 'validate':
            validate_request(os.environ)
            print('Native build request validated.')
        elif args.operation == 'inspect':
            runtime_sha = inspect_source(args.source.resolve(), os.environ)
            with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                print(f'runtime_sha={runtime_sha}', file=output)
            print('Private source revision and version verified.')
        elif args.operation == 'build':
            build(args.source.resolve(), args.output.resolve(), os.environ)
        else:
            expected = Path(os.environ['GITHUB_WORKSPACE']) / 'private-source'
            if args.source.resolve() != expected.resolve():
                raise ValueError('Unexpected cleanup directory')
            if expected.exists():
                remove_tree(expected)
    except Exception:
        print('Native build preparation or compilation failed. Private output was not published.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

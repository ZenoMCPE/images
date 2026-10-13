#!/usr/bin/env python3
"""Package existing Windows binaries; private helper output stays encrypted or local."""

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import traceback
import uuid
import zipfile


SPEC = importlib.util.spec_from_file_location('native_builder', Path(__file__).with_name('native-build.py'))
NATIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NATIVE)
REPOSITORY = 'ZenoMCPE/images'
SOURCE_REPOSITORY = 'ZenoMCPE/client'
TOOL_PATH = 'scripts/repair-windows-release.py'
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_TOOL_BYTES = 1024 * 1024
ORIGINAL_FIELDS = {'run_id', 'workflow_id', 'public_sha', 'request_id', 'artifact_id', 'artifact_digest'}


def require(condition, message):
    """Fail closed with a bounded diagnostic for the private scratch log."""
    if not condition:
        raise ValueError(message)


def validate_request(env):
    """Validate immutable repair inputs without contacting a service or reading source."""
    NATIVE.validate_request(env)
    for key in ('TOOL_SHA', 'RUNTIME_SHA'):
        require(re.fullmatch('[0-9a-f]{40}', env.get(key, '')), 'Full immutable revision required')
    require(re.fullmatch('[0-9a-f]{64}', env.get('INPUT_SHA', '')), 'Full original ZIP digest required')
    require(env.get('GITHUB_REPOSITORY') == REPOSITORY, 'Unexpected worker repository')
    original = json.loads(env.get('ORIGINAL', ''))
    require(isinstance(original, dict) and set(original) == ORIGINAL_FIELDS, 'Exact original identity fields required')
    for key in ('run_id', 'workflow_id', 'artifact_id'):
        require(type(original[key]) is int and original[key] > 0, 'Positive original service ID required')
    require(re.fullmatch('[0-9a-f]{40}', original['public_sha']), 'Full original worker revision required')
    require(str(uuid.UUID(original['request_id'])) == original['request_id'], 'Canonical original request UUID required')
    require(re.fullmatch('sha256:[0-9a-f]{64}', original['artifact_digest']), 'Original artifact digest required')
    return original


def api_json(endpoint, env, log):
    """Read GitHub metadata with the public runner's read-only token and hidden errors."""
    result = subprocess.run(['gh', 'api', endpoint], env=env, stdout=subprocess.PIPE,
                            stderr=log, check=True)
    require(len(result.stdout) <= 4 * 1024 * 1024, 'Service metadata exceeds its bound')
    return json.loads(result.stdout)


def verify_original(run, artifact, original, env):
    """Bind an original artifact to the approved successful dispatch and source identity."""
    title = f"Native {original['request_id']} {env['SOURCE_SHA']} {env['RELEASE_VERSION']}"
    require(run.get('id') == original['run_id'] and run.get('workflow_id') == original['workflow_id']
            and run.get('head_sha') == original['public_sha'] and run.get('head_branch') == 'main'
            and run.get('event') == 'workflow_dispatch' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success' and run.get('display_title') == title,
            'Original native run identity differs')
    repository = run.get('repository', {})
    head = run.get('head_repository', {})
    require(repository.get('full_name') == REPOSITORY and head.get('full_name') == REPOSITORY
            and type(repository.get('id')) is int and head.get('id') == repository['id'],
            'Original native run comes from another repository or fork')
    owner = artifact.get('workflow_run', {})
    require(artifact.get('id') == original['artifact_id'] and artifact.get('name') == 'release-windows-x86_64'
            and artifact.get('expired') is False and artifact.get('digest') == original['artifact_digest']
            and type(artifact.get('size_in_bytes')) is int
            and 0 < artifact['size_in_bytes'] <= MAX_DOWNLOAD_BYTES,
            'Original artifact identity differs')
    require(owner.get('id') == original['run_id'] and owner.get('head_sha') == original['public_sha']
            and owner.get('head_branch') == 'main'
            and owner.get('repository_id') == repository['id']
            and owner.get('head_repository_id') == repository['id'],
            'Original artifact producer differs')


def download_original(destination, artifact, original, env, log):
    """Download and hash only the exact public artifact admitted above."""
    with destination.open('xb') as output:
        subprocess.run(['gh', 'api', f"repos/{REPOSITORY}/actions/artifacts/{original['artifact_id']}/zip"],
                       env=env, stdout=output, stderr=log, check=True)
    require(destination.stat().st_size == artifact['size_in_bytes']
            and file_digest(destination) == original['artifact_digest'].removeprefix('sha256:'),
            'Downloaded original artifact bytes differ')


def file_digest(path):
    """Hash a bounded file incrementally rather than loading an archive into memory."""
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def asset_names(version):
    """Derive the four permitted Windows deliverables from the validated version."""
    prefix = f'ZenoClient-{version}-windows-x86_64'
    return {prefix + suffix for suffix in ('.zip', '.zip.sha256', '-setup.exe', '-setup.exe.sha256')}


def extract_original(container, directory, env):
    """Extract exactly four ordinary public binaries/checksums, rejecting aliases and bombs."""
    expected = asset_names(env['RELEASE_VERSION'])
    with zipfile.ZipFile(container) as archive:
        entries = archive.infolist()
        require(len(entries) == 4 and {entry.filename for entry in entries} == expected,
                'Original public artifact must contain only four Windows files')
        total = 0
        for entry in entries:
            require(entry.orig_filename == entry.filename and entry.filename in expected
                    and not entry.is_dir() and not entry.flag_bits & 1
                    and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
                    and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                    'Nonordinary original artifact member')
            total += entry.file_size
            require(0 <= entry.file_size <= MAX_DOWNLOAD_BYTES and total <= MAX_UNPACKED_BYTES
                    and entry.file_size <= max(1, entry.compress_size) * 1000,
                    'Original artifact member exceeds its bound')
            with archive.open(entry) as source, (directory / entry.filename).open('xb') as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
            require((directory / entry.filename).stat().st_size == entry.file_size, 'Truncated original member')
    portable = directory / f"ZenoClient-{env['RELEASE_VERSION']}-windows-x86_64.zip"
    require(file_digest(portable) == env['INPUT_SHA'], 'Original inner ZIP digest differs')
    NATIVE.verify_archive(portable, env['RELEASE_VERSION'], 'windows', 'x86_64')
    NATIVE.verify_setup(portable.with_name(portable.stem + '-setup.exe'), env['RELEASE_VERSION'], 'x86_64')
    return portable


def fetch_tool(source, env, log):
    """Read one immutable private helper blob using only the ephemeral source-read token."""
    token = env.get('CLIENT_SOURCE_TOKEN')
    require(token, 'Ephemeral source-read credential is required')
    read_env = dict(env, GH_TOKEN=token)
    read_env.pop('GH_DEBUG', None)
    commit = api_json(f"repos/{SOURCE_REPOSITORY}/commits/{env['TOOL_SHA']}", read_env, log)
    require(commit.get('sha') == env['TOOL_SHA'], 'Private tool commit identity differs')
    item = api_json(f"repos/{SOURCE_REPOSITORY}/contents/{TOOL_PATH}?ref={env['TOOL_SHA']}", read_env, log)
    require(item.get('type') == 'file' and item.get('path') == TOOL_PATH
            and item.get('encoding') == 'base64' and type(item.get('size')) is int
            and 0 < item['size'] <= MAX_TOOL_BYTES, 'Private tool blob metadata differs')
    content = base64.b64decode(item['content'].replace('\n', ''), validate=True)
    require(len(content) == item['size']
            and hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest() == item['sha'],
            'Private tool blob identity differs')
    # This untracked scratch file is inside the existing post-action cleanup boundary.
    path = source / '.native-windows-repair.py'
    with path.open('xb') as target:
        target.write(content)
    return path


def helper_environment(env):
    """Keep source-read and public API credentials out of the offline packaging child."""
    return {key: value for key, value in env.items()
            if key not in ('GH_TOKEN', 'GITHUB_TOKEN', 'CLIENT_SOURCE_TOKEN', 'SOURCE_READ_TOKEN', 'GH_DEBUG')}


def admit_output(directory, output, env):
    """Copy only verified four-file deliverables; keep helper reports/MSI/logs private."""
    portable = directory / f"ZenoClient-{env['RELEASE_VERSION']}-windows-x86_64.zip"
    setup = portable.with_name(portable.stem + '-setup.exe')
    NATIVE.verify_archive(portable, env['RELEASE_VERSION'], 'windows', 'x86_64')
    NATIVE.verify_setup(setup, env['RELEASE_VERSION'], 'x86_64')
    require(not output.exists(), 'Public output directory must be new')
    output.mkdir(parents=True)
    for name in sorted(asset_names(env['RELEASE_VERSION'])):
        path = directory / name
        require(path.is_file() and not path.is_symlink(), 'Missing ordinary corrected deliverable')
        shutil.copy2(path, output / name)


def repair(source, output, env):
    """Run local packaging only, with hidden source/helper diagnostics and no release writes."""
    original = validate_request(env)
    context = dict(env, RELEASE_PLATFORM='windows', RELEASE_ARCH='x86_64')
    with tempfile.TemporaryDirectory(prefix='zeno-native-windows-repair-') as temporary:
        work = Path(temporary)
        log_path = work / 'private-packaging.log'
        try:
            with log_path.open('wb') as log:
                require(NATIVE.inspect_source(source, env) == env['RUNTIME_SHA'], 'Frozen runtime pin differs')
                module = tomllib.loads((source / 'mods/zeno-client/Cargo.toml').read_text())
                require(module['package']['version'] == env['RELEASE_VERSION'], 'Frozen component version differs')
                run = api_json(f"repos/{REPOSITORY}/actions/runs/{original['run_id']}", env, log)
                artifact = api_json(f"repos/{REPOSITORY}/actions/artifacts/{original['artifact_id']}", env, log)
                verify_original(run, artifact, original, env)
                container = work / 'original-artifact.zip'
                download_original(container, artifact, original, env, log)
                portable = extract_original(container, work, env)
                tool = fetch_tool(source, env, log)
                corrected = work / 'corrected'
                command = ['python', str(tool), '--input-zip', str(portable), '--source', str(source),
                           '--source-sha', env['SOURCE_SHA'], '--version', env['RELEASE_VERSION'],
                           '--input-sha', env['INPUT_SHA'], '--output', str(corrected)]
                print('Windows packaging-only verification started.', flush=True)
                subprocess.run(command, env=helper_environment(env), stdout=log,
                               stderr=subprocess.STDOUT, check=True)
                admit_output(corrected, output, env)
            print('Windows packaging-only verification completed; four deliverables admitted.')
        except Exception:
            with log_path.open('a', encoding='utf-8') as log:
                traceback.print_exc(file=log)
            if env.get('PRIVATE_DEBUG_PUBLIC_KEY'):
                try:
                    NATIVE.encrypt_failure_log(log_path, context)
                    print('Encrypted failure diagnostics prepared; plaintext was not published.', file=sys.stderr)
                except Exception:
                    print('Could not encrypt diagnostics; plaintext was not published.', file=sys.stderr)
            raise


def main():
    """Expose separate validation and Windows-only package operations, leaving builds unchanged."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('validate', 'repair'))
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.operation == 'validate':
            validate_request(os.environ)
            print('Windows packaging-only request validated.')
        else:
            require(args.source is not None and args.output is not None, 'Source and output paths required')
            repair(args.source.resolve(), args.output.resolve(), dict(os.environ))
    except Exception:
        print('Windows packaging-only verification failed. Private output was not published.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

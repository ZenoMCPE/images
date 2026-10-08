'use strict';
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const workspace = process.env.GITHUB_WORKSPACE;
if (!workspace) {
  console.error('Private build cleanup requires the runner workspace.');
  process.exitCode = 1;
} else {
  const result = spawnSync('python', [
    path.join(workspace, 'builder', 'client-native', 'scripts', 'native-build.py'),
    'cleanup', '--source', path.join(workspace, 'private-source'),
  ], { stdio: 'inherit', windowsHide: true });
  if (result.error || result.status !== 0) {
    console.error('Private build cleanup failed.');
    process.exitCode = 1;
  }
}

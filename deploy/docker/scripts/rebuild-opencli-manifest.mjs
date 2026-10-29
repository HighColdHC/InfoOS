#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const opencliRoot = path.resolve(
  process.argv[2] || '/usr/local/lib/node_modules/@jackwener/opencli',
);
const clisDir = path.join(opencliRoot, 'clis');
const manifestPath = path.join(opencliRoot, 'cli-manifest.json');
const compilerPath = path.join(opencliRoot, 'dist', 'src', 'build-manifest.js');

if (!fs.existsSync(compilerPath)) {
  throw new Error(`OpenCLI manifest compiler not found: ${compilerPath}`);
}

const compiler = await import(pathToFileURL(compilerPath).href);
const { scanClisDir, serializeManifest } = compiler;
if (typeof scanClisDir !== 'function' || typeof serializeManifest !== 'function') {
  throw new Error('OpenCLI manifest compiler does not export scanClisDir/serializeManifest');
}

const { entries, failures } = await scanClisDir(clisDir);
if (failures.length > 0) {
  const details = failures
    .map((failure) => `${failure.filePath}: ${failure.cause?.message || failure.message}`)
    .join('\n');
  throw new Error(`OpenCLI adapter scan failed:\n${details}`);
}

const expectedZsxqCommands = new Set([
  'article',
  'dynamics',
  'groups',
  'login',
  'opportunity',
  'probe',
  'search',
  'topic',
  'topics',
  'whoami',
]);
const zsxqEntries = entries.filter((entry) => entry.site === 'zsxq');
const actualZsxqCommands = new Set(zsxqEntries.map((entry) => entry.name));
if (
  zsxqEntries.length !== expectedZsxqCommands.size
  || [...expectedZsxqCommands].some((name) => !actualZsxqCommands.has(name))
) {
  throw new Error(`ZSXQ manifest commands are incomplete: ${[...actualZsxqCommands].sort().join(', ')}`);
}
if (zsxqEntries.some((entry) => (
  entry.domain !== (entry.name === 'article' ? 'articles.zsxq.com' : entry.name === 'login' ? 'zsxq.com' : 'wx.zsxq.com')
))) {
  throw new Error('ZSXQ manifest command domains do not match their browser surfaces');
}

const feishuEntries = entries.filter((entry) => entry.site === 'feishu');
if (
  feishuEntries.length !== 1
  || feishuEntries[0].name !== 'document'
  || feishuEntries[0].domain !== 'feishu.cn'
) {
  throw new Error('Feishu manifest must contain exactly feishu document on domain=feishu.cn');
}

fs.writeFileSync(manifestPath, serializeManifest(entries));
process.stdout.write(
  `Rebuilt OpenCLI manifest with ${entries.length} entries, `
  + `${zsxqEntries.length} ZSXQ commands, and ${feishuEntries.length} Feishu command.\n`,
);

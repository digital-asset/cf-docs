#!/usr/bin/env node

/**
 * updateVersionDashboardData.js
 *
 * Reads repo-version-config.json and version-dashboard-meta.json, merges the data,
 * and generates snippets/generated/version-dashboard-data.mdx with the networkData export.
 */

const fs = require('fs');
const path = require('path');

const PROJECT_ROOT = path.join(__dirname, '../..');
const REPO_VERSION_CONFIG_PATH = path.join(PROJECT_ROOT, 'config/repo-version-config.json');
const VERSION_DASHBOARD_META_PATH = path.join(PROJECT_ROOT, 'config/version-dashboard-meta.json');
const OUTPUT_PATH = path.join(PROJECT_ROOT, 'docs-main/snippets/generated/version-dashboard-data.mdx');

const VERSION_ORDER = [
  'splice',
  'canton',
  'damlSdk',
  'pqs',
  'tokenStandard',
  'walletSdk',
  'dappSdk',
  'walletGateway',
  'dpm',
];

/**
 * Format helper for MDX output
 */
function formatValue(value, indent = 0) {
  const indentStr = '  '.repeat(indent);
  
  if (value === null || value === undefined) {
    return 'null';
  }
  
  if (typeof value === 'string') {
    return `'${value.replace(/'/g, "\\'")}'`;
  }
  
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  
  if (Array.isArray(value)) {
    if (value.length === 0) return '[]';
    const items = value.map(item => `${indentStr}  ${formatValue(item, indent + 1)}`).join(',\n');
    return `[\n${items},\n${indentStr}]`;
  }
  
  if (typeof value === 'object') {
    const keys = Object.keys(value);
    if (keys.length === 0) return '{}';
    const items = keys.map(key => {
      const val = formatValue(value[key], indent + 1);
      return `${indentStr}  ${key}: ${val}`;
    }).join(',\n');
    return `{\n${items},\n${indentStr}}`;
  }
  
  return String(value);
}

function formatLastUpdatedLabel(isoString) {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) {
    throw new Error(`Invalid generatedAt timestamp: ${isoString}`);
  }

  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    timeZone: 'UTC',
  });
}

function buildDashboardMetadata(repoConfig) {
  const generatedAt = repoConfig?._generated?.generatedAt;
  if (!generatedAt) {
    throw new Error('Missing _generated.generatedAt in repo-version-config.json');
  }

  return {
    lastUpdatedAt: generatedAt,
    lastUpdatedLabel: formatLastUpdatedLabel(generatedAt),
  };
}

function buildNetworkData(repoConfig, metaConfig) {
  const networkData = {};
  
  for (const networkKey of ['mainnet', 'testnet', 'devnet']) {
    const repoVersion = repoConfig.versions[networkKey];
    const metaVersion = metaConfig.versions[networkKey];
    
    if (!repoVersion) {
      console.warn(`Warning: ${networkKey} not found in repo-version-config.json`);
      continue;
    }
    
    if (!metaVersion) {
      console.warn(`Warning: ${networkKey} not found in version-dashboard-meta.json`);
      continue;
    }
    
    // Each repository's externalVersion for this network becomes a version entry.
    const versionsByRepo = {};
    if (repoConfig.repositories) {
      for (const [repoName, repo] of Object.entries(repoConfig.repositories)) {
        const versionMapping = repo.versionMapping[networkKey];
        if (versionMapping && versionMapping.externalVersion) {
          versionsByRepo[repoName] = versionMapping.externalVersion;
        }
      }
    }

    const versions = {};
    for (const repoName of VERSION_ORDER) {
      if (versionsByRepo[repoName] !== undefined) {
        versions[repoName] = versionsByRepo[repoName];
      }
    }
    for (const [repoName, externalVersion] of Object.entries(versionsByRepo)) {
      if (versions[repoName] === undefined) {
        versions[repoName] = externalVersion;
      }
    }
    
    const network = {
      name: repoVersion.name,
      description: metaVersion.description,
      color: metaVersion.color,
    };
    
    if (metaVersion.resetDate) {
      network.resetDate = metaVersion.resetDate;
    }
    
    network.versions = versions;
    network.advanced = repoVersion.advanced;
    network.endpoint = repoVersion.endpoint;
    network.substitutions = repoVersion.substitutions || {};
    
    networkData[networkKey] = network;
  }
  
  return networkData;
}

function generateMDX(dashboardMetadata, networkData) {
  const lines = [
    `export const lastUpdatedAt = ${formatValue(dashboardMetadata.lastUpdatedAt, 0)};`,
    `export const lastUpdatedLabel = ${formatValue(dashboardMetadata.lastUpdatedLabel, 0)};`,
    '',
    'export const networkData = {',
  ];
  
  for (const [networkKey, network] of Object.entries(networkData)) {
    lines.push(`  ${networkKey}: {`);
    lines.push(`    name: ${formatValue(network.name, 2)},`);
    lines.push(`    description: ${formatValue(network.description, 2)},`);
    lines.push(`    color: ${formatValue(network.color, 2)},`);
    
    // Add resetDate if present (before versions)
    if (network.resetDate) {
      lines.push(`    resetDate: ${formatValue(network.resetDate, 2)},`);
    }
    
    // Versions object
    lines.push(`    versions: {`);
    for (const [key, value] of Object.entries(network.versions)) {
      lines.push(`      ${key}: ${formatValue(value, 3)},`);
    }
    lines.push(`    },`);
    
    // Advanced object
    lines.push(`    advanced: {`);
    lines.push(`      minProtocolVersion: ${formatValue(network.advanced.minProtocolVersion, 3)},`);
    
    lines.push(`      releaseUrl: ${formatValue(network.advanced.releaseUrl, 3)},`);
    lines.push(`    },`);
    
    lines.push(`    endpoint: ${formatValue(network.endpoint, 2)},`);
    lines.push(`    substitutions: ${formatValue(network.substitutions, 2)},`);
    lines.push(`  },`);
  }
  
  lines.push('}');
  
  return lines.join('\n');
}

function main() {
  try {
    // Read repo-version-config.json
    let repoConfig;
    try {
      const repoConfigRaw = fs.readFileSync(REPO_VERSION_CONFIG_PATH, 'utf8');
      repoConfig = JSON.parse(repoConfigRaw);
    } catch (error) {
      throw new Error(`Failed to read repo-version-config.json: ${error.message}`);
    }
    
    // Read version-dashboard-meta.json
    let metaConfig;
    try {
      const metaConfigRaw = fs.readFileSync(VERSION_DASHBOARD_META_PATH, 'utf8');
      metaConfig = JSON.parse(metaConfigRaw);
    } catch (error) {
      throw new Error(`Failed to read version-dashboard-meta.json: ${error.message}`);
    }
    
    const networkData = buildNetworkData(repoConfig, metaConfig);
    const dashboardMetadata = buildDashboardMetadata(repoConfig);
    
    const mdxContent = generateMDX(dashboardMetadata, networkData);
    
    const outputDir = path.dirname(OUTPUT_PATH);
    fs.mkdirSync(outputDir, { recursive: true });
    
    fs.writeFileSync(OUTPUT_PATH, mdxContent + '\n', 'utf8');
    
    console.log(`Successfully generated ${OUTPUT_PATH}`);
  } catch (error) {
    console.error(`Error: ${error.message}`);
    process.exit(1);
  }
}

main();

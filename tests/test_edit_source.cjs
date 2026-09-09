const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');
const source = readFileSync(new URL('../docs-source/edit-source.js', `file://${__filename}`), 'utf8');

function link(href) {
  return {
    href,
    matches: () => true,
    querySelectorAll: () => [],
  };
}

function start(links = []) {
  let observer;
  vm.runInNewContext(source, {
    URL,
    location: { href: 'https://docs.canton.network/example' },
    document: { documentElement: {}, querySelectorAll: () => links },
    MutationObserver: class {
      constructor(callback) { observer = callback; }
      observe(_root, options) {
        assert.equal(options.childList, true);
        assert.equal(options.subtree, true);
        assert.equal(options.attributes, true);
        assert.equal(options.attributeFilter[0], 'href');
      }
    },
  });
  return observer;
}

test('existing edit links preserve branch, filename, query, and fragment', () => {
  const anchor = link('https://github.com/canton-network/cf-docs/edit/feature/network-tabs/docs-main/guides/my%20page.md?plain=1#L20');
  start([anchor]);
  assert.equal(anchor.href, 'https://github.com/canton-network/cf-docs/edit/feature/network-tabs/docs-source/guides/my%20page.md?plain=1#L20');
});

test('footer inserted after consent is redirected', () => {
  const mutations = start();
  const anchor = link('https://github.com/canton-network/cf-docs/edit/main/docs-main/index.mdx');
  mutations([{ type: 'childList', addedNodes: [{ querySelectorAll: () => [anchor] }] }]);
  assert.equal(anchor.href, 'https://github.com/canton-network/cf-docs/edit/main/docs-source/index.mdx');
});

test('navigation can insert an anchor or reuse its href without looping', () => {
  const mutations = start();
  const anchor = link('https://github.com/canton-network/cf-docs/edit/main/docs-main/first.mdx');
  mutations([{ type: 'childList', addedNodes: [anchor, {}] }]);
  assert.ok(anchor.href.endsWith('/docs-source/first.mdx'));
  anchor.href = 'https://github.com/canton-network/cf-docs/edit/main/docs-main/second.mdx';
  mutations([{ type: 'attributes', target: anchor }]);
  assert.ok(anchor.href.endsWith('/docs-source/second.mdx'));
  const rewritten = anchor.href;
  mutations([{ type: 'attributes', target: anchor }]);
  assert.equal(anchor.href, rewritten);
});

test('ordinary GitHub links, issue links, and other repositories are unchanged', () => {
  for (const href of [
    'https://github.com/canton-network/cf-docs/issues/new?body=docs-main/example.mdx',
    'https://github.com/canton-network/cf-docs/blob/main/docs-main/example.mdx',
    'https://github.com/canton-network/other/edit/main/docs-main/example.mdx',
    'https://example.com/canton-network/cf-docs/edit/main/docs-main/example.mdx',
    'https://github.com/canton-network/cf-docs/edit/main/docs-source/example.mdx',
    'https://github.com/canton-network/cf-docs/edit/main/README.md',
  ]) {
    const anchor = link(href);
    start([anchor]);
    assert.equal(anchor.href, href);
  }
});

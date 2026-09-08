// Mintlify builds Suggest edits from its deployment directory. Keep the branch,
// filename, query, and fragment, but send contributors to the authored corpus.
(() => {
  const selector = 'a[href*="github.com/canton-network/cf-docs/edit/"]';

  function updateLink(link) {
    const url = new URL(link.href, location.href);
    if (url.origin !== "https://github.com") return;
    const sourcePath = url.pathname.replace(
      /^(\/canton-network\/cf-docs\/edit\/.+\/)docs-main\//,
      "$1docs-source/",
    );
    if (sourcePath === url.pathname) return;
    url.pathname = sourcePath;
    link.href = url.href;
  }

  function updateLinks(root) {
    if (root.matches?.(selector)) updateLink(root);
    root.querySelectorAll?.(selector).forEach(updateLink);
  }

  updateLinks(document);
  // Consent and client-side navigation can insert or reuse the footer later.
  new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === "attributes") updateLinks(mutation.target);
      else mutation.addedNodes.forEach(updateLinks);
    }
  }).observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["href"],
  });
})();

(function() {
  // this script should only run once
  if (window.__CF_GISCUS) {
    return
  }

  window.__CF_GISCUS = true

  /**
   * Return the active Mintlify theme.
   *
   * Mintlify allows the user to easily toggle the color scheme, so it's not always going to be
   * the case that Giscus's built-in theme detection logic is going to work properly. So manually
   * tie the theme to the current scheme instead of relying on `preferred_color_scheme`.
   *
   * @returns {string}
   */
  function getActiveTheme() {
    return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
  }

  /**
   * Install the Giscus elements on the page. This needs to be rerun on every navigation because
   * Mintlify uses client-side re-rendering to improve page load performance, and that blows out
   * the script tag that loads Giscus.
   */
  function installGiscus() {
    const paginationElem = document.getElementById('pagination')
    if (!paginationElem) {
      return
    }

    const giscusElem = document.createElement("div")
    giscusElem.className = "giscus pb-16"

    const giscusScriptElem = document.createElement("script")
    giscusScriptElem.src = "https://giscus.app/client.js"
    giscusScriptElem.setAttribute('data-repo', 'canton-network/cf-docs');
    giscusScriptElem.setAttribute('data-repo-id', 'R_kgDOQqfG8w');
    giscusScriptElem.setAttribute('data-category', 'Giscus Comments');
    giscusScriptElem.setAttribute('data-category-id', 'DIC_kwDOQqfG884C_M8u');
    giscusScriptElem.setAttribute('data-mapping', 'pathname');
    giscusScriptElem.setAttribute('data-strict', '1');
    giscusScriptElem.setAttribute('data-reactions-enabled', '0');
    giscusScriptElem.setAttribute('data-emit-metadata', '0');
    giscusScriptElem.setAttribute('data-input-position', 'bottom');
    giscusScriptElem.setAttribute('data-theme', 'preferred_color_scheme');
    giscusScriptElem.setAttribute('data-lang', 'en');
    giscusScriptElem.setAttribute('data-theme', getActiveTheme())
    giscusScriptElem.crossOrigin = 'anonymous';
    giscusScriptElem.async = true;

    paginationElem.before(giscusElem)
    paginationElem.before(giscusScriptElem)
  }

  navigation.addEventListener('navigate', function() {
    // give the page some time to render its contents before (re-)installing Giscus
    setTimeout(installGiscus, 0)
  })

  const observer = new MutationObserver(() => {
    const iframe = document.querySelector('iframe.giscus-frame')
    if (iframe) {
      iframe.contentWindow.postMessage({ giscus: { setConfig: { theme: getActiveTheme() }}}, "https://giscus.app")
    }
  })
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })

  installGiscus()
})()

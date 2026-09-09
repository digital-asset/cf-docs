(function () {
  if (window.__CF_NAV_TRANSITION) {
    return;
  }

  window.__CF_NAV_TRANSITION = true;

  var FADE_MS = 120;
  var SPINNER_FADE_IN_MS = 150;
  var NAVIGATION_FALLBACK_MS = 1000;
  var JSON_API_REFERENCE_PREFIX = "/reference/json-api-reference/";
  var TARGET_SELECTOR = "#content-area";
  var MANUAL_API_BADGES_SELECTOR =
    ".x2mdx-ref-page--manual-api + .x2mdx-ref-hero .x2mdx-ref-badges";
  var HEADER_BADGES_ID = "x2mdx-ref-api-header-badges";
  var fadedOutFromClick = false;
  var spinnerElement = null;
  var contentCleanupTimer = null;
  var navigationFallbackTimer = null;
  var badgeSyncScheduled = false;

  var style = document.createElement("style");
  style.textContent =
    "#nav-loading-spinner {" +
    "  position: fixed;" +
    "  inset: 0;" +
    "  display: flex;" +
    "  align-items: center;" +
    "  justify-content: center;" +
    "  pointer-events: none;" +
    "  z-index: 5;" +
    "  opacity: 0;" +
    "  visibility: hidden;" +
    "  transition: opacity " +
    SPINNER_FADE_IN_MS +
    "ms ease-out, visibility 0s linear " +
    SPINNER_FADE_IN_MS +
    "ms;" +
    "}" +
    "#nav-loading-spinner.is-active {" +
    "  opacity: 1;" +
    "  visibility: visible;" +
    "  transition: opacity " +
    SPINNER_FADE_IN_MS +
    "ms ease-out, visibility 0s;" +
    "}" +
    ".nav-loading-spinner__ring {" +
    "  width: 1.25rem;" +
    "  height: 1.25rem;" +
    "  border: 2px solid rgba(115, 75, 226, 0.15);" +
    "  border-top-color: rgba(115, 75, 226, 0.55);" +
    "  border-radius: 50%;" +
    "  animation: nav-loading-spin 0.8s linear infinite;" +
    "}" +
    ":root.dark .nav-loading-spinner__ring," +
    "html.dark .nav-loading-spinner__ring {" +
    "  border-color: rgba(167, 133, 255, 0.15);" +
    "  border-top-color: rgba(167, 133, 255, 0.6);" +
    "}" +
    "@keyframes nav-loading-spin {" +
    "  to { transform: rotate(360deg); }" +
    "}";
  document.head.appendChild(style);

  function getContentArea() {
    return document.querySelector(TARGET_SELECTOR);
  }

  function syncManualApiHeaderBadges() {
    badgeSyncScheduled = false;

    var source = document.querySelector(MANUAL_API_BADGES_SELECTOR);
    var header = document.querySelector("#header");
    var hydrated = document.getElementById(HEADER_BADGES_ID);

    if (!source || !header) {
      if (hydrated) {
        hydrated.remove();
      }
      return;
    }

    if (!hydrated) {
      hydrated = source.cloneNode(true);
      hydrated.id = HEADER_BADGES_ID;
      hydrated.classList.add("x2mdx-ref-api-header-badges");
    }

    if (hydrated.innerHTML !== source.innerHTML) {
      hydrated.innerHTML = source.innerHTML;
    }

    var mobileContextMenu = Array.prototype.find.call(
      header.children,
      function (child) {
        return child.id === "page-context-menu";
      }
    );
    if (
      hydrated.parentElement !== header ||
      hydrated.nextElementSibling !== mobileContextMenu
    ) {
      header.insertBefore(hydrated, mobileContextMenu || null);
    }
  }

  function scheduleManualApiHeaderBadgeSync() {
    if (badgeSyncScheduled) {
      return;
    }
    badgeSyncScheduled = true;
    requestAnimationFrame(syncManualApiHeaderBadges);
  }

  function installSpinner() {
    if (spinnerElement && document.contains(spinnerElement)) {
      return;
    }

    spinnerElement = document.createElement("div");
    spinnerElement.id = "nav-loading-spinner";
    spinnerElement.setAttribute("aria-hidden", "true");
    spinnerElement.innerHTML = '<div class="nav-loading-spinner__ring"></div>';
    document.body.appendChild(spinnerElement);
  }

  function showSpinner() {
    installSpinner();
    if (!spinnerElement) {
      return;
    }

    spinnerElement.style.removeProperty("transition");
    spinnerElement.classList.remove("is-active");
    forceReflow(spinnerElement);
    spinnerElement.classList.add("is-active");
  }

  function hideSpinner() {
    if (!spinnerElement) {
      return;
    }

    spinnerElement.style.transition = "none";
    spinnerElement.classList.remove("is-active");
    forceReflow(spinnerElement);
    spinnerElement.style.removeProperty("transition");
  }

  function getPagePath(urlString) {
    return new URL(urlString, window.location.origin).pathname;
  }

  function isPageNavigation(fromUrl, toUrl) {
    return getPagePath(fromUrl) !== getPagePath(toUrl);
  }

  function isJsonApiReferenceTransition(fromUrl, toUrl) {
    return (
      getPagePath(fromUrl).startsWith(JSON_API_REFERENCE_PREFIX) &&
      getPagePath(toUrl).startsWith(JSON_API_REFERENCE_PREFIX)
    );
  }

  function scheduleNavigationFallback(fromUrl, toUrl) {
    if (!isJsonApiReferenceTransition(fromUrl, toUrl)) {
      return;
    }

    if (navigationFallbackTimer) {
      window.clearTimeout(navigationFallbackTimer);
    }

    var fromPath = getPagePath(fromUrl);
    navigationFallbackTimer = window.setTimeout(function () {
      navigationFallbackTimer = null;

      // Mintlify can suppress the client-side transition from the overview to
      // a manual API page at narrow breakpoints. Fall back to native navigation
      // only when the click has left the browser on the original route.
      if (window.location.pathname !== fromPath) {
        return;
      }

      window.location.assign(toUrl);
    }, NAVIGATION_FALLBACK_MS);
  }

  function prepareTransition(element) {
    element.style.transition = "opacity " + FADE_MS + "ms ease-out";
    element.style.position = "relative";
    element.style.zIndex = "10";
  }

  function clearTransitionStyles(element) {
    element.style.removeProperty("opacity");
    element.style.removeProperty("transition");
    element.style.removeProperty("position");
    element.style.removeProperty("z-index");
  }

  function forceReflow(element) {
    void element.offsetWidth;
  }

  function fadeOut() {
    var contentArea = getContentArea();
    if (!contentArea) {
      return false;
    }

    showSpinner();
    prepareTransition(contentArea);
    contentArea.style.opacity = "1";
    forceReflow(contentArea);
    contentArea.style.opacity = "0";
    return true;
  }

  function fadeIn() {
    var contentArea = getContentArea();
    if (!contentArea) {
      hideSpinner();
      return false;
    }

    hideSpinner();
    prepareTransition(contentArea);
    contentArea.style.opacity = "0";
    forceReflow(contentArea);
    contentArea.style.opacity = "1";

    var finished = false;

    function finishTransition() {
      if (finished) {
        return;
      }
      finished = true;
      contentArea.removeEventListener("transitionend", onOpacityTransitionEnd);
      if (contentCleanupTimer) {
        window.clearTimeout(contentCleanupTimer);
        contentCleanupTimer = null;
      }
      clearTransitionStyles(contentArea);
    }

    function onOpacityTransitionEnd(event) {
      if (event.target !== contentArea || event.propertyName !== "opacity") {
        return;
      }
      finishTransition();
    }

    contentArea.addEventListener("transitionend", onOpacityTransitionEnd);
    contentCleanupTimer = window.setTimeout(finishTransition, FADE_MS + 50);
    return true;
  }

  function isInternalNavigationLink(link) {
    var href = link.getAttribute("href");
    if (!href || href.startsWith("#") || href.startsWith("mailto:")) {
      return null;
    }

    try {
      var url = new URL(href, window.location.origin);
      if (url.origin !== window.location.origin) {
        return null;
      }
      return url;
    } catch (_error) {
      return null;
    }
  }

  function shouldIgnoreNavigationClick(event, link) {
    // Do not trigger navigation transition for cmd/ctrl/alt/shift clicks or other non-primary clicks
    if (event.defaultPrevented) {
      return true;
    }
    if (typeof event.button === "number" && event.button !== 0) {
      return true;
    }
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return true;
    }
    if (link.hasAttribute("download")) {
      return true;
    }
    var target = (link.getAttribute("target") || "").toLowerCase();
    if (target && target !== "_self") {
      return true;
    }
    return false;
  }

  function afterNavigationRender(callback) {
    requestAnimationFrame(function () {
      requestAnimationFrame(callback);
    });
  }

  function onPageReady() {
    scheduleManualApiHeaderBadgeSync();
    hideSpinner();
    fadeIn();
  }

  installSpinner();
  scheduleManualApiHeaderBadgeSync();

  var badgeObserver = new MutationObserver(scheduleManualApiHeaderBadgeSync);
  badgeObserver.observe(document.body, { childList: true, subtree: true });

  document.addEventListener(
    "click",
    function (event) {
      var link = event.target.closest("a[href]");
      if (!link) {
        return;
      }

      if (shouldIgnoreNavigationClick(event, link)) {
        return;
      }

      var url = isInternalNavigationLink(link);
      if (!url || url.pathname === window.location.pathname) {
        return;
      }

      fadedOutFromClick = fadeOut();
      scheduleNavigationFallback(window.location.href, url.href);
    },
    true
  );

  if (typeof navigation !== "undefined") {
    navigation.addEventListener("navigate", function (event) {
      var fromUrl = navigation.currentEntry.url;
      var toUrl =
        event.destination && event.destination.url
          ? event.destination.url
          : window.location.href;

      if (!isPageNavigation(fromUrl, toUrl)) {
        fadedOutFromClick = false;
        return;
      }

      if (!fadedOutFromClick) {
        fadeOut();
      }

      fadedOutFromClick = false;
      afterNavigationRender(onPageReady);
    });
  }
})();

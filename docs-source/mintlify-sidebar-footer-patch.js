(function () {
  "use strict";

  var PATCHED_ATTR = "data-mintlify-sidebar-footer-patch";
  var PATCH_MARKER = "__daMintlifySidebarFooterPatch";
  var PATCH_VERSION = "global-2026-04-29";
  var LARGE_NAV_MULTIPLIER = 2;
  var DEFAULT_TOP_REM = 4;

  var observedSidebar = null;
  var sidebarObserver = null;
  var scheduled = false;
  var lastSafeTopPx = null;

  function px(value) {
    if (!value || typeof value !== "string" || !value.endsWith("px")) {
      return null;
    }

    var parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function rootFontSizePx() {
    var parsed = px(window.getComputedStyle(document.documentElement).fontSize);
    return parsed || 16;
  }

  function safeTopPx(sidebar) {
    if (lastSafeTopPx !== null) {
      return lastSafeTopPx;
    }

    var inlineTop = px(sidebar.style.top);
    if (inlineTop !== null && inlineTop >= 0) {
      lastSafeTopPx = inlineTop;
      return inlineTop;
    }

    var navbar = document.getElementById("navbar");
    if (navbar) {
      var navbarBottom = navbar.getBoundingClientRect().bottom;
      if (navbarBottom > 0) {
        lastSafeTopPx = navbarBottom;
        return navbarBottom;
      }
    }

    lastSafeTopPx = DEFAULT_TOP_REM * rootFontSizePx();
    return lastSafeTopPx;
  }

  function shouldClamp(sidebar, navigationItems) {
    var inlineTop = px(sidebar.style.top);
    if (inlineTop === null || inlineTop >= 0) {
      return false;
    }

    var navHeight = navigationItems.clientHeight || navigationItems.scrollHeight || 0;
    var largeNavThreshold = window.innerHeight * LARGE_NAV_MULTIPLIER;
    return navHeight > largeNavThreshold;
  }

  function clampSidebarTop() {
    var sidebar = document.getElementById("sidebar");
    var navigationItems = document.getElementById("navigation-items");
    var sidebarContent = document.getElementById("sidebar-content");

    if (!sidebar || !navigationItems || !sidebarContent) {
      return;
    }

    observeSidebar(sidebar);

    var inlineTop = px(sidebar.style.top);
    if (inlineTop !== null && inlineTop >= 0) {
      lastSafeTopPx = inlineTop;
    }

    if (!shouldClamp(sidebar, navigationItems)) {
      return;
    }

    /*
     * Mintlify upstream patch candidate:
     * FooterAndSidebarScrollScript currently computes its sidebar adjustment
     * from #navigation-items.clientHeight. For long docs nav trees, that is
     * the full content height even though #sidebar-content is the actual
     * overflow-auto scrollport. The script should bound that height to the
     * visible sidebar scrollport before deriving sidebar.style.top.
     *
     * Until that upstream change exists, preserve Mintlify's footer-driven
     * bottom value and only clamp impossible negative top offsets on pages
     * with abnormally large nav trees.
     */
    sidebar.style.top = safeTopPx(sidebar) + "px";
    sidebar.setAttribute(PATCHED_ATTR, "clamped-negative-top");
  }

  function scheduleClamp() {
    if (scheduled) {
      return;
    }

    scheduled = true;
    window.requestAnimationFrame(function () {
      scheduled = false;
      clampSidebarTop();
    });
  }

  function observeSidebar(sidebar) {
    if (sidebar === observedSidebar) {
      return;
    }

    if (sidebarObserver) {
      sidebarObserver.disconnect();
    }

    observedSidebar = sidebar;
    sidebarObserver = new MutationObserver(scheduleClamp);
    sidebarObserver.observe(sidebar, {
      attributes: true,
      attributeFilter: ["style"],
    });
  }

  function patchHistoryMethod(name) {
    var original = window.history[name];
    if (typeof original !== "function") {
      return;
    }

    window.history[name] = function () {
      var result = original.apply(this, arguments);
      setTimeout(scheduleClamp, 0);
      return result;
    };
  }

  patchHistoryMethod("pushState");
  patchHistoryMethod("replaceState");

  window[PATCH_MARKER] = {
    loaded: true,
    version: PATCH_VERSION,
  };

  window.addEventListener("popstate", scheduleClamp);
  window.addEventListener("resize", scheduleClamp, { passive: true });
  window.addEventListener("scroll", scheduleClamp, { passive: true });
  document.addEventListener("scroll", scheduleClamp, {
    capture: true,
    passive: true,
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scheduleClamp, { once: true });
  } else {
    scheduleClamp();
  }
})();

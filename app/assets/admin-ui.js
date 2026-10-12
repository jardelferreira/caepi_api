/* Shared admin navigation and async interaction feedback. */
(() => {
  "use strict";
  const currentPath = window.location.pathname;
  const target = currentPath === "/admin/sources" || (currentPath === "/dashboard" && location.hash === "#source-manager") ? "sources" :
    currentPath === "/certificates" ? "certificates" :
    currentPath === "/dashboard" ? "monitor" :
    currentPath === "/admin/consult" ? "consult" :
    currentPath === "/admin/api-keys" ? "keys" : "home";

  document.querySelectorAll('nav.admin-nav [data-nav]').forEach(link => {
    if (link.dataset.nav === target) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });

  const progress = document.createElement("div");
  progress.id = "caepi-page-progress";
  progress.setAttribute("aria-hidden", "true");
  const live = document.createElement("span");
  live.id = "caepi-page-status";
  live.setAttribute("role", "status");
  live.setAttribute("aria-live", "polite");
  const header = document.querySelector("body:not(.login-page) header");
  if (header) header.insertAdjacentElement("afterend", progress);
  else document.body.prepend(progress);
  document.body.append(live);

  let activeAction = null;
  let actionSequence = 0;
  const nativeFetch = window.fetch.bind(window);

  function finish(action) {
    if (!action || action.finished) return;
    action.finished = true;
    clearTimeout(action.idleTimer);
    clearTimeout(action.fallbackTimer);
    if (action.button && action.button.isConnected) {
      action.button.innerHTML = action.originalHtml;
      action.button.disabled = action.originalDisabled;
      action.button.classList.remove("is-loading");
      action.button.removeAttribute("aria-busy");
      delete action.button.dataset.uiBusyActive;
    }
    if (activeAction === action) activeAction = null;
    if (!activeAction) {
      progress.classList.remove("is-active");
      live.textContent = "";
    }
  }

  function scheduleFinish(action, delay = 700) {
    if (!action || action.finished || action.pending > 0) return;
    clearTimeout(action.idleTimer);
    action.idleTimer = setTimeout(() => finish(action), delay);
  }

  function reserve(button) {
    if (!button || button.dataset.uiBusyActive === "true" || button.disabled) return null;
    if (activeAction && !activeAction.finished) {
      if (activeAction.button === button || activeAction.pending > 0) return activeAction;
      finish(activeAction);
    }
    const action = {
      id: ++actionSequence,
      button,
      originalHtml: button.innerHTML,
      originalDisabled: button.disabled,
      pending: 0,
      finished: false,
      visualStarted: false,
      idleTimer: null,
      fallbackTimer: null,
    };
    activeAction = action;
    button.dataset.uiBusyActive = "true";
    // Do not disable during capture: that can cancel the native submit action of a form.
    return action;
  }

  function showBusy(action) {
    if (!action || action.finished || action.visualStarted || !action.button.isConnected) return;
    const button = action.button;
    action.visualStarted = true;
    button.disabled = true;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    const label = button.dataset.uiLoadingLabel || "Aguarde…";
    const labelNode = document.createElement("span");
    labelNode.textContent = label;
    const spinner = document.createElement("span");
    spinner.className = "button-spinner";
    spinner.setAttribute("aria-hidden", "true");
    button.replaceChildren(spinner, labelNode);
    progress.classList.add("is-active");
    live.textContent = label;
    // Validation/cancel paths which never call fetch must not leave a button disabled.
    action.fallbackTimer = setTimeout(() => {
      if (action.pending === 0) finish(action);
    }, 1800);
  }

  function requestAction(button) {
    const action = reserve(button);
    if (!action || action.button !== button) return action;
    setTimeout(() => {
      if (action.finished) return;
      if (action.pending > 0) showBusy(action);
      else finish(action); // A validação foi cancelada ou não iniciou requisição: não bloquear o botão.
    }, 0);
    return action;
  }

  window.fetch = async (...args) => {
    // Associate short chained requests with the user's active operation. The progress
    // indicator stays active while any request is pending and across near-immediate chains.
    const action = activeAction && !activeAction.finished ? activeAction : null;
    if (action) {
      action.pending += 1;
      clearTimeout(action.idleTimer);
      clearTimeout(action.fallbackTimer);
      progress.classList.add("is-active");
    }
    try {
      return await nativeFetch(...args);
    } finally {
      if (action && !action.finished) {
        action.pending = Math.max(0, action.pending - 1);
        if (action.pending === 0) scheduleFinish(action, 650);
      }
    }
  };
})();

(() => {
  const nativeFetch = window.fetch.bind(window);
  let csrfPromise = null;
  let csrfToken = null;
  const safeMethods = new Set(["GET", "HEAD", "OPTIONS"]);

  async function loadSession() {
    if (!csrfPromise) {
      csrfPromise = nativeFetch("/api/v1/admin/auth/session", {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Accept": "application/json" }
      }).then(async response => {
        if (response.status === 401 || response.status === 503) {
          window.location.replace("/login");
          throw new Error("Sessão administrativa expirada.");
        }
        if (!response.ok) throw new Error("Não foi possível validar a sessão administrativa.");
        const data = await response.json();
        csrfToken = data.csrf_token;
        return data;
      }).catch(error => {
        csrfPromise = null;
        throw error;
      });
    }
    return csrfPromise;
  }

  window.caepiAdminSession = { load: loadSession };
  window.fetch = async (input, init = {}) => {
    const sourceRequest = input instanceof Request ? input : null;
    const url = new URL(sourceRequest ? sourceRequest.url : String(input), window.location.href);
    const method = String(init.method || (sourceRequest ? sourceRequest.method : "GET")).toUpperCase();
    const isAdminApi = url.origin === window.location.origin && url.pathname.startsWith("/api/v1/");
    if (isAdminApi && !safeMethods.has(method) && url.pathname !== "/api/v1/admin/auth/login") {
      await loadSession();
      const headers = new Headers(sourceRequest ? sourceRequest.headers : undefined);
      new Headers(init.headers || {}).forEach((value, key) => headers.set(key, value));
      headers.set("X-CSRF-Token", csrfToken);
      init = { ...init, headers, credentials: "same-origin", cache: "no-store" };
    }
    const response = await nativeFetch(input, init);
    // Se a sessão expirar enquanto a página estiver aberta, encaminha o operador
    // para o login, em vez de deixar os módulos falharem silenciosamente.
    if (isAdminApi && response.status === 401 &&
        !url.pathname.endsWith("/admin/auth/login") &&
        !url.pathname.endsWith("/admin/auth/session")) {
      window.location.replace("/login");
    }
    return response;
  };

  document.addEventListener("click", async event => {
    const button = event.target.closest("[data-admin-logout]");
    if (!button) return;
    button.disabled = true;
    try {
      const response = await window.fetch("/api/v1/admin/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error("Não foi possível encerrar a sessão.");
      window.location.replace("/login?logged_out=1");
    } catch (error) {
      alert(error.message || "Falha ao encerrar a sessão.");
      button.disabled = false;
    }
  });
})();

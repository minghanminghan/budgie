// Thumbs, source preview/subscribe, and unsubscribe. No framework.
async function api(method, url, body) {
  const resp = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try { data = await resp.json(); } catch (_) {}
  return { ok: resp.ok, status: resp.status, data };
}

document.addEventListener("click", async (event) => {
  const thumb = event.target.closest("button.thumb");
  if (thumb && !thumb.disabled) {
    const id = Number(thumb.dataset.impression);
    const pressed = thumb.getAttribute("aria-pressed") === "true";
    const value = pressed ? "clear" : thumb.dataset.value;
    const pair = document.querySelectorAll(`button.thumb[data-impression="${id}"]`);
    thumb.setAttribute("aria-busy", "true");
    const { ok, data } = await api("POST", "/api/feedback", { impression_id: id, value });
    thumb.removeAttribute("aria-busy");
    if (ok) {
      pair.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.value === data.value)));
    }
    return;
  }

  const unsub = event.target.closest("button[data-unsubscribe]");
  if (unsub) {
    const id = unsub.dataset.unsubscribe;
    const { ok } = await api("DELETE", `/api/sources/${id}`);
    if (ok) document.getElementById(`source-${id}`)?.remove();
    return;
  }

  const sub = event.target.closest("button[data-subscribe]");
  if (sub) {
    const box = document.getElementById("preview");
    sub.disabled = true;
    const { data } = await api("POST", "/api/sources", { url: sub.dataset.subscribe });
    box.textContent = data.message || "Something went wrong. Try again.";
    if (data.id) setTimeout(() => location.reload(), 900);
  }
});

const form = document.getElementById("add-source");
if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = form.elements.url.value.trim();
    const box = document.getElementById("preview");
    box.textContent = "Looking for a feed…";
    const { ok, data } = await api("POST", "/api/sources/preview", { url });
    box.replaceChildren();
    if (!ok || !data.found) {
      box.textContent = data.message || "No feed found";
      return;
    }
    const title = document.createElement("strong");
    title.textContent = `${data.title} (${data.site_domain})`;
    const list = document.createElement("ul");
    for (const t of data.preview_titles) {
      const li = document.createElement("li");
      li.textContent = t;
      list.append(li);
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn";
    button.dataset.subscribe = url;
    button.textContent = `Subscribe to ${data.title}`;
    box.append(title, list, button);
  });
}

"use strict";

const app = document.getElementById("app");

// --- Utilitaires ------------------------------------------------------------

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));

// Vitrine en ligne : pas de serveur, la collection exportée (collection.json) est lue
// en lecture seule. Voir export_static.py.
let staticData = null;

async function detectMode() {
  try {
    const res = await fetch("/api/stats", { cache: "no-store" });
    if (res.ok && res.headers.get("Content-Type")?.includes("json")) return;
  } catch { /* pas de serveur */ }
  const res = await fetch("collection.json", { cache: "no-cache" });
  if (!res.ok) throw new Error("Collection introuvable : lancez export_static.py avant de publier.");
  staticData = await res.json();
  document.body.classList.add("readonly");
  const date = new Date(staticData.generated_at).toLocaleDateString("fr-FR", { dateStyle: "long" });
  document.querySelector(".site-footer .wrap").insertAdjacentHTML("beforeend", ` · Collection mise à jour le ${date}`);
}

function staticApi(path) {
  const [route, query] = path.split("?");
  if (route === "/stats") return staticData.stats;
  if (route === "/series") {
    const q = (new URLSearchParams(query).get("q") || "").toLocaleLowerCase("fr");
    return staticData.series.filter((s) => !q
      || [s.title, s.author, s.genre].some((v) => (v || "").toLocaleLowerCase("fr").includes(q)));
  }
  const id = +(route.match(/^\/series\/(\d+)$/) || [])[1];
  const series = staticData.series.find((s) => s.id === id);
  if (!series) throw new Error("Série introuvable.");
  return series;
}

async function api(path, options = {}) {
  if (staticData) {
    if ((options.method || "GET") !== "GET") throw new Error("Cette version en ligne est en lecture seule.");
    return staticApi(path);
  }
  const res = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Erreur " + res.status);
  return data;
}

let toastTimer;
function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

const STATUS_CLASS = { "en cours": "ongoing", "terminée": "done", "abandonnée": "dropped" };
const statusChip = (s) => `<span class="chip chip-${STATUS_CLASS[s] || "ongoing"}">${esc(s || "en cours")}</span>`;

// Couleurs chaudes pour les couvertures sans image, choisies selon le titre.
const PALETTE = [
  ["#c8643a", "#8f3d1f"], ["#d9973a", "#a5641c"], ["#6b7f3a", "#45561f"],
  ["#3f6f73", "#24474a"], ["#8a4f6d", "#5c2f47"], ["#5a4a8a", "#372b5e"],
  ["#a8443f", "#6e2522"], ["#2f5a7a", "#1b3a52"],
];
function hashColor(text) {
  let h = 0;
  for (const ch of String(text)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

function placeholder({ title, tome, seriesTitle }) {
  const [a, b] = hashColor(seriesTitle || title);
  return `
    <div class="cover-placeholder" style="background:linear-gradient(160deg,${a},${b})">
      <span class="ph-series">${esc(seriesTitle || "")}</span>
      <span class="ph-title">${esc(title)}</span>
      <span class="ph-tome">${tome != null ? "#" + esc(tome) : ""}</span>
    </div>`;
}

/** Couverture : image si disponible, sinon couverture illustrée générée. */
function coverHTML({ cover, title, tome, seriesTitle, read, wanted }) {
  const ph = placeholder({ title, tome, seriesTitle });
  const img = cover
    ? `<img src="${esc(cover)}" alt="Couverture de ${esc(title)}" loading="lazy" onerror="this.remove()">`
    : "";
  // Le placeholder est dessous : si l'image échoue elle est retirée et il apparaît.
  const badge = wanted ? '<span class="badge-wanted">À acheter</span>'
    : read ? '<span class="badge-read">Lu ✓</span>' : "";
  return `<div class="cover">${ph}${img}${badge}</div>`;
}

function albumCard(album, seriesTitle, { linkToSeries = false } = {}) {
  const tag = linkToSeries ? "a" : "button";
  const attrs = linkToSeries
    ? `href="#/series/${album.series_id}"`
    : `type="button" data-album="${album.id}"`;
  return `
    <${tag} class="album-card${album.owned ? "" : " wanted"}" ${attrs}>
      ${coverHTML({ cover: album.cover, title: album.title, tome: album.tome, seriesTitle,
                    read: album.is_read, wanted: !album.owned })}
      <div class="album-meta">
        ${album.tome != null ? `<div class="tome">Tome ${esc(album.tome)}</div>` : ""}
        <h3>${esc(album.title)}</h3>
        <div class="sub">${esc(linkToSeries ? seriesTitle : (album.year || ""))}</div>
      </div>
    </${tag}>`;
}

/** Emplacement vide pour un tome absent de la collection. */
const missingSlot = (n) => `
  <button type="button" class="album-card" data-missing="${n}" title="Ajouter le tome ${n}">
    <div class="missing-slot"><strong>${n}</strong><span>Tome manquant</span><span class="edit-only">+ Ajouter</span></div>
    <div class="album-meta"><div class="tome">Tome ${n}</div><h3>Manquant</h3></div>
  </button>`;

const plural = (n, word) => `${n} ${word}${n > 1 ? "s" : ""}`;

// --- Pages ------------------------------------------------------------------

async function renderHome() {
  const s = await api("/stats");
  app.innerHTML = `
    <section class="hero">
      <h1>Bienvenue dans votre bédéthèque</h1>
      <p>Toutes vos séries et vos albums, bien rangés sur une seule étagère.
         Ajoutez vos BD, leurs couvertures, et suivez ce qu'il vous reste à lire.</p>
      <a class="btn btn-primary" href="#/series">Parcourir les séries →</a>
      <div class="bubble" aria-hidden="true">Bulles !</div>
    </section>

    <section class="stats" aria-label="Statistiques">
      <div class="stat"><strong>${s.series}</strong><span>${s.series > 1 ? "séries" : "série"}</span></div>
      <div class="stat"><strong>${s.albums}</strong><span>${s.albums > 1 ? "albums" : "album"}</span></div>
      <div class="stat"><strong>${s.read}</strong><span>${s.read > 1 ? "lus" : "lu"}</span></div>
      <div class="stat"><strong>${s.missing}</strong><span>à compléter</span></div>
    </section>

    <section>
      <div class="section-head">
        <h2>Derniers ajouts</h2>
        ${s.recent.length ? '<a href="#/series">Voir toutes les séries</a>' : ""}
      </div>
      ${s.recent.length
        ? `<div class="grid">${s.recent.map((a) => albumCard(a, a.series_title, { linkToSeries: true })).join("")}</div>`
        : `<div class="empty">
             <div class="big">📚</div>
             <p>Votre étagère est encore vide.</p>
             <button class="btn btn-primary" data-action="new-series">Créer ma première série</button>
           </div>`}
    </section>`;
}

let searchTimer;
async function renderSeriesList(query = "") {
  const list = await api("/series" + (query ? "?q=" + encodeURIComponent(query) : ""));
  const searchField = document.getElementById("series-search");
  if (!searchField) {
    app.innerHTML = `
      <h1>Mes séries</h1>
      <div class="toolbar">
        <div class="search">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          <input id="series-search" type="search" placeholder="Rechercher une série, un auteur, un genre…" aria-label="Rechercher">
        </div>
        <button class="btn btn-primary" data-action="new-series">+ Nouvelle série</button>
      </div>
      <div id="series-results"></div>`;
    document.getElementById("series-search").addEventListener("input", (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        if (location.hash === "#/series") renderSeriesList(e.target.value.trim());
      }, 200);
    });
  }
  const results = document.getElementById("series-results");
  if (!list.length) {
    results.innerHTML = query
      ? `<div class="empty"><div class="big">🔍</div><p>Aucune série ne correspond à « ${esc(query)} ».</p></div>`
      : `<div class="empty"><div class="big">📖</div><p>Aucune série pour l'instant.</p>
           <button class="btn btn-primary" data-action="new-series">Ajouter une série</button></div>`;
    return;
  }
  results.innerHTML = `<div class="grid grid-series">${list.map((s) => {
    const count = s.album_count || 0;
    const read = s.read_count || 0;
    const pct = count ? Math.round((read / count) * 100) : 0;
    return `
      <a class="series-card" href="#/series/${s.id}">
        ${coverHTML({ cover: s.cover, title: s.title, seriesTitle: s.title })}
        <div class="series-info">
          <h3>${esc(s.title)}</h3>
          <p class="sub">${esc([s.author, s.publisher].filter(Boolean).join(" · "))}</p>
          <div>${s.genre ? `<span class="chip">${esc(s.genre)}</span>` : ""}${statusChip(s.status)}</div>
          <div class="progress" title="${pct}% lus"><span style="width:${pct}%"></span></div>
          <div class="progress-label">${plural(count, "album")} · ${read} lu${read > 1 ? "s" : ""}${
            s.missing_count ? ` · <strong>${s.missing_count} manquant${s.missing_count > 1 ? "s" : ""}</strong>` : ""}</div>
        </div>
      </a>`;
  }).join("")}</div>`;
}

let currentSeries = null;
let seriesFilter = "all"; // all | owned | missing

async function renderSeries(id) {
  const s = await api("/series/" + id);
  if (currentSeries?.id !== s.id) seriesFilter = "all";
  currentSeries = s;
  const owned = s.albums.filter((a) => a.owned);
  const read = owned.filter((a) => a.is_read).length;
  const gaps = s.missing_tomes.length;
  const wanted = s.albums.length - owned.length;
  const total = s.total_tomes ? ` sur ${s.total_tomes} parus` : "";
  app.innerHTML = `
    <nav class="breadcrumb"><a href="#/series">← Toutes les séries</a></nav>
    <header class="series-header">
      <div class="series-header-main">
        <h1>${esc(s.title)}</h1>
        <p class="sub">${esc([s.author, s.publisher].filter(Boolean).join(" · "))}</p>
        <div>${s.genre ? `<span class="chip">${esc(s.genre)}</span>` : ""}${statusChip(s.status)}
          <span class="chip">${plural(owned.length, "album")}${total} · ${read} lu${read > 1 ? "s" : ""}</span></div>
        ${s.description ? `<p class="desc">${esc(s.description)}</p>` : ""}
      </div>
      <div class="series-actions">
        <button class="btn btn-primary" data-action="new-album">+ Ajouter un album</button>
        <button class="btn btn-ghost" data-action="enrich"
                title="Importe les couvertures manquantes et complète les informations vides à partir des ISBN">Compléter depuis internet</button>
        <button class="btn btn-ghost" data-action="edit-series">Modifier</button>
        <button class="btn btn-danger-ghost" data-action="delete-series">Supprimer</button>
      </div>
    </header>
    ${gaps ? `
      <div class="missing-banner">
        <p>Il vous manque <strong>${plural(gaps, "tome")}</strong> : ${gaps > 12
          ? s.missing_tomes.slice(0, 12).join(", ") + "…" : s.missing_tomes.join(", ")}.</p>
        <button class="btn btn-primary btn-small" data-action="add-missing">Tout ajouter à ma liste « à acheter »</button>
      </div>` : s.track_missing && !s.total_tomes && s.albums.length ? `
      <div class="missing-banner edit-only">
        <p>Indiquez le nombre de tomes parus pour repérer aussi les tomes manquants après le dernier que vous possédez.</p>
        <button class="btn btn-ghost btn-small" data-action="edit-series">Renseigner</button>
      </div>` : ""}
    ${s.albums.length || gaps ? `
      <div class="filters" role="group" aria-label="Filtrer les albums">
        ${[["all", "Tout"], ["owned", `Possédés (${owned.length})`], ["missing", `À compléter (${gaps + wanted})`]]
          .map(([k, label]) => `<button type="button" data-filter="${k}" aria-pressed="${seriesFilter === k}">${label}</button>`).join("")}
      </div>
      <div class="grid" id="album-grid"></div>`
      : `<div class="empty"><div class="big">🖼️</div><p>Cette série n'a pas encore d'album.</p>
           <button class="btn btn-primary" data-action="new-album">Ajouter le premier album</button></div>`}`;
  renderAlbumGrid();
}

/** Albums et emplacements de tomes manquants, mêlés dans l'ordre des tomes. */
function renderAlbumGrid() {
  const grid = document.getElementById("album-grid");
  if (!grid) return;
  const s = currentSeries;
  let items = [
    ...s.albums.map((a) => ({ tome: a.tome, html: albumCard(a, s.title), owned: a.owned })),
    ...s.missing_tomes.map((n) => ({ tome: n, html: missingSlot(n), owned: 0 })),
  ];
  if (seriesFilter === "owned") items = items.filter((i) => i.owned);
  if (seriesFilter === "missing") items = items.filter((i) => !i.owned);
  items.sort((a, b) => (a.tome ?? Infinity) - (b.tome ?? Infinity));
  grid.innerHTML = items.length
    ? items.map((i) => i.html).join("")
    : `<p class="sub">${seriesFilter === "missing" ? "Rien à compléter, la série est complète 🎉" : "Aucun album."}</p>`;
}

// --- Routeur ----------------------------------------------------------------

async function route() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const seriesMatch = hash.match(/^\/series\/(\d+)$/);
  const section = hash.startsWith("/series") ? "series" : "home";
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === section));
  app.innerHTML = '<div class="loading">Chargement…</div>';
  try {
    if (seriesMatch) await renderSeries(+seriesMatch[1]);
    else if (hash === "/series") await renderSeriesList();
    else await renderHome();
  } catch (err) {
    app.innerHTML = `<div class="empty"><div class="big">😕</div><p>${esc(err.message)}</p>
      <a class="btn btn-ghost" href="#/">Retour à l'accueil</a></div>`;
  }
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", () => route());

// --- Formulaire série -------------------------------------------------------

const seriesDialog = document.getElementById("series-dialog");
const seriesForm = document.getElementById("series-form");
let editingSeriesId = null;

function openSeriesForm(series = null) {
  editingSeriesId = series ? series.id : null;
  seriesForm.reset();
  seriesForm.querySelector(".modal-title").textContent = series ? "Modifier la série" : "Nouvelle série";
  seriesForm.querySelector(".form-error").hidden = true;
  if (series) {
    for (const el of seriesForm.elements) {
      if (el.name && el.name in series && el.type !== "checkbox") el.value = series[el.name] ?? "";
    }
    seriesForm.elements.track_missing.checked = series.track_missing !== 0;
  }
  seriesDialog.showModal();
  seriesForm.elements.title.focus();
}

seriesForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(seriesForm));
  body.track_missing = seriesForm.elements.track_missing.checked;
  try {
    const saved = editingSeriesId
      ? await api("/series/" + editingSeriesId, { method: "PUT", body })
      : await api("/series", { method: "POST", body });
    seriesDialog.close();
    toast(editingSeriesId ? "Série mise à jour" : "Série créée");
    if (location.hash === "#/series/" + saved.id) route();
    else location.hash = "#/series/" + saved.id;
  } catch (err) {
    showFormError(seriesForm, err.message);
  }
});

// --- Formulaire album -------------------------------------------------------

const albumDialog = document.getElementById("album-dialog");
const albumForm = document.getElementById("album-form");
const coverPreview = document.getElementById("cover-preview");
const coverFile = document.getElementById("cover-file");
let editingAlbum = null;
let pendingCoverData = null; // image locale redimensionnée (data URL)

function refreshPreview() {
  const f = albumForm.elements;
  coverPreview.innerHTML = coverHTML({
    cover: pendingCoverData || f.cover.value.trim(),
    title: f.title.value || "Titre",
    tome: f.tome.value || null,
    seriesTitle: currentSeries?.title,
  });
}

function openAlbumForm(album = null, defaults = {}) {
  editingAlbum = album;
  pendingCoverData = null;
  albumForm.reset();
  albumForm.querySelector(".modal-title").textContent = staticData ? "Fiche de l'album"
    : album ? "Modifier l'album" : "Nouvel album";
  albumForm.querySelector("fieldset").disabled = !!staticData;
  const source = document.getElementById("album-source");
  source.hidden = !album?.source_url;
  source.href = album?.source_url || "#";
  source.textContent = album?.source_url?.includes("bnf.fr") ? "Notice BnF ↗" : "Notice ↗";
  albumForm.querySelector(".form-error").hidden = true;
  document.getElementById("album-delete").hidden = !album;
  const f = albumForm.elements;
  if (album) {
    for (const name of ["tome", "title", "year", "isbn", "cover", "notes", "authors", "publisher", "pages"]) {
      f[name].value = album[name] ?? "";
    }
    f.is_read.checked = !!album.is_read;
    f.owned.checked = !!album.owned;
  } else if (defaults.tome) {
    // Tome manquant : pré-rempli, à acheter par défaut
    f.tome.value = defaults.tome;
    f.title.value = "Tome " + defaults.tome;
    f.owned.checked = false;
  } else {
    // Propose le tome suivant
    const tomes = currentSeries.albums.map((a) => a.tome).filter((t) => t != null);
    f.tome.value = tomes.length ? Math.max(...tomes) + 1 : 1;
  }
  document.getElementById("isbn-options").innerHTML = "";
  findIsbnBtn.hidden = !!album?.isbn;
  refreshPreview();
  albumDialog.showModal();
  if (!staticData) f.title.focus();
  // Tome manquant : on cherche tout de suite son ISBN, sa couverture et ses détails
  if (defaults.tome && !staticData) findIsbn().catch((err) => showFormError(albumForm, err.message));
}

["input", "change"].forEach((evt) => albumForm.addEventListener(evt, (e) => {
  if (e.target.name === "cover") pendingCoverData = null;
  if (e.target.name === "isbn") findIsbnBtn.hidden = !!e.target.value.trim();
  if (["cover", "title", "tome"].includes(e.target.name)) refreshPreview();
}));

/** Redimensionne l'image côté navigateur pour garder des fichiers légers. */
function resizeImage(file, maxSize = 1000) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, maxSize / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Image illisible")); };
    img.src = url;
  });
}

coverFile.addEventListener("change", async () => {
  const file = coverFile.files[0];
  if (!file) return;
  try {
    pendingCoverData = await resizeImage(file);
    albumForm.elements.cover.value = "";
    refreshPreview();
  } catch (err) {
    showFormError(albumForm, err.message);
  }
  coverFile.value = "";
});

// Recherche par ISBN : ne remplit que les champs vides, et ne télécharge la couverture
// que s'il n'y en a pas déjà une en local.
async function withBusy(btn, label, task) {
  const original = btn.textContent;
  btn.classList.add("busy");
  btn.disabled = true;
  btn.textContent = label;
  try {
    return await task();
  } finally {
    btn.classList.remove("busy");
    btn.disabled = false;
    btn.textContent = original;
  }
}

const findIsbnBtn = document.getElementById("find-isbn");
const coverIsbnBtn = document.getElementById("cover-isbn");

/** Cherche les éditions du tome à la BnF ; renseigne la meilleure si l'éditeur concorde. */
async function findIsbn() {
  const f = albumForm.elements;
  if (!f.tome.value) {
    showFormError(albumForm, "Indiquez d'abord le numéro du tome.");
    f.tome.focus();
    return;
  }
  albumForm.querySelector(".form-error").hidden = true;
  const { candidates } = await withBusy(findIsbnBtn, "Recherche…",
    () => api(`/find-isbn?series_id=${currentSeries.id}&tome=${f.tome.value}`));
  document.getElementById("isbn-options").innerHTML = candidates.map((c) =>
    `<option value="${esc(c.isbn)}">${esc([c.title, c.publisher, c.year].filter(Boolean).join(" · "))}</option>`).join("");
  if (!candidates.length) {
    showFormError(albumForm, `Aucune édition du tome ${f.tome.value} trouvée à la BnF.`);
    return;
  }
  const best = candidates[0];
  if (!best.same_publisher) {
    showFormError(albumForm, `${plural(candidates.length, "édition")} trouvée${candidates.length > 1 ? "s" : ""}, `
      + "mais pas chez l'éditeur de la série : choisissez dans la liste du champ ISBN.");
    return;
  }
  f.isbn.value = best.isbn;
  await lookupIsbn();
  if (candidates.length > 1) toast(`${candidates.length} éditions trouvées : les autres sont proposées dans le champ ISBN`);
}

findIsbnBtn.addEventListener("click", () => findIsbn().catch((err) => showFormError(albumForm, err.message)));
coverIsbnBtn.addEventListener("click", () => lookupIsbn().catch((err) => showFormError(albumForm, err.message)));

async function lookupIsbn() {
  const f = albumForm.elements;
  const isbn = f.isbn.value.replace(/[^0-9Xx]/g, "");
  if (!isbn) {
    showFormError(albumForm, "Saisissez d'abord l'ISBN de l'album, ou utilisez « Trouver l'ISBN ».");
    f.isbn.focus();
    return;
  }
  albumForm.querySelector(".form-error").hidden = true;
  const hasLocalCover = !!pendingCoverData || f.cover.value.startsWith("/covers/");
  await withBusy(coverIsbnBtn, "Recherche…", async () => {
    const info = await api(`/lookup?isbn=${isbn}&cover=${hasLocalCover ? 0 : 1}`);
    if (info.title && /^\s*(tome\s*\d+)?\s*$/i.test(f.title.value)) f.title.value = info.title;
    for (const name of ["authors", "publisher", "year", "pages"]) {
      if (info[name] && !f[name].value) f[name].value = info[name];
    }
    if (info.cover) {
      pendingCoverData = null;
      f.cover.value = info.cover;
    }
    if (info.source_url && editingAlbum) editingAlbum.source_url = info.source_url;
    refreshPreview();
    toast(info.cover ? "Couverture et informations récupérées" : hasLocalCover
      ? "Informations complétées (couverture conservée)" : "Informations complétées, pas de couverture trouvée");
  });
}

document.getElementById("cover-clear").addEventListener("click", () => {
  pendingCoverData = null;
  albumForm.elements.cover.value = "";
  refreshPreview();
});

albumForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = albumForm.elements;
  const body = {
    tome: f.tome.value, title: f.title.value, year: f.year.value, isbn: f.isbn.value,
    cover: f.cover.value.trim(), is_read: f.is_read.checked, owned: f.owned.checked, notes: f.notes.value,
    authors: f.authors.value, publisher: f.publisher.value, pages: f.pages.value,
  };
  if (pendingCoverData) body.cover_data = pendingCoverData;
  // Une couverture donnée par URL est téléchargée par le serveur : cela peut prendre un instant
  const label = /^https?:\/\//.test(body.cover) ? "Téléchargement…" : "Enregistrement…";
  try {
    await withBusy(albumForm.querySelector('button[type="submit"]'), label, () => editingAlbum
      ? api("/albums/" + editingAlbum.id, { method: "PUT", body })
      : api(`/series/${currentSeries.id}/albums`, { method: "POST", body }));
    albumDialog.close();
    toast(editingAlbum ? "Album mis à jour" : "Album ajouté");
    route();
  } catch (err) {
    showFormError(albumForm, err.message);
  }
});

document.getElementById("album-delete").addEventListener("click", async () => {
  if (!editingAlbum || !confirm(`Supprimer l'album « ${editingAlbum.title} » ?`)) return;
  try {
    await api("/albums/" + editingAlbum.id, { method: "DELETE" });
    albumDialog.close();
    toast("Album supprimé");
    route();
  } catch (err) {
    showFormError(albumForm, err.message);
  }
});

// --- Actions communes -------------------------------------------------------

function showFormError(form, message) {
  const el = form.querySelector(".form-error");
  el.textContent = message;
  el.hidden = false;
}

document.querySelectorAll("[data-close]").forEach((btn) =>
  btn.addEventListener("click", () => btn.closest("dialog").close()));

// Ferme la modale au clic sur le fond
document.querySelectorAll("dialog").forEach((d) => d.addEventListener("click", (e) => {
  if (e.target === d) d.close();
}));

app.addEventListener("click", async (e) => {
  if (staticData && e.target.closest("[data-missing]")) return;
  const albumBtn = e.target.closest("[data-album]");
  if (albumBtn) {
    openAlbumForm(currentSeries.albums.find((a) => a.id === +albumBtn.dataset.album));
    return;
  }
  const missingBtn = e.target.closest("[data-missing]");
  if (missingBtn) {
    openAlbumForm(null, { tome: +missingBtn.dataset.missing });
    return;
  }
  const filterBtn = e.target.closest("[data-filter]");
  if (filterBtn) {
    seriesFilter = filterBtn.dataset.filter;
    app.querySelectorAll("[data-filter]").forEach((b) => b.setAttribute("aria-pressed", b === filterBtn));
    renderAlbumGrid();
    return;
  }
  const action = e.target.closest("[data-action]")?.dataset.action;
  if (action === "new-series") openSeriesForm();
  else if (action === "edit-series") openSeriesForm(currentSeries);
  else if (action === "new-album") openAlbumForm();
  else if (action === "enrich") {
    const btn = e.target.closest("[data-action]");
    btn.classList.add("busy");
    btn.disabled = true;
    btn.textContent = "Recherche en cours…";
    try {
      const r = await api(`/series/${currentSeries.id}/enrich`, { method: "POST" });
      const parts = [`${plural(r.covers, "couverture")} importée${r.covers > 1 ? "s" : ""}`,
                     ...(r.isbns ? [`${r.isbns} ISBN trouvé${r.isbns > 1 ? "s" : ""}`] : []),
                     `${plural(r.infos, "album")} complété${r.infos > 1 ? "s" : ""}`];
      if (r.missing_covers) parts.push(`${r.missing_covers} toujours sans couverture`);
      toast(parts.join(" · "));
      route();
    } catch (err) {
      toast(err.message);
      btn.classList.remove("busy");
      btn.disabled = false;
      btn.textContent = "Compléter depuis internet";
    }
  }
  else if (action === "add-missing") {
    const n = currentSeries.missing_tomes.length;
    if (!confirm(`Ajouter ${plural(n, "tome")} manquant${n > 1 ? "s" : ""} à votre liste « à acheter » ?\n`
      + "Les titres seront « Tome N » : vous pourrez les modifier ensuite.")) return;
    try {
      const { added } = await api(`/series/${currentSeries.id}/missing`, { method: "POST" });
      toast(`${plural(added, "tome")} ajouté${added > 1 ? "s" : ""} à la liste, recherche des ISBN…`);
      await route();
      // Enchaîne sur la recherche d'ISBN, couvertures et détails des tomes ajoutés
      app.querySelector('[data-action="enrich"]')?.click();
    } catch (err) {
      toast(err.message);
    }
  }
  else if (action === "delete-series") {
    const n = currentSeries.albums.length;
    const msg = `Supprimer la série « ${currentSeries.title} »` + (n ? ` et ses ${plural(n, "album")} ?` : " ?");
    if (!confirm(msg)) return;
    try {
      await api("/series/" + currentSeries.id, { method: "DELETE" });
      toast("Série supprimée");
      location.hash = "#/series";
    } catch (err) {
      toast(err.message);
    }
  }
});

detectMode().then(route, (err) => {
  app.innerHTML = `<div class="empty"><div class="big">😕</div><p>${esc(err.message)}</p></div>`;
});

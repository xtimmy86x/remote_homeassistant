/* Experimental, dependency-free Home Assistant sidebar settings panel. */
const TYPE = "remote_homeassistant/panel";
const WORDS = {
  it: {
    title: "Remote Home Assistant", connection: "Connessione", loading: "Caricamento…",
    empty: "Aggiungi prima una connessione da Impostazioni → Dispositivi e servizi.",
    entities: "Entità", domains: "Domini", services: "Servizi", events: "Eventi",
    filters: "Filtri", advanced: "Avanzate", search: "Cerca nell'elenco…",
    all: "Importa tutte le entità", allHint: "Disattivato: importa solo le entità e i domini inclusi. Nessuna selezione significa zero importazioni.",
    include: "Includi", exclude: "Escludi", none: "Nessuna regola", clear: "Azzera",
    matching: "Risultati", shown: "Mostrati", selected: "Selezionati", add: "Aggiungi",
    newEvent: "Nome nuovo evento", remove: "Rimuovi", addFilter: "Aggiungi filtro",
    pattern: "ID entità o pattern", unit: "Unità", above: "Sopra", below: "Sotto",
    entityPrefix: "Prefisso ID entità", namePrefix: "Prefisso nome", servicePrefix: "Prefisso servizi",
    components: "Componenti da caricare", save: "Salva modifiche", saving: "Salvataggio…",
    saved: "Salvato. La connessione verrà ricaricata.", reload: "Ricarica",
    unsaved: "Modifiche non salvate", conflict: "Le opzioni sono cambiate altrove. Ricarica il pannello.",
    switchWarning: "Ci sono modifiche non salvate. Cambiare connessione?",
    more: "Digita per restringere l'elenco. Le azioni in blocco si applicano a tutti i risultati.",
    offline: "L'istanza non è connessa: le selezioni già salvate restano disponibili.",
  },
  en: {
    title: "Remote Home Assistant", connection: "Connection", loading: "Loading…",
    empty: "Add a connection in Settings → Devices & services first.",
    entities: "Entities", domains: "Domains", services: "Services", events: "Events",
    filters: "Filters", advanced: "Advanced", search: "Search this list…",
    all: "Import all entities", allHint: "Off: import only included entities and domains. No selection imports nothing.",
    include: "Include", exclude: "Exclude", none: "No rule", clear: "Clear",
    matching: "Matches", shown: "Shown", selected: "Selected", add: "Add",
    newEvent: "New event name", remove: "Remove", addFilter: "Add filter",
    pattern: "Entity ID or pattern", unit: "Unit", above: "Above", below: "Below",
    entityPrefix: "Entity ID prefix", namePrefix: "Name prefix", servicePrefix: "Service prefix",
    components: "Components to load", save: "Save changes", saving: "Saving…",
    saved: "Saved. The connection will reload.", reload: "Reload",
    unsaved: "Unsaved changes", conflict: "Settings changed elsewhere. Reload the panel.",
    switchWarning: "There are unsaved changes. Switch connection?",
    more: "Type to narrow the list. Bulk actions apply to all matches.",
    offline: "The remote is offline. Previously saved choices remain available.",
  },
};

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);

class RemoteHomeAssistantPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._entries = [];
    this._entryId = null;
    this._detail = null;
    this._options = null;
    this._tab = "entities";
    this._search = "";
    this._dirty = false;
    this._busy = false;
    this._message = "";
  }

  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this._started) this._start();
  }

  connectedCallback() {
    this._draw();
    if (this._hass && !this._started) this._start();
  }

  get _t() {
    return WORDS[this._hass?.language?.startsWith("it") ? "it" : "en"];
  }

  async _start() {
    this._started = true;
    this._busy = true;
    this._draw();
    try {
      this._entries = await this._hass.callWS({ type: `${TYPE}/list` });
      if (this._entries.length) await this._load(this._entries[0].entry_id);
    } catch (error) {
      this._message = error.message || String(error);
    } finally {
      this._busy = false;
      this._draw();
    }
  }

  async _load(entryId) {
    this._busy = true;
    this._draw();
    try {
      const detail = await this._hass.callWS({ type: `${TYPE}/get`, entry_id: entryId });
      this._entryId = entryId;
      this._detail = detail;
      this._options = structuredClone(detail.options);
      this._dirty = false;
      this._message = "";
      this._search = "";
    } catch (error) {
      this._message = error.message || String(error);
    } finally {
      this._busy = false;
      this._draw();
    }
  }

  _markDirty() {
    this._dirty = true;
    const note = this.shadowRoot.querySelector(".notice");
    if (note) note.textContent = this._t.unsaved;
  }

  _setChoice(kind, value, state) {
    const keys = kind === "entities"
      ? ["include_entities", "exclude_entities"]
      : ["include_domains", "exclude_domains"];
    for (const key of keys) this._options[key] = this._options[key].filter((item) => item !== value);
    if (state === "include") this._options[keys[0]].push(value);
    if (state === "exclude") this._options[keys[1]].push(value);
    this._markDirty();
    this._draw();
  }

  _choiceList(kind) {
    const t = this._t;
    const all = this._detail[kind];
    const matches = all.filter((item) => item.toLowerCase().includes(this._search.toLowerCase()));
    const visible = matches.slice(0, 120);
    const service = kind === "services";
    const keys = kind === "entities" ? ["include_entities", "exclude_entities"] : ["include_domains", "exclude_domains"];
    const rows = visible.map((value) => {
      const state = service
        ? this._options.services.includes(value) ? "include" : "none"
        : this._options[keys[1]].includes(value) ? "exclude"
          : this._options[keys[0]].includes(value) ? "include" : "none";
      const control = service
        ? `<input type="checkbox" data-choice="service" data-value="${escapeHtml(value)}" ${state === "include" ? "checked" : ""}>`
        : `<select data-choice="${kind}" data-value="${escapeHtml(value)}">
            <option value="none" ${state === "none" ? "selected" : ""}>${t.none}</option>
            <option value="include" ${state === "include" ? "selected" : ""}>${t.include}</option>
            <option value="exclude" ${state === "exclude" ? "selected" : ""}>${t.exclude}</option>
          </select>`;
      return `<div class="item"><span title="${escapeHtml(value)}">${escapeHtml(value)}</span>${control}</div>`;
    }).join("");
    const bulk = service
      ? ""
      : `<div class="actions"><button data-bulk="include">${t.include}</button><button data-bulk="exclude">${t.exclude}</button><button data-bulk="none">${t.clear}</button></div>`;
    return `<div class="toolbar"><input id="search" type="search" placeholder="${t.search}" value="${escapeHtml(this._search)}"></div>
      <div class="hint">${t.matching}: ${matches.length} · ${t.shown}: ${visible.length}. ${t.more}</div>
      ${bulk}<div class="items">${rows}</div>`;
  }

  _events() {
    const t = this._t;
    return `<form id="event-form"><input name="event" placeholder="${t.newEvent}" required><button>${t.add}</button></form>
      <div class="items">${this._options.subscribe_events.map((event) =>
        `<div class="item"><span>${escapeHtml(event)}</span><button data-remove-event="${escapeHtml(event)}">${t.remove}</button></div>`
      ).join("")}</div>`;
  }

  _filters() {
    const t = this._t;
    return `<button id="add-filter">${t.addFilter}</button>
      <div class="filters">${this._options.filter.map((filter, index) =>
        `<div class="filter" data-filter="${index}">
          <input data-field="entity_id" placeholder="${t.pattern}" value="${escapeHtml(filter.entity_id)}">
          <input data-field="unit_of_measurement" placeholder="${t.unit}" value="${escapeHtml(filter.unit_of_measurement)}">
          <input data-field="above" type="number" step="any" placeholder="${t.above}" value="${escapeHtml(filter.above)}">
          <input data-field="below" type="number" step="any" placeholder="${t.below}" value="${escapeHtml(filter.below)}">
          <button data-remove-filter="${index}">${t.remove}</button>
        </div>`
      ).join("")}</div>`;
  }

  _advanced() {
    const t = this._t;
    return `<div class="field-grid">
      <label>${t.entityPrefix}<input data-option="entity_prefix" value="${escapeHtml(this._options.entity_prefix)}"></label>
      <label>${t.namePrefix}<input data-option="entity_friendly_name_prefix" value="${escapeHtml(this._options.entity_friendly_name_prefix)}"></label>
      <label>${t.servicePrefix}<input data-option="service_prefix" value="${escapeHtml(this._options.service_prefix)}"></label>
    </div><h3>${t.components}</h3><div class="items">${this._detail.domains.map((domain) =>
      `<label class="item"><span>${escapeHtml(domain)}</span><input type="checkbox" data-component="${escapeHtml(domain)}" ${this._options.load_components.includes(domain) ? "checked" : ""}></label>`
    ).join("")}</div>`;
  }

  _draw() {
    const t = this._t;
    const focus = this.shadowRoot.activeElement?.id === "search";
    const position = focus ? this.shadowRoot.activeElement.selectionStart : null;
    this.shadowRoot.innerHTML = `<style>
      :host { display:block; min-height:100vh; background:var(--primary-background-color); color:var(--primary-text-color); font-family:var(--paper-font-body1_-_font-family, sans-serif) }
      * { box-sizing:border-box } main { max-width:1100px; margin:auto; padding:24px 18px 100px }
      h1 { font-size:26px; margin:8px 0 18px } h2 { font-size:20px } h3 { font-size:16px; margin-top:30px }
      .top, .toolbar, .actions, .footer { display:flex; align-items:center; gap:12px; flex-wrap:wrap }
      .top select { min-width:240px } .status, .hint { color:var(--secondary-text-color); font-size:14px }
      .tabs { display:flex; gap:4px; overflow:auto; margin:26px 0 20px; border-bottom:1px solid var(--divider-color) }
      .tabs button { border:0; border-radius:8px 8px 0 0; white-space:nowrap }
      .tabs button.active { color:var(--primary-color); border-bottom:3px solid var(--primary-color) }
      button, select, input { font:inherit; color:inherit; border:1px solid var(--divider-color); border-radius:8px; padding:10px 12px; background:var(--card-background-color) }
      button { cursor:pointer } button:hover { border-color:var(--primary-color) }
      button.primary { background:var(--primary-color); color:var(--text-primary-color, white); border:0 }
      button:disabled { opacity:.5; cursor:default } input[type=checkbox] { width:20px; height:20px; accent-color:var(--primary-color) }
      input[type=search] { min-width:min(100%,350px) } .hint { margin:12px 0 }
      .card { background:var(--card-background-color); border-radius:14px; padding:22px; box-shadow:0 2px 10px rgba(0,0,0,.08) }
      .items { max-height:58vh; overflow:auto; border-top:1px solid var(--divider-color) }
      .item { display:flex; justify-content:space-between; align-items:center; gap:16px; padding:8px 4px; border-bottom:1px solid var(--divider-color) }
      .item span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap } .item select { width:175px; flex-shrink:0 }
      .actions { margin:12px 0 } .footer { margin-top:25px } .notice { color:var(--secondary-text-color) }
      #event-form { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:14px } .filters { display:grid; gap:12px; margin-top:15px }
      .filter { display:grid; grid-template-columns:2fr 1fr 100px 100px auto; gap:8px }
      .filter input { min-width:0; width:100% } .field-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px }
      .field-grid label { display:grid; gap:8px } .error { color:var(--error-color); margin:12px 0 }
      @media(max-width:650px) { main { padding:16px 12px 90px } .card { padding:16px } .filter { grid-template-columns:1fr 1fr } .filter input:first-child { grid-column:1 / -1 } .item select { width:125px } }
    </style><main><h1>${t.title}</h1>
      ${this._entries.length ? `<div class="top"><label>${t.connection}
        <select id="entry">${this._entries.map(({entry_id, title}) =>
          `<option value="${escapeHtml(entry_id)}" ${entry_id === this._entryId ? "selected" : ""}>${escapeHtml(title)}</option>`
        ).join("")}</select></label><span class="status">${escapeHtml(this._detail?.status || "")}</span>
        <button id="reload" ${this._busy ? "disabled" : ""}>${t.reload}</button></div>` : `<p>${this._busy ? t.loading : t.empty}</p>`}
      ${this._message ? `<p class="error">${escapeHtml(this._message)}</p>` : ""}
      ${this._detail && this._options ? `<div class="tabs">${["entities", "domains", "services", "events", "filters", "advanced"].map((tab) =>
        `<button data-tab="${tab}" class="${tab === this._tab ? "active" : ""}">${t[tab]}</button>`
      ).join("")}</div><section class="card"><h2>${t[this._tab]}</h2>
        ${this._detail.status !== "connected" ? `<p class="hint">${t.offline}</p>` : ""}
        ${this._tab === "entities" ? `<label class="item"><span>${t.all}</span><input id="import-all" type="checkbox" ${this._options.import_all ? "checked" : ""}></label><p class="hint">${t.allHint}</p>` : ""}
        ${["entities", "domains", "services"].includes(this._tab) ? this._choiceList(this._tab)
          : this._tab === "events" ? this._events() : this._tab === "filters" ? this._filters() : this._advanced()}
      </section><div class="footer"><button id="save" class="primary" ${this._busy || !this._dirty ? "disabled" : ""}>${this._busy ? t.saving : t.save}</button>
        <span class="notice">${this._dirty ? t.unsaved : ""}</span></div>` : ""}
    </main>`;
    this._bind();
    if (focus) { const input = this.shadowRoot.querySelector("#search"); input?.focus(); input?.setSelectionRange(position, position); }
  }

  _bind() {
    const root = this.shadowRoot;
    root.querySelector("#entry")?.addEventListener("change", (event) => {
      const next = event.target.value;
      if (this._dirty && !window.confirm(this._t.switchWarning)) { this._draw(); return; }
      this._load(next);
    });
    root.querySelector("#reload")?.addEventListener("click", () => this._load(this._entryId));
    root.querySelector("#save")?.addEventListener("click", () => this._save());
    root.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => {
      this._tab = button.dataset.tab; this._search = ""; this._draw();
    }));
    root.querySelector("#search")?.addEventListener("input", (event) => { this._search = event.target.value; this._draw(); });
    root.querySelector("#import-all")?.addEventListener("change", (event) => {
      this._options.import_all = event.target.checked; this._markDirty(); this._draw();
    });
    root.querySelectorAll("[data-choice]").forEach((control) => control.addEventListener("change", () => {
      if (control.dataset.choice === "service") {
        const value = control.dataset.value;
        this._options.services = this._options.services.filter((item) => item !== value);
        if (control.checked) this._options.services.push(value);
        this._markDirty(); this._draw();
      } else this._setChoice(control.dataset.choice, control.dataset.value, control.value);
    }));
    root.querySelectorAll("[data-bulk]").forEach((button) => button.addEventListener("click", () => {
      const kind = this._tab;
      const matches = this._detail[kind].filter((item) => item.toLowerCase().includes(this._search.toLowerCase()));
      const keys = kind === "entities" ? ["include_entities", "exclude_entities"] : ["include_domains", "exclude_domains"];
      const set = new Set(matches);
      for (const key of keys) this._options[key] = this._options[key].filter((item) => !set.has(item));
      if (button.dataset.bulk !== "none") this._options[keys[button.dataset.bulk === "include" ? 0 : 1]].push(...matches);
      this._markDirty(); this._draw();
    }));
    root.querySelector("#event-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const value = event.target.elements.namedItem("event").value.trim();
      if (value && !this._options.subscribe_events.includes(value)) {
        this._options.subscribe_events.push(value); this._markDirty(); this._draw();
      }
    });
    root.querySelectorAll("[data-remove-event]").forEach((button) => button.addEventListener("click", () => {
      this._options.subscribe_events = this._options.subscribe_events.filter((item) => item !== button.dataset.removeEvent);
      this._markDirty(); this._draw();
    }));
    root.querySelector("#add-filter")?.addEventListener("click", () => {
      this._options.filter.push({}); this._markDirty(); this._draw();
    });
    root.querySelectorAll("[data-remove-filter]").forEach((button) => button.addEventListener("click", () => {
      this._options.filter.splice(Number(button.dataset.removeFilter), 1); this._markDirty(); this._draw();
    }));
    root.querySelectorAll("[data-filter] input").forEach((input) => input.addEventListener("input", () => {
      const filter = this._options.filter[Number(input.closest("[data-filter]").dataset.filter)];
      if (input.value === "") delete filter[input.dataset.field];
      else filter[input.dataset.field] = input.value;
      this._markDirty();
      root.querySelector("#save")?.removeAttribute("disabled");
    }));
    root.querySelectorAll("[data-option]").forEach((input) => input.addEventListener("input", () => {
      this._options[input.dataset.option] = input.value; this._markDirty();
      root.querySelector("#save")?.removeAttribute("disabled");
    }));
    root.querySelectorAll("[data-component]").forEach((input) => input.addEventListener("change", () => {
      const value = input.dataset.component;
      this._options.load_components = this._options.load_components.filter((item) => item !== value);
      if (input.checked) this._options.load_components.push(value);
      this._markDirty(); this._draw();
    }));
  }

  async _save() {
    if (this._busy || !this._dirty) return;
    this._busy = true; this._draw();
    try {
      const result = await this._hass.callWS({
        type: `${TYPE}/save`, entry_id: this._entryId,
        revision: this._detail.revision, options: this._options,
      });
      this._detail.revision = result.revision;
      this._dirty = false;
      this._message = this._t.saved;
    } catch (error) {
      this._message = error.code === "conflict" ? this._t.conflict : error.message || String(error);
    } finally {
      this._busy = false; this._draw();
    }
  }
}

customElements.define("remote-homeassistant-panel", RemoteHomeAssistantPanel);

// Saved judgments: folders and notes, kept in this browser's localStorage.
// Every read and write goes through this object, so it can later be swapped for
// calls to a server once the site has user accounts.

const SavedStore = (() => {
  const KEY = "law_help.saved.v1";

  function empty() { return { version: 1, folders: [], items: {} }; }

  function load() {
    try {
      const data = JSON.parse(localStorage.getItem(KEY));
      if (data && data.version === 1 && Array.isArray(data.folders) && data.items) return data;
    } catch (_) {}
    return empty();
  }

  function save(data) {
    try { localStorage.setItem(KEY, JSON.stringify(data)); } catch (_) {
      alert("Could not save: this browser is blocking storage for the site.");
    }
  }

  function update(fn) { const data = load(); fn(data); save(data); return data; }

  return {
    all: load,
    get: (id) => load().items[id] || null,

    // Save or update a judgment. `info` holds the fields shown in lists (title, case, bench, date).
    put(id, info, changes) {
      return update((d) => {
        const item = d.items[id] || { id: String(id), folders: [], note: "", saved_at: new Date().toISOString() };
        d.items[id] = { ...item, ...info, ...changes, id: String(id) };
        for (const f of d.items[id].folders) if (!d.folders.includes(f)) d.folders.push(f);
      }).items[id];
    },
    remove(id) { update((d) => { delete d.items[id]; }); },

    addFolder(name) { update((d) => { if (!d.folders.includes(name)) d.folders.push(name); }); },
    renameFolder(from, to) {
      update((d) => {
        d.folders = [...new Set(d.folders.map((f) => (f === from ? to : f)))];
        for (const it of Object.values(d.items)) it.folders = [...new Set(it.folders.map((f) => (f === from ? to : f)))];
      });
    },
    // Deleting a folder keeps its judgments; they stay under "All saved".
    deleteFolder(name) {
      update((d) => {
        d.folders = d.folders.filter((f) => f !== name);
        for (const it of Object.values(d.items)) it.folders = it.folders.filter((f) => f !== name);
      });
    },

    exportJSON() { return JSON.stringify(load(), null, 2); },
    // Merges a file from exportJSON into what is already saved; notes on the same judgment are joined.
    importJSON(text) {
      const incoming = JSON.parse(text);
      if (!incoming || incoming.version !== 1 || !incoming.items) throw new Error("This is not a law_help saved file.");
      let count = 0;
      update((d) => {
        for (const f of incoming.folders || []) if (!d.folders.includes(f)) d.folders.push(f);
        for (const [id, it] of Object.entries(incoming.items)) {
          const mine = d.items[id];
          const note = mine?.note && it.note && mine.note !== it.note ? `${mine.note}\n\n${it.note}` : (it.note || mine?.note || "");
          d.items[id] = { ...mine, ...it, note, folders: [...new Set([...(mine?.folders || []), ...(it.folders || [])])] };
          for (const f of d.items[id].folders) if (!d.folders.includes(f)) d.folders.push(f);
          count++;
        }
      });
      return count;
    },
  };
})();

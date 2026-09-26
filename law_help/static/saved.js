// Saved judgments page: /saved?folder=Name

function currentFolder() { return new URLSearchParams(location.search).get("folder") || ""; }

function go(folder) {
  history.pushState(null, "", folder ? `?${new URLSearchParams({ folder })}` : location.pathname);
  render();
}

function folderLink(name, label, count) {
  const a = el("a", { href: name ? `?${new URLSearchParams({ folder: name })}` : "/saved" }, label, el("span", { class: "count" }, ` ${count}`));
  a.addEventListener("click", (e) => { e.preventDefault(); go(name); });
  return el("li", { class: name === currentFolder() ? "active" : "" }, a);
}

function savedItem(it, folder) {
  const note = el("textarea", { rows: 2, placeholder: "Notes" });
  note.value = it.note || "";
  note.addEventListener("change", () => SavedStore.put(it.id, {}, { note: note.value }));
  const remove = el("button", { type: "button", class: "link" }, folder ? "Remove from folder" : "Delete");
  remove.addEventListener("click", () => {
    if (folder) SavedStore.put(it.id, {}, { folders: it.folders.filter((f) => f !== folder) });
    else if (confirm("Delete this saved judgment and its notes?")) SavedStore.remove(it.id);
    render();
  });
  return el("li", {},
    el("a", { class: "title", href: `/judgment?id=${it.id}` }, it.title || `Judgment ${it.id}`),
    el("div", { class: "meta" },
      [it.case, titleCase(it.bench), it.date && `Decided ${formatDate(it.date)}`].filter(Boolean).join(" · "),
      it.folders.map((f) => el("span", { class: "chip" }, f)),
    ),
    note,
    el("div", { class: "actions" }, remove),
  );
}

function render() {
  const data = SavedStore.all();
  const folder = data.folders.includes(currentFolder()) ? currentFolder() : "";
  const items = Object.values(data.items)
    .filter((it) => !folder || it.folders.includes(folder))
    .sort((a, b) => (b.saved_at || "").localeCompare(a.saved_at || ""));

  document.getElementById("folders").replaceChildren(
    folderLink("", "All saved", Object.keys(data.items).length),
    ...data.folders.map((f) => folderLink(f, f, Object.values(data.items).filter((it) => it.folders.includes(f)).length)),
  );
  document.getElementById("folder-title").textContent = folder || "All saved";
  document.title = `${folder || "Saved judgments"} · law_help`;

  const actions = [];
  if (folder) {
    const rename = el("button", { type: "button", class: "link" }, "Rename");
    rename.addEventListener("click", () => {
      const to = prompt("Rename folder", folder)?.trim();
      if (to && to !== folder) { SavedStore.renameFolder(folder, to); go(to); }
    });
    const del = el("button", { type: "button", class: "link" }, "Delete folder");
    del.addEventListener("click", () => {
      if (confirm(`Delete the folder "${folder}"? The judgments stay under All saved.`)) { SavedStore.deleteFolder(folder); go(""); }
    });
    actions.push(rename, " · ", del);
  }
  document.getElementById("folder-actions").replaceChildren(...actions);

  document.getElementById("saved").replaceChildren(...(items.length
    ? items.map((it) => savedItem(it, folder))
    : [el("li", { class: "empty" }, folder
      ? "Nothing in this folder yet. Open a judgment and pick this folder under Save."
      : "Nothing saved yet. Open a judgment and press Save.")]));
}

document.getElementById("new-folder").addEventListener("submit", (e) => {
  e.preventDefault();
  const name = e.target.elements.name.value.trim();
  if (!name) return;
  SavedStore.addFolder(name);
  e.target.reset();
  go(name);
});

document.getElementById("export").addEventListener("click", () => {
  const blob = new Blob([SavedStore.exportJSON()], { type: "application/json" });
  const a = el("a", { href: URL.createObjectURL(blob), download: `law_help-saved-${new Date().toISOString().slice(0, 10)}.json` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});

document.getElementById("import").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const n = SavedStore.importJSON(await file.text());
    alert(`Loaded ${n} saved ${n === 1 ? "judgment" : "judgments"}.`);
  } catch (err) {
    alert(`Could not load that file: ${err.message}`);
  }
  e.target.value = "";
  render();
});

window.addEventListener("popstate", render);
window.addEventListener("storage", render);
render();

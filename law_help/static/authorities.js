// List of authorities for a saved folder: copy it, or download it as .txt or as a Word (.doc) file
// laid out the way courts expect (Sr. No., Case, Citation, Relevant para).

const Authorities = (() => {
  const SUPREME = "Supreme Court of India";

  // Supreme Court first, then oldest to newest; or the order the judgments were saved in.
  function order(entries, keepSaved) {
    const saved = (a, b) => (a.saved_at || "").localeCompare(b.saved_at || "");
    if (keepSaved) return [...entries].sort(saved);
    return [...entries].sort((a, b) =>
      (b.court === SUPREME) - (a.court === SUPREME)
      || (a.decision_date ? 0 : 1) - (b.decision_date ? 0 : 1)
      || (a.decision_date || "").localeCompare(b.decision_date || "")
      || saved(a, b));
  }

  // "para 12", "paras 12-15", "paragraphs 4 and 9", "¶ 7" in the user's note.
  function paras(note) {
    const m = (note || "").match(/(?:\bparas?\b|\bparagraphs?\b|¶)\.?\s*(\d+[\d\s,&–-]*(?:\s*(?:and|to)\s*\d+)*)/i);
    return m ? m[1].trim().replace(/[\s,&-]+$/, "") : "";
  }

  // What follows the case name in the citation line: "2024 INSC 735 : ... [Criminal Appeal ...]".
  function citationPart(e) {
    return e.citation.startsWith(e.case_name) ? e.citation.slice(e.case_name.length).replace(/^,\s*/, "").trim() : e.citation;
  }

  function text(folder, entries) {
    const lines = ["LIST OF AUTHORITIES RELIED UPON", folder, ""];
    entries.forEach((e, i) => {
      lines.push(`${i + 1}. ${e.citation}`);
      const p = paras(e.note);
      if (p) lines.push(`   Relevant para: ${p}`);
      for (const line of (e.note || "").trim().split("\n").filter((l) => l.trim())) lines.push(`   ${line.trim()}`);
      lines.push("");
    });
    return lines.join("\n").trimEnd() + "\n";
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // Word opens HTML saved with a .doc name; the Office namespaces keep it in print layout.
  function doc(folder, entries) {
    const rows = entries.map((e, i) => `
<tr><td class="n">${i + 1}.</td>
<td><b>${esc(e.case_name || e.citation)}</b>${e.note?.trim() ? `<p class="note">${esc(e.note.trim()).replace(/\n/g, "<br>")}</p>` : ""}</td>
<td>${esc(citationPart(e))}</td>
<td class="n">${esc(paras(e.note))}</td></tr>`).join("");
    return `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">
<head><meta charset="utf-8"><title>List of authorities</title>
<!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View><w:Zoom>100</w:Zoom></w:WordDocument></xml><![endif]-->
<style>
@page { size: 21cm 29.7cm; margin: 2.5cm 2.5cm 2.5cm 4cm; }
body { font-family: "Times New Roman", serif; font-size: 14pt; line-height: 1.5; }
h1 { font-size: 14pt; text-align: center; text-decoration: underline; margin: 0 0 6pt; }
h2 { font-size: 12pt; text-align: center; font-weight: normal; margin: 0 0 12pt; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #000; padding: 4pt 6pt; vertical-align: top; font-size: 12pt; }
th { font-weight: bold; text-align: center; }
td.n { text-align: center; }
p.note { font-size: 11pt; font-style: italic; margin: 4pt 0 0; }
</style></head>
<body>
<h1>LIST OF AUTHORITIES RELIED UPON</h1>
<h2>${esc(folder)}</h2>
<table>
<tr><th style="width:8%">Sr. No.</th><th style="width:37%">Case</th><th style="width:40%">Citation</th><th style="width:15%">Relevant para</th></tr>${rows}
</table>
</body></html>`;
  }

  return { order, paras, text, doc };
})();

// Clipboard API needs https or localhost; the LAN address is plain http, so fall back like judgment.js.
async function copyAuthorities(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const area = el("textarea", { style: "position:fixed;opacity:0" }, text);
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  button.textContent = "Copied";
  setTimeout(() => { button.textContent = "Copy"; }, 1500);
}

function downloadFile(name, type, content) {
  const url = URL.createObjectURL(new Blob(["﻿", content], { type }));
  const a = el("a", { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// The panel under the folder heading. `items` are SavedStore items in this folder.
async function authoritiesPanel(folder, items) {
  const box = el("section", { class: "authorities" }, el("p", { class: "hint" }, "Loading citations…"));
  let found;
  try {
    const got = items.length ? await getJSON(`/judgments/citations?ids=${items.map((it) => it.id).join(",")}`) : [];
    const byId = Object.fromEntries(got.map((g) => [String(g.id), g]));
    // A judgment missing from the database still gets a line, from what was saved with it.
    found = items.map((it) => ({ ...it, ...(byId[it.id] || { citation: it.title || `Judgment ${it.id}`, case_name: "" }) }));
  } catch (err) {
    box.replaceChildren(el("p", { class: "error" }, `Could not load citations: ${err.message}`));
    return box;
  }
  if (!found.length) {
    box.replaceChildren(el("p", { class: "hint" }, "Nothing in this folder to list."));
    return box;
  }

  const keep = el("input", { type: "checkbox" });
  const preview = el("pre", { class: "authorities-text" });
  // Notes are read at the time of use, so edits made on the page are included.
  const list = () => Authorities.order(found.map((e) => ({ ...e, note: SavedStore.get(e.id)?.note ?? e.note })), keep.checked);
  const show = () => { preview.textContent = Authorities.text(folder, list()); };
  keep.addEventListener("change", show);
  box.refresh = show;
  show();

  const slug = folder.replace(/[^\w-]+/g, "-").replace(/^-|-$/g, "") || "folder";
  const copy = el("button", { type: "button" }, "Copy");
  copy.addEventListener("click", () => copyAuthorities(Authorities.text(folder, list()), copy));
  const txt = el("button", { type: "button" }, "Download .txt");
  txt.addEventListener("click", () => downloadFile(`authorities-${slug}.txt`, "text/plain;charset=utf-8", Authorities.text(folder, list())));
  const word = el("button", { type: "button" }, "Download .doc");
  word.addEventListener("click", () => downloadFile(`authorities-${slug}.doc`, "application/msword", Authorities.doc(folder, list())));

  box.replaceChildren(
    el("div", { class: "actions" }, copy, txt, word,
      el("label", { class: "keep-order" }, keep, " Keep saved order")),
    el("p", { class: "hint" }, "Supreme Court first, then by date. Write \"para 12\" in a note to fill Relevant para."),
    preview,
  );
  return box;
}

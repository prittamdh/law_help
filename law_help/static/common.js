// Helpers shared by the search and judgment pages.

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === "") continue;
    node.append(c instanceof Node ? c : String(c));
  }
  return node;
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch (_) {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

function titleCase(s) {
  return s ? s.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()) : "";
}

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, s.lastIndexOf(" ", n) > 0 ? s.lastIndexOf(" ", n) : n) + "…" : s;
}

function caseNumber(j) {
  // Supreme Court case types are words: "Criminal Appeal No. 1031 of 2015"
  if (j.case_type?.includes(" ") && j.case_number != null) {
    return `${titleCase(j.case_type)} No. ${j.case_number} of ${j.case_year}`;
  }
  return [j.case_type, j.case_number, j.case_year].filter((x) => x != null).join("/");
}

// Good law check (law_help.goodlaw): what a later judgment of this court did to this one.
const GOOD_LAW = {
  set_aside: "Set aside on appeal",
  partly_set_aside: "Partly set aside on appeal",
  recalled: "Recalled on review",
  overruled: "Overruled",
};

function goodLawChip(kind) {
  return kind ? el("span", { class: `chip bad-law${kind === "partly_set_aside" ? " partly" : ""}` }, GOOD_LAW[kind]) : "";
}

// Landmark (law_help.landmark): cited by many later judgments.
function landmarkChip(j) {
  return j.landmark ? el("span", { class: "chip landmark", title: `Cited by ${j.cited_by_count} later judgments` },
    `Landmark · cited ${j.cited_by_count}×`) : "";
}

function courtLabel(j) {
  return j.court === "Supreme Court of India" ? "Supreme Court" : titleCase(j.bench);
}

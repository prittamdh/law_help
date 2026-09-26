// Search page: the URL query string holds the search, so results can be shared and Back works.

const form = document.getElementById("search");
const FIELDS = ["q", "bench", "decided_from", "decided_to", "judge", "act", "section", "case_type", "disposal"];
const PAGE_SIZE = 20;

function stateFromURL() {
  const p = new URLSearchParams(location.search);
  const state = { page: Math.max(1, parseInt(p.get("page"), 10) || 1) };
  for (const f of FIELDS) state[f] = p.get(f) || "";
  return state;
}

function fillForm(state) {
  for (const f of FIELDS) form.elements[f].value = state[f];
}

function stateFromForm() {
  const state = { page: 1 };
  for (const f of FIELDS) state[f] = form.elements[f].value.trim();
  return state;
}

function navigate(state) {
  const p = new URLSearchParams();
  for (const f of FIELDS) if (state[f]) p.set(f, state[f]);
  if (state.page > 1) p.set("page", state.page);
  history.pushState(null, "", p.toString() ? `?${p}` : location.pathname);
  run(state);
}

function apiParams(state) {
  const p = new URLSearchParams({ page: state.page, page_size: PAGE_SIZE });
  for (const f of FIELDS) if (state[f]) p.set(f, state[f]);
  // A section on its own means nothing to the API, so drop it until an act is chosen.
  if (!state.act) p.delete("section");
  return p;
}

function resultItem(j) {
  const names = j.bench_judges?.length ? j.bench_judges : j.judges;
  const judges = names?.length ? names.map(titleCase).join(", ") : "";
  return el("li", {},
    el("a", { class: "title", href: `/judgment?id=${j.id}` }, j.title),
    el("div", { class: "meta" },
      el("span", { class: "chip" }, titleCase(j.bench)),
      j.disposal_nature && el("span", { class: "chip outcome" }, titleCase(j.disposal_nature)),
      j.decision_date ? `Decided ${formatDate(j.decision_date)}` : "Decision date unknown",
      judges && ` · ${judges}`,
      el("a", { href: j.pdf_url, target: "_blank", rel: "noopener" }, "PDF"),
    ),
    // The model summary when there is one, else the extractive one.
    (j.ai_summary?.summary || j.summary) &&
      el("p", { class: "snippet" }, truncate(j.ai_summary?.summary || j.summary, 260)),
  );
}

let current = 0;
async function run(state) {
  const summary = document.getElementById("summary");
  const list = document.getElementById("results");
  const pager = document.getElementById("pager");
  const token = ++current;
  summary.textContent = "Searching…";

  let body;
  try {
    body = await getJSON(`/judgments?${apiParams(state)}`);
  } catch (err) {
    if (token !== current) return;
    summary.textContent = "";
    list.replaceChildren(el("li", { class: "error" }, `Search failed: ${err.message}`));
    pager.hidden = true;
    return;
  }
  if (token !== current) return;

  const pages = Math.max(1, Math.ceil(body.total / PAGE_SIZE));
  const from = body.total ? (body.page - 1) * PAGE_SIZE + 1 : 0;
  const to = Math.min(body.total, body.page * PAGE_SIZE);
  summary.textContent = body.total
    ? `${from.toLocaleString()}–${to.toLocaleString()} of ${body.total.toLocaleString()} judgments`
    : "";
  list.replaceChildren(...(body.results.length
    ? body.results.map(resultItem)
    : [el("li", { class: "empty" }, "No judgments match. Try fewer words or clear a filter.")]));

  pager.hidden = pages <= 1;
  document.getElementById("page-info").textContent = `Page ${body.page} of ${pages.toLocaleString()}`;
  document.getElementById("prev").disabled = body.page <= 1;
  document.getElementById("next").disabled = body.page >= pages;
}

async function loadFacets() {
  let stats;
  try { stats = await getJSON("/stats"); } catch (_) { return; }
  document.getElementById("corpus").textContent =
    `${stats.total.toLocaleString()} Rajasthan High Court judgments` +
    (stats.total ? `, ${stats.with_text.toLocaleString()} with full text` : "");
  document.getElementById("judges").replaceChildren(
    ...stats.top_judges.map((r) => el("option", { value: r.judge })));
  document.getElementById("acts").replaceChildren(
    ...(stats.top_acts || []).map((r) => el("option", { value: r.act })));
  document.getElementById("case-types").replaceChildren(
    ...stats.top_case_types.map((r) => el("option", { value: r.case_type })));
  const disposal = form.elements.disposal;
  const selected = stateFromURL().disposal;
  for (const r of stats.by_disposal) {
    if (!r.disposal_nature) continue;
    disposal.append(el("option", { value: r.disposal_nature }, titleCase(r.disposal_nature)));
  }
  disposal.value = selected;
}

form.addEventListener("submit", (e) => { e.preventDefault(); navigate(stateFromForm()); });
form.elements.bench.addEventListener("change", () => navigate(stateFromForm()));
form.elements.disposal.addEventListener("change", () => navigate(stateFromForm()));
document.getElementById("clear").addEventListener("click", () => {
  navigate({ ...Object.fromEntries(FIELDS.map((f) => [f, ""])), q: form.elements.q.value.trim(), page: 1 });
  fillForm(stateFromURL());
});
document.getElementById("prev").addEventListener("click", () => {
  const s = stateFromURL(); navigate({ ...s, page: s.page - 1 }); scrollTo(0, 0);
});
document.getElementById("next").addEventListener("click", () => {
  const s = stateFromURL(); navigate({ ...s, page: s.page + 1 }); scrollTo(0, 0);
});
window.addEventListener("popstate", () => { const s = stateFromURL(); fillForm(s); run(s); });

const initial = stateFromURL();
fillForm(initial);
loadFacets();
run(initial);

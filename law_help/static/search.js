// Search page: the URL query string holds the search, so results can be shared and Back works.

const form = document.getElementById("search");
const FIELDS = ["q", "court", "bench", "decided_from", "decided_to", "judge", "act", "section", "case_type", "disposal", "landmark", "topic"];
const PAGE_SIZE = 20;

function stateFromURL() {
  const p = new URLSearchParams(location.search);
  const state = { page: Math.max(1, parseInt(p.get("page"), 10) || 1) };
  for (const f of FIELDS) state[f] = p.get(f) || "";
  state.mentions = p.get("mentions") === "1";
  if (state.court === "supreme") state.bench = "";  // an old link may carry both
  return state;
}

function fillForm(state) {
  for (const f of FIELDS) form.elements[f].value = state[f];
  showCourtFilters();
  countFilters(state);
}

function countFilters(state) {
  const active = FIELDS.filter((f) => f !== "q" && state[f]).length;
  document.getElementById("filters-toggle").textContent = active ? `Filters (${active})` : "Filters";
}

// On phones the filters start folded (style.css) so the results show first.
const filtersToggle = document.getElementById("filters-toggle");
filtersToggle.addEventListener("click", () => {
  const open = document.getElementById("filters").classList.toggle("open");
  filtersToggle.setAttribute("aria-expanded", open);
});

// Jaipur and Jodhpur are High Court benches: with the Supreme Court picked, hide the bench
// filter and show Supreme Court case types as the example.
function showCourtFilters() {
  const supreme = form.elements.court.value === "supreme";
  document.getElementById("bench-filter").hidden = supreme;
  if (supreme) form.elements.bench.value = "";
  form.elements.case_type.placeholder = supreme ? "e.g. CRIMINAL APPEAL" : "e.g. CW, CRLMB";
}

function stateFromForm() {
  showCourtFilters();
  const state = { page: 1 };
  for (const f of FIELDS) state[f] = form.elements[f].value.trim();
  return state;
}

function navigate(state) {
  const p = new URLSearchParams();
  for (const f of FIELDS) if (state[f]) p.set(f, state[f]);
  if (state.mentions) p.set("mentions", "1");
  if (state.page > 1) p.set("page", state.page);
  history.pushState(null, "", p.toString() ? `?${p}` : location.pathname);
  countFilters(state);
  run(state);
}

function apiParams(state) {
  const p = new URLSearchParams({ page: state.page, page_size: PAGE_SIZE });
  for (const f of FIELDS) if (state[f]) p.set(f, state[f]);
  if (state.mentions) p.set("mentions", "true");
  // A section on its own means nothing to the API, so drop it until an act is chosen.
  if (!state.act) p.delete("section");
  return p;
}

// "Follow (RSS)": the same search as a feed of the newest matches, so a feed reader shows new judgments.
function updateFeedLinks(state) {
  const p = apiParams(state);
  p.delete("page"); p.delete("page_size");
  const href = `${location.origin}/feed${p.toString() ? `?${p}` : ""}`;
  document.getElementById("follow-rss").href = href;
  document.getElementById("feed-alternate").href = href;
}

let current = 0;
async function run(state) {
  const summary = document.getElementById("summary");
  const list = document.getElementById("results");
  const pager = document.getElementById("pager");
  const token = ++current;
  summary.textContent = "Searching…";
  updateFeedLinks(state);

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

  // A word search ranks only the most recent matches (body.ranked); the pages cover those.
  const shown = body.ranked ?? body.total;
  const pages = Math.max(1, Math.ceil(shown / PAGE_SIZE));
  const from = shown ? (body.page - 1) * PAGE_SIZE + 1 : 0;
  const to = Math.min(shown, body.page * PAGE_SIZE);
  const total = `${body.total.toLocaleString()}${body.total_capped ? "+" : ""}`;
  const kinds = (body.kinds || []).join(" and ");
  summary.textContent = !body.total ? ""
    : kinds && !shown ? `No ${kinds} cases match`
    : kinds
      ? `${from.toLocaleString()}–${to.toLocaleString()} of ${shown.toLocaleString()} ${kinds} cases`
    : shown < body.total
      ? `${from.toLocaleString()}–${to.toLocaleString()} of the ${shown.toLocaleString()} most recent of ${total} judgments`
      : `${from.toLocaleString()}–${to.toLocaleString()} of ${total} judgments`;
  // A search naming a kind of case (contempt, bail) lists only those cases; one click shows every match.
  const withMentions = (on) => {
    const p = new URLSearchParams(location.search);
    p.delete("page");
    if (on) p.set("mentions", "1"); else p.delete("mentions");
    return `?${p}`;
  };
  if (kinds && body.mentioning > shown) {
    summary.append(" · ", el("a", { href: withMentions(true) }, `Show all ${body.mentioning.toLocaleString()} judgments with these words`));
  } else if (state.mentions && state.q) {
    summary.append(" · ", el("a", { href: withMentions(false) }, "Only cases of the kind searched for"));
  }
  // IPC 302 also found BNS 103: say so, since those results don't mention the section searched.
  const eqs = (body.equivalents || []).filter((e) => e.ref);
  if (eqs.length && body.total) {
    summary.append(el("span", { class: "equivalents" }, " · including ",
      eqs.flatMap((e, i) => [i ? ", " : "", el("a", { href: `/acts?${new URLSearchParams({ act: e.act, s: e.number || e.ref })}` },
        `${e.short} s. ${e.ref}`)]),
      eqs[0].direction === "new" ? " (new code)" : " (old code)"));
  }
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
    `${stats.total.toLocaleString()} Supreme Court and Rajasthan High Court judgments` +
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

// Practice-area topics (law_help.topics), without counts so the page stays quick.
async function loadTopics() {
  let list;
  try { list = await getJSON("/api/topics?counts=false"); } catch (_) { return; }
  const select = form.elements.topic;
  for (const t of list) select.append(el("option", { value: t.slug }, t.name));
  select.value = stateFromURL().topic;
}

form.addEventListener("submit", (e) => { e.preventDefault(); navigate(stateFromForm()); });
form.elements.court.addEventListener("change", () => navigate(stateFromForm()));
form.elements.bench.addEventListener("change", () => navigate(stateFromForm()));
form.elements.disposal.addEventListener("change", () => navigate(stateFromForm()));
form.elements.landmark.addEventListener("change", () => navigate(stateFromForm()));
form.elements.topic.addEventListener("change", () => navigate(stateFromForm()));
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
loadTopics();
run(initial);

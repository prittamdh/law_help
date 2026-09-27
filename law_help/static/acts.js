// Bare Acts: /acts (all acts), /acts?act=bns (contents), /acts?act=bns&s=103 (one section),
// /acts?act=bns&s=103&view=judgments (every judgment citing it, old and new code together).

const content = document.getElementById("content");
const params = new URLSearchParams(location.search);

function sectionLabel(act, number) {
  if (/^Order\b/.test(number)) return number;
  return `${act.kind === "article" ? "Art." : "s."} ${number}`;
}

function actHref(slug, number) {
  return `/acts?${new URLSearchParams(number ? { act: slug, s: number } : { act: slug })}`;
}

// "Find a section": pick an act, type a number, go.
function finder(acts, current) {
  const select = el("select", { name: "act", "aria-label": "Act" },
    acts.map((a) => el("option", { value: a.slug, selected: a.slug === current ? "" : null }, a.short)));
  const input = el("input", { name: "s", placeholder: "Section, e.g. 302 or Order VII Rule 11", "aria-label": "Section" });
  const form = el("form", { class: "finder" }, select, input, el("button", { type: "submit" }, "Open"));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const s = input.value.trim().replace(/^(?:section|sec\.?|s\.|article|art\.?)\s*/i, "");
    location.href = actHref(select.value, s || null);
  });
  return form;
}

async function listActs(acts) {
  document.title = "Bare Acts · law_help";
  const card = (a) => el("li", {},
    el("a", { class: "title", href: actHref(a.slug) }, a.act),
    el("div", { class: "meta" }, `${a.sections.toLocaleString()} ${a.kind === "article" ? "articles" : "sections"}`,
      a.replaces ? ` · replaced ${acts.find((x) => x.slug === a.replaces)?.short} from 1 July 2024` : "",
      a.replaced_by ? ` · replaced by ${acts.find((x) => x.slug === a.replaced_by)?.short}` : ""),
  );
  const newCodes = acts.filter((a) => a.replaces);
  const oldCodes = acts.filter((a) => a.replaced_by);
  const rest = acts.filter((a) => !a.replaces && !a.replaced_by);
  content.replaceChildren(el("div", { class: "acts" },
    el("h1", {}, "Bare Acts"),
    el("p", { class: "summary" }, "Open a section to read it, see the same provision in the old or new code, and find the judgments that cite it."),
    finder(acts),
    el("h2", {}, "New criminal laws (from 1 July 2024)"), el("ol", { class: "results" }, newCodes.map(card)),
    el("h2", {}, "Codes they replaced"), el("ol", { class: "results" }, oldCodes.map(card)),
    el("h2", {}, "Other acts"), el("ol", { class: "results" }, rest.map(card)),
  ));
}

async function showAct(slug, acts) {
  const act = await getJSON(`/api/acts/${encodeURIComponent(slug)}`);
  document.title = `${act.act} · law_help`;
  const filter = el("input", { type: "search", placeholder: "Filter by number or words, e.g. bail", "aria-label": "Filter sections" });
  const list = el("div", { class: "toc" });
  let counts = {};
  // "12 judgments" beside each section cited, filled in once the counts arrive.
  const countLink = (s) => counts[s.number]
    ? el("a", { class: "count", href: sectionJudgmentsHref(slug, s.number), title: "Judgments on this section" },
      `${counts[s.number].toLocaleString()} ${counts[s.number] > 1 ? "judgments" : "judgment"}`) : "";
  const render = () => {
    const q = filter.value.trim().toLowerCase();
    const groups = [];
    for (const s of act.sections) {
      if (q && !s.number.toLowerCase().startsWith(q) && !s.title.toLowerCase().includes(q)) continue;
      if (!groups.length || groups[groups.length - 1].chapter !== s.chapter) groups.push({ chapter: s.chapter, rows: [] });
      groups[groups.length - 1].rows.push(s);
    }
    list.replaceChildren(...(groups.length ? groups.flatMap((g) => [
      g.chapter ? el("h3", {}, g.chapter) : "",
      el("ul", { class: "plain" }, g.rows.map((s) => el("li", {},
        el("a", { href: actHref(slug, s.number) }, sectionLabel(act, s.number)), " ", s.title, countLink(s)))),
    ]) : [el("p", { class: "summary" }, "No section matches.")]));
  };
  filter.addEventListener("input", render);
  render();
  getJSON(`/api/acts/${encodeURIComponent(slug)}/judgment-counts`).then((c) => { counts = c; render(); }).catch(() => {});
  content.replaceChildren(el("article", { class: "judgment act" },
    el("p", { class: "crumbs" }, el("a", { href: "/acts" }, "Bare Acts")),
    el("h1", {}, act.act),
    el("p", { class: "summary" }, `Text: ${act.source}.`,
      act.replaced_by ? [" Replaced by the ", el("a", { href: actHref(act.replaced_by) },
        acts.find((a) => a.slug === act.replaced_by)?.act), " from 1 July 2024; it still governs offences and cases from before."] : ""),
    finder(acts, slug), filter, list,
  ));
}

function equivalentsBlock(sec) {
  const eqs = sec.equivalents || [];
  if (!eqs.length) {
    const newer = { ipc: "BNS", crpc: "BNSS", evidence: "BSA" }[sec.act.slug];
    const older = { bns: "IPC", bnss: "CrPC", bsa: "Evidence Act" }[sec.act.slug];
    return newer || older
      ? el("p", { class: "equiv" }, `No corresponding section in the ${newer || older} was found.`) : "";
  }
  const dir = eqs[0].direction === "new" ? "Now" : "Earlier";
  const items = eqs.map((e) => e.ref === null
    ? el("li", {}, `Dropped: the ${e.short} has no corresponding section.`)
    : el("li", {},
      e.number ? el("a", { href: actHref(e.act, e.number) }, `${e.short} ${e.ref.startsWith("Order") ? e.ref : "s. " + e.ref}`)
               : `${e.short} s. ${e.ref}`,
      e.title ? ` ${e.title}` : ""));
  const bySource = eqs.every((e) => e.source === "ncrb")
    ? "From NCRB's corresponding section table."
    : "Matched by comparing the wording of the two codes; check before relying on it.";
  return el("section", { class: "equiv" },
    el("h3", {}, dir === "Now" ? "In the new code" : "In the old code"),
    el("ul", { class: "plain" }, items),
    el("p", { class: "note" }, bySource));
}

// "Judgments on this section (N)": the most cited few, old and new code together, and a link to all.
function citingList(sec, cites) {
  const other = (cites.equivalents || []).filter((e) => e.ref);
  const withOther = other.length ? ` or ${other[0].short} s. ${other.map((e) => e.ref).join(", ")}` : "";
  const all = sectionJudgmentsHref(sec.act.slug, sec.number);
  if (!cites.total) return [el("h2", {}, "Judgments on this section"),
    el("p", { class: "summary" }, `No judgment in the collection cites this section${withOther} by its number.`)];
  return [
    el("h2", {}, el("a", { href: all }, `Judgments on this section (${cites.total.toLocaleString()})`)),
    withOther ? el("p", { class: "summary" }, `Judgments citing it${withOther}, most cited first.`) : "",
    el("ol", { class: "results" }, cites.results.map(resultItem)),
    cites.total > cites.results.length
      ? el("p", {}, el("a", { href: all }, `See all ${cites.total.toLocaleString()} judgments`)) : "",
  ];
}

async function showSection(slug, number) {
  let sec, cites;
  const path = `/api/acts/${encodeURIComponent(slug)}/sections/${encodeURIComponent(number)}`;
  try {
    [sec, cites] = await Promise.all([getJSON(`${path}?limit=0`), getJSON(`${path}/judgments?page_size=5`)]);
  } catch (err) {
    content.replaceChildren(el("p", { class: "error" }, `Could not open ${number}: ${err.message}`),
      el("p", {}, el("a", { href: actHref(slug) }, "See all sections")));
    return;
  }
  const act = sec.act;
  document.title = `${act.short} ${sectionLabel(act, sec.number)} ${sec.title} · law_help`;
  const nav = (s, back) => s
    ? el("a", { href: actHref(slug, s.number) }, back ? `‹ ${sectionLabel(act, s.number)}` : `${sectionLabel(act, s.number)} ›`)
    : el("span", {});
  content.replaceChildren(el("article", { class: "judgment act" },
    el("p", { class: "crumbs" }, el("a", { href: "/acts" }, "Bare Acts"), " › ", el("a", { href: actHref(slug) }, act.act),
      sec.chapter ? ` › ${sec.chapter}` : ""),
    el("h1", {}, `${act.short} ${sectionLabel(act, sec.number)}. ${sec.title}`),
    equivalentsBlock(sec),
    el("div", { class: "text act-text" }, sec.text || "(No text.)"),
    el("p", { class: "note" }, `Text: ${sec.source}.`),
    el("div", { class: "pager" }, nav(sec.previous, true), nav(sec.next, false)),
    citingList(sec, cites),
  ));
}

// /acts?act=ipc&s=420&view=judgments: every judgment citing IPC 420 or BNS 318(4), most cited first.
async function showSectionJudgments(slug, number) {
  const court = params.get("court") || "";
  const page = Math.max(1, parseInt(params.get("page"), 10) || 1);
  const PAGE_SIZE = 20;
  const href = (changes) => {
    const p = new URLSearchParams({ act: slug, s: number, view: "judgments" });
    const next = { court, page, ...changes };
    if (next.court) p.set("court", next.court);
    if (next.page > 1) p.set("page", next.page);
    return `/acts?${p}`;
  };
  let body;
  try {
    body = await getJSON(`/api/acts/${encodeURIComponent(slug)}/sections/${encodeURIComponent(number)}/judgments?${
      new URLSearchParams({ page, page_size: PAGE_SIZE, ...(court ? { court } : {}) })}`);
  } catch (err) {
    content.replaceChildren(el("p", { class: "error" }, `Could not open ${number}: ${err.message}`),
      el("p", {}, el("a", { href: actHref(slug) }, "See all sections")));
    return;
  }
  const act = body.act;
  const label = `${act.short} ${sectionLabel(act, body.number)}`;
  document.title = `Judgments on ${label} · law_help`;
  const eqs = body.equivalents.filter((e) => e.ref);
  const select = el("select", { "aria-label": "Court" },
    [["", "All courts"], ["supreme", "Supreme Court"], ["rajasthan", "Rajasthan High Court"]]
      .map(([v, t]) => el("option", { value: v, selected: v === court ? "" : null }, t)));
  select.addEventListener("change", () => { location.href = href({ court: select.value, page: 1 }); });
  const pages = Math.max(1, Math.ceil(body.total / PAGE_SIZE));
  const from = (page - 1) * PAGE_SIZE + 1;
  const to = Math.min(body.total, page * PAGE_SIZE);
  content.replaceChildren(el("article", { class: "judgment act section-judgments" },
    el("p", { class: "crumbs" }, el("a", { href: "/acts" }, "Bare Acts"), " › ", el("a", { href: actHref(slug) }, act.act),
      " › ", el("a", { href: actHref(slug, body.number) }, label)),
    el("h1", {}, `Judgments on ${label}. ${body.title}`),
    eqs.length ? el("p", { class: "summary" }, "Including ",
      eqs.flatMap((e, i) => [i ? ", " : "", e.number ? el("a", { href: actHref(e.act, e.number) }, `${e.short} s. ${e.ref}`)
        : `${e.short} s. ${e.ref}`]),
      eqs[0].direction === "new" ? " in the new code." : " in the old code.") : "",
    el("div", { class: "section-filter" }, select,
      el("a", { href: actHref(slug, body.number) }, "Read the section"),
      el("a", { href: `/?${new URLSearchParams({ act: act.act, section: body.number, ...(court ? { court } : {}) })}` },
        "Search within these")),
    el("p", { class: "summary" }, body.total
      ? `${from.toLocaleString()}–${to.toLocaleString()} of ${body.total.toLocaleString()} judgments, most cited first`
      : ""),
    el("ol", { class: "results" }, body.results.length ? body.results.map(resultItem)
      : el("li", { class: "empty" }, court ? "No judgment of this court cites this section." : "No judgment cites this section.")),
    pages > 1 ? el("div", { class: "pager" },
      page > 1 ? el("a", { href: href({ page: page - 1 }) }, "Previous") : el("span", {}),
      el("span", {}, `Page ${page} of ${pages.toLocaleString()}`),
      page < pages ? el("a", { href: href({ page: page + 1 }) }, "Next") : el("span", {})) : "",
  ));
}

async function load() {
  if (params.get("view") === "judgments" && params.get("act") && params.get("s")) {
    return showSectionJudgments(params.get("act"), params.get("s"));
  }
  let acts;
  try { acts = await getJSON("/api/acts"); } catch (err) {
    content.replaceChildren(el("p", { class: "error" }, `Could not load the acts: ${err.message}`));
    return;
  }
  const slug = params.get("act");
  const number = params.get("s");
  if (slug && number) return showSection(slug, number);
  if (slug) return showAct(slug, acts).catch((err) =>
    content.replaceChildren(el("p", { class: "error" }, `Could not open this act: ${err.message}`)));
  return listActs(acts);
}

load();

// Bare Acts: /acts (all acts), /acts?act=bns (contents), /acts?act=bns&s=103 (one section).

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
        el("a", { href: actHref(slug, s.number) }, sectionLabel(act, s.number)), " ", s.title))),
    ]) : [el("p", { class: "summary" }, "No section matches.")]));
  };
  filter.addEventListener("input", render);
  render();
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

function citingList(sec) {
  const c = sec.cited_by;
  const searchHref = `/?${new URLSearchParams({ act: sec.act.act, section: sec.number })}`;
  const other = (sec.equivalents || []).filter((e) => e.ref);
  const withOther = other.length ? ` or ${other[0].short} s. ${other.map((e) => e.ref).join(", ")}` : "";
  if (!c.total) return [el("h2", {}, "Judgments citing it"),
    el("p", { class: "summary" }, "No judgment in the collection cites this section by its number.",
      withOther ? [" ", el("a", { href: searchHref }, `Search judgments citing it${withOther}`)] : "")];
  return [
    el("h2", {}, `Cited in ${c.total.toLocaleString()} ${c.total > 1 ? "judgments" : "judgment"}`),
    el("ul", { class: "plain" }, c.results.map((r) => el("li", {},
      el("a", { href: `/judgment?id=${r.id}` }, titleCase(r.title.replace(/^\S+ of /, ""))),
      el("span", { class: "cites" }, " · ", [caseNumber(r), courtLabel(r), formatDate(r.decision_date)].filter(Boolean).join(" · "))))),
    el("p", {}, el("a", { href: searchHref },
      c.total > c.results.length ? `See all ${c.total.toLocaleString()}${withOther ? `, and those citing${withOther}` : ""}`
        : `Search these judgments${withOther ? ` and those citing${withOther}` : ""}`)),
  ];
}

async function showSection(slug, number) {
  let sec;
  try {
    sec = await getJSON(`/api/acts/${encodeURIComponent(slug)}/sections/${encodeURIComponent(number)}`);
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
    citingList(sec),
  ));
}

async function load() {
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

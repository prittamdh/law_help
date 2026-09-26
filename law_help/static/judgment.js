// Judgment detail page: /judgment?id=123

const content = document.getElementById("content");

function field(label, value) {
  return value && (!Array.isArray(value) || value.length) ? [el("dt", {}, label), el("dd", {}, value)] : [];
}

function searchLink(params, text) {
  return el("a", { href: `/?${new URLSearchParams(params)}` }, text);
}

function joined(nodes, sep = ", ") {
  return nodes.flatMap((n, i) => [i ? sep : "", n]);
}

function actsList(acts) {
  return el("ul", { class: "plain" }, acts.map((a) => el("li", {},
    searchLink({ act: a.act }, a.act),
    a.sections.length ? [": ", joined(a.sections.map((s) =>
      searchLink({ act: a.act, section: s }, /^(Order|Rule)\b/.test(s) ? s : `s. ${s}`)))] : "",
  )));
}

function casesList(cases) {
  return el("ul", { class: "plain" }, cases.map((c) => el("li", {},
    c.name || "Unnamed case",
    c.citations?.length ? el("span", { class: "cites" }, ` · ${c.citations.join("; ")}`) : "",
  )));
}

// Other judgments of this court, linked: "CW/6863/2014 · Kheta Ram Vs State · 12 Mar 2015".
function judgmentLinks(rows) {
  return el("ul", { class: "plain" }, rows.map((r) => el("li", {},
    el("a", { href: `/judgment?id=${r.id}` }, titleCase(r.title.replace(/^\S+ of /, ""))),
    el("span", { class: "cites" }, " · ", [caseNumber(r), titleCase(r.bench), formatDate(r.decision_date)]
      .filter(Boolean).join(" · ")),
    r.connected ? el("span", { class: "cites" }, ` (and ${r.connected} connected ${r.connected > 1 ? "cases" : "case"})`) : "",
  )));
}

function citedBy(j) {
  if (!j.cited_by?.length) return "";
  const more = j.cited_by_total - j.cited_by.length;
  return [
    el("h2", {}, `Cited by ${j.cited_by_total} later ${j.cited_by_total > 1 ? "judgments" : "judgment"}`),
    judgmentLinks(j.cited_by),
    more > 0 ? el("p", { class: "note" }, `Showing the latest ${j.cited_by.length}.`) : "",
    el("p", { class: "note" }, "Found by matching case numbers cited in later judgments; some citations are missed."),
  ];
}

// The model summary when there is one; otherwise the two sentences copied from the judgment.
function summaryBlock(j) {
  const ai = j.ai_summary;
  if (!ai?.summary) return j.summary ? el("p", { class: "summary-text" }, j.summary) : "";
  return el("section", { class: "ai-summary" },
    el("p", { class: "summary-text" }, ai.summary),
    ai.issues?.length ? [el("h3", {}, "Issues"), el("ul", { class: "plain" }, ai.issues.map((i) => el("li", {}, i)))] : "",
    ai.holding ? [el("h3", {}, "Held"), el("p", {}, ai.holding)] : "",
    el("p", { class: "note" }, "Summary written by AI. Check it against the judgment before relying on it."),
  );
}

async function load() {
  const id = new URLSearchParams(location.search).get("id");
  if (document.referrer && new URL(document.referrer).origin === location.origin) {
    document.getElementById("back").addEventListener("click", (e) => { e.preventDefault(); history.back(); });
  }
  if (!/^\d+$/.test(id || "")) {
    content.replaceChildren(el("p", { class: "error" }, "No judgment id in the link."));
    return;
  }
  let j;
  try {
    j = await getJSON(`/judgments/${id}`);
  } catch (err) {
    content.replaceChildren(el("p", { class: "error" }, `Could not load this judgment: ${err.message}`));
    return;
  }
  document.title = `${j.title} · law_help`;

  // Judges printed on the judgment are more reliable than the eCourts metadata.
  const judges = j.bench_judges?.length ? j.bench_judges : j.judges || [];
  const judgeLinks = joined(judges.map((name) => searchLink({ judge: name }, titleCase(name))));
  const parties = j.parties || {};
  const advocates = j.advocates || {};
  // eCourts names the lead parties more reliably; the PDF's lists are used when a side has several.
  const petitioners = parties.petitioners?.length > 1 ? parties.petitioners.join("; ") : j.petitioner;
  const respondents = parties.respondents?.length > 1 ? parties.respondents.join("; ") : j.respondent;

  content.replaceChildren(el("article", { class: "judgment" },
    el("h1", {}, j.title),
    j.headline && el("p", { class: "headline" }, j.headline),
    summaryBlock(j),
    j.key_reasoning && el("section", { class: "key-reasoning" },
      el("h3", {}, "Key passage"),
      el("p", {}, j.key_reasoning),
      el("p", { class: "note" }, "Picked automatically from the judgment's reasoning."),
    ),
    el("a", { class: "pdf", href: j.pdf_url, target: "_blank", rel: "noopener" }, "Open the judgment PDF"),
    el("dl", {},
      field("Case", caseNumber(j)),
      field("Citation", j.neutral_citation),
      field("Bench", [titleCase(j.bench), j.bench_strength && ` (${j.bench_strength} bench)`].join("")),
      field("Decided", formatDate(j.decision_date)),
      field("Registered", formatDate(j.date_of_registration)),
      field("Outcome", j.outcome || titleCase(j.disposal_nature)),
      field(judges.length > 1 ? "Judges" : "Judge", judgeLinks),
      field("Petitioner", petitioners),
      field("For petitioner", (advocates.petitioner || []).join(", ")),
      field("Respondent", respondents),
      field("For respondent", (advocates.respondent || []).join(", ")),
      field("CNR", j.cnr),
    ),
    j.acts_cited?.length ? [el("h2", {}, "Acts and sections cited"), actsList(j.acts_cited)] : "",
    citedBy(j),
    j.cases_cited?.length ? [el("h2", {}, "Cases cited"), casesList(j.cases_cited)] : "",
    j.cites?.length ? [el("h2", {}, "Earlier judgments of this court it cites"), judgmentLinks(j.cites)] : "",
    j.description && [el("h2", {}, "Opening lines"), el("div", { class: "text" }, j.description)],
    j.full_text
      ? [el("h2", {}, "Full text"), el("div", { class: "text" }, j.full_text)]
      : el("p", { class: "summary" }, "Full text has not been extracted for this judgment yet. The PDF above has it."),
  ));
}

load();

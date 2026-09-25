// Judgment detail page: /judgment?id=123

const content = document.getElementById("content");

function field(label, value) {
  return value ? [el("dt", {}, label), el("dd", {}, value)] : [];
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

  const judgeLinks = (j.judges || []).flatMap((name, i) => [
    i ? ", " : "",
    el("a", { href: `/?judge=${encodeURIComponent(name)}` }, titleCase(name)),
  ]);

  content.replaceChildren(el("article", { class: "judgment" },
    el("h1", {}, j.title),
    el("a", { class: "pdf", href: j.pdf_url, target: "_blank", rel: "noopener" }, "Open the judgment PDF"),
    el("dl", {},
      field("Case", caseNumber(j)),
      field("Bench", [titleCase(j.bench), j.bench_strength && ` (${j.bench_strength} bench)`].join("")),
      field("Decided", formatDate(j.decision_date)),
      field("Registered", formatDate(j.date_of_registration)),
      field("Outcome", titleCase(j.disposal_nature)),
      judgeLinks.length ? [el("dt", {}, j.judges.length > 1 ? "Judges" : "Judge"), el("dd", {}, judgeLinks)] : [],
      field("Petitioner", j.petitioner),
      field("Respondent", j.respondent),
      field("CNR", j.cnr),
    ),
    j.description && [el("h2", {}, "Opening lines"), el("div", { class: "text" }, j.description)],
    j.full_text
      ? [el("h2", {}, "Full text"), el("div", { class: "text" }, j.full_text)]
      : el("p", { class: "summary" }, "Full text has not been extracted for this judgment yet. The PDF above has it."),
  ));
}

load();

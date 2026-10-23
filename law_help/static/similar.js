// "Similar judgments" on the judgment page (law_help.similar). Loaded after the judgment
// itself, so a slow lookup never holds up the page.

function similarPanel(id) {
  const box = el("section", { class: "similar" },
    el("h2", {}, "Similar judgments"),
    el("p", { class: "note" }, "Finding similar judgments…"));
  getJSON(`/judgments/${id}/similar`).then(({ results }) => {
    if (!results.length) { box.remove(); return; }
    box.replaceChildren(
      el("h2", {}, "Similar judgments"),
      el("ul", { class: "plain" }, results.map((r) => el("li", {},
        el("a", { href: `/judgment?id=${r.id}` }, titleCase(r.title.replace(/^\S+ of /, ""))),
        el("span", { class: "cites" }, " · ", [caseNumber(r), courtLabel(r), formatDate(r.decision_date)]
          .filter(Boolean).join(" · ")),
        el("div", { class: "reason" }, r.reason),
      ))),
      el("p", { class: "note" }, "Picked by the cases, acts and sections they cite in common."),
    );
  }).catch(() => box.remove());
  return box;
}

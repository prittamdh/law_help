// "Check case status": links to the court's own case status search (law_help.status_links).
// Those pages need the CNR or case number typed in with a captcha, so the values sit beside the
// links with copy buttons. Uses copyText from judgment.js.

function caseStatusBox(j) {
  const s = j.status_links;
  if (!s?.links?.length) return "";
  const [main, ...more] = s.links;
  const open = (l, cls) => el("a", { class: cls, href: l.url, target: "_blank", rel: "noopener noreferrer" }, l.label);
  return el("section", { class: "case-status" },
    el("div", { class: "actions" }, open(main, "button"), more.map((l) => open(l, "more"))),
    el("dl", {}, s.fields.map((f) => {
      let copy = "";
      if (f.copy) {
        copy = el("button", { type: "button", class: "link" }, "Copy");
        copy.addEventListener("click", () => copyText(f.value, copy, "Copy"));
      }
      return [el("dt", {}, f.label), el("dd", {}, el("code", {}, f.value), copy ? [" ", copy] : "")];
    })),
    s.note && el("p", { class: "note" }, s.note),
  );
}

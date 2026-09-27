// How each later judgment treats this one (law_help.treatment): followed, distinguished,
// doubted, or just cited. Labels come from matching phrases in the later judgment's text.
const TREATMENTS = {
  followed: "Followed",
  distinguished: "Distinguished",
  doubted: "Doubted",
  cited: "Cited",
};

// A small chip after a "Cited by" item; hover shows the later judgment's own words.
function treatmentChip(r) {
  const kind = r.treatment || "cited";
  return el("span", { class: `chip treatment ${kind}`, title: r.treatment_quote || "No comment on it found" },
    TREATMENTS[kind] || kind);
}

// "Followed 12 · Distinguished 3 · Cited 40", counting a common order once.
function treatmentSummary(j) {
  const counts = j.cited_by_treatments || {};
  const parts = Object.keys(TREATMENTS).filter((k) => counts[k]).map((k) => `${TREATMENTS[k]} ${counts[k]}`);
  return parts.length ? el("p", { class: "treatment-summary" }, parts.join(" · ")) : "";
}

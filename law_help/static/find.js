// Full text of a judgment on its page: numbered paragraphs (#p12 links to one), a find box,
// and "Copy with cite" per paragraph. Used by judgment.js as fullText(j).

// A line that opens a numbered paragraph: "12. The", "12) The", "(12) The", Hindi "१२. यह".
// Not "11.1." or "12.03.2015".
const PARA_NUM_RE = /^\s*\(?([0-9०-९]{1,3})[.)]\)?\s+\S/;
const paraNum = (s) => +s.replace(/[०-९]/g, (d) => d.charCodeAt(0) - 0x966);

// The judgment's own numbering: the longest run of lines numbered 1, 2, 3… in order. The run
// must start at 1, which skips party lists ("1. Ramesh… 2. State…") before the body restarts at 1.
function numberedRun(lines) {
  const nums = lines.map((ln) => { const m = PARA_NUM_RE.exec(ln); return m ? paraNum(m[1]) : 0; });
  let best = [];
  nums.forEach((n, start) => {
    if (n !== 1) return;
    const run = [start];
    for (let i = start + 1; i < nums.length; i++) if (nums[i] === run.length + 1) run.push(i);
    if (run.length >= best.length) best = run;  // on a tie the later run, the body after the header
  });
  return best.length >= 2 ? best : [];
}

// Without numbering: a paragraph ends on a line that closes a sentence well short of the usual
// line width (PDF text keeps the printed line breaks).
function unnumberedBreaks(lines) {
  const widths = lines.map((ln) => ln.length).sort((a, b) => a - b);
  const full = widths[Math.floor(widths.length * 0.8)] || 0;
  const starts = [0];
  for (let i = 1; i < lines.length; i++) {
    const prev = lines[i - 1].trim();
    if (/[.:?!।]["'”)]?$/.test(prev) && prev.length < full * 0.75) starts.push(i);
  }
  return starts;
}

// [{num, text}]: num is the judgment's own paragraph number, else counted from 1, and null for
// the header before a numbered judgment's first paragraph. `own` says which it was.
function splitParagraphs(text) {
  const lines = text.split("\n");
  const run = numberedRun(lines);
  const own = run.length > 0;
  const starts = own ? run : unnumberedBreaks(lines);
  const paras = [];
  if (own && starts[0] > 0) paras.push({ num: null, text: lines.slice(0, starts[0]).join("\n") });
  starts.forEach((s, k) => {
    const text = lines.slice(s, starts[k + 1] ?? lines.length).join("\n");
    // The number shows beside the paragraph, so it comes off the text.
    paras.push({ num: k + 1, text: own ? text.replace(/^\s*\(?[0-9०-९]{1,3}[.)]\)?\s+/, "") : text });
  });
  return { paras, own };
}

// Words of a search box query as find terms: quoted phrases stay whole; "or" and -excluded words go.
function findTerms(q) {
  const terms = [];
  (q || "").replace(/"([^"]+)"|(\S+)/g, (_, phrase, word) => {
    const t = (phrase || word || "").trim();
    if (t && !(word && (/^-/.test(t) || /^or$/i.test(t)))) terms.push(t);
  });
  return terms;
}

function findRegex(terms) {
  const parts = terms.filter((t) => t.length >= 2)
    .sort((a, b) => b.length - a.length)
    // A line break in the PDF text can fall inside a phrase.
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+"));
  return parts.length ? new RegExp(parts.join("|"), "giu") : null;
}

// Paragraph text with its matches wrapped in <mark>.
function highlighted(text, re) {
  if (!re) return [text];
  const out = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (!m[0]) continue;
    out.push(text.slice(last, m.index), el("mark", {}, m[0]));
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

// Scrolls the text box (not the page) so `node` sits a third of the way down it.
function scrollInBox(box, node) {
  box.scrollTop += node.getBoundingClientRect().top - box.getBoundingClientRect().top - box.clientHeight / 3;
}

function fullText(j) {
  const { paras, own } = splitParagraphs(j.full_text);
  const box = el("div", { class: "text paras" });
  const bodies = paras.map((p) => {
    const body = el("span", { class: "para-text" }, p.text);
    if (p.num == null) { box.append(el("div", { class: "para" }, body)); return body; }
    const copy = el("button", { type: "button", class: "link copy-cite" }, "Copy with cite");
    copy.addEventListener("click", () => {
      const quote = p.text.replace(/\s*\n\s*/g, " ").trim();
      copyText(`${quote}\n\n${j.citation}, para ${p.num}`, copy, "Copy with cite");
    });
    box.append(el("div", { class: "para", id: `p${p.num}` },
      el("a", { class: "para-num", href: `#p${p.num}`, title: `Link to paragraph ${p.num}` }, p.num),
      body, copy));
    return body;
  });

  const input = el("input", { type: "search", placeholder: "Find in judgment", "aria-label": "Find in judgment" });
  const count = el("span", { class: "find-count", "aria-live": "polite" });
  const prev = el("button", { type: "button", title: "Previous (Shift+Enter)" }, "Previous");
  const next = el("button", { type: "button", title: "Next (Enter)" }, "Next");
  let marks = [];
  let at = -1;
  let found = null;  // the query the marks are for

  function show(i) {
    if (!marks.length) return;
    marks[at]?.classList.remove("current");
    at = (i + marks.length) % marks.length;
    marks[at].classList.add("current");
    count.textContent = `${at + 1} of ${marks.length}`;
    scrollInBox(box, marks[at]);
  }

  function find() {
    found = input.value;
    const re = findRegex(findTerms(found));
    paras.forEach((p, i) => bodies[i].replaceChildren(...highlighted(p.text, re)));
    marks = [...box.querySelectorAll("mark")];
    at = -1;
    count.textContent = re ? (marks.length ? `${marks.length} found` : "Not found") : "";
    prev.disabled = next.disabled = !marks.length;
  }

  let timer;
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { find(); show(0); }, 150); });
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    clearTimeout(timer);
    if (found !== input.value) find();
    show(at < 0 ? (e.shiftKey ? -1 : 0) : at + (e.shiftKey ? -1 : 1));
  });
  prev.addEventListener("click", () => show(at - 1));
  next.addEventListener("click", () => show(at + 1));

  // Arrived from a search: highlight its words. A #p12 link goes to that paragraph instead.
  function goToHash() {
    const target = /^#p\d+$/.test(location.hash) && document.getElementById(location.hash.slice(1));
    if (!target) return false;
    box.querySelector(".para.target")?.classList.remove("target");
    target.classList.add("target");
    box.scrollIntoView({ block: "start" });
    scrollInBox(box, target);
    box.scrollTop += box.clientHeight / 3 - 8;  // paragraph at the top of the box
    return true;
  }
  input.value = new URLSearchParams(location.search).get("q") || "";
  find();
  window.addEventListener("hashchange", goToHash);
  // After judgment.js has put the text in the page.
  setTimeout(() => { if (!goToHash() && marks.length) show(0); });

  return [
    el("div", { class: "find-bar" }, input, count, prev, next),
    box,
    own ? "" : el("p", { class: "note" }, "The judgment does not number its paragraphs, so these numbers are counted here."),
  ];
}

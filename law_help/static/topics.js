// Topics: /topics (all topics with counts), /topics?topic=bail (one topic).

const content = document.getElementById("content");
const params = new URLSearchParams(location.search);

const BENCHES = { "supreme court": "Supreme Court", jaipur: "Jaipur", jodhpur: "Jodhpur" };

function topicHref(slug) {
  return `/topics?${new URLSearchParams({ topic: slug })}`;
}

function searchHref(slug, extra = {}) {
  return `/?${new URLSearchParams({ topic: slug, ...extra })}`;
}

async function listTopics() {
  document.title = "Topics · law_help";
  let list;
  try { list = await getJSON("/api/topics"); } catch (err) {
    content.replaceChildren(el("p", { class: "error" }, `Could not load the topics: ${err.message}`));
    return;
  }
  content.replaceChildren(el("div", { class: "acts" },
    el("h1", {}, "Topics"),
    el("p", { class: "summary" }, "Common kinds of cases in the Rajasthan High Court and the Supreme Court. Open one for its most cited and latest judgments."),
    el("ol", { class: "results topics" }, list.map((t) => el("li", {},
      el("a", { class: "title", href: topicHref(t.slug) }, t.name),
      t.total != null ? el("span", { class: "count" }, ` ${t.total.toLocaleString()} judgments`) : "",
      el("div", { class: "meta" }, t.blurb)))),
  ));
}

// One judgment as a short line: title, then case number, court, date and chips.
function judgmentItem(j) {
  return el("li", {},
    el("a", { href: `/judgment?id=${j.id}` }, titleCase(j.title.replace(/^\S+ of /, ""))),
    el("span", { class: "cites" }, " · ", [caseNumber(j), courtLabel(j), formatDate(j.decision_date)].filter(Boolean).join(" · ")),
    " ", goodLawChip(j.good_law),
    j.cited_by_count ? el("span", { class: "chip" }, `cited ${j.cited_by_count}×`) : "",
    j.headline && el("div", { class: "headline" }, j.headline));
}

function searchBox(t) {
  const input = el("input", { type: "search", name: "q", placeholder: `Search within ${t.name.toLowerCase()}`, "aria-label": "Keywords" });
  // A plain GET to the search page, which reads its filters from the URL.
  return el("form", { class: "finder", action: "/", method: "get" },
    el("input", { type: "hidden", name: "topic", value: t.slug }), input, el("button", { type: "submit" }, "Search"));
}

async function showTopic(slug) {
  let t;
  try { t = await getJSON(`/api/topics/${encodeURIComponent(slug)}`); } catch (err) {
    content.replaceChildren(el("p", { class: "error" }, `Could not open this topic: ${err.message}`),
      el("p", {}, el("a", { href: "/topics" }, "See all topics")));
    return;
  }
  document.title = `${t.name} · Topics · law_help`;
  const benches = t.by_bench.map((b) => `${BENCHES[b.bench] || titleCase(b.bench)} ${b.n.toLocaleString()}`);
  const years = t.by_year.map((y) => `${y.year}: ${y.n.toLocaleString()}`);
  content.replaceChildren(el("article", { class: "judgment topic" },
    el("p", { class: "crumbs" }, el("a", { href: "/topics" }, "Topics")),
    el("h1", {}, t.name),
    el("p", { class: "summary" }, t.blurb),
    searchBox(t),
    el("p", { class: "counts" }, el("strong", {}, `${t.total.toLocaleString()} ${t.total === 1 ? "judgment" : "judgments"}`),
      benches.length ? ` · ${benches.join(" · ")}` : ""),
    years.length ? el("p", { class: "note" }, `By year decided: ${years.join(" · ")}`) : "",
    el("h2", {}, "Most cited"),
    t.most_cited.length
      ? [el("ul", { class: "plain" }, t.most_cited.map(judgmentItem)),
         el("p", {}, el("a", { href: searchHref(slug, { landmark: "true" }) }, "Landmark judgments in this topic"))]
      : el("p", { class: "summary" }, "No judgment in this topic is cited by a later one yet."),
    el("h2", {}, "Latest"),
    t.latest.length
      ? [el("ul", { class: "plain" }, t.latest.map(judgmentItem)),
         el("p", {}, el("a", { href: searchHref(slug) }, `See all ${t.total.toLocaleString()}`))]
      : el("p", { class: "summary" }, "No judgments yet."),
    el("section", { class: "equiv" },
      el("h3", {}, "How judgments are picked"),
      el("p", { class: "note" }, "A judgment is in this topic if any one of these matches:"),
      el("ul", { class: "plain" }, t.rules.map((r) => el("li", {}, r)))),
  ));
}

const slug = params.get("topic");
if (slug) showTopic(slug); else listTopics();

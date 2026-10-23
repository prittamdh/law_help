# Fonts

Self-hosted so pages make no third-party requests. All are under the SIL Open Font License 1.1
(https://openfontlicense.org), downloaded from Google Fonts as variable woff2 files:

- Inter (Rasmus Andersson): interface text
- Source Serif 4 (Adobe): headings and judgment text
- Noto Sans Devanagari, Noto Serif Devanagari (Google): Hindi text

`style.css` declares them with `unicode-range`, so a browser downloads only the subsets a page uses.

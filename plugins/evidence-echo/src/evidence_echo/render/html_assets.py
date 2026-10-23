# plugins/evidence-echo/src/evidence_echo/render/html_assets.py
"""Inline CSS (and, from Task 4, JS) for the HTML pages (SP5a-H). No fonts, images or URLs.

The look is a test lab's ratings issue: paper and ink, a black masthead bar, heavy rules that open
each chart and section, Harvey-ball grades always paired with the tier letter, and one red that
only marks problems (red flags, disputes, a failed source check)."""

# Colour tokens per colour scheme. Every (text, background) pair in CONTRAST_PAIRS meets WCAG AA
# (4.5:1); tests/test_render_html.py checks it, so a colour change cannot slip below.
PALETTES = {
    "light": {
        "bg": "#ffffff", "fg": "#151412", "muted": "#58554e", "card": "#f2f0ea",
        "border": "#d5d1c7", "rule": "#151412", "link": "#151412",
        "mast-bg": "#151412", "mast-fg": "#ffffff", "mast-muted": "#bcb8ae",
        "red": "#ad1328", "warn-bg": "#fbeeee", "warn-fg": "#8f0f22",
        "ok-fg": "#151412", "bad-fg": "#ad1328", "mix-fg": "#58554e",
        "zebra": "#f9f8f4", "mark": "#fdf0a6",
    },
    "dark": {
        "bg": "#161513", "fg": "#ecebe6", "muted": "#aaa69c", "card": "#211f1c",
        "border": "#3b3934", "rule": "#ecebe6", "link": "#ecebe6",
        "mast-bg": "#0b0b0a", "mast-fg": "#ecebe6", "mast-muted": "#aaa69c",
        "red": "#ff8593", "warn-bg": "#2d1619", "warn-fg": "#ffb3bc",
        "ok-fg": "#ecebe6", "bad-fg": "#ff8593", "mix-fg": "#aaa69c",
        "zebra": "#1c1b18", "mark": "#4a4220",
    },
}

TIER_KEYS = ("a", "b", "c", "d", "x")  # x = ungraded, shown as "?"
TIER_FILL = {"a": "100%", "b": "75%", "c": "50%", "d": "25%", "x": "0%"}  # Harvey-ball fill

CONTRAST_PAIRS = [("fg", "bg"), ("fg", "card"), ("muted", "bg"), ("muted", "card"),
                  ("link", "bg"), ("link", "card"), ("red", "bg"), ("red", "card"),
                  ("warn-fg", "warn-bg"), ("link", "warn-bg"), ("fg", "warn-bg"),
                  ("mast-fg", "mast-bg"), ("mast-muted", "mast-bg"),
                  ("ok-fg", "bg"), ("bad-fg", "bg"), ("mix-fg", "bg"),
                  ("fg", "zebra"), ("muted", "zebra"), ("red", "zebra"), ("link", "zebra"),
                  ("fg", "mark")]


def _vars(tokens: dict) -> str:
    return "".join(f"--{name}:{value};" for name, value in tokens.items())


_FONTS = (":root{"
          "--sans:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;"
          "--cond:Bahnschrift,'DIN Alternate','Avenir Next Condensed','Roboto Condensed',"
          "'Arial Narrow',system-ui,sans-serif;"
          "--serif:Charter,'Bitstream Charter','Sitka Text',Cambria,Georgia,serif;"
          "--measure:66ch;--page:78rem}")

_BASE = (
    ":root{color-scheme:light dark}"
    "*{box-sizing:border-box}"
    "html{-webkit-text-size-adjust:100%}"
    "body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 var(--sans);"
    "font-variant-numeric:tabular-nums;overflow-wrap:anywhere}"
    "::selection{background:var(--fg);color:var(--bg)}"
    "main{max-width:var(--page);margin:0 auto;padding:0 2rem 2.5rem}"
    "a{color:var(--link);text-decoration-thickness:1px;text-underline-offset:.18em;"
    "text-decoration-color:var(--muted)}"
    "a:hover{text-decoration-color:currentColor;text-decoration-thickness:2px}"
    ":focus-visible{outline:2px solid var(--fg);outline-offset:2px}"
    "summary{cursor:pointer}"
    "summary:focus-visible{outline-offset:1px}"
    "h1,h2,h3{font-family:var(--cond);font-stretch:condensed;letter-spacing:-.01em;"
    "text-wrap:balance}"
    "h1{font-size:2.6rem;line-height:1.05;font-weight:700;margin:2.25rem 0 .35rem}"
    "h2{font-size:1.15rem;line-height:1.2;font-weight:700;text-transform:uppercase;"
    "letter-spacing:.04em;margin:3rem 0 0;padding:.5rem 0 .6rem;border-top:3px solid var(--rule)}"
    "h3{font-size:1.4rem;line-height:1.15;font-weight:700;margin:0 0 .5rem}"
    ".meta{color:var(--muted);font-size:.9rem;margin:.25rem 0}"
    ".empty{color:var(--muted);font-style:italic;font-family:var(--serif)}"
    ".sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}"
    "[hidden]{display:none!important}"
    # masthead: the black bar; the first crumb is the masthead's name
    ".ee-mast{background:var(--mast-bg);color:var(--mast-fg);border-bottom:3px solid var(--mast-bg)}"
    "@media (prefers-color-scheme: dark){.ee-mast{border-bottom-color:var(--rule)}}"
    ".ee-mast-in{max-width:var(--page);margin:0 auto;padding:.7rem 2rem;display:flex;flex-wrap:wrap;"
    "align-items:baseline;gap:.25rem 1.5rem}"
    ".ee-mast a{color:var(--mast-fg);text-decoration-color:var(--mast-muted)}"
    ".ee-brand,.ee-crumbs li:first-child{font:700 1.35rem/1.2 var(--cond);font-stretch:condensed;"
    "letter-spacing:-.005em;color:var(--mast-fg)}"
    ".ee-brand a,.ee-crumbs li:first-child a{text-decoration:none}"
    ".ee-crumbs ol{display:flex;flex-wrap:wrap;align-items:baseline;list-style:none;margin:0;"
    "padding:0;font-size:.875rem;color:var(--mast-muted)}"
    ".ee-crumbs li+li::before{content:'\\203a';margin:0 .45rem;color:var(--mast-muted)}"
    ".ee-crumbs li:first-child+li::before{margin-left:.6rem}"
    ".ee-crumbs a{color:var(--mast-fg)}"
    ".ee-crumbs [aria-current]{color:var(--mast-fg)}"
    ".ee-links{display:flex;flex-wrap:wrap;gap:.25rem 1.25rem;margin-left:auto;font-size:.875rem}"
    # page head
    ".ee-head{margin:0 0 1.5rem}"
    ".ee-head .meta{font-size:1rem}"
    # Harvey-ball tier mark: the ball is drawn, the letter carries the meaning
    ".tier{display:inline-flex;align-items:baseline;gap:.3em;text-indent:0;font:700 1em/1 var(--cond);"
    "font-stretch:condensed;white-space:nowrap;color:var(--fg)}"
    ".tier::before{content:'';flex:none;width:1.05em;height:1.05em;border-radius:50%;"
    "border:.13em solid currentColor;transform:translateY(.14em);"
    "background:conic-gradient(currentColor var(--fill),transparent 0)}"
    ".tier-x::before{border-style:dotted}"
    # the ratings chart (report page)
    ".ee-chart{width:100%;border-collapse:collapse;margin:0 0 1rem;font-size:.95rem}"
    ".ee-chart caption{text-align:left;font:700 1.15rem/1.2 var(--cond);font-stretch:condensed;"
    "text-transform:uppercase;letter-spacing:.04em;padding:.5rem 0 .6rem;border-top:3px solid var(--rule)}"
    ".ee-chart th{font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;"
    "color:var(--muted);text-align:left;padding:.4rem .75rem;border-bottom:1px solid var(--rule);white-space:nowrap}"
    ".ee-chart td{padding:.65rem .75rem;border-bottom:1px solid var(--border);vertical-align:baseline}"
    ".ee-chart tbody tr:nth-child(even) td{background:var(--card)}"
    ".ee-chart .tier{font-size:1.15rem}"
    ".ee-chart td.ee-c-study{font-weight:600;width:44%}"
    ".ee-chart td.ee-c-study a{text-decoration:none}"
    ".ee-chart td.ee-c-study a:hover{text-decoration:underline}"
    ".ee-chart .ee-none{color:var(--muted)}"
    ".ee-warn,.ee-chart .ee-warn{color:var(--red);font-weight:600}"
    ".ee-mark{font-family:var(--sans);font-weight:800;margin-right:.2em}"
    ".ee-key{margin:-.5rem 0 0;font-size:.875rem;display:flex;flex-wrap:wrap;gap:.25rem 1.5rem}"
    ".ee-key .tier{font-size:1.1em;margin-right:.35rem}.ee-key .ee-mark{font-size:1.1em}"
    "article.card{padding:1.75rem 1rem;border-top:1px solid var(--border);scroll-margin-top:1rem}"
    # zebra: every other write-up; on the topic page the script marks the visible ones (.ee-alt)
    "section>article.card:nth-of-type(even),#ee-studies:not(.ee-js)>article.card:nth-child(even),"
    "#ee-studies.ee-js>article.ee-alt{background:var(--zebra)}"
    "h2+article.card,h2+.ee-chart,#ee-studies>article.card:first-child{border-top:0}"
    # write-ups: title and summary | the grade as a small block on top of the facts
    ".ee-card{display:grid;grid-template-columns:minmax(0,var(--measure)) minmax(16rem,1fr);"
    "grid-template-rows:auto 1fr;gap:0 2.5rem}"
    ".ee-card-main{grid-column:1;grid-row:1/span 2}"
    ".ee-card-rate{grid-column:2;grid-row:1;display:flex;flex-wrap:wrap;align-items:baseline;gap:.2rem .75rem;"
    "padding:0 0 .5rem;margin:0 0 .6rem;border-bottom:1px solid var(--border)}"
    ".ee-card-side{grid-column:2;grid-row:2}"
    ".ee-card-rate .label{display:flex;flex-wrap:wrap;align-items:baseline;gap:.2rem .6rem;margin:0;"
    "color:var(--muted);font-size:.85rem;line-height:1.35}"
    ".ee-card-rate .tier{font-size:1.2rem}"
    ".ee-card-rate .ee-warn{font-size:.85rem}"
    ".ee-card-main p{font:1.075rem/1.62 var(--serif);margin:0}"
    ".ee-f{margin:0 0 .4rem;padding-left:8.8rem;text-indent:-8.8rem;font-size:.9rem;line-height:1.45}"
    ".ee-f b{display:inline-block;width:8.5rem;padding-right:.75rem;text-indent:0;vertical-align:top;"
    "font-weight:600;color:var(--muted);font-size:.8rem;line-height:1.6}"
    ".ee-f.ee-flags{color:var(--red);font-weight:600}"
    ".warn{background:var(--warn-bg);color:var(--warn-fg);border:1px solid currentColor;"
    "padding:.55rem .75rem;margin:0 0 .9rem;font-size:.9rem}"
    # the skeptic's note: a quiet grey note; only its first label is red
    ".dispute{background:var(--card);color:var(--fg);border:1px solid var(--border);"
    "padding:.55rem .75rem;margin:0 0 .9rem;font-size:.875rem;line-height:1.5}"
    ".dispute b{color:var(--muted);font-weight:600}.dispute b:first-child{color:var(--red)}"
    "@media (prefers-reduced-motion:no-preference){article.card:target{animation:ee-find 1.6s "
    "cubic-bezier(.16,1,.3,1)}}"
    "@keyframes ee-find{from{background:var(--card);box-shadow:0 0 0 1rem var(--card)}"
    "to{background:transparent;box-shadow:0 0 0 1rem transparent}}"
    # other lists
    ".seen{list-style:none;padding:0;margin:0}"
    ".seen li{padding:.6rem 0;border-bottom:1px solid var(--border)}"
    "footer{margin-top:2.5rem}"
    ".table-wrap{overflow-x:auto;max-width:100%}"
    "table{border-collapse:collapse}"
    ".ee-rej th,.ee-rej td:nth-child(3),.ee-rej td:nth-child(4){white-space:nowrap}"
    ".ee-rej td:first-child{min-width:8em}"
    "th,td{padding:.45rem .75rem;vertical-align:top;text-align:left}"
    "th{border-bottom:1px solid var(--rule);font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;"
    "color:var(--muted)}"
    "td{border-bottom:1px solid var(--border)}"
    # topic page
    ".ee-reports{margin:0 0 1rem}"
    ".ee-reports summary{color:var(--muted);font-size:.9rem}"
    ".ee-reports ul{columns:12rem;margin:.5rem 0 0;padding:0;list-style:none;font-size:.9rem}"
    ".ee-reports li{padding:.15rem 0}"
    ".ee-index{margin:0 0 1rem}"
    ".ee-index ol{list-style:none;margin:0;padding:0;display:grid;"
    "grid-template-columns:repeat(auto-fill,minmax(17rem,1fr));gap:0 2rem}"
    ".ee-index li{display:grid;grid-template-columns:minmax(0,1fr) 2.5rem;gap:0 .5rem;align-items:baseline;"
    "padding:.35rem 0 .2rem;font-size:.9rem}"
    ".ee-index a{text-decoration:none}.ee-index a:hover{text-decoration:underline}"
    ".ee-index .ee-n{text-align:right;color:var(--muted)}"
    ".ee-index .ee-extent{grid-column:1/-1;height:3px;margin-top:.3rem;background:var(--border)}"
    ".ee-index .ee-extent span{display:block;height:100%;width:var(--w);background:var(--rule)}"
    ".subtopic{scroll-margin-top:1rem}"
    ".subtopic>h3{margin:2rem 0 .25rem;padding-top:.5rem;border-top:1px solid var(--rule)}"
    ".ee-legend{margin:0 0 .5rem}"
    ".ee-questions{list-style:none;margin:.25rem 0 .75rem;padding:0}"
    ".ee-question{padding:.6rem 0;border-bottom:1px solid var(--border)}"
    ".ee-cmp{display:block;font-size:.85rem;color:var(--muted)}"
    ".ee-question>strong,.ee-question>details>summary>strong{font-weight:600}"
    ".ee-question>details.ee-studies{margin:0;font-size:1rem}"
    ".ee-question>details.ee-studies>summary{color:var(--fg);list-style-position:outside}"
    ".ee-question>details.ee-studies>summary:hover strong{text-decoration:underline;"
    "text-decoration-thickness:1px;text-underline-offset:.18em}"
    ".ee-question>details.ee-studies>ol{font-size:.9rem}"
    ".ee-reports li .meta{margin-left:.4rem;font-size:.85rem}"
    ".ee-label{color:var(--muted);white-space:nowrap}"
    ".ee-label span{display:inline-block;width:1.1em;font-weight:800}"
    ".ee-label-consistent span{color:var(--ok-fg)}"
    ".ee-label-contested span{color:var(--bad-fg)}"
    ".ee-label-varied{color:var(--mix-fg)}"
    ".ee-why{margin:.5rem 0;font-size:.9rem;border:1px solid var(--border);background:var(--card);"
    "padding:.4rem .7rem}"
    ".ee-why[open]{padding-bottom:.6rem}"
    ".ee-why p{margin:.4rem 0;font-family:var(--serif);font-size:1rem;max-width:var(--measure)}"
    ".ee-why ul{margin:.4rem 0;padding-left:1.2rem}"
    ".ee-why li{margin:.5rem 0}"
    ".ee-why blockquote{margin:.5rem 0 .5rem .25rem;padding-left:.9rem;border-left:1px solid var(--rule);"
    "font-family:var(--serif);font-size:1rem;max-width:var(--measure)}"
    ".ee-studies,.ee-others{margin:.35rem 0;font-size:.9rem}"
    ".ee-studies summary,.ee-others summary{color:var(--muted)}"
    ".ee-studies ol{margin:.35rem 0;padding-left:1.5rem}"
    # a study entry: tier mark | [question on its own line] name · headline [date]; one link
    ".ee-studies li{margin:.35rem 0}"
    ".ee-row{display:grid;grid-template-columns:2.4em minmax(0,1fr);color:inherit;text-decoration:none}"
    ".ee-row>.tier{grid-row:1/span 2;font-size:.95em;align-self:start;margin-top:.1em}"
    ".ee-q,.ee-row-line{grid-column:2}"
    ".ee-q{font-weight:600}"
    ".ee-date{color:var(--muted);white-space:nowrap}"
    "a.ee-row:hover .ee-q,a.ee-row:hover .ee-row-line{text-decoration:underline;"
    "text-decoration-thickness:1px;text-underline-offset:.18em}"
    ".ee-change{margin:.25rem 0 .25rem 1.5rem}"
    "#ee-count{margin:0 0 .5rem;color:var(--muted)}"
    # page footer: the document's end, a heavy rule like every section opener
    ".ee-foot{padding:0 2rem}"
    ".ee-foot-in{max-width:calc(var(--page) - 4rem);margin:0 auto;padding:.75rem 0 2.5rem;display:flex;flex-wrap:wrap;"
    "justify-content:space-between;gap:.5rem 1.5rem;border-top:3px solid var(--rule);font-size:.85rem;"
    "color:var(--muted)}"
    ".ee-foot a{font-weight:600}"
    # filters: closed by default; a grid of captioned fields; a field with a value is highlighted
    ".ee-filter-box{margin:0 0 1rem;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)}"
    ".ee-filter-box>summary{padding:.6rem 0;font-weight:600}"
    "#ee-active{font-weight:400;color:var(--muted)}"
    ".filters{display:grid;grid-template-columns:repeat(auto-fill,minmax(14rem,1fr));gap:.9rem 1.5rem;"
    "align-items:end;padding:.25rem 0 1rem;font-size:.9rem}"
    ".filters label{display:flex;flex-direction:column;gap:.3rem;min-width:0;max-width:100%;"
    "color:var(--muted);font-size:.8rem;font-weight:600}"
    ".filters label:has(input[type=checkbox]){flex-direction:row;align-items:center;gap:.4rem;"
    "color:var(--fg);font-size:.9rem;font-weight:400;padding:.3rem .45rem;border:1px solid transparent}"
    ".filters fieldset{grid-column:1/-1;border:1px solid transparent;margin:0;padding:.3rem .45rem;"
    "display:flex;flex-wrap:wrap;align-items:center;gap:.25rem 1rem}"
    ".filters fieldset label:has(input[type=checkbox]){padding:0;border:0}"
    ".filters legend{float:left;padding:0;margin-right:.5rem;color:var(--muted);font-size:.8rem;font-weight:600}"
    ".filters select,.filters input[type=search]{width:100%;min-width:0;font:400 .9rem/1.3 var(--sans);"
    "color:var(--fg);background:var(--bg);border:1px solid var(--muted);border-radius:0;padding:.4rem .5rem}"
    ".filters input[type=checkbox]{accent-color:var(--fg)}"
    ".filters .ee-on{color:var(--fg)}"
    ".filters .ee-on select,.filters .ee-on input[type=search]{background:var(--mark);border-color:var(--fg);"
    "box-shadow:inset 0 0 0 1px var(--fg)}"
    ".filters label.ee-on:has(input[type=checkbox]),.filters fieldset.ee-on{background:var(--mark);"
    "border-color:var(--fg)}"
    "#ee-reset{justify-self:start;font:600 .9rem/1.3 var(--sans);padding:.4rem .9rem;color:var(--fg);"
    "background:var(--bg);border:1px solid var(--fg);border-radius:0;cursor:pointer}"
    "#ee-reset:hover{background:var(--fg);color:var(--bg)}"
    # overview
    ".ee-topics{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,26rem),1fr));gap:0 3rem}"
    "section.card h2{margin-top:2rem}"
    "section.card ul{list-style:none;margin:.5rem 0;padding:0}"
    "section.card li{display:grid;grid-template-columns:3rem minmax(0,1fr);gap:0 .5rem;padding:.45rem 0;"
    "border-bottom:1px solid var(--border)}"
    "section.card nav{display:flex;flex-wrap:wrap;gap:.25rem 1.25rem;margin:.75rem 0 0;font-weight:600}"
    # narrow screens: the write-up folds to one column, the chart to stacked rows
    "@media (max-width:58rem){.ee-card{grid-template-columns:minmax(0,1fr);grid-template-rows:none}"
    ".ee-card-main,.ee-card-rate,.ee-card-side{grid-column:1;grid-row:auto}"
    ".ee-card-side{margin-top:1rem}}"
    "@media (max-width:44rem){main{padding:0 1rem 2rem}.ee-mast-in{padding:.6rem 1rem}"
    ".ee-foot{padding:0 1rem}.ee-foot-in{padding-bottom:2rem}"
    ".ee-links{margin-left:0}h1{font-size:2rem;margin-top:1.5rem}"

    ".ee-f{padding-left:0;text-indent:0}.ee-f b{display:inline;width:auto;padding-right:.25rem}"
    ".ee-chart thead{display:none}.ee-chart,.ee-chart tbody,.ee-chart caption{display:block}"
    ".ee-chart tr{display:grid;grid-template-columns:3.5rem minmax(0,1fr);gap:.1rem .75rem;"
    "padding:.6rem .5rem;border-bottom:1px solid var(--border)}"
    ".ee-chart tbody tr:nth-child(even){background:var(--card)}"
    ".ee-chart td{display:block;padding:0;border:0;background:none!important;grid-column:2}"
    ".ee-chart td.ee-c-grade{grid-column:1;grid-row:1/span 4}.ee-chart td.ee-c-study{width:auto}"
    ".ee-chart td[data-name]::before{content:attr(data-name) ': ';color:var(--muted);font-weight:400}"
    "}"
    "@media print{:root{--bg:#fff;--fg:#000;--rule:#000;--link:#000;--card:#fff;--muted:#444;--border:#bbb}body{background:#fff;color:#000}"
    ".ee-mast{background:none;color:#000;border-bottom:3px solid #000}"
    ".ee-mast a,.ee-crumbs li:first-child{color:#000}.ee-filter-box,.ee-foot a{display:none}}"
)

_TIERS = "".join(f".tier-{k}{{--fill:{TIER_FILL[k]}}}" for k in TIER_KEYS)

CSS = (":root{" + _vars(PALETTES["light"]) + "}"
       + "@media (prefers-color-scheme: dark){:root{" + _vars(PALETTES["dark"]) + "}}"
       + _FONTS + _BASE + _TIERS)


# Topic page filters. Reads the study data from #ee-data; only hides, shows and reorders the
# cards already in #ee-studies, so the page lists every study without JS (the form stays hidden).
JS = """(function () {
  var form = document.getElementById('ee-filters');
  var list = document.getElementById('ee-studies');
  var count = document.getElementById('ee-count');
  var dataEl = document.getElementById('ee-data');
  if (!form || !list || !count || !dataEl) return;
  var box = document.getElementById('ee-filter-box');
  var reset = document.getElementById('ee-reset');
  var active = document.getElementById('ee-active');
  var studies = JSON.parse(dataEl.textContent).studies;
  var cards = {};
  Array.prototype.forEach.call(list.querySelectorAll('[data-study]'), function (el) {
    cards[el.getAttribute('data-study')] = el;
  });
  var TIER = {A: 0, B: 1, C: 2, D: 3};
  function rank(s) { return s.tier in TIER ? TIER[s.tier] : 9; }
  function value(name) { var el = form.elements[name]; return el ? el.value : ''; }
  function tiers() {
    var out = [];
    Array.prototype.forEach.call(form.querySelectorAll('input[name=tier]:checked'), function (el) {
      out.push(el.value);
    });
    return out;
  }
  function matches(s, f) {
    if (f.tiers.indexOf(s.tier in TIER ? s.tier : '?') < 0) return false;
    if (f.flag === '*' && !s.flags.length) return false;
    if (f.flag && f.flag !== '*' && s.flags.indexOf(f.flag) < 0) return false;
    if (f.relation && s.relation !== f.relation) return false;
    if (f.series && s.series !== f.series) return false;
    if (f.subtopic && s.subtopic !== f.subtopic) return false;
    if (f.metric && s.metric !== f.metric) return false;
    if (f.entity && (s.entities || []).indexOf(f.entity) < 0) return false;
    if (f.disputed && !s.disputed) return false;
    if (f.q && s.text.indexOf(f.q) < 0) return false;
    return true;
  }
  function ordered(sort) {
    var rows = studies.slice();
    if (sort === 'tier') {
      rows.sort(function (a, b) { return rank(a) - rank(b) || a.pos - b.pos; });
    } else if (sort === 'first') {
      rows.sort(function (a, b) {
        if (a.first_reported === b.first_reported) return a.pos - b.pos;
        return a.first_reported < b.first_reported ? -1 : 1;
      });
    } else {
      rows.sort(function (a, b) { return a.pos - b.pos; });
    }
    return rows;
  }
  function marked() {   // highlights every field that narrows or reorders the list; returns how many
    var n = 0;
    Array.prototype.forEach.call(form.querySelectorAll('select, input[type=search], input[name=disputed]'),
      function (el) {
        var on = el.type === 'checkbox' ? el.checked
          : el.name === 'sort' ? el.value !== 'newest' : el.value.trim() !== '';
        var label = el.closest('label');
        if (label) label.classList.toggle('ee-on', on);
        if (on) n += 1;
      });
    var tiersOn = form.querySelectorAll('input[name=tier]:not(:checked)').length > 0;
    var fieldset = form.querySelector('fieldset');
    if (fieldset) fieldset.classList.toggle('ee-on', tiersOn);
    return n + (tiersOn ? 1 : 0);
  }
  function apply() {
    var f = {tiers: tiers(), flag: value('flag'), relation: value('relation'),
             series: value('series'), subtopic: value('subtopic'), metric: value('metric'), entity: value('entity'), disputed: form.elements['disputed'].checked,
             q: value('q').trim().toLowerCase()};
    var shown = 0;
    ordered(value('sort')).forEach(function (s) {
      var el = cards[String(s.id)];
      if (!el) return;
      var ok = matches(s, f);
      el.hidden = !ok;
      if (ok) shown += 1;
      el.classList.toggle('ee-alt', ok && shown % 2 === 0);
      list.appendChild(el);
    });
    count.textContent = shown + ' of ' + studies.length + ' studies';
    var n = marked();
    if (reset) reset.hidden = n === 0;
    if (active) active.textContent = n ? '(' + n + ' active)' : '';
  }
  list.classList.add('ee-js');
  if (box) box.hidden = false;
  if (reset) reset.addEventListener('click', function () { form.reset(); apply(); });
  form.hidden = false;
  form.addEventListener('input', apply);
  form.addEventListener('change', apply);
  form.addEventListener('submit', function (event) { event.preventDefault(); });
  apply();
})();"""

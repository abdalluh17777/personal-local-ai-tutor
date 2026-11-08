// The tutor's replies as HTML. The text is escaped first, so the markup below is the only HTML that can appear.

export function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const link = (url, text) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${text}</a>`;

// A citation label [S2] becomes a small numbered chip; the chat gives it the passage's name and opens the Sources
// sheet on it (the label stays in data-label)
const cite = n => `<button type="button" class="cite" data-label="S${n}" aria-label="Source ${n}">${n}</button>`;

// Code spans and links are set aside first (as \u0000n\u0001), so emphasis never applies inside a URL or code
function inlineMd(s) {
  const kept = [];
  const keep = html => `\u0000${kept.push(html) - 1}\u0001`;
  return s
    .replace(/`([^`\n]+)`/g, (_, code) => keep(`<code>${code}</code>`))
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, text, url) => keep(link(url, text)))
    // A bare address; quotes (escaped already) and trailing punctuation are not part of it
    .replace(/https?:\/\/(?:(?!&quot;|&#39;|&lt;|&gt;)[^\s\u0000\u0001])+/g, url => {
      let tail = url.match(/[.,;:!?)\]]+$/)?.[0] || "";
      let bare = url.slice(0, url.length - tail.length);
      // A closing bracket the address opened is part of it (…/Habit_(psychology))
      if (tail.startsWith(")") && (bare.split("(").length > bare.split(")").length)) { bare += ")"; tail = tail.slice(1); }
      return keep(link(bare, bare)) + tail;
    })
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/\s*\[S(\d+)\]/g, (_, n) => " " + cite(n))   // a no-break space: the chip stays with its word
    .replace(/\u0000(\d+)\u0001/g, (_, i) => kept[i]);
}

// Every block gets its own direction (dir="auto"): a reply that opens with an Arabic line keeps its English
// paragraphs left to right. A list takes one direction from its items (on <ul>/<ol>, not on each <li>: dir="auto"
// does not look inside children that have their own dir, and an Arabic list would keep its indent on the left,
// its bullets outside the message). Code is always left to right.
export function renderMarkdown(text) {
  let html = "";
  let list = null;
  let para = [];
  let code = null;
  const flushPara = () => { if (para.length) { html += `<p dir="auto">${inlineMd(para.join(" "))}</p>`; para = []; } };
  const flushList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const line of escapeHtml(text).split("\n")) {
    if (line.trim().startsWith("```")) {
      if (code) { html += `<pre dir="ltr"><code>${code.join("\n")}</code></pre>`; code = null; }
      else { flushPara(); flushList(); code = []; }
      continue;
    }
    if (code) { code.push(line); continue; }
    let m;
    if ((m = line.match(/^\s*(#{1,4})\s+(.*)$/))) {
      flushPara(); flushList();
      const level = Math.min(m[1].length + 2, 5);
      html += `<h${level} dir="auto">${inlineMd(m[2])}</h${level}>`;
    } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flushPara(); flushList();
      html += "<hr>";
    } else if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) {
      flushPara();
      if (list !== "ul") { flushList(); html += `<ul dir="auto">`; list = "ul"; }
      html += `<li>${inlineMd(m[1])}</li>`;
    } else if ((m = line.match(/^\s*(\d+)[.)]\s+(.*)$/))) {
      flushPara();
      // A list the model interrupted with a paragraph goes on with its own numbers
      if (list !== "ol") { flushList(); html += `<ol dir="auto"${m[1] !== "1" ? ` start="${Number(m[1])}"` : ""}>`; list = "ol"; }
      html += `<li>${inlineMd(m[2])}</li>`;
    } else if (!line.trim()) {
      flushPara(); flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  if (code) html += `<pre dir="ltr"><code>${code.join("\n")}</code></pre>`;
  flushPara(); flushList();
  return html;
}

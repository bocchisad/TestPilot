type DemoTest = {
  test_id: string;
  score: string;
  rank: number;
  dur: string;
  reason: string;
};

type DemoCase = {
  head: string;
  subject: string;
  authored_at: string;
  diff: string;
  n_selected: number;
  n_candidates: number;
  saved_ms: number | null;
  caught: boolean | null;
  caught_reason: string | null;
  new_fail_n: number | null;
  new_fail_selected: number | null;
  budget_frac: number;
  synthetic: boolean;
  safe_fallback: boolean;
  safe_reasons: string[];
  recent_k: number | null;
  ranker: string;
  tests: DemoTest[];
};

type DemoFile = {
  placeholder: boolean;
  cases: DemoCase[];
};

type Copy = {
  savedMissing: string;
  caughtMissing: string;
  caughtNone: string;
  caughtYes: string;
  caughtNo: string;
  nothingToCatch: string;
  onlyFlaky: string;
  missedBudget: string;
  planted: string;
  safe: string;
  synthetic: string;
  scoreNote: string;
  reason: Record<string, string>;
  safeReason: Record<string, string>;
};

function gloss(table: Record<string, string>, code: string, recentK: number | null): string | null {
  const text = table[code];
  if (!text) return null;
  if (code === "recent_fail") {
    if (recentK === null) return null;
    return text.replaceAll("{k}", String(recentK));
  }
  return text;
}

function fillDiff(pre: HTMLElement, diff: string) {
  pre.replaceChildren();
  for (const line of diff.split("\n")) {
    const span = document.createElement("span");
    span.textContent = `${line}\n`;
    if (line.startsWith("+") && !line.startsWith("+++")) span.className = "plus";
    else if (line.startsWith("-") && !line.startsWith("---")) span.className = "minus";
    pre.append(span);
  }
}

function fillReasons(list: HTMLElement, codes: string[], table: Record<string, string>) {
  list.replaceChildren();
  for (const code of codes) {
    const item = document.createElement("li");
    const name = document.createElement("code");
    name.textContent = code;
    item.append(name);
    const text = table[code];
    if (text) {
      item.append(` ${text}`);
    } else {
      name.className = "unknown";
    }
    list.append(item);
  }
}

function fillTests(list: HTMLElement, item: DemoCase, copy: Copy) {
  list.replaceChildren();
  for (const test of item.tests) {
    const row = document.createElement("div");
    row.className = "rankrow";
    const num = document.createElement("span");
    num.className = "ranknum";
    num.textContent = String(test.rank);
    const body = document.createElement("div");
    body.className = "testname";
    const id = document.createElement("span");
    id.textContent = test.test_id;
    body.append(id);
    const why = document.createElement("span");
    const text = gloss(copy.reason, test.reason, item.recent_k);
    if (text) {
      why.className = "why";
      const code = document.createElement("code");
      code.textContent = test.reason;
      why.append(code, ` ${text}`);
    } else {
      why.className = "why unknown";
      why.textContent = test.reason;
    }
    body.append(why);
    const score = document.createElement("span");
    score.className = "score";
    score.textContent = test.score;
    row.append(num, body, score);
    list.append(row);
  }
}

function caughtValue(item: DemoCase, copy: Copy): string {
  if (item.caught === true) return copy.caughtYes;
  if (item.caught === false) return copy.caughtNo;
  if (item.caught_reason === "no_outcomes") return copy.caughtMissing;
  return copy.caughtNone;
}

function caughtCaption(item: DemoCase, copy: Copy): string {
  if (item.caught === true && item.synthetic) return copy.planted;
  if (item.caught === false) {
    const pct = Math.round(item.budget_frac * 100);
    return copy.missedBudget
      .replaceAll("{pct}", String(pct))
      .replaceAll("{failed}", String(item.new_fail_n ?? 0))
      .replaceAll("{selected}", String(item.new_fail_selected ?? 0));
  }
  if (item.caught_reason === "nothing_to_catch") return copy.nothingToCatch;
  if (item.caught_reason === "only_flaky") return copy.onlyFlaky;
  return "";
}

function show(index: number, data: DemoFile, copy: Copy) {
  const item = data.cases[index];
  if (!item) return;
  const root = document.getElementById("demo");
  if (!root) return;
  for (const button of root.querySelectorAll<HTMLButtonElement>("[data-case]")) {
    button.setAttribute("aria-pressed", button.dataset.case === String(index) ? "true" : "false");
  }
  const diff = document.getElementById("demo-diff");
  if (diff) fillDiff(diff, item.diff);
  const selected = document.getElementById("demo-selected");
  if (selected) selected.textContent = `${item.n_selected} / ${item.n_candidates}`;
  const saved = document.getElementById("demo-saved");
  if (saved) saved.textContent = item.saved_ms === null ? copy.savedMissing : String(item.saved_ms);
  const caught = document.getElementById("demo-caught");
  if (caught) caught.textContent = caughtValue(item, copy);
  document.getElementById("demo-synthetic-metric")?.toggleAttribute("hidden", !item.synthetic);
  const note = document.getElementById("demo-caught-note");
  if (note) {
    const caption = caughtCaption(item, copy);
    note.hidden = caption === "";
    note.textContent = caption;
  }
  const score = document.getElementById("demo-score-note");
  if (score) score.textContent = copy.scoreNote.replaceAll("{ranker}", item.ranker);
  const tests = document.getElementById("demo-tests");
  if (tests) fillTests(tests, item, copy);
  const safe = document.getElementById("demo-safe");
  const reasons = document.getElementById("demo-safe-reasons");
  if (safe) safe.hidden = !item.safe_fallback;
  if (reasons) fillReasons(reasons, item.safe_reasons, copy.safeReason);
}

function boot() {
  const dataEl = document.getElementById("demo-data");
  const copyEl = document.getElementById("demo-copy");
  const root = document.getElementById("demo");
  if (!dataEl?.textContent || !copyEl?.textContent || !root) return;
  const data = JSON.parse(dataEl.textContent) as DemoFile;
  const copy = JSON.parse(copyEl.textContent) as Copy;
  if (data.placeholder || data.cases.length === 0) return;
  for (const button of root.querySelectorAll<HTMLButtonElement>("[data-case]")) {
    button.addEventListener("click", () => {
      const index = Number(button.dataset.case);
      if (Number.isInteger(index)) show(index, data, copy);
    });
  }
}

boot();

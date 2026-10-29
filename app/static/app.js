/* FeeLens UI.
 *
 * Plain DOM, no framework, no build step. Two rules this file follows:
 *
 * 1. Values are written with textContent, never innerHTML. The API is ours, but
 *    a fee label is still data, and data does not get to become markup.
 * 2. Money is never parsed into a float to be displayed. The API sends decimal
 *    strings; this file groups the digits and leaves the value alone.
 */

const SYMBOLS = { USD: "$", EUR: "€", GBP: "£", PHP: "₱", AUD: "A$", INR: "₹" };

const state = { config: null };

const $ = (id) => document.getElementById(id);

/* --- formatting --------------------------------------------------------- */

function groupDigits(digits) {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** "5148.85" -> "5,148.85". No float arithmetic anywhere. */
function decimalText(value) {
  const raw = String(value);
  const negative = raw.startsWith("-");
  const [whole, fraction = ""] = (negative ? raw.slice(1) : raw).split(".");
  return `${negative ? "-" : ""}${groupDigits(whole)}.${fraction.padEnd(2, "0").slice(0, 2)}`;
}

function money(value, currency) {
  return `${decimalText(value)} ${currency}`;
}

function isZero(value) {
  return String(value).replace(/[-.,0]/g, "") === "";
}

/* --- config ------------------------------------------------------------- */

async function boot() {
  try {
    const response = await fetch("/api/config");
    if (!response.ok) throw new Error("Could not load the fee configuration.");
    state.config = await response.json();
  } catch (error) {
    showError(error.message);
    return;
  }

  renderCorridors();
  renderPaymentOptions();

  $("fee-date").textContent = state.config.fee_retrieved_date;
  $("disclaimer").textContent = state.config.disclaimer;
  $("corridor-help").textContent =
    `You are paid into a ${state.config.receive_region} business account holding ${state.config.receive_currency}.`;

  // The template cannot answer a question, so an enabled box would be a lie:
  // you would type something, press Explain, and get a generic paragraph back.
  if (!state.config.explanation.model_configured) {
    const question = $("explain-question");
    question.disabled = true;
    question.placeholder = "Questions need a model key — this build explains from a fixed template";
    question.title =
      "Set OPENROUTER_API_KEY to ask questions. The template still explains every fee line.";
  }

  syncAmountPrefix();
  $("corridor").addEventListener("change", syncAmountPrefix);
  $("quote-form").addEventListener("submit", onSubmit);
  $("explain-button").addEventListener("click", onExplain);

  // Prose written for one invoice must not survive into another. If any input
  // that changes the price moves, the explanation is cleared rather than left
  // sitting under a table it no longer describes.
  $("amount").addEventListener("input", resetExplanation);
  $("corridor").addEventListener("change", resetExplanation);
  $("payment-options").addEventListener("change", resetExplanation);
}

function renderCorridors() {
  const select = $("corridor");
  for (const corridor of state.config.corridors) {
    const option = document.createElement("option");
    option.value = corridor.invoice_currency;
    option.textContent = `${corridor.country} — ${corridor.currency_name} (${corridor.invoice_currency})`;
    select.append(option);
  }
}

function renderPaymentOptions() {
  const container = $("payment-options");
  let currentGroup = null;

  for (const [index, option] of state.config.payment_options.entries()) {
    if (option.group !== currentGroup) {
      currentGroup = option.group;
      const heading = document.createElement("p");
      heading.className = "option-group";
      heading.textContent = option.group;
      container.append(heading);
    }

    const label = document.createElement("label");
    label.className = "option";

    const input = document.createElement("input");
    input.type = "radio";
    input.name = "payment";
    input.value = option.id;
    input.checked = index === 0;

    const text = document.createElement("span");
    const title = document.createElement("span");
    title.className = "option-label";
    title.textContent = option.label;
    const description = document.createElement("span");
    description.className = "option-desc";
    description.textContent = option.description;
    text.append(title, description);

    // A product caveat is not a fee, so it is not part of the quote. It is
    // shown here, before the choice is made, because a warning that arrives
    // with the result is a warning about a decision already taken.
    if (option.caveat) {
      const caveat = document.createElement("span");
      caveat.className = "option-caveat";
      caveat.textContent = option.caveat;
      text.append(caveat);
    }

    label.append(input, text);
    container.append(label);
  }
}

function syncAmountPrefix() {
  const currency = $("corridor").value || "EUR";
  $("amount-prefix").textContent = SYMBOLS[currency] || currency;
}

/* --- submit ------------------------------------------------------------- */

async function onSubmit(event) {
  event.preventDefault();
  hideError();

  const button = $("submit");
  button.disabled = true;
  button.textContent = "Calculating…";

  try {
    const selected = document.querySelector('input[name="payment"]:checked');
    if (!selected) throw new Error("Choose how the money arrives.");

    const response = await fetch("/api/quote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        amount: $("amount").value,
        corridor: $("corridor").value,
        payment: selected.value,
      }),
    });

    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not calculate that.");

    render(data);
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Calculate";
  }
}

function showError(message) {
  const box = $("form-error");
  box.textContent = message;
  box.hidden = false;
}

function hideError() {
  $("form-error").hidden = true;
}

/* --- render ------------------------------------------------------------- */

function render(data) {
  const current = data.current;
  const invoiceCurrency = data.corridor.invoice_currency;
  const receiveCurrency = current.net_received_currency;

  $("results").hidden = false;

  // The chip is set from the payload, not from markup, so it cannot drift from
  // the data the rest of the card was built from.
  $("tier-chip").textContent = data.tier;

  $("net-received").textContent =
    `${SYMBOLS[receiveCurrency] || ""}${decimalText(current.net_received)}`;
  $("headline-sub").textContent =
    `PayPal keeps ${money(current.total_fees, receiveCurrency)} of the ${money(current.gross_received, receiveCurrency)} you receive.`;

  $("fact-invoice").textContent = money(data.amount, invoiceCurrency);
  $("fact-fees").textContent = money(current.total_fees, receiveCurrency);
  $("fact-rate").textContent = `${current.effective_rate_pct}%`;

  renderBreakdown($("breakdown-body"), current.lines);
  renderPayer(current);
  renderFx(current, data.amount, invoiceCurrency, receiveCurrency);
  renderRail(data, receiveCurrency);
  renderSavings(data, receiveCurrency);
  renderList($("assumptions"), data.assumptions);
  renderList($("rejected"), data.rejected.map((item) => `${item.path_id.replace(/_/g, " ")}: ${item.reason}`));

  $("disclaimer").textContent = data.disclaimer;
  resetExplanation();
  $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* --- explanation --------------------------------------------------------- */

function resetExplanation() {
  $("explain-text").textContent = "";
  $("explain-checks").textContent = "";
  $("explain-checks").classList.remove("bad");
  $("explain-source").hidden = true;
  $("explain-trace-wrap").hidden = true;
  const caveats = $("explain-caveats");
  caveats.replaceChildren();
  caveats.hidden = true;
}

async function onExplain() {
  const button = $("explain-button");
  const selected = document.querySelector('input[name="payment"]:checked');
  if (!selected) return;

  button.disabled = true;
  button.textContent = "Thinking…";

  try {
    const response = await fetch("/api/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        amount: $("amount").value,
        corridor: $("corridor").value,
        payment: selected.value,
        question: $("explain-question").value,
      }),
    });

    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not explain that.");

    renderExplanation(data.explanation);
  } catch (error) {
    resetExplanation();
    $("explain-text").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Explain";
  }
}

function renderExplanation(explanation) {
  const chip = $("explain-source");
  chip.hidden = false;
  chip.textContent = explanation.source === "model" ? "ai" : "template";
  chip.classList.toggle("warm", explanation.source !== "model");

  $("explain-text").textContent = explanation.text;

  const caveats = $("explain-caveats");
  caveats.replaceChildren();
  for (const caveat of explanation.caveats || []) {
    const li = document.createElement("li");
    li.textContent = caveat;
    caveats.append(li);
  }
  caveats.hidden = !(explanation.caveats || []).length;

  const checks = $("explain-checks");
  checks.classList.toggle("bad", !explanation.checks.ok);
  if (explanation.checks.ok) {
    const traced = explanation.checks.numbers_checked;
    const cited = (explanation.checks.rules_cited || []).length;
    const author = explanation.model
      ? `Written by ${explanation.model}.`
      : `Written without a model${explanation.note ? ` — ${explanation.note}` : ""}.`;
    checks.textContent =
      `${traced} figure${traced === 1 ? "" : "s"} traced back to the fee engine, `
      + `${cited} rule${cited === 1 ? "" : "s"} cited. ${author}`;
  } else {
    checks.textContent = explanation.note || "This answer was not shown.";
  }

  const trace = $("explain-trace");
  trace.replaceChildren();
  for (const step of explanation.trace || []) {
    const li = document.createElement("li");
    const args = Object.entries(step.arguments || {})
      .map(([key, value]) => `${key}=${value}`)
      .join(", ");
    li.textContent = args ? `${step.name}(${args})` : `${step.name}()`;
    trace.append(li);
  }
  $("explain-trace-wrap").hidden = !(explanation.trace || []).length;
}

function renderBreakdown(tbody, lines) {
  tbody.replaceChildren();

  for (const line of lines) {
    const row = document.createElement("tr");

    const fee = document.createElement("td");
    const label = document.createElement("span");
    label.textContent = line.label;
    const rule = document.createElement("span");
    rule.className = "rule-id";
    rule.textContent = line.rule_id;
    fee.append(label, rule);

    const percent = document.createElement("td");
    percent.className = "num";
    if (isZero(line.amount)) {
      percent.textContent = "—";
      percent.classList.add("zero");
    } else {
      percent.textContent = money(line.amount, line.currency);
    }

    const fixed = document.createElement("td");
    fixed.className = "num";
    if (isZero(line.fixed_amount)) {
      fixed.textContent = "—";
      fixed.classList.add("zero");
    } else {
      fixed.textContent = money(line.fixed_amount, line.fixed_currency);
    }

    const source = document.createElement("td");
    source.className = "src";
    if (line.source_url.startsWith("https://")) {
      const link = document.createElement("a");
      link.href = line.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = line.verified ? "PayPal" : "unverified";
      source.append(link);
    } else {
      source.textContent = "—";
    }

    row.append(fee, percent, fixed, source);
    tbody.append(row);
  }
}

function renderPayer(current) {
  const card = $("payer-card");
  if (!current.payer_lines.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const tbody = $("payer-body");
  tbody.replaceChildren();
  for (const line of current.payer_lines) {
    const row = document.createElement("tr");
    const fee = document.createElement("td");
    fee.textContent = line.label;
    const amount = document.createElement("td");
    amount.className = "num";
    amount.textContent = money(line.amount, line.currency);
    row.append(fee, amount);
    tbody.append(row);
  }
}

function renderFx(current, amount, invoiceCurrency, receiveCurrency) {
  const note = $("fx-note");
  note.classList.toggle("stale", current.fx_stale);

  if (invoiceCurrency === receiveCurrency) {
    note.textContent = "No currency conversion: the invoice and the receiving account are both in "
      + `${receiveCurrency}.`;
    return;
  }

  const age = current.fx_age_days;
  const ageText = age === 0 ? "today" : age === 1 ? "1 day old" : `${age} days old`;
  note.textContent =
    `${money(amount, invoiceCurrency)} converted to ${money(current.gross_received, receiveCurrency)} `
    + `at the ECB reference rate for ${current.fx_snapshot_date} (${ageText}).`;
}

function renderSavings(data, receiveCurrency) {
  const card = $("savings-card");
  const client = $("savings-client");
  const annual = $("savings-annual");
  const value = data.savings;
  const cheaper = !isZero(value) && String(value).startsWith("-") === false;

  card.hidden = false;
  client.hidden = true;
  annual.hidden = true;

  if (!cheaper) {
    card.classList.add("neutral");
    $("savings-title").textContent = "No cheaper route";
    $("savings-value").textContent =
      `You are already invoicing in ${data.corridor.invoice_currency}, so there is no conversion to remove.`;
    $("savings-caveat").textContent = "";
    return;
  }

  card.classList.remove("neutral");
  const caveat = data.assumptions.find((line) => line.includes("4.00%"));

  $("savings-title").textContent = "A cheaper route — and who pays for it";
  $("savings-value").textContent =
    `Invoice in ${data.optimized.net_received_currency} instead: you keep `
    + `${money(data.optimized.net_received, receiveCurrency)}, which is `
    + `${money(value, data.savings_currency)} more.`;

  // The saving exists because the conversion moved, not because it vanished.
  // Saying who now pays it is the difference between an audit and a pitch.
  const side = data.client_side;
  const net = data.net_savings;
  if (side && side.payer_converts && !isZero(side.total)) {
    let text =
      `Your client pays ${money(side.total, side.currency)} more to fund a `
      + `${side.invoice_currency} invoice from a ${side.payer_currency} balance.`;
    if (net && !isZero(net.magnitude)) {
      if (net.direction === "worse_off") {
        text += ` Net of that, the two of you are `
          + `${money(net.magnitude, net.currency)} worse off on this invoice.`;
      } else if (net.direction === "better_off") {
        text += ` Even after that, the two of you are `
          + `${money(net.magnitude, net.currency)} better off on this invoice.`;
      }
    }
    client.hidden = false;
    client.textContent = text;
  }

  const year = data.savings_if_monthly_for_a_year;
  if (year && !isZero(year.receiver_savings)) {
    let text =
      `Repeated monthly for a year: ${money(year.receiver_savings, data.savings_currency)} `
      + "more for you";
    if (!isZero(year.client_cost)) {
      text += `, ${money(year.client_cost, data.savings_currency)} more for your client`;
    }
    const netYear = year.net;
    if (netYear && netYear.direction === "worse_off") {
      text += `, a net ${money(netYear.magnitude, netYear.currency)} worse for the pair.`;
    } else if (netYear && netYear.direction === "better_off") {
      text += `, a net ${money(netYear.magnitude, netYear.currency)} better for the pair.`;
    } else {
      text += ".";
    }
    annual.hidden = false;
    annual.textContent = text;
  }

  $("savings-caveat").textContent = caveat || "";
}

function renderRail(data, receiveCurrency) {
  const card = $("rail-card");
  const rail = data.rail_comparison;
  if (!rail) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  $("rail-text").textContent =
    `${rail.basis} On this invoice it would take `
    + `${money(rail.fee, rail.currency)} and leave `
    + `${money(rail.net, rail.currency)}, against `
    + `${money(data.current.net_received, receiveCurrency)} through PayPal.`;
}

function renderList(list, items) {
  list.replaceChildren();
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = item;
    list.append(li);
  }
}

boot();

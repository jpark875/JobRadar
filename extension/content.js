(() => {
  if (window.__jobradarLoaded) return;
  window.__jobradarLoaded = true;

  const FIELDS = [
    ["email", /e-?mail/],
    ["first_name", /first.?name|given.?name|\bfname\b|preferred.?name/],
    ["last_name", /last.?name|family.?name|surname|\blname\b/],
    ["full_name", /^name\b|full.?name|your name|legal name|^name\*?$/],
    ["phone", /phone|mobile|\btel\b|cell/],
    ["linkedin", /linkedin/],
    ["github", /github/],
    ["portfolio", /website|portfolio|personal (site|url)|other url|blog/],
    ["city", /\bcity\b|current location|^location|where are you (located|based)/],
    ["school", /school|university|college|institution/],
    ["degree", /\bdegree\b|field of study|major/],
    ["graduation_date", /graduat/],
    ["gpa", /\bgpa\b|grade point/],
  ];
  const SKIP_TYPES = new Set(["hidden", "file", "submit", "button", "checkbox", "radio", "password", "search", "image", "reset"]);

  function api(path, options) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: "api", path, options }, (reply) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        reply && reply.ok ? resolve(reply.data) : reject(new Error(reply ? reply.error : "No response"));
      });
    });
  }

  function labelFor(el) {
    const parts = [];
    if (el.labels) parts.push(...[...el.labels].map((l) => l.innerText));
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      labelledBy.split(/\s+/).forEach((id) => {
        const node = document.getElementById(id);
        if (node) parts.push(node.innerText);
      });
    }
    parts.push(el.getAttribute("aria-label") || "", el.placeholder || "");
    if (!parts.join("").trim()) {
      const container = el.closest("fieldset, .field, .application-question, .form-group, [data-automation-id], li, div");
      const heading = container && container.querySelector("label, legend, .application-label, .text, h3, h4");
      if (heading) parts.push(heading.innerText);
    }
    parts.push(el.name || "", el.id || "");
    return parts.join(" ").replace(/\s+/g, " ").trim();
  }

  function questionText(el) {
    const human = [];
    if (el.labels) human.push(...[...el.labels].map((l) => l.innerText));
    const fieldset = el.closest("fieldset");
    const legend = fieldset && fieldset.querySelector("legend");
    if (legend) human.push(legend.innerText);
    if (!human.join("").trim()) human.push(el.getAttribute("aria-label") || el.placeholder || "");
    if (!human.join("").trim()) {
      const container = el.closest(".field, .application-question, .form-group, [data-automation-id], li");
      const heading = container && container.querySelector("label, .application-label, .text, h3, h4");
      if (heading) human.push(heading.innerText);
    }
    return human.join(" ").replace(/\s+/g, " ").replace(/\*$/, "").trim();
  }

  function setValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
    el.classList.add("jobradar-filled");
  }

  function chooseOption(select, answer) {
    const target = answer.toLowerCase();
    const option = [...select.options].find((o) => o.text.trim().toLowerCase() === target)
      || [...select.options].find((o) => o.text.trim().toLowerCase().startsWith(target));
    if (!option) return false;
    select.value = option.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    select.classList.add("jobradar-filled");
    return true;
  }

  function chooseRadio(group, answer) {
    const target = answer.toLowerCase();
    for (const radio of group) {
      const text = labelFor(radio).toLowerCase();
      if (text === target || text.startsWith(target + " ") || (radio.value || "").toLowerCase() === target) {
        radio.click();
        return true;
      }
    }
    return false;
  }

  function visible(el) {
    return el.offsetParent !== null || el.getClientRects().length > 0;
  }

  async function autofill() {
    const { profile } = await api("/api/autofill");
    let filled = 0;
    const questions = new Map();

    const inputs = [...document.querySelectorAll("input, textarea")].filter(
      (el) => !SKIP_TYPES.has((el.type || "").toLowerCase()) && !el.disabled && !el.readOnly && visible(el)
    );
    for (const el of inputs) {
      if (el.value && el.value.trim()) continue;
      const label = labelFor(el).toLowerCase();
      if (el.type === "email" && profile.email) {
        setValue(el, profile.email);
        filled++;
        continue;
      }
      if (el.type === "tel" && profile.phone) {
        setValue(el, profile.phone);
        filled++;
        continue;
      }
      const match = FIELDS.find(([key, pattern]) => pattern.test(label) && profile[key]);
      if (match) {
        setValue(el, String(profile[match[0]]));
        filled++;
      } else {
        const question = questionText(el);
        if (question.length > 6) questions.set(question, el);
      }
    }

    const choices = [];
    document.querySelectorAll("select").forEach((select) => {
      if (!visible(select) || (select.value && select.selectedIndex > 0)) return;
      const question = questionText(select) || labelFor(select);
      if (question) choices.push([question, (answer) => chooseOption(select, answer)]);
    });
    const radioGroups = {};
    document.querySelectorAll("input[type=radio]").forEach((radio) => {
      if (!radio.name || !visible(radio)) return;
      (radioGroups[radio.name] = radioGroups[radio.name] || []).push(radio);
    });
    Object.values(radioGroups).forEach((group) => {
      if (group.some((r) => r.checked)) return;
      const fieldset = group[0].closest("fieldset");
      const legend = fieldset && fieldset.querySelector("legend");
      const container = group[0].closest(".field, .application-question, [data-automation-id], li, div");
      const heading = legend || (container && container.querySelector("label, .application-label, .text"));
      const question = heading ? heading.innerText.trim() : "";
      if (question) choices.push([question, (answer) => chooseRadio(group, answer)]);
    });

    const allQuestions = [...questions.keys(), ...choices.map(([q]) => q)];
    let coverLetter = "";
    if (allQuestions.length) {
      const result = await api("/api/answer", { method: "POST", body: { ...pageJob(), questions: allQuestions } });
      coverLetter = result.cover_letter;
      for (const [question, el] of questions) {
        if (/cover letter/i.test(question) && el instanceof HTMLTextAreaElement) {
          setValue(el, coverLetter);
          filled++;
        } else if (result.answers[question]) {
          setValue(el, result.answers[question]);
          filled++;
        }
      }
      for (const [question, choose] of choices) {
        const answer = result.answers[question];
        if (answer && choose(answer)) filled++;
      }
    }

    const hasResumeUpload = !!document.querySelector("input[type=file]");
    const tip = hasResumeUpload && profile.resume_filename ? ` Attach ${profile.resume_filename} manually.` : "";
    toast(`Filled ${filled} field${filled === 1 ? "" : "s"}.${tip}`, coverLetter);
  }

  const ATS_SLUGS = [
    [/greenhouse\.io$/, (u) => u.pathname.split("/")[1]],
    [/^jobs\.lever\.co$/, (u) => u.pathname.split("/")[1]],
    [/^jobs\.ashbyhq\.com$/, (u) => u.pathname.split("/")[1]],
    [/^jobs\.smartrecruiters\.com$/, (u) => u.pathname.split("/")[1]],
    [/^apply\.workable\.com$/, (u) => u.pathname.split("/")[1]],
    [/^jobs\.jobvite\.com$/, (u) => u.pathname.split("/")[1]],
    [/myworkdayjobs\.com$|bamboohr\.com$|breezy\.hr$|recruitee\.com$|icims\.com$/, (u) => u.hostname.split(".")[0].replace(/^careers-/, "")],
  ];

  function companyFromPage() {
    const meta = document.querySelector("meta[property='og:site_name']");
    if (meta && meta.content && !/greenhouse|lever|ashby|workday|workable|smartrecruiters/i.test(meta.content)) return meta.content;
    const url = new URL(location.href);
    const rule = ATS_SLUGS.find(([pattern]) => pattern.test(url.hostname));
    const slug = rule ? rule[1](url) : "";
    return (slug || "").replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function pageJob() {
    const heading = document.querySelector("h1, .posting-headline h2, [data-automation-id=jobPostingHeader]");
    const title = (heading && heading.innerText.trim()) || document.title.split(/[|\-–]/)[0].trim();
    const description = (document.querySelector("#content, .content, .posting-page, [data-automation-id=jobPostingDescription], main") || document.body).innerText.slice(0, 20000);
    return { url: location.href, title, company: companyFromPage(), description };
  }

  function toast(message, copyText) {
    document.querySelector(".jobradar-toast")?.remove();
    const box = document.createElement("div");
    box.className = "jobradar-toast";
    box.textContent = message;
    if (copyText) {
      const button = document.createElement("button");
      button.textContent = "Copy cover letter";
      button.onclick = () => navigator.clipboard.writeText(copyText).then(() => (button.textContent = "Copied"));
      box.appendChild(button);
    }
    const close = document.createElement("button");
    close.textContent = "×";
    close.onclick = () => box.remove();
    box.appendChild(close);
    document.body.appendChild(box);
    setTimeout(() => box.remove(), 12000);
  }

  function addButton() {
    if (document.querySelector(".jobradar-fab")) return;
    const button = document.createElement("button");
    button.className = "jobradar-fab";
    button.textContent = "Autofill with JobRadar";
    button.onclick = () => autofill().catch((error) => toast(error.message));
    document.body.appendChild(button);
  }

  let recorded = false;
  document.addEventListener("submit", () => {
    if (recorded) return;
    recorded = true;
    api("/api/jobs/capture", { method: "POST", body: { ...pageJob(), applied: true } })
      .then(() => toast("Marked as applied in JobRadar"))
      .catch(() => {});
  }, true);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "autofill") {
      autofill().then(() => sendResponse({ ok: true })).catch((error) => sendResponse({ ok: false, error: error.message }));
      return true;
    }
    if (message.type === "page-job") {
      sendResponse(pageJob());
    } else if (message.type === "ping") {
      sendResponse({ ok: true });
    }
    return false;
  });

  if (document.querySelector("form input, form textarea")) addButton();
  new MutationObserver(() => {
    if (document.querySelector("form input, form textarea")) addButton();
  }).observe(document.body, { childList: true, subtree: true });
})();

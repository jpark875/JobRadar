const $ = (id) => document.getElementById(id);

function api(path, options) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({ type: "api", path, options }, (reply) => {
      reply && reply.ok ? resolve(reply.data) : reject(new Error(reply ? reply.error : "No response"));
    });
  });
}

function status(text, isError) {
  $("status").textContent = text;
  $("status").style.color = isError ? "#b91c1c" : "#15803d";
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function ensureContentScript(tab) {
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "ping" });
  } catch {
    await chrome.scripting.insertCSS({ target: { tabId: tab.id }, files: ["content.css"] });
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  }
}

async function pageJob(tab) {
  await ensureContentScript(tab);
  return chrome.tabs.sendMessage(tab.id, { type: "page-job" });
}

async function refresh() {
  const { server } = await chrome.storage.sync.get({ server: "http://127.0.0.1:8765" });
  $("server").value = server;
  $("open").onclick = (event) => {
    event.preventDefault();
    chrome.tabs.create({ url: server });
  };
  try {
    const data = await api("/api/queue");
    $("progress").textContent = `${data.applied_today}/${data.goal}`;
    $("bar").style.width = `${Math.min(100, (data.applied_today / Math.max(1, data.goal)) * 100)}%`;
    $("queue").innerHTML = "";
    data.queue.slice(0, 5).forEach((job) => {
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.href = job.url;
      link.textContent = `${job.title} · ${job.company}`;
      link.onclick = (event) => {
        event.preventDefault();
        chrome.tabs.create({ url: job.url });
      };
      li.appendChild(link);
      $("queue").appendChild(li);
    });
    if (!data.queue.length) $("queue").innerHTML = '<li class="muted">Queue is empty</li>';
  } catch (error) {
    $("progress").textContent = "offline";
    status("Can't reach JobRadar. Start it with `jobradar serve`.", true);
  }

  const tab = await activeTab();
  if (!tab || !/^https?:/.test(tab.url || "")) {
    $("job").textContent = "Open a job posting to save or autofill it.";
    return;
  }
  try {
    const { job } = await api(`/api/lookup?url=${encodeURIComponent(tab.url)}`);
    $("job").textContent = job ? `In JobRadar: ${job.title} (${Math.round(job.score)}% match, ${job.status})` : "Not in JobRadar yet.";
  } catch {
    $("job").textContent = "";
  }
}

$("server").addEventListener("change", async () => {
  await chrome.storage.sync.set({ server: $("server").value.trim() || "http://127.0.0.1:8765" });
  refresh();
});

$("autofill").addEventListener("click", async () => {
  const tab = await activeTab();
  try {
    await ensureContentScript(tab);
    const reply = await chrome.tabs.sendMessage(tab.id, { type: "autofill" });
    reply && reply.ok ? status("Filled. Review everything before submitting.") : status(reply.error, true);
  } catch (error) {
    status(error.message, true);
  }
});

async function capture(applied) {
  const tab = await activeTab();
  try {
    const job = await pageJob(tab);
    const saved = await api("/api/jobs/capture", { method: "POST", body: { ...job, applied } });
    status(applied ? `Marked applied: ${saved.title}` : `Saved: ${saved.title} (${Math.round(saved.score)}% match)`);
    refresh();
  } catch (error) {
    status(error.message === "duplicate" ? "Already in JobRadar" : error.message, error.message !== "duplicate");
  }
}

$("save").addEventListener("click", () => capture(false));
$("applied").addEventListener("click", () => capture(true));

refresh();

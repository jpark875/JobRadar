let pendingApply = null;

document.addEventListener("click", (event) => {
  const copy = event.target.closest("[data-copy]");
  if (copy) {
    event.preventDefault();
    const source = document.querySelector(copy.dataset.copy);
    const text = source.value !== undefined ? source.value : source.innerText;
    navigator.clipboard.writeText(text).then(() => {
      const label = copy.textContent;
      copy.textContent = "Copied";
      setTimeout(() => (copy.textContent = label), 1200);
    });
    return;
  }

  const confirmButton = event.target.closest("button[data-confirm]");
  if (confirmButton && !confirm(confirmButton.dataset.confirm)) {
    event.preventDefault();
    return;
  }

  const apply = event.target.closest("[data-open-apply]");
  if (apply) {
    pendingApply = { id: apply.dataset.openApply, title: document.title };
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form.dataset.confirm && !confirm(form.dataset.confirm)) {
    event.preventDefault();
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-autosubmit]")) {
    event.target.form.submit();
  }
});

function showAppliedPrompt(jobId) {
  document.querySelector(".toast")?.remove();
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.innerHTML = `
    <span>Did you submit the application?</span>
    <form method="post" action="/jobs/${jobId}/status">
      <input type="hidden" name="status" value="applied">
      <button class="btn small">Yes, mark applied</button>
    </form>
    <button class="btn small ghost" data-dismiss-toast>Not yet</button>`;
  toast.querySelector("[data-dismiss-toast]").addEventListener("click", () => toast.remove());
  document.body.appendChild(toast);
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && pendingApply) {
    showAppliedPrompt(pendingApply.id);
    pendingApply = null;
  }
});

const selectAll = document.querySelector("[data-select-all]");
const counter = document.querySelector("[data-selected-count]");
function updateCount() {
  if (!counter) return;
  const n = document.querySelectorAll("[data-select]:checked").length;
  counter.textContent = `${n} selected`;
}
if (selectAll) {
  selectAll.addEventListener("change", () => {
    document.querySelectorAll("[data-select]").forEach((box) => (box.checked = selectAll.checked));
    updateCount();
  });
}
document.querySelectorAll("[data-select]").forEach((box) => box.addEventListener("change", updateCount));

let lastChecked = null;
document.querySelectorAll("[data-select]").forEach((box) => {
  box.addEventListener("click", (event) => {
    if (event.shiftKey && lastChecked) {
      const boxes = [...document.querySelectorAll("[data-select]")];
      const [a, b] = [boxes.indexOf(lastChecked), boxes.indexOf(box)].sort((x, y) => x - y);
      boxes.slice(a, b + 1).forEach((item) => (item.checked = box.checked));
      updateCount();
    }
    lastChecked = box;
  });
});

document.querySelectorAll("[data-autohide]").forEach((el) => {
  setTimeout(() => {
    el.style.transition = "opacity .4s";
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 400);
  }, 6000);
});

document.querySelectorAll("[data-add-to]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const field = document.querySelector(chip.dataset.addTo);
    const lines = field.value.split("\n").map((l) => l.trim()).filter(Boolean);
    if (!lines.includes(chip.dataset.value)) lines.push(chip.dataset.value);
    field.value = lines.join("\n");
    chip.classList.add("picked");
  });
});

document.querySelectorAll("[data-dropzone]").forEach((zone) => {
  const input = zone.querySelector("input[type=file]");
  const label = zone.querySelector("[data-filename]");
  const show = () => { if (label && input.files.length) label.textContent = input.files[0].name; };
  input.addEventListener("change", () => { show(); if (zone.dataset.autosubmit !== undefined) zone.closest("form").submit(); });
  ["dragenter", "dragover"].forEach((type) => zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((type) => zone.addEventListener(type, () => zone.classList.remove("over")));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    input.files = e.dataTransfer.files;
    input.dispatchEvent(new Event("change"));
  });
});

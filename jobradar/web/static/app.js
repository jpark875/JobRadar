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

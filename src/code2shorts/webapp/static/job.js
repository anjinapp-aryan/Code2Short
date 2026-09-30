/* Job page poller.
 *
 * Every value it renders comes from /api/jobs/<id>, which reports the
 * workflow runner's real per-node state. Nothing here advances a bar on a
 * timer: if the backend says a stage is still running, the bar does not
 * move.
 *
 * Text is written with textContent, never innerHTML - stage details and
 * log lines can carry provider error text, which is untrusted.
 */
(function () {
  "use strict";

  var root = document.querySelector(".job");
  if (!root) return;

  var jobId = root.dataset.job;
  var barFill = document.getElementById("bar-fill");
  var bar = document.getElementById("bar");
  var pct = document.getElementById("pct");
  var stagesEl = document.getElementById("stages");
  var logsEl = document.getElementById("logs");
  var headline = document.getElementById("headline");
  var watch = document.getElementById("watch");
  var cancelBtn = document.getElementById("cancel");

  var POLL_MS = 1500;
  var BACKOFF_MAX_MS = 15000;
  var delay = POLL_MS;

  document.getElementById("toggle-logs").addEventListener("click", function () {
    logsEl.classList.toggle("is-hidden");
    this.textContent = logsEl.classList.contains("is-hidden")
      ? "View logs" : "Hide logs";
  });

  cancelBtn.addEventListener("click", function () {
    cancelBtn.disabled = true;
    cancelBtn.textContent = "Cancelling…";
    fetch("/api/jobs/" + encodeURIComponent(jobId) + "/cancel", { method: "POST" });
  });

  function renderStages(stages) {
    var list = document.createElement("ol");
    list.className = "stages";
    stages.forEach(function (stage) {
      var item = document.createElement("li");
      item.className = "stage stage-" + stage.state;

      var mark = document.createElement("span");
      mark.className = "stage-mark";
      mark.setAttribute("aria-hidden", "true");
      item.appendChild(mark);

      var label = document.createElement("span");
      label.className = "stage-label";
      label.textContent = stage.label;
      item.appendChild(label);

      if (stage.detail) {
        var detail = document.createElement("span");
        detail.className = "stage-detail";
        detail.textContent = stage.detail;
        item.appendChild(detail);
      }
      list.appendChild(item);
    });
    stagesEl.replaceChildren(list);
  }

  function finish(job) {
    if (job.status === "completed" && job.version) {
      headline.textContent = "Your teaching video is ready.";
      watch.href = "/videos/" + encodeURIComponent(job.algorithm) + "/v" + job.version;
      watch.classList.remove("is-hidden");
    } else if (job.status === "failed") {
      headline.textContent = job.error || "Generation failed.";
    } else {
      headline.textContent = "Generation " + job.status + ".";
    }
    cancelBtn.classList.add("is-hidden");
  }

  function poll() {
    fetch("/api/jobs/" + encodeURIComponent(jobId), { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error("status " + response.status);
        return response.json();
      })
      .then(function (job) {
        delay = POLL_MS;
        barFill.style.width = job.progress + "%";
        bar.setAttribute("aria-valuenow", String(job.progress));
        pct.textContent = String(job.progress);
        renderStages(job.stages);
        logsEl.textContent = (job.logs || []).join("\n");

        if (job.finished) {
          finish(job);
          return;
        }
        window.setTimeout(poll, delay);
      })
      .catch(function () {
        // A dropped request must not kill the page. Back off rather than
        // hammering a server that is busy running Manim.
        delay = Math.min(delay * 2, BACKOFF_MAX_MS);
        window.setTimeout(poll, delay);
      });
  }

  poll();
})();

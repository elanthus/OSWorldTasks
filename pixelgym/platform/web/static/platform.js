// Progressive enhancement for the control plane. Every page works without this script.
// Served from /static so the "default-src 'self'" Content-Security-Policy allows it; it
// writes only through textContent and setAttribute and never builds HTML from strings.
"use strict";

(function () {
  var POLL_INTERVAL_MS = 2000;
  // Stop polling after one hour; a reload resumes it.
  var MAX_POLLS = 1800;
  var LINKABLE_SCHEMES = ["http:", "https:", "s3:"];

  function focusErrorSummary() {
    var summary = document.querySelector(".error-summary");
    if (summary) {
      summary.focus();
    }
  }

  function setSubmitting(form, submitting) {
    var buttons = form.querySelectorAll('button[type="submit"]');
    for (var index = 0; index < buttons.length; index += 1) {
      buttons[index].disabled = submitting;
      if (submitting) {
        buttons[index].setAttribute("aria-disabled", "true");
      } else {
        buttons[index].removeAttribute("aria-disabled");
      }
    }
    if (submitting) {
      form.setAttribute("data-submitting", "true");
    } else {
      form.removeAttribute("data-submitting");
    }
  }

  // A double click or repeated Enter sends a state-changing form once.
  function guardDuplicateSubmission() {
    document.addEventListener("submit", function (event) {
      var form = event.target;
      if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== "post") {
        return;
      }
      if (form.hasAttribute("data-submitting")) {
        event.preventDefault();
        return;
      }
      setSubmitting(form, true);
    });
    // A page restored from the back/forward cache must not keep its buttons disabled.
    window.addEventListener("pageshow", function (event) {
      if (!event.persisted) {
        return;
      }
      var forms = document.querySelectorAll("form[data-submitting]");
      for (var index = 0; index < forms.length; index += 1) {
        setSubmitting(forms[index], false);
      }
    });
  }

  function statusTone(status) {
    if (status === "Complete") {
      return "good";
    }
    if (status === "Failed" || status === "Cancelled") {
      return "bad";
    }
    return "neutral";
  }

  function mutedText(text) {
    var span = document.createElement("span");
    span.className = "muted";
    span.textContent = text;
    return span;
  }

  function isLinkable(uri) {
    try {
      return LINKABLE_SCHEMES.indexOf(new URL(uri, window.location.href).protocol) !== -1;
    } catch (error) {
      return false;
    }
  }

  function replaceChildren(element, child) {
    while (element.firstChild) {
      element.removeChild(element.firstChild);
    }
    element.appendChild(child);
  }

  function live(container, name) {
    return container.querySelector('[data-live="' + name + '"]') ||
      document.querySelector('[data-live="' + name + '"]');
  }

  function applyStatus(container, data) {
    var status = live(container, "status");
    if (status && status.textContent !== data.status) {
      status.textContent = data.status;
      var badgeSlot = live(container, "status-badge");
      if (badgeSlot) {
        var badge = document.createElement("span");
        badge.className = "badge badge--" + statusTone(data.status);
        badge.textContent = data.status;
        replaceChildren(badgeSlot, badge);
      }
    }
    var pathspec = live(container, "pathspec");
    if (pathspec) {
      pathspec.textContent =
        data.metaflow_pathspec || (data.cancellable ? "pending" : "not recorded");
    }
    var mlflow = live(container, "mlflow");
    if (mlflow) {
      if (data.mlflow_uri && isLinkable(data.mlflow_uri)) {
        var link = document.createElement("a");
        link.setAttribute("href", data.mlflow_uri);
        link.textContent = "MLflow run ↗";
        replaceChildren(mlflow, link);
      } else {
        replaceChildren(
          mlflow,
          mutedText(data.cancellable ? "MLflow run pending" : "No MLflow run recorded")
        );
      }
    }
    var candidate = live(container, "candidate");
    if (candidate) {
      if (data.candidate_id) {
        var candidateLink = document.createElement("a");
        candidateLink.setAttribute(
          "href",
          "/candidates/" + encodeURIComponent(data.candidate_id)
        );
        candidateLink.textContent = data.candidate_id;
        replaceChildren(candidate, candidateLink);
      } else {
        replaceChildren(candidate, mutedText("No candidate registered"));
      }
    }
    var partial = live(container, "partial-evidence");
    if (partial) {
      partial.hidden = !(
        !data.candidate_id &&
        data.mlflow_uri &&
        (data.status === "Failed" || data.status === "Cancelled")
      );
    }
    if (!data.cancellable) {
      var form = live(container, "cancel-form");
      var closed = live(container, "cancel-closed");
      if (form) {
        form.hidden = true;
      }
      if (closed) {
        closed.hidden = false;
      }
    }
  }

  // Bounded polling: each request is one short status read; nothing waits on the evaluation.
  function pollSubmissionStatus() {
    var container = document.querySelector("[data-submission-status-url]");
    if (!container) {
      return;
    }
    var url = container.getAttribute("data-submission-status-url");
    var polls = 0;

    function schedule() {
      if (polls < MAX_POLLS) {
        window.setTimeout(poll, POLL_INTERVAL_MS);
      }
    }

    function poll() {
      polls += 1;
      window
        .fetch(url, {
          cache: "no-store",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("status request failed");
          }
          return response.json();
        })
        .then(function (data) {
          applyStatus(container, data);
          if (data.cancellable) {
            schedule();
          }
        })
        .catch(schedule);
    }

    schedule();
  }

  function start() {
    focusErrorSummary();
    guardDuplicateSubmission();
    pollSubmissionStatus();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

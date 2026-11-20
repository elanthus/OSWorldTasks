(function () {
  "use strict";

  // Keep this text synchronized with ui.INCOMPLETE_SUBMISSION_MESSAGE. capture.py imports that
  // Python constant and asserts the settled rendered state before retaining a dataset record.
  var INCOMPLETE_SUBMISSION_MESSAGE = "Complete all required fields before submitting.";
  var INCOMPLETE_SUBMISSION_RECORDING_FAILED_MESSAGE =
    INCOMPLETE_SUBMISSION_MESSAGE + " Submission attempt was not recorded.";

  function renderRequestCard(fields) {
    document.getElementById("rc-company_name").textContent = fields.company_name;
    document.getElementById("rc-contact_email").textContent = fields.contact_email;
    document.getElementById("rc-contact_phone").textContent = fields.contact_phone;
    document.getElementById("rc-tax_id").textContent = fields.tax_id;
    document.getElementById("rc-country").textContent = fields.country;
    document.getElementById("rc-payment_terms").textContent = fields.payment_terms;
    document.getElementById("rc-expedited_onboarding").textContent = fields.expedited_onboarding
      ? "Yes"
      : "No";
  }

  function populateCountryOptions(options) {
    var select = document.getElementById("country");
    options.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
  }

  function populatePaymentTermsOptions(options) {
    var container = document.getElementById("payment_terms");
    options.forEach(function (value, index) {
      var id = "payment_terms_" + index;
      var label = document.createElement("label");
      var input = document.createElement("input");
      input.type = "radio";
      input.name = "payment_terms";
      input.id = id;
      input.value = value;
      label.appendChild(input);
      label.appendChild(document.createTextNode(value));
      container.appendChild(label);
    });
  }

  function readForm() {
    var form = document.getElementById("vendor-form");
    var selectedTerms = form.querySelector('input[name="payment_terms"]:checked');
    return {
      task_id: form.task_id.value,
      company_name: form.company_name.value,
      contact_email: form.contact_email.value,
      contact_phone: form.contact_phone.value,
      tax_id: form.tax_id.value,
      country: form.country.value,
      payment_terms: selectedTerms ? selectedTerms.value : "",
      expedited_onboarding: form.expedited_onboarding.checked,
    };
  }

  function showStatus(message) {
    document.getElementById("submit-status").textContent = message;
  }

  function isComplete(values) {
    return Boolean(
      values.company_name &&
        values.contact_email &&
        values.contact_phone &&
        values.tax_id &&
        values.country &&
        values.payment_terms,
    );
  }

  function markInitializationError(stage) {
    delete document.body.dataset.pixelgymReady;
    document.body.dataset.pixelgymReadyError = stage;
    showStatus("Page initialization failed: " + stage + ".");
  }

  async function initializePage() {
    var failureStage = "task-fetch";
    try {
      var response = await fetch("/api/task");
      if (!response.ok) {
        throw new Error("no active task");
      }
      var task = await response.json();

      failureStage = "render";
      document.getElementById("task_id").value = task.task_id;
      renderRequestCard(task.fields);
      populateCountryOptions(task.options.country);
      populatePaymentTermsOptions(task.options.payment_terms);

      failureStage = "font-load";
      var loadedFaces = await Promise.all([
        document.fonts.load('14px "PixelGym Sans"'),
        document.fonts.load('bold 14px "PixelGym Sans"'),
      ]);
      if (
        !loadedFaces.every(function (faces) {
          return faces.length === 1 && faces[0].status === "loaded";
        })
      ) {
        throw new Error("deterministic font face did not load");
      }

      failureStage = "page-ready";
      response = await fetch("/api/page-ready", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: task.task_id }),
      });
      if (!response.ok) {
        throw new Error("page-ready report failed");
      }
      var ready = await response.json();
      if (ready.ready !== true || typeof ready.page !== "number") {
        throw new Error("page-ready report was not acknowledged");
      }
      pageToken = ready.page;
      document.body.dataset.pixelgymReady = "true";
    } catch (_error) {
      markInitializationError(failureStage);
    }
  }

  // -- Input contract (ui.INPUT_CONTRACT_VERSION) ---------------------------------------------
  //
  // The fake backend models this page from Python (pixelgym/tasks/vendor_form/ui.py). Wherever
  // native Chromium behaviour depends on the platform, a timer, or state that is invisible in
  // pixels, this page pins it to the shared rule instead:
  // - Tab walks TAB_ORDER; Tab from Submit leaves the form and a further Tab does nothing until
  //   a control gains focus again. No native sequential-navigation starting point applies.
  // - Text inputs keep one collapsed caret at the end of the value (the native caret is
  //   transparent, so a movable caret would be hidden state). Arrow keys are inert in text inputs.
  // - The focused text input shows a static, non-blinking caret element right after the last
  //   character, so a trailing space is visible (v3). See updateCaret.
  // - The country select handles ArrowUp/ArrowDown (clamped, never back onto the placeholder),
  //   ignores ArrowLeft/ArrowRight, and treats each printable key as a fresh first-letter
  //   search. Native arrows open the popup on macOS and native type-ahead is timed. Enter and
  //   Space are inert on the select (v3): natively they open the popup on some platforms.
  // Keep this text synchronized with ui.INPUT_CONTRACT_VERSION.
  var INPUT_CONTRACT_VERSION = "pixelgym-vendor-form-input-v3";
  var TAB_ORDER = [
    "company_name",
    "contact_email",
    "contact_phone",
    "tax_id",
    "country",
    "payment_terms",
    "expedited_onboarding",
    "submit-button",
  ];
  var tabExitedForm = false;

  function isTextInput(element) {
    return Boolean(element) && element.tagName === "INPUT" && element.type === "text";
  }

  function pinCaretToEnd(element) {
    var end = element.value.length;
    if (element.selectionStart !== end || element.selectionEnd !== end) {
      element.setSelectionRange(end, end);
    }
  }

  // The caret is an ordinary absolutely positioned element with no animation, transition, or
  // timer. It moves only when this function runs, which happens on focus changes and input
  // events, so between inputs it is exactly as static as the rest of the page (invariant 10).
  var CARET_HEIGHT = 16;

  function updateCaret() {
    var caret = document.getElementById("pixelgym-caret");
    var active = document.activeElement;
    if (!isTextInput(active)) {
      caret.style.display = "none";
      return;
    }
    var mirror = document.getElementById("pixelgym-caret-mirror");
    mirror.textContent = active.value;
    var textWidth = mirror.getBoundingClientRect().width;
    var box = active.getBoundingClientRect();
    var style = window.getComputedStyle(active);
    var contentLeft = box.left + active.clientLeft + parseFloat(style.paddingLeft);
    var contentRight =
      box.left + active.clientLeft + active.clientWidth - parseFloat(style.paddingRight);
    // A value wider than the field scrolls so its end stays in view; the caret then sits at the
    // right edge of the content box. Whole pixels keep the 1px caret free of anti-aliasing.
    var x = Math.round(Math.min(contentLeft + textWidth, contentRight - 1));
    caret.style.left = x + "px";
    caret.style.top = Math.round(box.top + (box.height - CARET_HEIGHT) / 2) + "px";
    caret.style.display = "block";
  }

  function tabStopOf(element) {
    if (!element || element === document.body) {
      return null;
    }
    if (element.type === "radio" && element.name === "payment_terms") {
      return "payment_terms";
    }
    return TAB_ORDER.indexOf(element.id) === -1 ? null : element.id;
  }

  function focusTabStop(stop) {
    if (stop === "payment_terms") {
      var radios = document.querySelectorAll('input[name="payment_terms"]');
      var checked = document.querySelector('input[name="payment_terms"]:checked');
      (checked || radios[0]).focus();
      return;
    }
    document.getElementById(stop).focus();
  }

  function advanceFocus() {
    var current = tabStopOf(document.activeElement);
    if (current === null) {
      if (!tabExitedForm) {
        focusTabStop(TAB_ORDER[0]);
      }
      return;
    }
    if (current === "submit-button") {
      document.activeElement.blur();
      tabExitedForm = true;
      return;
    }
    focusTabStop(TAB_ORDER[TAB_ORDER.indexOf(current) + 1]);
  }

  function moveCountry(select, key) {
    // Option 0 is the disabled placeholder; options 1..n are the countries.
    var last = select.options.length - 1;
    if (key === "ArrowDown") {
      select.selectedIndex = Math.min(Math.max(select.selectedIndex + 1, 1), last);
    } else if (key === "ArrowUp" && select.selectedIndex > 1) {
      select.selectedIndex -= 1;
    }
  }

  function typeAheadCountry(select, key) {
    var folded = key.toLowerCase();
    for (var index = 1; index < select.options.length; index += 1) {
      if (select.options[index].text.toLowerCase().indexOf(folded) === 0) {
        select.selectedIndex = index;
        return;
      }
    }
  }

  function handleContractKeydown(event) {
    var target = event.target;
    if (event.key === "Tab") {
      event.preventDefault();
      advanceFocus();
      return;
    }
    if (isTextInput(target)) {
      if (event.key.indexOf("Arrow") === 0) {
        event.preventDefault();
        return;
      }
      // Insertion and Backspace act on the selection as it stands after keydown dispatch.
      pinCaretToEnd(target);
      return;
    }
    if (target.id === "country") {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
      } else if (event.key.indexOf("Arrow") === 0) {
        event.preventDefault();
        moveCountry(target, event.key);
      } else if (event.key.length === 1 && event.key !== " ") {
        event.preventDefault();
        typeAheadCountry(target, event.key);
      }
    }
  }

  function installInputContract() {
    document.body.dataset.pixelgymInputContract = INPUT_CONTRACT_VERSION;
    document.addEventListener("keydown", handleContractKeydown, true);
    document.addEventListener(
      "mousedown",
      function (event) {
        if (isTextInput(event.target)) {
          // A native mousedown would place the hidden caret under the pointer.
          event.preventDefault();
          event.target.focus();
          pinCaretToEnd(event.target);
        }
      },
      true,
    );
    document.addEventListener(
      "focusin",
      function (event) {
        tabExitedForm = false;
        if (isTextInput(event.target)) {
          pinCaretToEnd(event.target);
        }
        updateCaret();
      },
      true,
    );
    document.addEventListener("focusout", updateCaret, true);
    document.addEventListener("input", updateCaret, true);
    document.addEventListener("selectionchange", function () {
      if (isTextInput(document.activeElement)) {
        pinCaretToEnd(document.activeElement);
      }
    });
  }

  // -- Input acknowledgement barrier --------------------------------------------------------
  //
  // A host backend cannot tell from pixels whether the browser has processed an input yet, or
  // whether a submission the input caused has reached the server: two equal frames look the same
  // before the input is handled as after. Every trusted agent input therefore ends with an
  // acknowledgement that the host counts. The acknowledgement for input N is posted only after
  // (1) the whole input task has run, including the click or implicit submission it caused,
  // (2) any submission POST that input started has settled, and (3) the resulting DOM has been
  // committed to a frame. A host that waits for exactly N acknowledgements after its Nth input
  // can then read submissions and capture the settled frame without any timing assumption.
  // A CLICK ends with one primary-button mouseup and a KEY with one non-modifier keyup.
  var MODIFIER_KEYS = ["Shift", "Control", "Alt", "Meta", "AltGraph", "CapsLock"];
  var inputsProcessed = 0;
  // Issued by the server at page-ready; acknowledgements from an earlier page carry an old token
  // and are refused, so a late one can never stand in for this page's input.
  var pageToken = null;
  var acknowledgementChain = Promise.resolve();
  var pendingSubmission = Promise.resolve();

  function afterCurrentTask() {
    // A message task runs after the current input task without reading any clock.
    return new Promise(function (resolve) {
      var channel = new MessageChannel();
      channel.port1.onmessage = function () {
        channel.port1.close();
        resolve();
      };
      channel.port2.postMessage(null);
    });
  }

  function afterNextFrame() {
    return new Promise(function (resolve) {
      requestAnimationFrame(function () {
        afterCurrentTask().then(resolve);
      });
    });
  }

  function postAcknowledgement(count) {
    return fetch("/api/input-ack", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task_id: document.getElementById("task_id").value,
        page: pageToken,
        count: count,
      }),
    }).catch(function () {
      // A lost acknowledgement leaves the host waiting; it fails closed at its own deadline.
    });
  }

  function acknowledgeInput(event) {
    if (!event.isTrusted) {
      return;
    }
    if (event.type === "keyup" && MODIFIER_KEYS.indexOf(event.key) !== -1) {
      return;
    }
    if (event.type === "mouseup" && event.button !== 0) {
      return;
    }
    inputsProcessed += 1;
    var count = inputsProcessed;
    var previous = acknowledgementChain;
    acknowledgementChain = afterCurrentTask()
      .then(function () {
        // Read only now: a click's submit handler runs after mouseup in the same input task.
        return Promise.all([previous, pendingSubmission]);
      })
      .then(afterNextFrame)
      .then(function () {
        return postAcknowledgement(count);
      });
  }

  function init() {
    initializePage();
    installInputContract();
    window.addEventListener("keyup", acknowledgeInput, true);
    window.addEventListener("mouseup", acknowledgeInput, true);

    document.getElementById("vendor-form").addEventListener("submit", function (event) {
      event.preventDefault();
      var values = readForm();
      var complete = isComplete(values);
      if (!complete) {
        showStatus(INCOMPLETE_SUBMISSION_MESSAGE);
      }
      pendingSubmission = fetch("/api/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("submit failed");
          }
          return response.json();
        })
        .then(function () {
          // Incomplete attempts are still privileged submission events so the evaluator can reject
          // them. Keep their deterministic validation state visible after the event is recorded.
          if (complete) {
            showStatus("Submitted.");
          }
        })
        .catch(function () {
          showStatus(
            complete ? "Submission failed." : INCOMPLETE_SUBMISSION_RECORDING_FAILED_MESSAGE,
          );
        });
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();

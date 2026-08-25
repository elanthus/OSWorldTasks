"use strict";

(() => {
  const payload = window.__V5_TASK__;
  const task = payload.public;
  const internal = payload.internal;
  const root = document.getElementById("task-card");
  const ready = document.getElementById("ready-sentinel");
  const state = {
    stageIndex: 0,
    textValue: "",
    visibleError: null,
    intentionalErrorsEntered: [],
    irreversibleFailure: false,
    submitted: false,
  };

  function advance() {
    state.stageIndex += 1;
    state.textValue = "";
    state.visibleError = null;
    if (state.stageIndex === task.stages.length) state.submitted = true;
    render();
  }

  function fail(message) {
    state.visibleError = message;
    render();
  }

  function activate(controlId) {
    const stage = task.stages[state.stageIndex];
    if (controlId === "repair_implicated") {
      advance();
      return;
    }
    if (stage.kind === "text") {
      if (controlId === "continue" && state.textValue === internal.required_text[state.stageIndex]) {
        advance();
      } else if (controlId === "continue") {
        fail("The short code does not match the visible source. Repair only this entry.");
      }
      return;
    }
    if (controlId !== internal.targets[state.stageIndex]) {
      if (stage.kind === "commit") {
        state.irreversibleFailure = true;
        fail("The incorrect final commit is sealed and cannot be repaired.");
      } else {
        fail("That choice conflicts with the visible evidence. Recheck this decision.");
      }
      return;
    }
    if (internal.recovery_stages[state.stageIndex] && !state.intentionalErrorsEntered.includes(state.stageIndex)) {
      state.intentionalErrorsEntered.push(state.stageIndex);
      fail("Verification rejected this selection: repair the implicated reference only.");
      return;
    }
    advance();
  }

  function controlMarkup(stage) {
    if (state.visibleError && internal.recovery_stages[state.stageIndex]) {
      return '<button data-control-id="repair_implicated">Repair the implicated verification selection</button>';
    }
    if (stage.kind === "text") {
      return '<input data-control-id="text_input" aria-label="Short code" maxlength="5" value="' + state.textValue + '">' +
        '<button data-control-id="continue" data-role="continue">Continue</button>';
    }
    return stage.controls.map((control) =>
      '<button data-control-id="' + control.control_id + '">' + control.label + '</button>'
    ).join("");
  }

  function render() {
    if (state.irreversibleFailure) {
      root.innerHTML = '<h1>' + task.title + '</h1><h2>Final commit rejected</h2><p class="error">' + state.visibleError + '</p>';
    } else if (state.submitted) {
      root.innerHTML = '<h1>' + task.title + '</h1><h2>Submission recorded for host evaluation.</h2>';
    } else {
      const stage = task.stages[state.stageIndex];
      root.innerHTML = '<h1>' + task.title + '</h1>' +
        '<h2>Decision ' + (state.stageIndex + 1) + ' of ' + task.stages.length + ' · ' + stage.heading + '</h2>' +
        '<p>' + stage.instruction + '</p><ul>' + stage.facts.map((fact) => '<li>' + fact + '</li>').join("") + '</ul>' +
        (state.visibleError ? '<p class="error">' + state.visibleError + '</p>' : '') +
        '<div class="controls">' + controlMarkup(stage) + '</div>';
      root.querySelectorAll("button").forEach((button) => button.addEventListener("click", () => activate(button.dataset.controlId)));
      const input = root.querySelector("input");
      if (input) input.addEventListener("input", () => { state.textValue = input.value; });
    }
    ready.dataset.ready = "true";
  }

  window.__pixelgymV5State = () => JSON.parse(JSON.stringify(state));
  window.__pixelgymV5Candidates = () => Array.from(root.querySelectorAll("button,input")).map((element) => {
    const rect = element.getBoundingClientRect();
    return {control_id: element.dataset.controlId, bbox: [rect.left, rect.top, rect.right, rect.bottom]};
  });
  render();
})();

"""Unit tests for the fake backend: determinism, the deterministic RGB
frame, click/key interaction semantics, and the privileged submission state.

These exercise the backend directly, below the Gymnasium contract. The
environment-level contract -- spaces, action rejection, reward timing,
termination vs. truncation -- is tested in `test_env.py`.

Nothing here touches the network, a browser, OSWorld, or the clock.
"""

from __future__ import annotations

import numpy as np
import pytest

from pixelgym.backends.fake import FakeBackend
from pixelgym.tasks.vendor_form import generator, render
from pixelgym.tasks.vendor_form.ui import (
    INCOMPLETE_SUBMISSION_MESSAGE,
    TAB_ORDER,
    TEXT_WIDGETS,
    FormState,
    Layout,
    WidgetId,
)


@pytest.fixture
def backend() -> FakeBackend:
    fake = FakeBackend()
    fake.reset(7)
    return fake


def _click(backend: FakeBackend, widget: WidgetId) -> None:
    backend.click(*backend.layout.controls[widget].center)


def _type(backend: FakeBackend, text: str) -> None:
    for character in text:
        backend.key(character)


# -- Determinism -------------------------------------------------------------


def test_same_seed_produces_an_identical_task_record():
    first = FakeBackend().reset(7)
    second = FakeBackend().reset(7)

    assert generator.canonical_json(dict(first)) == generator.canonical_json(dict(second))


def test_same_seed_produces_a_byte_identical_frame():
    one, other = FakeBackend(), FakeBackend()
    one.reset(7)
    other.reset(7)

    assert np.array_equal(one.screenshot(), other.screenshot())


def test_different_seed_changes_the_rendered_frame():
    """The request card is drawn from the task, so a different seed must be
    visible in pixels -- otherwise "the screenshot is the only observation"
    would leave an agent nothing to read."""
    one, other = FakeBackend(), FakeBackend()
    one.reset(7)
    other.reset(8)

    assert not np.array_equal(one.screenshot(), other.screenshot())


def test_reset_is_idempotent_for_a_seed_and_clears_submissions(backend):
    _click(backend, WidgetId.COMPANY_NAME)
    _type(backend, "typed")
    _click(backend, WidgetId.SUBMIT)
    first_task_id = backend.read_submissions()[0].task_id
    frame_before_reset = backend.screenshot()

    record = backend.reset(7)

    assert record["task_id"] == first_task_id
    assert backend.read_submissions() == []
    assert backend.form.text[WidgetId.COMPANY_NAME] == ""
    assert not np.array_equal(frame_before_reset, backend.screenshot())


def test_reset_to_a_different_seed_replaces_the_task(backend):
    before = backend.current_fields()

    backend.reset(8)

    assert backend.current_fields() != before


# -- The deterministic RGB frame --------------------------------------------


def test_screenshot_shape_and_dtype_match_the_backend_dimensions():
    fake = FakeBackend(width=320, height=240)
    fake.reset(7)

    frame = fake.screenshot()

    assert frame.shape == (240, 320, 3)
    assert frame.dtype == np.uint8


def test_screenshot_before_reset_raises():
    with pytest.raises(RuntimeError, match="reset"):
        FakeBackend().screenshot()


def test_screenshot_returns_a_copy_the_caller_cannot_corrupt(backend):
    frame = backend.screenshot()

    frame[:] = 0

    assert not np.array_equal(backend.screenshot(), frame)


def test_typing_changes_the_frame(backend):
    before = backend.screenshot()

    _click(backend, WidgetId.COMPANY_NAME)
    _type(backend, "Blue Harbor")

    assert not np.array_equal(before, backend.screenshot())


def test_submitting_changes_the_frame(backend):
    before = backend.screenshot()

    _click(backend, WidgetId.SUBMIT)

    assert not np.array_equal(before, backend.screenshot())


def test_frame_is_stable_when_nothing_changes(backend):
    assert np.array_equal(backend.screenshot(), backend.screenshot())


# -- Focus and text entry ----------------------------------------------------


def test_clicking_a_text_field_focuses_it_and_typing_appends(backend):
    _click(backend, WidgetId.CONTACT_EMAIL)
    _type(backend, "a@b.example")

    assert backend.form.focus is WidgetId.CONTACT_EMAIL
    assert backend.form.text[WidgetId.CONTACT_EMAIL] == "a@b.example"


def test_backspace_deletes_one_character(backend):
    _click(backend, WidgetId.TAX_ID)
    _type(backend, "TAX-1")

    backend.key("Backspace")

    assert backend.form.text[WidgetId.TAX_ID] == "TAX-"


def test_backspace_on_an_empty_field_is_harmless(backend):
    _click(backend, WidgetId.TAX_ID)

    backend.key("Backspace")

    assert backend.form.text[WidgetId.TAX_ID] == ""


def test_clicking_the_background_blurs_focus(backend):
    _click(backend, WidgetId.COMPANY_NAME)

    backend.click(backend.layout.request_panel.center[0], backend.height - 1)

    assert backend.form.focus is None


def test_typing_with_nothing_focused_changes_no_field(backend):
    _type(backend, "ignored")

    assert all(backend.form.text[widget] == "" for widget in TEXT_WIDGETS)


def test_tab_walks_the_document_order_then_leaves_the_form(backend):
    seen = []
    for _ in range(len(TAB_ORDER) + 2):
        backend.key("Tab")
        seen.append(backend.form.focus)

    assert seen == [*TAB_ORDER, None, TAB_ORDER[0]]


def test_arrow_keys_are_inert_inside_a_text_field(backend):
    """Caret movement is not modeled -- typing always appends. Pinning this
    down keeps a trajectory from quietly depending on caret behavior the real
    browser would have but the fake does not."""
    _click(backend, WidgetId.COMPANY_NAME)
    _type(backend, "abc")

    backend.key("ArrowLeft")
    _type(backend, "d")

    assert backend.form.text[WidgetId.COMPANY_NAME] == "abcd"


# -- Country dropdown --------------------------------------------------------


def test_clicking_the_select_opens_the_dropdown(backend):
    _click(backend, WidgetId.COUNTRY)

    assert backend.form.country_open is True
    assert backend.form.country_value == ""


def test_clicking_an_option_selects_it_and_closes_the_dropdown(backend):
    _click(backend, WidgetId.COUNTRY)

    backend.click(*backend.layout.country_options[2].center)

    assert backend.form.country_open is False
    assert backend.form.country_value == backend.form.country_options[2]


def test_clicking_outside_an_open_dropdown_dismisses_it_without_selecting(backend):
    _click(backend, WidgetId.COUNTRY)

    backend.click(backend.layout.request_panel.center[0], backend.height - 1)

    assert backend.form.country_open is False
    assert backend.form.country_value == ""


def test_a_click_that_dismisses_the_dropdown_does_not_reach_the_control_beneath(backend):
    """A native popup swallows the click that closes it. The Submit button sits
    under the open list, so this also proves a stray click cannot submit."""
    _click(backend, WidgetId.COUNTRY)

    _click(backend, WidgetId.SUBMIT)

    assert backend.read_submissions() == []


def test_arrow_down_moves_the_selection_and_clamps_at_the_end(backend):
    options = backend.form.country_options
    _click(backend, WidgetId.COUNTRY)

    for _ in range(len(options) + 3):
        backend.key("ArrowDown")

    assert backend.form.country_value == options[-1]


def test_arrow_up_cannot_return_to_the_disabled_placeholder(backend):
    _click(backend, WidgetId.COUNTRY)
    backend.key("ArrowDown")

    backend.key("ArrowUp")
    backend.key("ArrowUp")

    assert backend.form.country_value == backend.form.country_options[0]


def test_arrow_up_on_the_placeholder_leaves_it_showing(backend):
    _click(backend, WidgetId.COUNTRY)

    backend.key("ArrowUp")

    assert backend.form.country_value == ""


@pytest.mark.parametrize("country_index", range(6))
def test_country_selection_uses_the_transferable_click_and_keyboard_contract(
    backend, country_index
):
    """Only the select control has portable geometry; native popup rows do not."""
    _click(backend, WidgetId.COUNTRY)

    backend.key(backend.form.country_options[country_index][0].lower())
    backend.key("Enter")

    assert backend.form.country_open is False
    assert backend.form.country_value == backend.form.country_options[country_index]
    assert backend.read_submissions() == []


# -- Payment-terms radio group ----------------------------------------------


def test_clicking_a_radio_selects_that_option(backend):
    backend.click(*backend.layout.payment_options[1].center)

    assert backend.form.payment_terms_value == backend.form.payment_options[1]


def test_radio_arrows_wrap_in_both_directions(backend):
    options = backend.form.payment_options
    backend.click(*backend.layout.payment_options[0].center)

    backend.key("ArrowUp")
    assert backend.form.payment_terms_value == options[-1]

    backend.key("ArrowDown")
    assert backend.form.payment_terms_value == options[0]


def test_arrow_on_an_untouched_radio_group_selects_the_first_option(backend):
    for _ in range(TAB_ORDER.index(WidgetId.PAYMENT_TERMS) + 1):
        backend.key("Tab")

    backend.key("ArrowDown")

    assert backend.form.payment_terms_value == backend.form.payment_options[0]


def test_all_radio_hit_regions_support_varying_label_lengths():
    payment_options = ("N", "Due on receipt", "Net 123456789")
    layout = Layout(
        1024,
        768,
        country_option_count=2,
        payment_option_count=len(payment_options),
    )
    state = FormState(
        layout=layout,
        country_options=("Canada", "Japan"),
        payment_options=payment_options,
    )

    for index, rect in enumerate(layout.payment_options):
        state.click(*rect.center)
        assert state.payment_terms_value == payment_options[index], f"payment_terms[{index}]"


# -- Checkbox ----------------------------------------------------------------


def test_clicking_the_checkbox_toggles_it(backend):
    _click(backend, WidgetId.EXPEDITED_ONBOARDING)
    assert backend.form.expedited is True

    _click(backend, WidgetId.EXPEDITED_ONBOARDING)
    assert backend.form.expedited is False


def test_space_toggles_a_focused_checkbox(backend):
    _click(backend, WidgetId.EXPEDITED_ONBOARDING)  # focuses and checks

    backend.key(" ")

    assert backend.form.expedited is False


# -- Submission --------------------------------------------------------------


def test_clicking_submit_records_the_current_values(backend):
    fields = backend.current_fields()
    backend.install_form_values(fields)

    _click(backend, WidgetId.SUBMIT)

    submissions = backend.read_submissions()
    assert len(submissions) == 1
    assert dict(submissions[0].values) == fields


def test_submission_carries_the_task_identity_not_the_form_contents(backend):
    record = backend.reset(7)

    _click(backend, WidgetId.SUBMIT)

    submission = backend.read_submissions()[0]
    assert submission.task_id == record["task_id"]
    assert submission.seed == record["seed"]
    assert submission.final is True


def test_an_incomplete_form_still_records_a_submission(backend):
    """The real app records whatever was submitted and lets the evaluator
    judge it. A backend that refused to record an empty form would hide a
    reward-hacking surface instead of testing it."""
    _click(backend, WidgetId.SUBMIT)

    values = dict(backend.read_submissions()[0].values)
    assert values["company_name"] == ""
    assert values["country"] == ""
    assert values["payment_terms"] == ""
    assert values["expedited_onboarding"] is False
    assert backend.form.status == INCOMPLETE_SUBMISSION_MESSAGE


def test_a_complete_form_shows_submitted_status(backend):
    backend.install_form_values(backend.current_fields())

    _click(backend, WidgetId.SUBMIT)

    assert backend.form.status == "Submitted."


def test_submitted_values_are_whitespace_normalized_like_the_real_app(backend):
    _click(backend, WidgetId.COMPANY_NAME)
    _type(backend, "  Blue Harbor Supply Co.  ")

    _click(backend, WidgetId.SUBMIT)

    assert backend.read_submissions()[0].values["company_name"] == "Blue Harbor Supply Co."


def test_repeated_submissions_get_increasing_step_numbers(backend):
    _click(backend, WidgetId.SUBMIT)
    _click(backend, WidgetId.SUBMIT)

    assert [s.submitted_at_step for s in backend.read_submissions()] == [1, 2]


@pytest.mark.parametrize(
    "widget",
    [*TEXT_WIDGETS, WidgetId.EXPEDITED_ONBOARDING, WidgetId.SUBMIT],
    ids=lambda widget: widget.value,
)
def test_enter_submits_only_from_browser_implicit_submission_controls(backend, widget):
    backend.form.focus = widget

    backend.key("Enter")

    assert len(backend.read_submissions()) == 1


@pytest.mark.parametrize(
    "focus",
    [None, WidgetId.COUNTRY, WidgetId.PAYMENT_TERMS],
    ids=["no-focus", "country", "payment-terms"],
)
def test_enter_does_not_submit_from_other_focus_states(backend, focus):
    backend.form.focus = focus

    backend.key("Enter")

    assert backend.read_submissions() == []


def test_enter_while_the_dropdown_is_open_closes_it_without_submitting(backend):
    _click(backend, WidgetId.COUNTRY)
    backend.key("ArrowDown")

    backend.key("Enter")

    assert backend.form.country_open is False
    assert backend.form.country_value == backend.form.country_options[0]
    assert backend.read_submissions() == []


def test_space_activates_a_focused_submit_button(backend):
    for _ in range(len(TAB_ORDER)):
        backend.key("Tab")
    assert backend.form.focus is WidgetId.SUBMIT

    backend.key(" ")

    assert len(backend.read_submissions()) == 1


def test_read_submissions_returns_a_detached_list(backend):
    _click(backend, WidgetId.SUBMIT)

    backend.read_submissions().clear()

    assert len(backend.read_submissions()) == 1


# -- Boundary clicks ---------------------------------------------------------


@pytest.mark.parametrize("corner", ["top_left", "bottom_right"])
def test_clicks_at_the_extreme_corners_are_harmless(backend, corner):
    x, y = (0, 0) if corner == "top_left" else (backend.width - 1, backend.height - 1)

    backend.click(x, y)

    assert backend.read_submissions() == []
    assert backend.form.focus is None


# -- Test-only hooks ---------------------------------------------------------


def test_install_form_values_puts_the_form_into_an_exact_state(backend):
    fields = backend.current_fields()

    backend.install_form_values(fields)

    assert backend.form.values() == fields


def test_install_form_values_accepts_the_empty_choice_for_a_selection(backend):
    backend.install_form_values({"country": "Australia", "payment_terms": "Net 30"})

    backend.install_form_values({"country": "", "payment_terms": ""})

    assert backend.form.country_value == ""
    assert backend.form.payment_terms_value == ""


def test_install_form_values_rejects_an_option_that_does_not_exist(backend):
    with pytest.raises(ValueError, match="Atlantis"):
        backend.install_form_values({"country": "Atlantis"})


def test_install_form_values_rejects_an_unknown_field_name(backend):
    with pytest.raises(ValueError, match="not a form field"):
        backend.install_form_values({"vendor_rating": "A"})


def test_install_form_values_rejects_the_submit_button(backend):
    """`submit` is a control, not a field -- there is no value to install."""
    with pytest.raises(ValueError, match="not a form field"):
        backend.install_form_values({"submit": True})


@pytest.mark.parametrize("value", [1, None, ["Blue Harbor"], True])
def test_install_form_values_rejects_a_non_string_text_value(backend, value):
    with pytest.raises(TypeError, match="text field"):
        backend.install_form_values({"company_name": value})


@pytest.mark.parametrize("value", [1, 0, "true", "Yes", None])
def test_install_form_values_rejects_a_checkbox_value_that_is_not_a_bool(backend, value):
    """The evaluator compares types strictly, so an int 1 is not a stand-in for
    True -- and no checkbox click could produce one."""
    with pytest.raises(TypeError, match="checkbox"):
        backend.install_form_values({"expedited_onboarding": value})


@pytest.mark.parametrize("field", ["country", "payment_terms"])
def test_install_form_values_rejects_a_non_string_selection_value(backend, field):
    with pytest.raises(TypeError, match="selection field"):
        backend.install_form_values({field: 0})


def test_install_form_values_is_atomic_when_a_later_field_is_rejected(backend):
    """A field validated before the offending one must not be written. A
    half-applied form is a state no interaction could produce, and a test that
    silently started from one would be asserting against fiction."""
    backend.install_form_values({"company_name": "Kept", "expedited_onboarding": True})
    frame_before = backend.screenshot()

    with pytest.raises(ValueError):
        backend.install_form_values(
            {
                "contact_email": "a@b.example",  # valid, and listed first
                "tax_id": "TAX-1",  # valid
                "country": "Atlantis",  # rejected
            }
        )

    assert backend.form.text[WidgetId.CONTACT_EMAIL] == ""
    assert backend.form.text[WidgetId.TAX_ID] == ""
    assert backend.form.text[WidgetId.COMPANY_NAME] == "Kept"  # earlier call survives
    assert backend.form.expedited is True
    assert np.array_equal(frame_before, backend.screenshot())
    # Rendered independently of the backend's cache: an unchanged screenshot
    # would prove nothing on its own, since a stale cache also looks unchanged.
    redrawn = render.render(generator.generate_task(7), backend.form, backend.layout)
    assert np.array_equal(backend.screenshot(), redrawn)


def test_install_form_values_leaves_focus_and_status_untouched(backend):
    _click(backend, WidgetId.SUBMIT)  # focuses Submit and sets the status line
    focus_before, status_before = backend.form.focus, backend.form.status

    backend.install_form_values({"company_name": "Blue Harbor Supply Co."})

    assert backend.form.focus is focus_before
    assert backend.form.status == status_before


def test_install_submission_appends_a_record_without_touching_the_form(backend):
    fields = backend.current_fields()

    backend.install_submission(fields)

    assert len(backend.read_submissions()) == 1
    assert backend.form.values()["company_name"] == ""


def test_install_submission_defaults_to_the_active_task_and_next_step(backend):
    record = backend.reset(7)

    submission = backend.install_submission(backend.current_fields())

    assert submission.task_id == record["task_id"]
    assert submission.seed == record["seed"]
    assert submission.submitted_at_step == 1
    assert submission.final is True


def test_install_submission_can_build_a_non_final_draft(backend):
    backend.install_submission(backend.current_fields(), final=False)

    assert backend.read_submissions()[0].final is False


def test_install_submission_can_build_a_stale_task_id_record(backend):
    """A submission left over from a previous episode. No click can produce
    one, so without this the evaluator's stale-task defense is untestable."""
    stale = backend.install_submission(backend.current_fields(), task_id="vf-stale00000000")

    assert stale.task_id == "vf-stale00000000"
    assert backend.read_submissions()[0].task_id == "vf-stale00000000"


def test_install_submission_can_build_a_wrong_seed_record(backend):
    record = backend.reset(7)

    wrong = backend.install_submission(backend.current_fields(), seed=record["seed"] + 1)

    assert wrong.task_id == record["task_id"]  # right task, wrong seed
    assert wrong.seed == record["seed"] + 1


def test_install_submission_can_set_an_explicit_step_number(backend):
    installed = backend.install_submission(backend.current_fields(), submitted_at_step=99)

    assert installed.submitted_at_step == 99


def test_install_submission_records_overrides_verbatim(backend):
    """The hook never re-attributes an installed record to the active task --
    a test that asked for a stale record must get exactly that back."""
    installed = backend.install_submission(
        {"company_name": "Ghost Co."},
        task_id="vf-somethingelse",
        seed=-1,
        submitted_at_step=42,
        final=False,
    )

    stored = backend.read_submissions()[0]
    assert stored == installed
    assert (stored.task_id, stored.seed, stored.submitted_at_step, stored.final) == (
        "vf-somethingelse",
        -1,
        42,
        False,
    )
    assert dict(stored.values) == {"company_name": "Ghost Co."}


def test_a_real_submission_after_an_installed_one_stays_monotonic(backend):
    """Step numbers must keep increasing even past an out-of-band installed
    record: two submissions sharing a number is the ambiguous history the
    evaluator refuses to score, and an agent must not be able to cause it."""
    backend.install_submission(backend.current_fields(), submitted_at_step=99)

    _click(backend, WidgetId.SUBMIT)
    _click(backend, WidgetId.SUBMIT)

    assert [s.submitted_at_step for s in backend.read_submissions()] == [99, 100, 101]


def test_a_real_submission_is_always_attributed_to_the_active_task(backend):
    """Overrides are the hook's alone. A submission the agent caused always
    carries the active task's identity."""
    record = backend.reset(7)
    backend.install_submission({}, task_id="vf-stale00000000", seed=-1)

    _click(backend, WidgetId.SUBMIT)

    real = backend.read_submissions()[1]
    assert real.task_id == record["task_id"]
    assert real.seed == record["seed"]


# -- Layout ------------------------------------------------------------------


def test_no_two_controls_overlap(backend):
    """Overlapping rectangles would make a scripted click ambiguous -- the
    frozen golden trajectory must always resolve each click unambiguously."""
    layout = backend.layout
    rects = [
        rect for widget, rect in layout.controls.items() if widget is not WidgetId.PAYMENT_TERMS
    ]
    rects += list(layout.payment_options)

    for index, first in enumerate(rects):
        for second in rects[index + 1 :]:
            separated = (
                first.right <= second.x
                or second.right <= first.x
                or first.bottom <= second.y
                or second.bottom <= first.y
            )
            assert separated, f"{first} overlaps {second}"


def test_every_control_center_hit_tests_back_to_that_control(backend):
    layout = backend.layout

    for widget, rect in layout.controls.items():
        if widget is WidgetId.PAYMENT_TERMS:
            continue  # the group includes non-clickable space between radio labels
        hit = layout.hit_test(*rect.center, country_open=False)
        assert hit is not None and hit[0] is widget

    for index, rect in enumerate(layout.payment_options):
        assert layout.hit_test(*rect.center, country_open=False) == (
            WidgetId.PAYMENT_TERMS,
            index,
        )


def test_layout_degrades_without_collapsing_on_a_small_screen():
    small = FakeBackend(width=64, height=48)
    small.reset(7)

    for rect in small.layout.controls.values():
        assert rect.width >= 1 and rect.height >= 1
        assert rect.right <= small.width and rect.bottom <= small.height


# -- Contract drift guards ---------------------------------------------------


def test_form_values_cover_exactly_the_generated_field_names(backend):
    assert set(backend.form.values()) == set(generator.FIELD_NAMES)


def test_widget_ids_cover_every_generated_field_name():
    field_widgets = {widget.value for widget in WidgetId} - {WidgetId.SUBMIT.value}

    assert field_widgets == set(generator.FIELD_NAMES)

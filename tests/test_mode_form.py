from safescroll.forms import ModeForm


def _mode_form(app, **overrides):
    data = {
        "name": "Study mode",
        "strictness": "3",
        **overrides,
    }
    context = app.test_request_context("/modes/new", method="POST", data=data)
    context.push()
    return context, ModeForm()


def test_mode_form_allows_optional_icon_and_color(app):
    context, form = _mode_form(app)
    try:
        assert form.validate(), form.errors
        assert form.strictness.data == 3
    finally:
        context.pop()


def test_mode_form_rejects_color_outside_palette(app):
    context, form = _mode_form(app, color="#123456")
    try:
        assert not form.validate()
        assert form.color.errors
    finally:
        context.pop()


def test_mode_form_requires_a_day_for_scheduled_times(app):
    context, form = _mode_form(
        app,
        schedule_start="08:00",
        schedule_end="14:00",
    )
    try:
        assert not form.validate()
        assert "Select at least one day for this schedule." in form.schedule_days.errors
    finally:
        context.pop()


def test_mode_form_requires_end_time_after_start_time(app):
    context, form = _mode_form(
        app,
        schedule_days="mon",
        schedule_start="14:00",
        schedule_end="08:00",
    )
    try:
        assert not form.validate()
        assert "End time must be later than start time." in form.schedule_end.errors
    finally:
        context.pop()

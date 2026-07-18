from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    PasswordField,
    RadioField,
    SelectMultipleField,
    StringField,
    SubmitField,
    TextAreaField,
    TimeField,
)
from wtforms.fields import EmailField, IntegerRangeField
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)

from safescroll.utils import password_error


MODE_COLOR_CHOICES = [
    ("#14B8A6", "Teal"),
    ("#3B82F6", "Blue"),
    ("#6366F1", "Indigo"),
    ("#8B5CF6", "Violet"),
    ("#F59E0B", "Amber"),
    ("#F97316", "Orange"),
    ("#EC4899", "Pink"),
]
MODE_COLOR_VALUES = frozenset(value for value, _label in MODE_COLOR_CHOICES)


def strip_filter(value):
    return value.strip() if isinstance(value, str) else value


def strong_password(_form, field) -> None:
    error = password_error(field.data)
    if error:
        raise ValidationError(error)


class RegistrationForm(FlaskForm):
    full_name = StringField(
        "Full name", filters=[strip_filter], validators=[DataRequired(), Length(min=2, max=120)]
    )
    email = EmailField(
        "Email", filters=[strip_filter], validators=[DataRequired(), Email(), Length(max=254)]
    )
    password = PasswordField("Password", validators=[DataRequired(), strong_password])
    confirm_password = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords do not match.")],
    )
    submit = SubmitField("Create account")


class LoginForm(FlaskForm):
    email = EmailField(
        "Email", filters=[strip_filter], validators=[DataRequired(), Email(), Length(max=254)]
    )
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])
    remember_me = BooleanField("Remember me")
    submit = SubmitField("Log in")


class ForgotPasswordForm(FlaskForm):
    email = EmailField(
        "Email", filters=[strip_filter], validators=[DataRequired(), Email(), Length(max=254)]
    )
    submit = SubmitField("Send reset link")


class ResetPasswordForm(FlaskForm):
    password = PasswordField("New password", validators=[DataRequired(), strong_password])
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("password", message="Passwords do not match.")],
    )
    submit = SubmitField("Reset password")


class ProfileForm(FlaskForm):
    full_name = StringField(
        "Full name", filters=[strip_filter], validators=[DataRequired(), Length(min=2, max=120)]
    )
    email = EmailField(
        "Email", filters=[strip_filter], validators=[DataRequired(), Email(), Length(max=254)]
    )
    timezone = StringField(
        "Time zone", filters=[strip_filter], validators=[DataRequired(), Length(max=64)]
    )
    submit = SubmitField("Save profile")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "Current password", validators=[DataRequired(), Length(max=128)]
    )
    new_password = PasswordField("New password", validators=[DataRequired(), strong_password])
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords do not match.")],
    )
    submit = SubmitField("Update password")


class DeleteAccountForm(FlaskForm):
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])
    confirmation = StringField("Type DELETE", validators=[DataRequired(), Length(max=16)])
    submit = SubmitField("Delete account")


class ContactForm(FlaskForm):
    name = StringField(
        "Name", filters=[strip_filter], validators=[DataRequired(), Length(min=2, max=120)]
    )
    email = EmailField(
        "Email", filters=[strip_filter], validators=[DataRequired(), Email(), Length(max=254)]
    )
    message = TextAreaField("Message", validators=[DataRequired(), Length(min=10, max=5000)])
    submit = SubmitField("Send message")


class ModeForm(FlaskForm):
    name = StringField(
        "Mode name", filters=[strip_filter], validators=[DataRequired(), Length(min=2, max=80)]
    )
    icon = StringField(
        "Icon", filters=[strip_filter], validators=[Optional(), Length(max=16)], default="🎯"
    )
    color = RadioField(
        "Mode color",
        choices=MODE_COLOR_CHOICES,
        default="#14B8A6",
        validators=[Optional()],
    )
    description = TextAreaField("Description", validators=[Optional(), Length(max=500)])
    preferred_categories = TextAreaField(
        "Preferred categories", validators=[Optional(), Length(max=1000)]
    )
    blocked_categories = TextAreaField(
        "Blocked categories", validators=[Optional(), Length(max=1000)]
    )
    preferred_keywords = TextAreaField(
        "Preferred keywords", validators=[Optional(), Length(max=1000)]
    )
    blocked_keywords = TextAreaField(
        "Blocked keywords", validators=[Optional(), Length(max=1000)]
    )
    strictness = IntegerRangeField(
        "Strictness",
        validators=[DataRequired(), NumberRange(min=1, max=5)],
        default=3,
    )
    schedule_days = SelectMultipleField(
        "Schedule days",
        choices=[
            ("mon", "Monday"),
            ("tue", "Tuesday"),
            ("wed", "Wednesday"),
            ("thu", "Thursday"),
            ("fri", "Friday"),
            ("sat", "Saturday"),
            ("sun", "Sunday"),
        ],
        validators=[],
    )
    schedule_start = TimeField("Start time", validators=[Optional()])
    schedule_end = TimeField("End time", validators=[Optional()])
    is_protected = BooleanField("Protected mode")
    protection_pin = PasswordField("Protection PIN", validators=[Optional(), Length(min=4, max=8)])
    submit = SubmitField("Save mode")

    def validate_protection_pin(self, field) -> None:
        if field.data and not field.data.isdigit():
            raise ValidationError("Protection PIN must contain digits only.")

    def validate_color(self, field) -> None:
        if field.data and field.data not in MODE_COLOR_VALUES:
            raise ValidationError("Choose a color from the SafeScroll palette.")

    def validate_schedule_days(self, field) -> None:
        if (self.schedule_start.data or self.schedule_end.data) and not field.data:
            raise ValidationError("Select at least one day for this schedule.")

    def validate_schedule_end(self, field) -> None:
        if bool(self.schedule_start.data) != bool(field.data):
            raise ValidationError("Provide both a schedule start and end time.")
        if self.schedule_start.data and field.data and field.data <= self.schedule_start.data:
            raise ValidationError("End time must be later than start time.")


class EmptyForm(FlaskForm):
    submit = SubmitField("Confirm")


class ModeUnlockForm(FlaskForm):
    pin = PasswordField(
        "Protection PIN", validators=[DataRequired(), Length(min=4, max=8)]
    )
    submit = SubmitField("Unlock mode")

    def validate_pin(self, field) -> None:
        if field.data and not field.data.isdigit():
            raise ValidationError("Protection PIN must contain digits only.")

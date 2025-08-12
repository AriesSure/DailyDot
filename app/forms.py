from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import SubmitField, HiddenField, StringField, PasswordField, BooleanField, SelectField, DateField, TimeField
from wtforms.fields.numeric import IntegerField
from wtforms.fields.simple import TextAreaField
from wtforms.validators import DataRequired, EqualTo, NumberRange, ValidationError, Email, Optional, Length
from app import db
from datetime import datetime
from app.models import User

class ChooseForm(FlaskForm):
    choice = HiddenField('Choice')

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')
    submit = SubmitField('Log In')


def password_policy(form, field):
    message = """A password must be at least 8 characters long, and have an
                uppercase and lowercase letter, a digit, and a character which is
                neither a letter or a digit"""
    if len(field.data) < 8:
        raise ValidationError(message)
    flg_upper = flg_lower = flg_digit = flg_non_let_dig = False
    for ch in field.data:
        flg_upper = flg_upper or ch.isupper()
        flg_lower = flg_lower or ch.islower()
        flg_digit = flg_digit or ch.isdigit()
        flg_non_let_dig = flg_non_let_dig or not ch.isalnum()
    if not (flg_upper and flg_lower and flg_digit and flg_non_let_dig):
        raise ValidationError(message)

class ChangePasswordForm(FlaskForm):
    password = PasswordField('Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired(), password_policy])
    confirm = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password', message="Passwords must match")])
    submit = SubmitField('Change Password')

    @staticmethod
    def validate_password(form, field):
        if not current_user.check_password(field.data):
            raise ValidationError("Incorrect password")


class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), password_policy])
    confirm = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Register')

    @staticmethod
    def validate_username(form, field):
        q = db.select(User).where(User.username==field.data)
        if db.session.scalar(q):
            raise ValidationError("Username already taken, please choose another")

    @staticmethod
    def validate_email(form, field):
        q = db.select(User).where(User.email==field.data)
        if db.session.scalar(q):
            raise ValidationError("Email address already taken, please choose another")




class HabitForm(FlaskForm):
    habit_name = StringField('Habit Name', validators=[DataRequired()])
    icon = SelectField('Icon', validators=[DataRequired()])
    frequency = SelectField(
        'Frequency',
        choices=[
            ('', ''),
            ('Once a week', 'Once a week'),
            ('Twice a week', 'Twice a week'),
            ('3 times a week', '3 times a week'),
            ('4 times a week', '4 times a week'),
            ('5 times a week', '5 times a week'),
            ('6 times a week', '6 times a week'),
            ('Every day this week', 'Every day this week')
        ],validators=[DataRequired()])

    time_period = SelectField(
        'Time Period',
        choices=[
            ('', ''),
            ('Any time', 'Any time'),
            ('After waking up', 'After waking up'),
            ('Morning', 'Morning'),
            ('Noon', 'Noon'),
            ('Afternoon', 'Afternoon'),
            ('Evening', 'Evening'),
            ('Before bedtime', 'Before bedtime')
        ],validators=[DataRequired()])
    note = TextAreaField('Note', validators=[Optional(), Length(max=1024)] )
    submit = SubmitField('Save Habit')


class TodoForm(FlaskForm):

    def validate_time(form, field):
        try:
            # Parse date and time from form
            date_str = form.date.data.strftime('%d/%m/%Y') if hasattr(form.date.data, 'strftime') else str(form.date.data)
            time_str = field.data.strftime('%H:%M') if hasattr(field.data, 'strftime') else str(field.data)
            
            # Convert to datetime for comparison
            combined_datetime = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")
            if combined_datetime < datetime.now():
                raise ValidationError("The time cannot be earlier than the current time.")
        except (ValueError, AttributeError):
            # If parsing fails, skip validation
            pass

    event = StringField('Event Name', validators=[DataRequired(message='The event name cannot be empty.'), Length(max=128, message='The maximum length of an event name is 128 characters.')])
    date = DateField('Date (DD/MM/YYYY)', format='%d/%m/%Y', validators=[DataRequired()])
    time = TimeField('Time (HH:MM)', format='%H:%M', validators=[DataRequired(), validate_time])
    note = TextAreaField('Note', validators=[Optional(), Length(max=1024)])
    submit = SubmitField('Save Todo')


class CheckinForm(FlaskForm):

    time = TimeField('Time (HH:MM)', validators=[DataRequired()])
    note = TextAreaField('Note', validators=[Optional(), Length(max=1024)])
    submit = SubmitField('Check In')





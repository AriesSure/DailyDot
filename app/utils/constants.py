"""Shared constants used across multiple blueprints."""

ICON_CHOICES = [
    ('fas fa-sun', 'sun'),
    ('fas fa-bed', 'bed'),
    ('fas fa-book', 'book'),
    ('fas fa-music', 'music'),
    ('fas fa-dumbbell', 'dumbbell'),
    ('fas fa-coffee', 'coffee'),
    ('fas fa-pen', 'pen'),
    ('fas fa-laptop', 'laptop'),
    ('fas fa-heart', 'heart'),
    ('fas fa-running', 'running'),
    ('fas fa-swimmer', 'swimmer'),
    ('fas fa-bicycle', 'bicycle'),
]

TIME_PERIOD_CHOICES = [
    ('', ''),
    ('Any time', 'Any time'),
    ('After waking up', 'After waking up'),
    ('Morning', 'Morning'),
    ('Noon', 'Noon'),
    ('Afternoon', 'Afternoon'),
    ('Evening', 'Evening'),
    ('Before bedtime', 'Before bedtime'),
]

FREQUENCY_CHOICES = [
    ('', ''),
    ('Once a week', 'Once a week'),
    ('Twice a week', 'Twice a week'),
    ('3 times a week', '3 times a week'),
    ('4 times a week', '4 times a week'),
    ('5 times a week', '5 times a week'),
    ('6 times a week', '6 times a week'),
    ('Every day this week', 'Every day this week'),
]

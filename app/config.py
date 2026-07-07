"""Multi-environment configuration classes.

Usage:
    app.config.from_object(config_by_name['development'])
    app.config.from_object(config_by_name['testing'])
    app.config.from_object(config_by_name['production'])
"""

import os

# Root of the project (directory containing `app/`)
_basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class Config:
    """Base configuration — shared by all environments."""
    SECRET_KEY = os.environ.get('SECRET_KEY') or b'WR#&f&+%78er0we=%799eww+#7^90-;s'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False

    UPLOAD_FOLDER = os.path.join(_basedir, 'app', 'data', 'uploads')
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024  # 1 MB


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_ECHO = True
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get('DATABASE_URL')
        or 'sqlite:///' + os.path.join(_basedir, 'app', 'data', 'data.sqlite')
    )


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_ECHO = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


class ProductionConfig(Config):
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'mysql+pymysql://user:pass@localhost:3306/dailydot',
    )


config_by_name = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
}

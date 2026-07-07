from flask import Blueprint

todos_bp = Blueprint('todos', __name__, url_prefix='/todos')

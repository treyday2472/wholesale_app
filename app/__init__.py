from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
csrf = CSRFProtect()  # <-- instantiate

def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    # Optional: print which DB file is being used
    print("DB URI:", app.config['SQLALCHEMY_DATABASE_URI'])

    db.init_app(app)
    csrf.init_app(app)  # <-- now works

    from .routes import main
    app.register_blueprint(main)

    with app.app_context():
        db.create_all()

    return app

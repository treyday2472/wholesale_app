from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

def create_app():
    from .routes import main

    app = Flask(__name__)
    app.config.from_object('config.Config')
    print("DB URI:", app.config['SQLALCHEMY_DATABASE_URI'])


    db.init_app(app)

    with app.app_context():
        db.create_all()

    app.register_blueprint(main)

    return app
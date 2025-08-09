from app import create_app, db  # use create_app, not app

app = create_app()  # instantiate the app
with app.app_context():
    db.drop_all()
    db.create_all()

print("Database reset complete.")

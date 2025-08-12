from app import create_app, db
import sqlalchemy as sa

app = create_app()
with app.app_context():
    print("DB URL:", db.engine.url)

    insp = sa.inspect(db.engine)
    cols = [c["name"] for c in insp.get_columns("lead")]
    if "intake" not in cols:
        print("Adding column lead.intake ...")
        with db.engine.connect() as con:
            con.execute(sa.text("ALTER TABLE lead ADD COLUMN intake TEXT"))
            con.commit()
        print("Added.")
    else:
        print("Column intake already exists.")

    # Make sure any new tables (e.g., LeadEvent) get created
    db.create_all()
    print("create_all done.")

"""
Syncs student records from the OBE system's MySQL database into this
app's local `students` table.

Run this from a machine that has access to the OBE local network
(e.g. as a scheduled job on the OBE server itself, pushing to this
app's public Postgres, since this app cannot reach the OBE network
directly). Read-only against OBE; this app's `students` table is a
mirror, not the source of truth.

Adjust the OBE query below to match the actual `student` table's
columns once you confirm them.
"""
import pymysql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import SessionLocal
from app.core.config import settings
from app.models.models import Student


def fetch_obe_students(obe_db_url: str) -> list[dict]:
    # obe_db_url format: mysql+pymysql://user:pass@host/dbname — parse manually
    # since pymysql.connect wants discrete kwargs, not a URL.
    import re
    m = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^/]+)/(.+)", obe_db_url)
    user, password, host, db = m.groups()

    conn = pymysql.connect(host=host, user=user, password=password, database=db,
                            cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT S_ID AS obe_student_id, full_name, enrollment_number,
                       degree_program, batch_id AS batch, current_semester
                FROM student
            """)
            return cursor.fetchall()
    finally:
        conn.close()


def sync():
    rows = fetch_obe_students(settings.obe_db_url)
    db = SessionLocal()
    try:
        for row in rows:
            stmt = pg_insert(Student).values(
                obe_student_id=str(row["obe_student_id"]),
                full_name=row["full_name"],
                enrollment_number=row["enrollment_number"],
                degree_program=row.get("degree_program"),
                batch=str(row.get("batch")) if row.get("batch") else None,
                current_semester=row.get("current_semester"),
            ).on_conflict_do_update(
                index_elements=["obe_student_id"],
                set_={
                    "full_name": row["full_name"],
                    "enrollment_number": row["enrollment_number"],
                    "degree_program": row.get("degree_program"),
                    "batch": str(row.get("batch")) if row.get("batch") else None,
                    "current_semester": row.get("current_semester"),
                },
            )
            db.execute(stmt)
        db.commit()
        print(f"Synced {len(rows)} students")
    finally:
        db.close()


if __name__ == "__main__":
    sync()

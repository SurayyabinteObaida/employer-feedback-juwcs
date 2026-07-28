"""
Admin utilities for managing employer access and magic links.
Run this script to generate secure links for employers.

Usage:
    python admin_utils.py generate-link <employer_id> <expires_hours>
    python admin_utils.py list-employers
    python admin_utils.py create-employer <email> <name> <designation>
"""

import os
import sys
import uuid
import secrets
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set in environment")
    sys.exit(1)

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

def create_magic_link(employer_id: str, expires_hours: int = 48) -> str:
    """Generate a secure magic link token for an employer."""
    db = Session()
    try:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=expires_hours)
        
        db.execute(
            "INSERT INTO magic_links (id, employer_id, token, expires_at) VALUES (:id, :employer_id, :token, :expires_at)",
            {
                "id": str(uuid.uuid4()),
                "employer_id": employer_id,
                "token": token,
                "expires_at": expires_at
            }
        )
        db.commit()
        
        return token, expires_at
    except Exception as e:
        print(f"Error creating magic link: {e}")
        return None, None
    finally:
        db.close()

def list_employers():
    """List all employers in the system."""
    db = Session()
    try:
        result = db.execute("SELECT id, work_email, name, designation, created_at FROM employers ORDER BY created_at DESC")
        employers = result.fetchall()
        
        if not employers:
            print("No employers found")
            return
        
        print("\nEmployers in System:")
        print("=" * 100)
        print(f"{'ID':<36} {'Email':<40} {'Name':<25} {'Designation':<25}")
        print("-" * 100)
        for emp in employers:
            emp_id, email, name, designation, created = emp
            print(f"{emp_id} {email:<40} {(name or '—'):<25} {(designation or '—'):<25}")
        print("=" * 100)
    except Exception as e:
        print(f"Error listing employers: {e}")
    finally:
        db.close()

def create_employer(email: str, name: str = None, designation: str = None) -> str:
    """Create a new employer account."""
    db = Session()
    try:
        employer_id = str(uuid.uuid4())
        
        db.execute(
            "INSERT INTO employers (id, work_email, name, designation, created_via) VALUES (:id, :email, :name, :designation, :created_via)",
            {
                "id": employer_id,
                "email": email,
                "name": name,
                "designation": designation,
                "created_via": "admin_utils"
            }
        )
        db.commit()
        
        print(f"\nEmployer created successfully")
        print(f"ID: {employer_id}")
        print(f"Email: {email}")
        print(f"Name: {name or '(not set)'}")
        print(f"Designation: {designation or '(not set)'}")
        
        return employer_id
    except Exception as e:
        print(f"Error creating employer: {e}")
        return None
    finally:
        db.close()

def generate_access_link(employer_id: str, expires_hours: int = 48) -> str:
    """Generate a complete access link for an employer."""
    db = Session()
    try:
        result = db.execute("SELECT work_email FROM employers WHERE id = :id", {"id": employer_id}).first()
        
        if not result:
            print(f"Employer with ID {employer_id} not found")
            return None
        
        employer_email = result[0]
        token, expires_at = create_magic_link(employer_id, expires_hours)
        
        if not token:
            print("Failed to create magic link")
            return None
        
        frontend_url = os.getenv("FRONTEND_URL", "https://employer-panel.onrender.com")
        access_link = f"{frontend_url}?token={token}"
        
        print(f"\n{'='*80}")
        print(f"MAGIC LINK GENERATED")
        print(f"{'='*80}")
        print(f"\nEmployer Email: {employer_email}")
        print(f"Expires: {expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')} ({expires_hours} hours from now)")
        print(f"\nAccess Link (send this to the employer):")
        print(f"{access_link}")
        print(f"\n{'='*80}\n")
        
        return access_link
    except Exception as e:
        print(f"Error generating access link: {e}")
        return None
    finally:
        db.close()

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "generate-link":
        if len(sys.argv) < 3:
            print("Usage: python admin_utils.py generate-link <employer_id> [expires_hours]")
            sys.exit(1)
        
        employer_id = sys.argv[2]
        expires_hours = int(sys.argv[3]) if len(sys.argv) > 3 else 48
        
        generate_access_link(employer_id, expires_hours)
    
    elif command == "list-employers":
        list_employers()
    
    elif command == "create-employer":
        if len(sys.argv) < 3:
            print("Usage: python admin_utils.py create-employer <email> [name] [designation]")
            sys.exit(1)
        
        email = sys.argv[2]
        name = sys.argv[3] if len(sys.argv) > 3 else None
        designation = sys.argv[4] if len(sys.argv) > 4 else None
        
        employer_id = create_employer(email, name, designation)
        if employer_id:
            print(f"\nGenerating access link...")
            generate_access_link(employer_id)
    
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)

if __name__ == "__main__":
    main()

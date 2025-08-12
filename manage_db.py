#!/usr/bin/env python3
"""
Database management script for DailyDot application.
This script provides commands to manage database migrations and data.
"""

import os
import sys
from app import app, db
from app.debug_utils import init_migrations, upgrade_db, create_migration, reset_db

def main():
    if len(sys.argv) < 2:
        print("Usage: python manage_db.py <command>")
        print("\nAvailable commands:")
        print("  init      - Initialize migrations (run once)")
        print("  migrate   - Create a new migration")
        print("  upgrade   - Apply all pending migrations")
        print("  reset     - Reset database (WARNING: deletes all data)")
        print("  status    - Show migration status")
        return

    command = sys.argv[1].lower()

    with app.app_context():
        if command == "init":
            print("Initializing database migrations...")
            init_migrations()
            
        elif command == "migrate":
            if len(sys.argv) < 3:
                print("Usage: python manage_db.py migrate <message>")
                print("Example: python manage_db.py migrate 'Add user preferences'")
                return
            message = sys.argv[2]
            print(f"Creating migration: {message}")
            create_migration(message)
            
        elif command == "upgrade":
            print("Upgrading database...")
            upgrade_db()
            
        elif command == "reset":
            confirm = input("WARNING: This will delete ALL data. Are you sure? (yes/no): ")
            if confirm.lower() == 'yes':
                print("Resetting database...")
                reset_db()
            else:
                print("Database reset cancelled.")
                
        elif command == "status":
            try:
                from flask_migrate import current, history
                print("Current migration:", current())
                print("\nMigration history:")
                for rev in history():
                    print(f"  {rev.revision}: {rev.comment}")
            except Exception as e:
                print(f"Error checking status: {e}")
                
        else:
            print(f"Unknown command: {command}")
            print("Use 'python manage_db.py' to see available commands.")

if __name__ == "__main__":
    main() 
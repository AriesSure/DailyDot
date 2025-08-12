# Database Migration Usage Guide

## Overview
DailyDot application now uses Flask-Migrate to manage database structure changes, which allows safe database updates without data loss.

## Quick Start

### 1. Initialize Migration (run only once)
```bash
python manage_db.py init
```

### 2. Apply Existing Migrations
```bash
python manage_db.py upgrade
```

## Daily Usage

### Check Migration Status
```bash
python manage_db.py status
```

### Create New Migration
When you modify models (models.py), you need to create a new migration:
```bash
python manage_db.py migrate "Describe your changes"
```
Example:
```bash
python manage_db.py migrate "Add user preferences field"
```

### Apply New Migration
After creating a migration, you need to apply it:
```bash
python manage_db.py upgrade
```

## Development Environment

### Reset Database (development only)
⚠️ **Warning: This will delete all data!**
```bash
python manage_db.py reset
```

## Production Environment

In production environment, you should:

1. **Backup Database**: Backup data before applying migrations
2. **Test Migration**: Test migrations in test environment first
3. **Apply Migration**: Use `python manage_db.py upgrade`

## Migration Files

Migration files are stored in the `migrations/versions/` directory, each file contains:
- Upgrade operations (upgrade): Apply changes
- Downgrade operations (downgrade): Rollback changes

## Common Issues

### Q: What to do if migration fails?
A: Check error messages, you may need to manually fix the database or rollback to previous version.

### Q: How to rollback migration?
A: Use `flask db downgrade` command.

### Q: What to do with conflicting migration files?
A: Delete conflicting migration files and recreate migration.

## Best Practices

1. **Create Migrations Frequently**: Create migration immediately after modifying models
2. **Descriptive Messages**: Use clear descriptions to explain migration content
3. **Test Migrations**: Test before applying in production environment
4. **Backup Data**: Backup database before important data changes

## Command Summary

| Command | Purpose | Usage Frequency |
|---------|---------|-----------------|
| `init` | Initialize migration system | One-time |
| `upgrade` | Apply migrations | Frequently |
| `migrate` | Create new migration | After modifying models |
| `status` | Check status | As needed |
| `reset` | Reset database | Development only | 
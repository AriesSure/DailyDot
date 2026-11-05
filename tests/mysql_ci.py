"""CI-only, read-only preflight; never creates or removes database objects."""

import re
import sys

import sqlalchemy as sa

from tests.network_boundary import NetworkBoundary, mysql_ci_url


REQUIRED_PRIVILEGES = frozenset({
    'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'INDEX', 'ALTER', 'REFERENCES',
})


def validate_grants(grants):
    """Fail closed on roles, global business privileges, wildcards or grant option."""
    scopes = []
    seen_database = False
    for grant in grants:
        match = re.fullmatch(r'GRANT (.+) ON (.+) TO .+', grant)
        if not match or 'WITH GRANT OPTION' in grant.upper():
            raise RuntimeError('mysql_ci_unexpected_grants')
        privileges, scope = match.groups()
        if scope == '*.*' and privileges == 'USAGE':
            scopes.append('USAGE ON *.* (no global business privileges)')
        elif scope == r'`dailydot\_test`.*' and not seen_database:
            if frozenset(privileges.split(', ')) != REQUIRED_PRIVILEGES:
                raise RuntimeError('mysql_ci_unexpected_grants')
            seen_database = True
            scopes.append(privileges + ' ON ' + scope)
        else:
            raise RuntimeError('mysql_ci_unexpected_grants')
    if not seen_database:
        raise RuntimeError('mysql_ci_database_grant_missing')
    return scopes


def inspect_server(engine, require_empty=False):
    """Check actual server identity/grants; all statements here are read-only."""
    with engine.connect() as connection:
        database, account, version, partial_revokes = connection.execute(sa.text(
            'SELECT DATABASE(), CURRENT_USER(), VERSION(), @@GLOBAL.partial_revokes'
        )).one()
        if (database != 'dailydot_test' or account != 'dailydot_test@%'
                or not version.startswith('8.0.') or partial_revokes != 0):
            raise RuntimeError('mysql_ci_server_identity_invalid')
        grants = list(connection.execute(sa.text('SHOW GRANTS FOR CURRENT_USER()')).scalars())
        scopes = validate_grants(grants)
        # No other business schema may be visible. System metadata is expected.
        visible = set(connection.execute(sa.text('SHOW DATABASES')).scalars())
        if visible - {'dailydot_test', 'information_schema', 'performance_schema', 'sys'}:
            raise RuntimeError('mysql_ci_other_database_visible')
    if require_empty:
        inspector = sa.inspect(engine)
        if inspector.get_table_names() or inspector.get_view_names():
            raise RuntimeError('mysql_ci_database_not_empty')
    return database, account, version, scopes


def main():
    # No default application, dotenv loading, migration or DDL in this entry point.
    import pytest
    patch = pytest.MonkeyPatch()
    boundary = NetworkBoundary()
    engine = None
    try:
        url = mysql_ci_url()
        boundary.install(patch)
        engine = sa.create_engine(url, echo=False, hide_parameters=True)
        database, account, version, scopes = inspect_server(engine, require_empty=True)
        print('MySQL CI preflight: fixed runner profile; empty schema; identity verified.')
        print('Database:', database, 'Account:', account, 'Version:', version)
        for scope in scopes:
            print('Verified grant:', scope)
        return 0
    except Exception:
        print('MySQL CI preflight failed (configuration, connection, identity, grants or empty schema).')
        return 1
    finally:
        if engine is not None:
            engine.dispose()
        patch.undo()


if __name__ == '__main__':
    sys.exit(main())

from typing import Union
from datetime import datetime, date, timedelta
import functools, os
import urllib.parse

import requests
from loguru import logger
import sqlalchemy as sqla
from sqlalchemy.sql import ColumnElement
from .misc import to_iso_duration


def get_druid_engine(**kwargs):
    """ Get an SQLAlchemy Engine object towards production Druid """
    # Acquire the credentials
    url = urllib.parse.urlparse(os.environ['DRUID_SERVICE_URL'])
    path = "druid/v2/sql/?header=true"
    port = url.port or 443
    username = os.environ.get('DRUID_SERVICE_USERNAME')
    password = os.environ.get('DRUID_SERVICE_PASSWORD')

    if username:
        sqla_url =  f"druid+{url.scheme}://{username}:{password}@{url.hostname}:{port}/{path}"
    else:
        sqla_url =  f"druid+{url.scheme}://{url.hostname}:{port}/{path}"

    # For some reason sqla/pydruid renders `cast(col, sqla.TIMESTAMP)` to `CAST(col AS LONG)`. LONG
    # isn't even a valid druid type. Several other cast types seem to be broken as well. This is a
    # manual override to make sqla render them properly.
    from sqlalchemy.ext.compiler import compiles
    cast_fixes = {
        sqla.types.TIMESTAMP: "TIMESTAMP",
        sqla.types.INT: "INT",
    }

    # TODO: Maybe should do this somewhere else to make sure it doesn't get called twice
    for (sqla_type, override) in cast_fixes.items():
        compiles(sqla_type, "druid")(lambda type_, compiler, override=override, **kw: override)

    kwargs = {
        "context": {
            # By default, druid does an approximate count for COUNT(DISTINCT), this turns that off.
            # You can still use APPROX_COUNT_DISTINCT to do approx count explicitly
            'useApproximateCountDistinct': False,
            # Return SQL Arrays as arrays instead of strings
            "sqlStringifyArrays": False,
            **kwargs.get("context", {}),
        },
        **kwargs,
    }

    return sqla.create_engine(sqla_url, **kwargs)

@functools.cache
def get_table(name: str, engine: sqla.engine.Engine):
    """
    Retrieve a SQLAlchemy Table object by name from the Druid database. Handles switching between
    tables by env.
    This is a little awkward to do because we don't have an ORM.
    """
    # env = get_app_settings().env
    # dev_env = table_dev_env_overrides.get(name, 'prod')
    # env = env if (env == "prod" or dev_env == "current") else dev_env
    # env_prefix = "" if env == "prod" else f"{env}-"
    # name = f"{env_prefix}{name}"

    return sqla.Table(name, sqla.MetaData(), autoload_with=engine)


def to_timestamp(col) -> ColumnElement:
    """ Cast col to sqla TIMESTAMP if needed. """
    if isinstance(col, (datetime, date)):
        return sqla.literal(col.isoformat()).cast(sqla.TIMESTAMP)
    else:
        col_type = getattr(col, 'type', None)
        if isinstance(col_type, sqla.TIMESTAMP) or col_type == sqla.TIMESTAMP:
            return col
        else:
            # TODO: There's some issue in Druid causing "ghost segments" or something. Restarted
            # ingest tasks cause several issues in the table, here it sometimes thinks that strings
            # are multivalue columns, and so the TIMESTAMP cast fails. Druid just kind of infers on
            # the fly whether a string column is multivalue so mv_to_string will work on plain
            # strings as well, and this is a workaround for part of the issue. Need to figure out
            # where things are getting corrupted.
            return sqla.cast(col, sqla.TIMESTAMP)


def time_floor(timestamp_col, granularity: Union[timedelta, str], origin=None) -> ColumnElement:
    """
    Wrapper around Druid time_floor.
    You'll typically want to use your query range start as origin.
    """
    # In our typical timeseries queries where we are grouping by the floored timestamp, if you leave
    # origin as the epoch, the first timestamp may be floored down less than your query range start/end
    # if start isn't aligned to granularity. This also leaves you with a "partial" point at the beginning
    # that doesn't include the data for its full interval. E.g. if you have 5s granularity, and start
    # is ...T00:00:3, then the first point would be ...T00:00:00 but only contain data for seconds 3 to 5.
    # If you set "origin" to the start of the query range the first timestamp returned by the query will
    # always be exactly the same as start. You can still have a partial data point at the end though.
    timestamp_col = to_timestamp(timestamp_col)
    granularity = granularity if isinstance(granularity, str) else to_iso_duration(granularity)

    if origin:
        return sqla.func.time_floor(timestamp_col, granularity, to_timestamp(origin))
    else:
        return sqla.func.time_floor(timestamp_col, granularity)


def _size_func(func):
    """ Call any_value, latest_by, etc. with a literal size value (can't use a normal bound param)"""
    def f(*args):
        if isinstance(args[-1], int):
            return func(*args[:-1], sqla.text(str(args[-1])))
        else:
            return func(*args)
    return f

any_value = _size_func(sqla.func.any_value)
latest = _size_func(sqla.func.latest)
latest_by = _size_func(sqla.func.latest_by)
earliest = _size_func(sqla.func.earliest)
earliest_py = _size_func(sqla.func.earliest_py)


def table_is_ready(engine, tbl: str):
    """
    Check whether a Druid datasource exists yet (vs. not having received its first Kafka message),
    so callers can distinguish that expected transient state from a genuine query failure.

    iDT4GDC: this used to go through `sqla.inspect(engine).has_table(tbl)`, but the installed
    pydruid dialect's `has_table()` doesn't accept the `info_cache` kwarg SQLAlchemy 2.x's
    Inspector always passes, so it raises `TypeError` unconditionally -- for every table name,
    real or not (verified directly against this stack: has_table() throws the same error for an
    existing datasource, a nonexistent one, and an aliased query name). The old except clause
    matched that error message and returned False, which meant this function always reported
    "not ready" whenever it was reached, regardless of the table's actual state. See
    execute_ignore_missing below for why that mattered beyond just this function. Query Druid's
    own INFORMATION_SCHEMA instead, which the SQL engine actually supports correctly.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sqla.text("SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = :tbl"),
                {"tbl": tbl},
            ).first()
            return row is not None
    except Exception:
        return False


def execute_ignore_missing(conn, stmt) -> sqla.CursorResult:
    """
    Wrapper conn.execute that handles missing tables.
    Druid can't have "empty" tables, so if any table in the query is missing this return an empty
    cursor. Note this may have unexpected results if you have joins/aggregations/etc that would have
    returned data with an empty table.
    """
    try:
        return conn.execute(stmt)
    except Exception as e:
        try:
            # iDT4GDC: get_final_froms() returns the FROM clause objects, which may be aliased
            # (e.g. `orm.scheduler_sim_job.alias("jobs")`). The alias's own `.name` is "jobs", not
            # a real datasource -- checking readiness of that would always report "missing"
            # regardless of the real table's state. Resolve down to the underlying table name.
            def real_table_name(t):
                return getattr(getattr(t, "element", None), "name", None) or t.name
            stmt_tables = set(real_table_name(t) for t in stmt.get_final_froms())
            missing_tables = [tbl for tbl in stmt_tables if not table_is_ready(conn.engine, tbl)]
            if missing_tables:
                logger.info(f"table(s) {', '.join(missing_tables)} missing, returning empty result")
                return conn.execute(sqla.text("SELECT 1 FROM (VALUES (1)) AS tbl(a) WHERE 1 != 1"))
        except:
            pass # Just raise the original error
        raise e


def submit_ingest(ingest: dict):
    tbl = ingest['spec']['dataSchema']['dataSource']
    url = os.environ['DRUID_SERVICE_URL'].removesuffix("/")

    # We only submit the ingests in standalone dev mode so we can skip the auth for now
    # username = os.environ.get('DRUID_SERVICE_USERNAME')
    # password = os.environ.get('DRUID_SERVICE_PASSWORD')

    logger.info(f"Submitting ingest for {tbl}")
    response = requests.post(f"{url}/druid/indexer/v1/supervisor",
        json = ingest,
        timeout = 30,
    )
    response.raise_for_status()

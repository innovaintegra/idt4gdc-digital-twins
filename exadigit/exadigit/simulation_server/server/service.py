from typing import Optional, Any
from datetime import datetime, timedelta, timezone
import psutil
import functools
import uuid, time, json, base64, os, sys, subprocess, tempfile
import sqlalchemy as sqla
from loguru import logger
from pydantic import ValidationError
from fastapi import HTTPException
from ..models.sim import Sim, ServerSimConfig, SIM_FILTERS, SIM_FIELD_SELECTORS
from ..models.base import ResponseFormat
from ..models.output import (
    COOLING_CDU_API_FIELDS, COOLING_CDU_FIELD_SELECTORS,
    COOLING_CEP_API_FIELDS, COOLING_CEP_FIELD_SELECTORS,
    SCHEDULER_SIM_JOB_API_FIELDS, SCHEDULER_SIM_JOB_FIELD_SELECTORS,
    SCHEDULER_SIM_JOB_POWER_HISTORY_API_FIELDS, SCHEDULER_SIM_JOB_POWER_HISTORY_FIELD_SELECTORS,
)
from ..util.misc import pick, omit
from ..util.k8s import submit_job, get_k8s_job, get_k8s_job_state, get_k8s_job_end_time
from ..util.druid import to_timestamp, any_value, latest, execute_ignore_missing
from ..util.api_queries import (
    Filters, Sort, QuerySpan, Granularity, expand_field_selectors, DatetimeValidator,
    DEFAULT_FIELD_TYPES,
)
from . import orm
from .config import AppDeps


def wait_until_exists(stmt: sqla.Select, *, timeout: timedelta = timedelta(minutes=1), druid_engine: sqla.Engine):
    # Hack to block until row shows up in Druid (after being read from Kafka)
    # When we upgrade druid we may be able to use a SQL INSERT instead

    # Perhaps we should move the sim table table out of Druid and into our Postgres instance so we
    # can directly update it, and just use Druid for the large timeseries tables
    record = False
    def find_record():
        try:
            return conn.execute(stmt).first()
        except Exception:
            return None

    with druid_engine.connect() as conn:
        start = time.time()
        record = find_record()
        while (time.time() - start) < timeout.total_seconds() and not record:
            time.sleep(0.5)
            record = find_record()

    if not record:
        logger.error("Timeout while waiting for record to be saved to druid")
        raise TimeoutError("Timeout while waiting for record to be saved to druid")

    return record



def run_simulation(sim_config: ServerSimConfig, deps: AppDeps):
    sim = Sim(
        # Random sim id, use base32 to make it a bit shorter
        id = base64.b32encode(uuid.uuid4().bytes).decode().rstrip('=').lower(),
        user = "unknown", # TODO pull this from cookie/auth header
        system = sim_config.system_configs[0].system_name,
        state = "running",
        start = sim_config.start,
        end = sim_config.end,
        execution_start = datetime.now(timezone.utc),
        execution_end = None,
        progress = 0,
        progress_date = sim_config.start,
        config = sim_config.model_dump(mode = 'json'),
    )
    logger.info(f"Launching simulation {sim.id}")
    deps.kafka_producer.produce("svc-event-exadigit-sim", sim.serialize_for_druid())
    deps.kafka_producer.flush()

    if 'KUBERNETES_SERVICE_HOST' in os.environ: # We're running on k8s
        submit_job({
            "metadata": {
                "name": f"exadigit-simulation-server-{sim.id}",
                "labels": {"app": "exadigit-simulation-server"},
            },
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "main",
                                "image": deps.settings.job_image,
                                "command": ['python3', "-m", "simulation_server.simulation.main"],
                                "env": [
                                    {"name": "SIM", "value": sim.model_dump_json()},
                                ],
                                "envFrom": [
                                    {"secretRef": {'name': 'prod-infra-envconfig-service-sens-creds'}},
                                ],
                                "resources": {
                                    "requests": {"cpu": "2000m", "memory": "1Gi"},
                                    "limits": {"cpu": "4000m", "memory": "6Gi"},
                                },
                            }
                        ],
                        "restartPolicy": "Never",
                    }
                },
                "backoffLimit": 0, # Don't retry on failure
            }
        })
    else: # Running locally, just use a subprocess
        # A large inline job list (roughly 1650+ jobs in the bridge/loadtest {job_id,
        # submit_time_s, runtime_s, nodes, gpus} schema) pushes the serialized Sim JSON past the
        # Linux kernel's per-argument/env-string limit (MAX_ARG_STRLEN, 128 KiB). Passing it as a
        # single "SIM" env var then makes execve() fail with
        # "OSError: [Errno 7] Argument list too long" before the child process even starts, which
        # surfaces to the caller as a 500 on POST /simulation/run. Write the payload to a temp file
        # and hand off the path instead (main.py reads it and deletes it). We still set a small SIM
        # env var with just the id, since cleanup_jobs() below reads it via psutil to identify
        # orphaned simulation subprocesses.
        with tempfile.NamedTemporaryFile(mode = 'w', suffix = '.json', delete = False) as f:
            f.write(sim.model_dump_json())
            sim_file = f.name
        proc = subprocess.Popen(
            args = [sys.executable, "-m", "simulation_server.simulation.main", "--sim-file", sim_file],
            env = {
                "SIM": json.dumps({"id": sim.id}),
                **os.environ,
            },
        )

    tbl = orm.sim
    stmt = sqla.select(tbl.c.id).where(tbl.c.id == sim.id)
    wait_until_exists(stmt, timeout = timedelta(minutes=1), druid_engine = deps.druid_engine)
    logger.info(f"Simulation {sim.id} launched")

    return sim


def cleanup_jobs(druid_engine, kafka_producer, settings):
    """
    If a simulation job dies unexpectedly (e.g. OOM error), it won't be able to send the kafka
    message marking the sim as complete, leaving the sim stuck as running. This task checks all
    running sim jobs and cleans them up if their job is dead.
    """
    if 'KUBERNETES_SERVICE_HOST' in os.environ and settings.env != 'prod':
        # Skip job cleanup on stage/dev k8s deployments to avoid multiple instances of the server
        # trying to cancel jobs
        return
    logger.info(f"Checking for stuck jobs")

    now = datetime.now(timezone.utc)
    # threshold after job has ended before sending a cancel (incase the job did send its own
    # cancel message and it just hasn't shown up in Druid yet)
    threshold = timedelta(minutes=2)

    running_sims, _ = query_sims(
        filters=SIM_FILTERS(state = ["eq:running"]),
        fields = ["all"],
        limit = 1000, # If somehow there's more than that we'll just get them next trigger
        druid_engine = druid_engine,
    )

    running_jobs = set()
    if 'KUBERNETES_SERVICE_HOST' in os.environ:
        for sim in running_sims:
            job = get_k8s_job(f"exadigit-simulation-server-{sim.id}")
            # Add a little bit of threshold to avoid potentially sending duplicate fail messages
            if get_k8s_job_state(job) == "running" or (job and now - get_k8s_job_end_time(job) < threshold):
                running_jobs.add(sim.id)
    else:
        for proc in psutil.Process().children():
            try:
                if 'simulation_server.simulation.main' in proc.cmdline():
                    sim_id = json.loads(proc.environ()["SIM"])['id']
                    if proc.is_running():
                        running_jobs.add(sim_id)
            except (psutil.Error):
                pass

    for sim in running_sims:
        if sim.id not in running_jobs and now - sim.execution_start > threshold:
            sim.state = 'fail'
            sim.execution_end = now
            sim.error_messages = "Simulation crashed"
            logger.warning(f"Marking stuck sim {sim.id} as failed")
            kafka_producer.produce("svc-event-exadigit-sim", sim.serialize_for_druid())

            # Block until saved to make sure we don't double-send
            stmt = (
                sqla.select(orm.sim.c.id)
                    .where(orm.sim.c.id == sim.id, orm.sim.c.state == 'fail')
            )
            wait_until_exists(stmt, timeout = timedelta(minutes=1), druid_engine = druid_engine)


def query_sims(*,
    filters: Optional[Filters] = None, sort: Optional[Sort] = None,
    fields: Optional[list[str]] = None,
    limit = 100, offset = 0,
    druid_engine: sqla.Engine,
) -> tuple[list[Sim], int]:
    filters = filters or Filters()
    sort = sort or Sort()
    fields = expand_field_selectors(fields, SIM_FIELD_SELECTORS)
    fields = ['id', *fields]
    needs_progress = bool(set(fields).intersection(['progress', 'progress_date']))

    if needs_progress:
        query_fields = [f for f in fields if f not in ['progress', 'progress_date']]
        query_fields = [*dict.fromkeys(query_fields + [ # Need these to calculate progress
            'id', 'start', 'end', 'execution_start', 'execution_end', 'progress_date',
        ])]
    else:
        query_fields = fields

    sims = orm.sim
    progress_tbl = orm.scheduler_sim_system

    cols = {
        "id": sims.c.id,
        "user": sims.c.user,
        "system": sims.c.system,
        "state": sims.c.state,
        "error_messages": sims.c.error_messages,
        "start": to_timestamp(sims.c.start),
        "end": to_timestamp(sims.c.end),
        "execution_start": sims.c.execution_start,
        "execution_end": to_timestamp(sims.c.execution_end),
        "progress_date": to_timestamp(sims.c.progress_date),
        "config": sims.c.config,
    }

    grouped_cols = {
        "id": any_value(sims.c.id, 40),
        "user": any_value(sims.c.user, 40),
        "system": any_value(sims.c.system, 40),
        "state": latest(sims.c.state, 12),
        "error_messages": latest(sims.c.error_messages, 512),
        "start": to_timestamp(any_value(sims.c.start, 32)),
        "end": to_timestamp(any_value(sims.c.end, 32)),
        "execution_start": to_timestamp(any_value(sims.c.execution_start, 32)),
        "execution_end": to_timestamp(latest(sims.c.execution_end, 32)),
        "progress_date": to_timestamp(latest(sims.c.progress_date, 32)),
        "config": any_value(sims.c.config, 4 * 1024),
    }

    stmt = sqla.select(*[grouped_cols[name].label(name) for name in query_fields])
    stmt = stmt.where(*filters.filter_sql(omit(cols, ['state'])))
    stmt = stmt.group_by(cols['id'])
    if filters.get('state'):
        stmt = stmt.having(*filters.filter_sql(pick(grouped_cols, ['state'])))

    count_stmt = sqla.select(sqla.func.count()).select_from(stmt.subquery())

    stmt = stmt.order_by(*sort.sort_sql(grouped_cols))
    stmt = stmt.limit(limit).offset(offset)

    with druid_engine.connect() as conn:
        results = (r._asdict() for r in execute_ignore_missing(conn, stmt))
        if 'config' in fields:
            results = ({**r, 'config': json.loads(r['config'])} for r in results)
        results = [Sim.model_validate(r) for r in results]

        if len(results) >= limit:
            total_results = execute_ignore_missing(conn, count_stmt).scalar() or 0
        else:
            total_results = len(results)

        if needs_progress:
            incomplete = [sim.id for sim in results if not sim.execution_end]
            progresses = {}

            if len(incomplete) > 0:
                stmt = (
                    sqla.select(
                        progress_tbl.c.sim_id,
                        sqla.func.max(to_timestamp(progress_tbl.c['__time'])).label('progress')
                    )
                        .where(progress_tbl.c.sim_id.in_(incomplete))
                        .group_by(progress_tbl.c.sim_id)
                )
                for r in execute_ignore_missing(conn, stmt).all():
                    progresses[r.sim_id] = DatetimeValidator.validate_strings(r.progress)

            for sim in results:
                sim_dur = sim.end - sim.start
                if sim.id in progresses:
                    # Never return progress 1 if incomplete
                    sim.progress_date = min(progresses[sim.id], sim.end - timedelta(seconds=1))
                    progress = round((progresses[sim.id] - sim.start) / sim_dur, 3)
                    sim.progress = min(max(0, progress), 0.99)
                elif not sim.execution_end:
                    sim.progress_date = sim.start
                    sim.progress = 0
                else:
                    # Use progress_date from the DB
                    sim.progress = round((sim.progress_date - sim.start) / sim_dur, 3)

        results = [Sim.model_validate(pick(r.model_dump(), fields)) for r in results]

    return results, total_results


def get_extent(tbl,
    filters: list, start: Optional[datetime], end: Optional[datetime],
    druid_engine: sqla.engine.Engine,
) -> tuple[datetime, datetime]:
    assert not start or not end or start <= end
    if not start or not end:
        stmt = (
            sqla.select(
                sqla.func.min(tbl.c['__time']).label("start"),
                sqla.func.max(tbl.c['__time']).label("end")
            )
                .where(*filters)
        )
        with druid_engine.connect() as conn:
            row = execute_ignore_missing(conn, stmt).one_or_none()
            extent_start, extent_end = None, None

            # If result set is empty, Druid returns invalid date strings (max/min possible date)
            # This bug will probably be fixed when we update Druid
            if row:
                try:
                    extent_start = DatetimeValidator.validate_strings(row.start)
                    extent_end = DatetimeValidator.validate_strings(row.end) + timedelta(seconds=1)
                except ValidationError:
                    pass

            if not extent_start or not extent_end:
                filler = start or end or datetime.now(timezone.utc)
                extent_start, extent_end = filler, filler

        if not start and end:
            start = min(end, extent_start)
        elif start and not end:
            end = max(start, extent_end)
        else: # neither are set
            start, end = extent_start, extent_end

    return (start, end)


def get_sim_extent(
    sim_id: str, start: Optional[datetime], end: Optional[datetime],
    druid_engine: sqla.engine.Engine,
):
    tbl = orm.sim
    if not start or not end:
        stmt = (
            sqla.select(tbl.c.start, tbl.c.end)
            .where(tbl.c.id == sim_id)
        )
        with druid_engine.connect() as conn:
            row = execute_ignore_missing(conn, stmt).first()
            extent_start, extent_end = None, None

            if row:
                extent_start = DatetimeValidator.validate_strings(row.start)
                extent_end = DatetimeValidator.validate_strings(row.end)
            else:
                filler = start or end or datetime.now(timezone.utc)
                extent_start, extent_end = filler, filler

        if not start and end:
            start = min(end, extent_start)
        elif start and not end:
            end = max(start, extent_end)
        else: # neither are set
            start, end = extent_start, extent_end

    return (start, end)


def _split_list(l):
    return l.split(",") if l else []


def _build_ts_query(tbl, *,
    id: str, span: QuerySpan,
    fields: list[str], filters: Filters,
    group_cols: dict, agg_cols: dict, filter_cols: dict,
):
    all_cols = {**group_cols, **agg_cols}
    select_cols = [
        span.floor(tbl.c['__time']).label('timestamp'),
        *[all_cols[name].label(name) for name in fields],
    ]

    stmt = (
        sqla.select(*select_cols)
            .where(
                tbl.c['sim_id'] == id,
                *span.filter(tbl.c['__time']),
                *filters.filter_sql(filter_cols),
            )
            .group_by(sqla.text("1"), *group_cols.values())
            .order_by(sqla.text("1"), *group_cols.values())
    )
    fields = ['timestamp', *fields]
    return fields, stmt


def _run_ts_query(*, stmt,
    span: QuerySpan, fields: list[str], format: ResponseFormat,
    druid_engine: sqla.engine.Engine,
):
    response = span.model_dump(mode = 'json')
    with druid_engine.connect() as conn:
        if format == "array":
            response['fields'] = fields
            response['data'] = [list(r) for r in execute_ignore_missing(conn, stmt)]
        else:
            response['data'] = [r._asdict() for r in execute_ignore_missing(conn, stmt)]
    return response


def build_cooling_sim_cdu_query(*,
    id: str, span: QuerySpan,
    fields: Optional[list[str]] = None, filters: Optional[Filters] = None,
):
    fields = expand_field_selectors(fields, COOLING_CDU_FIELD_SELECTORS)
    filters = filters or Filters()

    tbl = orm.cooling_sim_cdu.alias("cdus")
    filter_cols = {c: tbl.c[c] for c in COOLING_CDU_API_FIELDS}
    group_cols = {
        "name": tbl.c['name'],
    }
    agg_cols: dict[str, sqla.sql.ColumnElement] = {
        **{
            c: sqla.func.max(tbl.c[c]) for c in COOLING_CDU_API_FIELDS
            if c not in ['name', 'row', 'col']
        },
        **{c: sqla.func.any_value(tbl.c[c]) for c in ['row', 'col']},
    }

    return _build_ts_query(tbl,
        id = id, span = span, fields = fields, filters = filters,
        group_cols = group_cols, agg_cols = agg_cols, filter_cols = filter_cols,
    )


def query_cooling_sim_cdu(*,
    id: str,
    start: Optional[datetime] = None, end: Optional[datetime] = None, granularity: Granularity,
    fields: Optional[list[str]] = None, filters: Optional[Filters] = None,
    format: ResponseFormat = "object",
    druid_engine: sqla.engine.Engine,
):
    start, end = get_sim_extent(id, start, end, druid_engine = druid_engine)
    span = QuerySpan(start = start, end = end, granularity = granularity.get(start, end))
    fields, stmt = build_cooling_sim_cdu_query(
        id = id, span = span, fields = fields, filters = filters,
    )
    return _run_ts_query(
        span = span, stmt = stmt, fields = fields,
        format = format, druid_engine = druid_engine,
    )


def build_cooling_sim_cep_query(*,
    id: str, span: QuerySpan, fields: Optional[list[str]] = None,
):
    tbl = orm.cooling_sim_cep.alias("cep")

    cols = {
        "htw_flowrate": sqla.func.max(tbl.c.htw_flowrate),
        "ctw_flowrate": sqla.func.max(tbl.c.ctw_flowrate),
        "htw_return_pressure": sqla.func.max(tbl.c.htw_return_pressure),
        "htw_supply_pressure": sqla.func.max(tbl.c.htw_supply_pressure),
        "ctw_return_pressure": sqla.func.max(tbl.c.ctw_return_pressure),
        "ctw_supply_pressure": sqla.func.max(tbl.c.ctw_supply_pressure),
        "htw_return_temp": sqla.func.max(tbl.c.htw_return_temp),
        "htw_supply_temp": sqla.func.max(tbl.c.htw_supply_temp),
        "ctw_return_temp": sqla.func.max(tbl.c.ctw_return_temp),
        "ctw_supply_temp": sqla.func.max(tbl.c.ctw_supply_temp),
        "power_consumption_htwps": sqla.func.max(tbl.c.power_consumption_htwps),
        "power_consumption_ctwps": sqla.func.max(tbl.c.power_consumption_ctwps),
        "power_consumption_fan": sqla.func.max(tbl.c.power_consumption_fan),
        "htwp_speed": sqla.func.max(tbl.c.htwp_speed),
        "nctwps_staged": sqla.func.max(tbl.c.nctwps_staged),
        "nhtwps_staged": sqla.func.max(tbl.c.nhtwps_staged),
        "pue_output": sqla.func.max(tbl.c.pue_output),
        "nehxs_staged": sqla.func.max(tbl.c.nehxs_staged),
        "ncts_staged": sqla.func.max(tbl.c.ncts_staged),
        "facility_return_temp": sqla.func.max(tbl.c.facility_return_temp),
        "cdu_loop_bypass_flowrate": sqla.func.max(tbl.c.cdu_loop_bypass_flowrate),
    }

    fields = expand_field_selectors(fields, COOLING_CEP_FIELD_SELECTORS)

    return _build_ts_query(tbl,
        id = id, span = span, fields = fields, filters = Filters(),
        group_cols = {}, agg_cols = cols, filter_cols = {},
    )


def query_cooling_sim_cep(*,
    id: str,
    start: Optional[datetime] = None, end: Optional[datetime] = None, granularity: Granularity,
    fields: Optional[list[str]] = None,
    format: ResponseFormat = "object",
    druid_engine: sqla.engine.Engine,
):
    start, end = get_sim_extent(id, start, end, druid_engine = druid_engine)
    span = QuerySpan(start = start, end = end, granularity = granularity.get(start, end))
    fields, stmt = build_cooling_sim_cep_query(id = id, span = span)
    results = _run_ts_query(
        span = span, stmt = stmt, fields = fields,
        format = format, druid_engine = druid_engine,
    )
    return results


def build_scheduler_sim_jobs_query(*,
    id: str, start: Optional[datetime] = None, end: Optional[datetime] = None,
    time_travel: Optional[datetime] = None, limit = 100, offset = 0,
    fields: Optional[list[str]] = None, filters: Optional[Filters] = None,
    sort: Optional[Sort] = None,
):
    fields = expand_field_selectors(fields, SCHEDULER_SIM_JOB_FIELD_SELECTORS)
    filters = filters or Filters()
    sort = sort or Sort()

    tbl = orm.scheduler_sim_job.alias("jobs")

    cols = {
        "job_id": tbl.c.job_id,
        "name": any_value(tbl.c.name, 256),
        "node_count": latest(tbl.c.node_count),
        "time_snapshot": sqla.func.max(tbl.c['__time']),
        "time_submission": to_timestamp(latest(tbl.c.time_submission, 32)),
        "time_limit": latest(tbl.c.time_limit),
        "time_start": to_timestamp(latest(tbl.c.time_start, 32)),
        "time_end": to_timestamp(latest(tbl.c.time_end, 32)),
        "state_current": latest(tbl.c.state_current, 12),
        "nodes": latest(tbl.c.nodes, 128 * 1024),
    }


    # These fields won't change during the course of a job so we can filter using a WHERE
    where_filters = {
        **{f: tbl.c[f] for f in ['job_id', 'name', 'node_count', 'time_limit']},
        **{f: to_timestamp(tbl.c[f]) for f in ['time_submission', 'time_start']},
    }
    having_filters = {k: cols[k] for k in ["time_end", "state_current"]}

    stmt = sqla.select(*[cols[name].label(name) for name in fields])
    stmt = stmt.where(tbl.c['sim_id'] == id)
    if time_travel:
        stmt = stmt.where(tbl.c['__time'] <= to_timestamp(time_travel))

    # time_submission won't change over the course of a job_id, and time_end once set won't change
    # either. Technically Slurm can reschedule a job which updates time_submission, but we aren't
    # simulating that. And if we do, we'd just add an allocation_id like the telemetry data to
    # differentiate between requeueings.
    if start:
        stmt = stmt.where(
            to_timestamp(start) <= tbl.c['__time'], # Can ignore snapshots before start
            sqla.or_(tbl.c.time_end.is_(None), to_timestamp(start) < to_timestamp(tbl.c.time_end)),
        )
    if end:
        stmt = stmt.where(to_timestamp(tbl.c.time_submission) < to_timestamp(end))

    stmt = stmt.where(*filters.filter_sql(where_filters))
    stmt = stmt.group_by("job_id")
    stmt = stmt.having(*filters.filter_sql(having_filters))

    count_stmt = sqla.select(sqla.func.count()).select_from(stmt.subquery())
    stmt = stmt.order_by(*sort.sort_sql(cols))
    stmt = stmt.limit(limit).offset(offset)

    return fields, stmt, count_stmt


def query_scheduler_sim_jobs(*,
    id: str, start: Optional[datetime] = None, end: Optional[datetime] = None,
    time_travel: Optional[datetime] = None, limit = 100, offset = 0,
    fields: Optional[list[str]] = None, filters: Optional[Filters] = None,
    sort: Optional[Sort] = None,
    druid_engine: sqla.engine.Engine,
):
    fields, stmt, count_stmt = build_scheduler_sim_jobs_query(
        id = id, start = start, end = end,
        time_travel = time_travel, limit = limit, offset = offset,
        fields = fields, filters = filters, sort = sort,
    )

    with druid_engine.connect() as conn:
        results = (r._asdict() for r in execute_ignore_missing(conn, stmt))
        if 'nodes' in fields:
            results = [{
                **j,
                'nodes': _split_list(j['nodes']),
            } for j in results]
        else:
            results = [j for j in results]

        if len(results) >= limit:
            total_results = execute_ignore_missing(conn, count_stmt).scalar() or 0
        else:
            total_results = len(results)

    return results, total_results


def build_scheduler_sim_system_query(*,
    id: str, span: QuerySpan,
):
    tbl = orm.scheduler_sim_system.alias("system")

    cols = {
        # "down_nodes": latest(tbl.c.down_nodes, 1024),
        # Bug in druid causes this to error sometimes. This is fixed in recent versions, for now
        # just omit the column. https://lists.apache.org/api/plain?thread=3oodx9x08p0zbstqmz0o0ydxhyl1o88s
        "down_nodes": sqla.literal(""),
        "num_samples": latest(tbl.c.num_samples),
        "jobs_completed": latest(tbl.c.jobs_completed),
        "jobs_running": latest(tbl.c.jobs_running),
        "jobs_pending": latest(tbl.c.jobs_pending),
        "throughput": latest(tbl.c.throughput),
        "average_power": latest(tbl.c.average_power),
        "min_loss": latest(tbl.c.min_loss),
        "average_loss": latest(tbl.c.average_loss),
        "max_loss": latest(tbl.c.max_loss),
        "system_power_efficiency": latest(tbl.c.system_power_efficiency),
        "total_energy_consumed": latest(tbl.c.total_energy_consumed),
        "carbon_emissions": latest(tbl.c.carbon_emissions),
        "total_cost": latest(tbl.c.total_cost),
        # TODO: max is not the best aggregation here, we want latest, but there's issue in our druid
        # version with LATEST and null handling. Should be fixed when we update druid.
        "p_flops": sqla.func.max(tbl.c.p_flops),
        "g_flops_w": sqla.func.max(tbl.c.g_flops_w),
        "system_util": latest(tbl.c.system_util),
    }

    fields = [*cols.keys()] # TODO add fields to endpoint

    return _build_ts_query(tbl,
        id = id, span = span, fields = fields, filters = Filters(),
        group_cols = {}, agg_cols = cols, filter_cols = {},
    )


def query_scheduler_sim_system(*,
    id: str,
    start: Optional[datetime] = None, end: Optional[datetime] = None, granularity: Granularity,
    format: ResponseFormat = "object",
    druid_engine: sqla.engine.Engine,
):
    start, end = get_sim_extent(id, start, end, druid_engine = druid_engine)
    span = QuerySpan(start = start, end = end, granularity = granularity.get(start, end))
    fields, stmt = build_scheduler_sim_system_query(id = id, span = span)
    results = _run_ts_query(
        span = span, stmt = stmt, fields = fields,
        format = format, druid_engine = druid_engine,
    )

    if 'down_nodes' in fields:
        for r in results['data']:
            r['down_nodes'] = _split_list(r['down_nodes'])

    return results


def query_scheduler_sim_power_history(*,
    id: str, job_id: str,
    start: Optional[datetime] = None, end: Optional[datetime] = None, granularity: Granularity,
    fields: Optional[list[str]] = None,
    format: ResponseFormat = "object",
    druid_engine: sqla.engine.Engine,
):
    tbl = orm.scheduler_sim_job_power_history.alias("power-history")
    start, end = get_extent(tbl, [
        tbl.c.sim_id == id, tbl.c.job_id == job_id,
    ], start, end, druid_engine = druid_engine)
    span = QuerySpan(start = start, end = end, granularity = granularity.get(start, end))
    fields, stmt = build_scheduler_sim_power_history_query(
        id = id, job_id = job_id, span = span, fields = fields,
    )
    return _run_ts_query(
        span = span, stmt = stmt, fields = fields,
        format = format, druid_engine = druid_engine,
    )


def build_scheduler_sim_power_history_query(*,
    id: str, job_id: str, span: QuerySpan,
    fields: Optional[list[str]] = None,
):
    fields = expand_field_selectors(fields, SCHEDULER_SIM_JOB_POWER_HISTORY_FIELD_SELECTORS)

    tbl = orm.scheduler_sim_job_power_history.alias("power-history")
    agg_cols: dict[str, sqla.sql.ColumnElement] = {
        "power": sqla.func.max(tbl.c['power']),
        "job_id": any_value(tbl.c['job_id'], 12), # We're filtering by job_id so they should all be the same
    }
    filters = Filters.parse_filters({'job_id': 'string'}, DEFAULT_FIELD_TYPES, {
        'job_id': [f'eq:{job_id}'],
    })
    return _build_ts_query(tbl,
        id = id, span = span, fields = fields, filters = filters,
        group_cols = {}, agg_cols = agg_cols, filter_cols = {"job_id": tbl.c['job_id']},
    )


@functools.cache
def get_systems():
    from raps.system_config import list_systems
    return [get_system_info(s) for s in list_systems()]


@functools.cache
def get_system_info(system: str):
    from raps.system_config import list_systems
    from raps import Engine, SingleSimConfig
    from raps.stats import get_gauge_limits
    if system not in list_systems():
        raise HTTPException(status_code=404, detail=f"System {system} not found")
    engine = Engine(SingleSimConfig(system = system))
    return {
        "name": system,
        **get_gauge_limits(engine),
    }

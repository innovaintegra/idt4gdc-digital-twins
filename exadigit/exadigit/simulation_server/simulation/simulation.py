from typing import NamedTuple
from datetime import datetime, timedelta
import functools, itertools
import importlib, importlib.util
import orjson
from loguru import logger
from raps import Engine
from raps.job import Job as RapsJob
from raps.stats import RunningStats
from ..models.sim import ServerSimConfig
from ..models.output import (
    JobStateEnum, SchedulerSimJob, SchedulerSimJobPowerHistory, SchedulerSimSystem, CoolingSimCDU,
    CoolingSimCEP,
)
from ..util.misc import nest_dict
from . import SimException


class SimTickOutput(NamedTuple):
    timestamp: datetime
    scheduler_sim_system: list[SchedulerSimSystem]
    scheduler_sim_jobs: list[SchedulerSimJob]
    cooling_sim_cdus: list[CoolingSimCDU]
    cooling_sim_cep: list[CoolingSimCEP]
    power_history: list[SchedulerSimJobPowerHistory]


def get_job_hash(job: RapsJob):
    """ Return string that can be used to check if any meaningful state changed """
    return orjson.dumps([
        str(job.id),
        job.name,
        job.nodes_required,
        job.submit_time,
        job.time_limit,
        job.start_time,
        job.end_time,
        job.current_state.name,
        # Node list shouldn't change once set so just do len instead of serializing the large list
        len(job.scheduled_nodes) if job.scheduled_nodes else None,
    ], option=orjson.OPT_SERIALIZE_NUMPY)


def snap_sample_rate(desired_rate: int, actual_rate: int):
    """
    Returns a sample rate close to desired_rate, but is still divisible by actual_rate.
    E.g. if power is being ticked every 3 seconds, but disired sample rate is 10, round it to 9.
    """
    if actual_rate >= desired_rate:
        return actual_rate
    else:
        return int(desired_rate / actual_rate) * actual_rate


def run_simulation(sim_config: ServerSimConfig):
    # if sim_config.replay_files:
    #     sim_config = sim_config.model_copy(update={
    #         "dataloader": f"simulation_server.simulation.dataloaders.{sim_config.system}",
    #         "replay": sim_config.replay_files
    #     })
    if sim_config.jobs:
        sim_config = sim_config.model_copy(update={
            "dataloader": "simulation_server.simulation.dataloaders.inline_jobs"
        })
    elif sim_config.replay:
        if not isinstance(sim_config.system, str):
            raise SimException(f"replay is not supported for custom systems")
        dataloader = f"simulation_server.simulation.dataloaders.{sim_config.system}"
        if not importlib.util.find_spec(dataloader):
            raise SimException(f"{sim_config.system} does not support replay")
        sim_config = sim_config.model_copy(update = {"dataloader": dataloader})

    engine = Engine(sim_config)
    running_stats = RunningStats(engine)

    sample_system = 1
    sample_power = snap_sample_rate(5, int(sim_config.time_delta.total_seconds()))
    sample_cooling = snap_sample_rate(5, int(sim_config.time_delta.total_seconds()))

    def offset_to_time(offset):
        if offset is not None:
            return engine.start + timedelta(seconds=offset - engine.timestep_start)
        else:
            return None

    # Memoized function to convert raps indexes into node names.
    # Memo increases performance since it gets called on snapshots of the same job multiple times.
    @functools.lru_cache(maxsize = 65_536)
    def parse_nodes(node_indexes: tuple[int]):
        return [engine.telemetry.node_index_to_name(i) for i in node_indexes]

    @functools.lru_cache(maxsize = 16384)
    def cdu_info(cdu_index: int):
        cdu_name = engine.telemetry.cdu_index_to_name(cdu_index)
        row, col = engine.telemetry.cdu_pos(cdu_index)
        return cdu_name, row, col
    
    def parse_job(job: RapsJob, timestamp: datetime):
        # Output jobs only if something changed
        time_end = offset_to_time(job.end_time)
        # end_time is set to its planned end once its scheduled. Set it to None for
        # unfinished jobs here
        if time_end is not None and (job.start_time is None or time_end > timestamp):
            time_end = None
        return SchedulerSimJob.model_validate({
            "job_id": str(job.id),
            "name": job.name,
            "node_count": job.nodes_required,
            "time_snapshot": timestamp,
            "time_submission": offset_to_time(job.submit_time),
            "time_limit": job.time_limit,
            "time_start": offset_to_time(job.start_time),
            "time_end": time_end,
            "state_current": JobStateEnum(job.current_state.name),
            "nodes": parse_nodes(tuple(job.scheduled_nodes)) if job.scheduled_nodes else None,
            # How does the new job.power attribute work? Is it total_energy?
            # Or just the current wattage?
            # power = job.power,
        })

    job_hashes: dict[int, bytes] = {}
    # Keep record of how many power history steps we've emitted for each job
    job_power_history_counts: dict[int, int] = {}

    for tick in engine.run_simulation():
        timestamp: datetime = offset_to_time(tick.current_timestep)
        unix_timestamp = int(timestamp.timestamp())
        is_last_tick = (timestamp + timedelta(seconds=1) >= sim_config.end)

        scheduler_sim_system: list[SchedulerSimSystem] = []
        if unix_timestamp % sample_system == 0 or is_last_tick:
            down_nodes = parse_nodes(tuple(tick.down_nodes))
            stats = running_stats.get_stats()

            scheduler_sim_system = [SchedulerSimSystem.model_validate({
                "timestamp": timestamp,
                "down_nodes": down_nodes,
                "num_samples": stats['num_samples'],

                "jobs_completed": engine.jobs_completed,
                "jobs_running": len(tick.running),
                "jobs_pending": len(tick.queue),
                "throughput": stats["throughput"],

                "average_power": stats['average_power'] * 1_000_000,
                "min_loss": stats['min_loss'] * 1_000_000,
                "average_loss": stats['average_loss'] * 1_000_000,
                "max_loss": stats['max_loss'] * 1_000_000,
                "system_power_efficiency": stats['system_power_efficiency'],
                "total_energy_consumed": stats['total_energy_consumed'],
                "carbon_emissions": stats['carbon_emissions'],
                "total_cost": stats['total_cost'],

                "p_flops": tick.p_flops,
                "g_flops_w": tick.g_flops_w,
                "system_util": tick.system_util,
            })]

        scheduler_sim_jobs: list[SchedulerSimJob] = []
        power_history: list[SchedulerSimJobPowerHistory] = []

        # Only output running jobs when the state changes
        for job in tick.queue:
            # Just use a constant as hash for queued jobs to avoid computing the hash repeatedly for
            # them. This assumes queued jobs don't change any meaningful state until they run
            job_hash = b"queued"
            if is_last_tick or job_hashes.get(job.id) != job_hash:
                scheduler_sim_jobs.append(parse_job(job, timestamp))
                job_hashes[job.id] = job_hash
        for job in tick.running:
            job_hash = get_job_hash(job)
            if is_last_tick or job_hashes.get(job.id) != job_hash:
                scheduler_sim_jobs.append(parse_job(job, timestamp))
                job_hashes[job.id] = job_hash
        for job in itertools.chain(tick.completed, tick.killed):
            scheduler_sim_jobs.append(parse_job(job, timestamp))
            job_hashes.pop(job.id, None)

        for job in itertools.chain(tick.running, tick.completed, tick.killed):
            if job_power_history_counts.get(job.id, 0) < len(job.power_history):
                power_history.append(SchedulerSimJobPowerHistory(
                    timestamp = timestamp,
                    job_id = str(job.id),
                    power = job.power_history[-1],
                ))
                job_power_history_counts[job.id] = len(job.power_history)

        cooling_sim_cdus: list[CoolingSimCDU] = []
        cooling_sim_cep: list[CoolingSimCEP] = []

        cooling_sim_cdu_map: dict[int, dict] = {}
        if tick.power_df is not None and (is_last_tick or unix_timestamp % sample_power == 0):
            # TODO: RAPS supports any number of racks per CDU, while this is still hard-coded to the
            # 3 in Frontier. This will work for any system with 3 or less. We need to rethink how
            # the racks are stored in the DB, maybe a separate table
            for i, point in tick.power_df.iterrows():
                cooling_sim_cdu_map[int(point['CDU'])] = {
                    "rack_1_power": point.get('Rack 1'),
                    "rack_2_power": point.get('Rack 2'),
                    "rack_3_power": point.get('Rack 3'),
                    "total_power": point['Sum'],
                    "rack_1_loss": point.get('Loss 1'),
                    "rack_2_loss": point.get('Loss 2'),
                    "rack_3_loss": point.get('Loss 3'),
                    "total_loss": point['Loss'],
                }

        if tick.fmu_outputs and (is_last_tick or unix_timestamp % sample_cooling == 0):
            # CDU columns are output in the dict with keys like this:
            # "simulator[1].datacenter[1].computeBlock[1].cdu[1].summary.m_flow_prim"
            # "simulator[1].datacenter[1].computeBlock[1].cdu[1].summary.V_flow_prim_GPM"
            # "simulator[1].datacenter[1].computeBlock[2].cdu[1].summary.m_flow_prim"
            # "simulator[1].datacenter[1].computeBlock[2].cdu[1].summary.V_flow_prim_GPM"
            # nest_dict will un-flatten it
            fmu_data = nest_dict({**tick.fmu_outputs})

            cdus_data = fmu_data['simulator'][1]['datacenter'][1]['computeBlock']
            for cdu, cdu_data in cdus_data.items():
                cdu_data = cdu_data['cdu'][1]['summary']
                cooling_sim_cdu_map[cdu] = {
                    **cooling_sim_cdu_map.get(cdu, {}),
                    "work_done_by_cdup": cdu_data['W_flow_CDUP_kW'],
                    "rack_return_temp": cdu_data['T_sec_r_C'],
                    "rack_supply_temp": cdu_data['T_sec_s_C'],
                    "rack_supply_pressure": cdu_data['p_sec_s_psig'],
                    "rack_return_pressure": cdu_data['p_sec_r_psig'],
                    "rack_flowrate": cdu_data['V_flow_sec_GPM'],
                    "facility_return_temp": cdu_data["T_prim_r_C"],
                    "facility_supply_temp": cdu_data['T_prim_s_C'],
                    "facility_supply_pressure": cdu_data['p_prim_s_psig'],
                    "facility_return_pressure": cdu_data['p_prim_r_psig'],
                    "facility_flowrate": cdu_data['V_flow_prim_GPM'],
                }

            cep_data = fmu_data['simulator'][1]['centralEnergyPlant'][1]
            cooling_sim_cep = [CoolingSimCEP.model_validate({
                "timestamp": timestamp,
                "htw_flowrate": cep_data['hotWaterLoop'][1]['summary']['V_flow_htw_GPM'],
                "ctw_flowrate": cep_data['coolingTowerLoop'][1]['summary']['V_flow_ctw_GPM'],
                "htw_return_pressure": cep_data['hotWaterLoop'][1]['summary']['p_fac_htw_r_psig'],
                "htw_supply_pressure": cep_data['hotWaterLoop'][1]['summary']['p_fac_htw_s_psig'],
                "ctw_return_pressure": cep_data['coolingTowerLoop'][1]['summary']['p_fac_ctw_r_psig'],
                "ctw_supply_pressure": cep_data['coolingTowerLoop'][1]['summary']['p_fac_ctw_s_psig'],
                "htw_return_temp": cep_data['hotWaterLoop'][1]['summary']['T_fac_htw_r_C'],
                "htw_supply_temp": cep_data['hotWaterLoop'][1]['summary']['T_fac_htw_s_C'],
                "ctw_return_temp": cep_data['coolingTowerLoop'][1]['summary']['T_fac_ctw_r_C'],
                "ctw_supply_temp": cep_data['coolingTowerLoop'][1]['summary']['T_fac_ctw_s_C'],
                "power_consumption_htwps": cep_data['hotWaterLoop'][1]['summary']['W_flow_HTWP_kW'],
                "power_consumption_ctwps": cep_data['coolingTowerLoop'][1]['summary']['W_flow_CTWP_kW'],
                "power_consumption_fan": cep_data['coolingTowerLoop'][1]['summary']['W_flow_CT_kW'],
                "htwp_speed": cep_data['hotWaterLoop'][1]['summary']['N_HTWP'],
                "nctwps_staged": cep_data['coolingTowerLoop'][1]['summary']['n_CTWPs'],
                "nhtwps_staged": cep_data['hotWaterLoop'][1]['summary']['n_HTWPs'],
                "pue_output": fmu_data['pue'],
                "nehxs_staged": cep_data['hotWaterLoop'][1]['summary']['n_EHXs'],
                "ncts_staged": cep_data['coolingTowerLoop'][1]['summary']['n_CTs'],
                "facility_return_temp": cep_data['hotWaterLoop'][1]['summary']['T_fac_htw_r_C'],
                "cdu_loop_bypass_flowrate": fmu_data['simulator'][1]['datacenter'][1]['summary']['V_flow_bypass_GPM'],
            })]

        for cdu_index, cdu_data in cooling_sim_cdu_map.items():
            cdu_name, row, col = cdu_info(cdu_index)
            cdu_data.update(timestamp = timestamp, name = cdu_name, row = row, col = col)
            cooling_sim_cdus.append(CoolingSimCDU.model_validate(cdu_data))

        yield SimTickOutput(
            timestamp = timestamp,
            scheduler_sim_system = scheduler_sim_system,
            scheduler_sim_jobs = scheduler_sim_jobs,
            cooling_sim_cdus = cooling_sim_cdus,
            cooling_sim_cep = cooling_sim_cep,
            power_history = power_history,
        )

_CP_WATER = 4.186      # kJ/(kg·K)
_KG_S_TO_GPM = 15.8508  # 1 kg/s → US GPM


class SimpleCoolingModel:
    """
    Linear PUE cooling model for liquid-cooled systems that don't have an FMU.

    PUE is interpolated linearly between pue_idle (at 0% load) and pue_full_load
    (at 100% load), reflecting the improved efficiency of liquid cooling under
    higher thermal load.

    Temperature estimates use a steady-state energy balance across each CDU:
      Q = ṁ · Cp · ΔT
    Flow rate is fixed at the value required to handle peak IT load with a 15°C
    secondary-loop temperature rise. Facility supply temperature is derived from
    the configured wet-bulb temperature plus a 3°C chiller approach; rack supply
    adds a further 5°C CDU heat-exchanger approach.
    """

    def __init__(self, **config):
        self.pue_idle = config.get('PUE_IDLE', 1.25)
        self.pue_full_load = config.get('PUE_FULL_LOAD', 1.10)
        self.num_cdus = max(int(config.get('NUM_CDUS', 1)), 1)

        # Facility supply temp: chiller output is wet-bulb + 3°C
        wet_bulb_k = config.get('WET_BULB_TEMP', 285.0)
        self._t_facility_supply_c = (wet_bulb_k - 273.15) + 3.0

        # Rack-side supply is ~5°C above facility supply (CDU HX approach)
        self._hx_approach_c = 5.0

        # Nominal secondary flow rate sized for peak IT load at 15°C ΔT
        target_dt = 15.0
        available_nodes = config.get('AVAILABLE_NODES', config.get('TOTAL_NODES', 512))
        nodes_per_cdu = available_nodes / self.num_cdus
        peak_node_w = (
            config.get('POWER_CPU_MAX', 280) * config.get('CPUS_PER_NODE', 2)
            + config.get('POWER_GPU_MAX', 0) * config.get('GPUS_PER_NODE', 0)
            + config.get('POWER_MEM', 0)
        )
        peak_kw_per_cdu = (nodes_per_cdu * peak_node_w) / 1000.0
        self._nominal_flow_kg_s = peak_kw_per_cdu / (_CP_WATER * target_dt)

        # CDU pump power per CDU (kW)
        self._cdu_pump_kw = config.get('POWER_CDU', 0) / 1000.0 / self.num_cdus

        # Fixed nominal loop pressures (psig)
        self._p_supply_psig = 20.0
        self._p_return_psig = 15.0

    def initialize(self):
        pass

    def simulate_cooling(self, *, rack_power, engine):
        total_it_kw = float(engine.sys_power)
        load_frac = engine.num_active_nodes / max(engine.config['AVAILABLE_NODES'], 1)
        pue = self.pue_idle + (self.pue_full_load - self.pue_idle) * load_frac
        cooling_kw = total_it_kw * (pue - 1.0)

        # Per-CDU energy balance: Q = ṁ·Cp·ΔT  →  T_return = T_supply + Q/(ṁ·Cp)
        it_kw_per_cdu = total_it_kw / self.num_cdus
        flow = max(self._nominal_flow_kg_s, 1e-9)
        dt = it_kw_per_cdu / (_CP_WATER * flow)

        t_sec_s = self._t_facility_supply_c + self._hx_approach_c
        t_sec_r = t_sec_s + dt
        t_prim_s = self._t_facility_supply_c
        t_prim_r = t_prim_s + dt
        flow_gpm = flow * _KG_S_TO_GPM

        cdu_temps = {
            i + 1: {
                'rack_supply_temp': t_sec_s,
                'rack_return_temp': t_sec_r,
                'facility_supply_temp': t_prim_s,
                'facility_return_temp': t_prim_r,
                'rack_flowrate': flow_gpm,
                'facility_flowrate': flow_gpm,
                'rack_supply_pressure': self._p_supply_psig,
                'rack_return_pressure': self._p_return_psig,
                'facility_supply_pressure': self._p_supply_psig,
                'facility_return_pressure': self._p_return_psig,
                'work_done_by_cdup': self._cdu_pump_kw,
            }
            for i in range(self.num_cdus)
        }

        cooling_inputs = {'total_it_power_kw': total_it_kw}
        cooling_outputs = {
            'total_cooling_power_kw': cooling_kw,
            'total_facility_power_kw': total_it_kw + cooling_kw,
            'pue': pue,
            'cdu_temps': cdu_temps,
        }
        return cooling_inputs, cooling_outputs
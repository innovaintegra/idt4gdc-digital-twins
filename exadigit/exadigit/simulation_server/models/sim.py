from __future__ import annotations
from typing import Optional, Literal, Annotated as A, Any
import json
from pathlib import Path
from pydantic import AwareDatetime, Field, model_validator, BeforeValidator
from raps import SingleSimConfig
from raps.utils import AutoAwareDatetime, ResolvedPath

from .base import BaseModel
from ..util.misc import omit
from ..util.api_queries import filter_params, sort_params


class Sim(BaseModel):
    """ Represents a single simulation run """

    id: str
    """ Unique id of the simulation """

    user: Optional[str] = None
    """ User who launched the simulation """

    system: Optional[str] = None

    state: Optional[Literal['running', 'success', 'fail']] = None

    error_messages: Optional[str] = None
    """ Any warnings or error messages from the simulation run """

    start: Optional[AwareDatetime] = None
    """
    The start of the date range the simulation is running over.

    If the simulation is configured to use real telemetry data (such as replaying jobs) then this is
    the start of the date range the telemetry will come from.
    If the simulation is simulating everything from scratch, then the start/end date range only 
    matters to set how long the simulation should run for.
    """

    end: Optional[AwareDatetime] = None
    """ See sim_start """

    execution_start: Optional[AwareDatetime] = None
    """ The real time the simulation started running """

    execution_end: Optional[AwareDatetime] = None
    """ The real time the simulation finished """

    progress_date: Optional[AwareDatetime] = None
    """
    Current point in the simulation. You can assume every time step smaller than this has been
    generated. For succesfully completed sims, this will always be the same as end. For failed sims,
    it will show how far the sim got before failure.
    """

    progress: A[Optional[float], Field(ge=0, le=1)] = None
    """ Float from 0 -> 1 summarizing the simulation progress """

    config: Optional[dict] = None
    """ Original config used to run the simulation """


    def serialize_for_druid(self):
        timestamp = self.execution_end or self.execution_start
        return json.dumps({
            **self.model_dump(mode = 'json', exclude = {"progress"}),
            "timestamp": timestamp.isoformat(),
            "config": json.dumps(self.config),
        }).encode()


SIM_API_FIELDS = {
    'id': 'string',
    'user': 'string',
    'system': 'string',
    'state': 'string',
    'error_messages': 'string',
    'start': 'date',
    'end': 'date',
    'execution_start': 'date',
    'execution_end': 'date',
    'progress_date': 'date',
    'progress': 'number',
    'config': 'string',
}
SIM_FIELD_SELECTORS = {
    "default": [
        "user", "system", "state", "error_messages", "start", "end", "execution_start", "execution_end",
        "progress", "progress_date",
    ],
    "all": ['default', 'config'],
}
SIM_FILTERS = filter_params(omit(SIM_API_FIELDS, ['progress', 'progress_date', 'config']))
SIM_SORT = sort_params(omit(SIM_API_FIELDS, ['progress', 'progress_date', 'config']), [
    "asc:execution_start", "asc:execution_end", "asc:start", "asc:end", "asc:id",
])


class ServerSimConfig(SingleSimConfig):
    start: AutoAwareDatetime  # make start required
    """ Start of the simulation """

    # replay_files: list[ResolvedPath] | None = None
    jobs: list[dict[str, Any]] | None = None
    """
    Optional inline workload. If provided, the server will construct a workload from these jobs instead of using DB replay.
    Expected dict keys: job_id, submit_time_s, runtime_s, nodes, gpus
    """

    replay: A[list[ResolvedPath] | None,
              BeforeValidator(lambda r: ['database'] if r else None, bool)] = None
    """ Whether to enable job replay. Pulls data from the database """
    # RAPS replay expects a list of paths, but that's not relevant when we are pulling the data from
    # the database. We accept true/false as input and just put a dummy value in for the list.

    def __init__(self, /, **data):
        # Override context to set base_path
        RAPS_PATH = (Path(__file__) / '../../../raps').resolve()
        self.__pydantic_validator__.validate_python(
            data,
            self_instance=self,
            context={
                "base_path": RAPS_PATH,
                "force_under_base_path": True,
            }
        )

    @model_validator(mode = "before")
    def _validate_server_sim_config(cls, data):
        data = {**data}
        # Force these options regardless of input
        data['noui'] = True
        data['output'] = "none"
        if data.get("workload") == "replay" and "replay" not in data and not data.get("jobs"):
            data['replay'] = True

        return data

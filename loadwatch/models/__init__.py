"""ORM-модели. Импорт пакета регистрирует все таблицы в ``Base.metadata``."""

from loadwatch.models.scenario import Scenario
from loadwatch.models.scenario_secret import ScenarioSecret
from loadwatch.models.schedule import Schedule
from loadwatch.models.site import Site, VerificationMethod, VerificationStatus
from loadwatch.models.test_run import RunMetrics, RunSource, RunStatus, TestRun
from loadwatch.models.user import User

__all__ = [
    "RunMetrics",
    "RunSource",
    "RunStatus",
    "Scenario",
    "ScenarioSecret",
    "Schedule",
    "Site",
    "TestRun",
    "User",
    "VerificationMethod",
    "VerificationStatus",
]

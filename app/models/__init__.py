"""All model modules must be imported here so `Base.metadata` is fully
populated before Alembic (or `create_all`) inspects it — cross-domain
foreign keys are declared by table name string and only resolve once every
module has been loaded once."""

from app.models.base import Base
from app.models import (  # noqa: F401
    academic_record,
    academic_structure,
    admin,
    attendance,
    awards,
    grades,
    learners,
    organization,
    rbac,
    reports,
    subjects,
)

__all__ = ["Base"]

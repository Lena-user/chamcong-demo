"""Import every model so Base.metadata is complete (used by Alembic and tests)."""
from app.core.models_base import Base
from app.modules.attendance import models as _attendance  # noqa: F401
from app.modules.employees import models as _employees  # noqa: F401
from app.modules.payroll import models as _payroll  # noqa: F401
from app.modules.requests import models as _requests  # noqa: F401

__all__ = ["Base"]

"""API request contracts shared by route handlers.

Validation defaults and limits live here; processing stays in the API/services.
"""
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field
from src.tools.validation_rules import ValidationRule


class CleaningAction(BaseModel):
    model_config = {"extra": "forbid", "allow_inf_nan": False}
    action: str
    column: str
    strategy: str | None = None
    fill_value: Any = None
    target_case: str | None = None
    date_format: str | None = None
    context_column: str | None = None
    context_values: List[str] = Field(default_factory=list)
    statistic: str | None = None
    round_result: bool = False
    lower_bound: float | None = None
    upper_bound: float | None = None
    mapping: Dict[str, str] = Field(default_factory=dict, max_length=500)
    approved: bool = False

class CleaningRequest(BaseModel):
    filename: str
    actions: List[CleaningAction] = Field(default_factory=list, max_length=500)
    dataset_id: str | None = None
    reading_revision: str | None = None
    decision_revision: str | None = None

class ReadingOptions(BaseModel):
    model_config = {"extra": "forbid"}
    delimiter: str | None = None
    encoding: str | None = Field(default=None, max_length=80)
    header_row: int = Field(default=1, ge=1, le=10000)
    sheet_name: str | None = Field(default=None, max_length=200)

class InspectionConfirmation(BaseModel):
    revision: str

class PreviewQuery(BaseModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    query: str = Field(default="", max_length=500)
    sort_column: str | None = None
    descending: bool = False
    missing_column: str | None = None
    columns: list[str] | None = None
    revision: str

RoleName = Literal['unknown', 'identifier', 'email', 'phone', 'rating', 'salary', 'date', 'category', 'skills', 'text', 'number']

class DecisionRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    revision: str
    roles: Dict[str, RoleName] = Field(default_factory=dict, max_length=500)
    rules: List[ValidationRule] = Field(default_factory=list, max_length=200)

class EffectRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    revision: str
    decision_revision: str | None = None
    actions: List[CleaningAction] = Field(default_factory=list, max_length=500)

class SavePlanRequest(EffectRequest):
    name: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")
    approved: bool = False
    plan_id: str | None = None

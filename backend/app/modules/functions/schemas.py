from pydantic import BaseModel, Field
from typing import Literal


class FunctionCategory(BaseModel):
    name: str
    count: int


class BuiltInFunction(BaseModel):
    name: str
    category: str
    signature: str
    return_type: str
    description: str


class UDFCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    database: str = Field(default='', max_length=256)
    function_type: Literal['sql', 'java', 'python'] = 'sql'
    scope: Literal['database', 'global'] = 'database'
    args: list[dict] = Field(default_factory=list)  # [{name, type}]
    return_type: str = Field(default='STRING')
    body: str = Field(default='')  # SQL expression for SQL UDF
    properties: dict[str, str] = Field(default_factory=dict)  # symbol, type, file for Java


class UDFResponse(BaseModel):
    name: str
    database: str
    function_type: str
    scope: str
    args: str
    return_type: str
    body: str | None = None


class UDFListResponse(BaseModel):
    functions: list[UDFResponse]
    count: int


class BuiltInFunctionListResponse(BaseModel):
    functions: list[BuiltInFunction]
    categories: list[FunctionCategory]
    count: int

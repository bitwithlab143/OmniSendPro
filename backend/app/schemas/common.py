from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    message: str

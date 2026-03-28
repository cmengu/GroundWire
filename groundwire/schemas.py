"""Pydantic models for Anthropic structured outputs (messages.parse / model_validate_json)."""
from pydantic import BaseModel, Field


class TrajectoryRubric(BaseModel):
    goal_alignment: float = Field(ge=0.0, le=1.0)
    action_efficiency: float = Field(ge=0.0, le=1.0)
    risk_signal: float = Field(ge=0.0, le=1.0)
    reason: str
    suggestion: str


class QuirksList(BaseModel):
    quirks: list[str] = Field(default_factory=list)


class IntentPhrase(BaseModel):
    phrase: str = Field(..., description="3-7 words, what the agent is trying to do now")


class CritiqueText(BaseModel):
    critique: str = Field(..., description="Reflexion critique in plain English")


class CompressedGoal(BaseModel):
    goal: str = Field(..., description="Two-sentence or three-line structured goal for the agent")


class SemanticProfile(BaseModel):
    profile: str = Field(..., description="One sentence strategic site profile")


class FaithfulnessScore(BaseModel):
    faithfulness: float = Field(ge=0.0, le=1.0)
    notes: str

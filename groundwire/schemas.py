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


class HypothesisResult(BaseModel):
    """
    Structured output from the SelfHealer's hypothesis-generation LLM call.

    Fields:
        quirk: One-sentence description of the site behaviour causing the deviation.
        suggested_goal_prefix: Instruction prefix to prepend to the replanned goal
            (e.g. "Accept the cookie modal before navigating to the target section.").
        confidence: Healer's prior confidence (0–1) that this hypothesis explains the stall.
    """

    quirk: str = Field(..., description="One-sentence site-behaviour quirk causing the deviation")
    suggested_goal_prefix: str = Field(
        ...,
        description="Short instruction to prepend to the replanned goal to avoid the deviation",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Prior confidence this hypothesis is correct")


class BlockClassification(BaseModel):
    """
    Structured output from AdversarialHardener's block-classification LLM call.

    Fields:
        block_type: Canonical block category (e.g. "cloudflare", "datadome", "captcha", "geo_block").
        note: Human-readable explanation of what was detected.
        escalate_to_human: True when the hardener cannot auto-recover (CAPTCHA challenge,
            hard geo-block, account suspension). False when auto-retry with a stealth
            profile is worth attempting.
        recommended_profile: TinyFish browser profile for the retry ("lite" or "stealth").
        recommended_proxy: True if a residential proxy is advised for the retry.
    """

    block_type: str = Field(..., description="Canonical block category identifier")
    note: str = Field(..., description="Human-readable explanation of the block")
    escalate_to_human: bool = Field(
        ...,
        description="True when auto-recovery is not possible; human intervention required",
    )
    recommended_profile: str = Field(
        default="stealth",
        description="TinyFish browser profile for retry: 'lite' or 'stealth'",
    )
    recommended_proxy: bool = Field(
        default=False,
        description="True if a residential proxy should be used for the retry",
    )

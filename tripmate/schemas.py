"""Pydantic schema for the structured itinerary produced by the output parser."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

WeatherSource = Literal["forecast", "historical_proxy", "historical_actual", "unavailable", "not_checked"]


class DayPlan(BaseModel):
    day: int = Field(description="Day number starting at 1")
    date: Optional[str] = Field(None, description="YYYY-MM-DD if known")
    title: str = Field(description="Short theme for the day")
    morning: str
    afternoon: str
    evening: str
    weather: Optional[str] = Field(None, description="That day's weather exactly as reported by the tool, else null")


class BudgetLine(BaseModel):
    category: str
    amount: float


class TripPlan(BaseModel):
    destination: str
    country: Optional[str] = None
    start_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    travellers: int = 1
    interests: List[str] = Field(default_factory=list)
    weather_source: WeatherSource = Field(description="Value of the weather tool's 'source' field, "
                                                      "'unavailable' if it failed, 'not_checked' if never called")
    weather_summary: str
    days: List[DayPlan]
    budget_currency: Optional[str] = None
    budget_lines: List[BudgetLine] = Field(default_factory=list)
    budget_total: Optional[float] = Field(None, description="Total from the budget tool, else null")
    tips: List[str] = Field(default_factory=list)
    caveats: List[str] = Field(default_factory=list,
                               description="Failed tools, fallbacks, estimates and anything the user should verify")
    sources: List[str] = Field(default_factory=list, description="Services the facts came from")

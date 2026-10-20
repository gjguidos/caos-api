from pydantic import BaseModel


# 💰 Economic Identity
class EconomicProfile(BaseModel):
    economic_class: str | None = "middle"
    economic_outlook: str | None = "balanced"
    occupation_type: str | None = None
    trade_preferences: str | None = "neutral"
    education_level: str | None = "high"
    year_income: float | None = 0.0
    financial_knowledge: str | None = "basic"
    financial_goals: str | None = "stable"
    spending_habits: str | None = "balanced"
    investment_preferences: str | None = "diverse"
    savings_rate: float | None = 0.2
    retirement_age: int | None = 65
    retirement_savings: float | None = 0.0
    retirement_goals: str | None = "comfortable"
    employment_status: str | None = "employed"
    wallet_allowance: float | None = 0.0
    wallet_quantity_permited: float | None = 0.0
    agentverse_salary_per_orchestration: float | None = 0.0
    llm_wallet_allowance: bool = False
    llm_token_allowance_per_call: float | None = 0.0
    llm_token_balance_permited: float | None = 0.0
    transportation_mode: str | None = "public"
    transportation_budget: float | None = 0.0
    transportation_preferences: str | None = "eco-friendly"
    housing_type: str | None = "apartment"
    housing_budget: float | None = 0.0
    housing_preferences: str | None = "eco-friendly"
    housing_location: str | None = "urban"
    housing_size: float | None = 0.0
    housing_amenities: str | None = "basic"
    housing_security: str | None = "standard"
    housing_community: str | None = "friendly"
    housing_neighborhood: str | None = "safe"
    housing_proximity: str | None = "convenient"
    housing_environment: str | None = "green"
    housing_maintenance: str | None = "standard"
    housing_rent_or_own: str | None = "rent"
    housing_lease_duration: int | None = 12
    housing_lease_terms: str | None = "standard"
    housing_lease_security_deposit: float | None = 0.0
    housing_lease_rent_increase: float | None = 0.0
    housing_lease_rent_due_date: str | None = "monthly"
    housing_lease_rent_payment_method: str | None = "bank_transfer"
    housing_lease_rent_payment_frequency: str | None = "monthly"
    alimentation_budget: float | None = 0.0
    hobbies_budget: float | None = 0.0
    entertainment_budget: float | None = 0.0
    health_budget: float | None = 0.0
    insurance_budget: float | None = 0.0

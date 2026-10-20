from pydantic import BaseModel, Field


class ProblematicWordMatch(BaseModel):
    """Individual problematic word or phrase match."""

    text: str = Field(..., description="The normalized matched text")
    matched_against: str = Field(..., description="The problematic word/phrase it matched against")
    confidence: float = Field(
        ..., description="Confidence score (1.0 for exact, <1.0 for fuzzy)", ge=0, le=1
    )
    type: str = Field(..., description="Match type: 'exact' or 'close'", pattern="^(exact|close)$")
    start: int = Field(..., description="Start index in original text", ge=0)
    end: int = Field(..., description="End index in original text", ge=0)


class ProblematicWordResult(BaseModel):
    """Result of problematic word matching."""

    exact_matches: list[ProblematicWordMatch] = Field(
        default_factory=list, description="List of exact matches found"
    )
    close_matches: list[ProblematicWordMatch] = Field(
        default_factory=list, description="List of fuzzy/close matches found"
    )

    @property
    def total_count(self) -> int:
        """Return the total number of problematic words found."""
        return len(self.exact_matches) + len(self.close_matches)


class GreenClaimRequest(BaseModel):
    """Request model for green claim analysis."""

    text: str = Field(
        ...,
        description="The text to analyze for green claims",
        min_length=1,
    )
    chunk_text: bool = Field(description="Whether to chunk the text for analysis", default=False)
    use_second_opinion: bool = Field(
        default=False,
        description="Whether to run analysis twice and merge with confidence ratings",
    )
    include_formatted_report: bool = Field(
        default=False,
        description="Whether to include a human-readable formatted report in the response",
    )


class PrincipleViolation(BaseModel):
    """
    Represents a single violated green-claim principle.
    """

    principle: str = Field(
        ...,
        description="Exact name of the violated principle (must match authoritative list)",
    )

    explanation: str = Field(
        ...,
        description="Concise, deterministic explanation of why the principle is violated (≤ 30 words)",
    )

    guidelines_reference: list[str] = Field(
        ...,
        description="Lexicographically sorted list of ICC guideline references (e.g., ['4. Scope'])",
    )

    improvement_recommendation: str = Field(
        ...,
        description="Concrete, actionable recommendation to fix the violation (≤ 20 words)",
    )

    confidence: str = Field(
        default="strong",
        description="Confidence level: 'strong' (found in both analyses) or 'weak' (found in one analysis)",
        pattern="^(strong|weak)$",
    )


class GreenClaimReportItem(BaseModel):
    """
    Represents a single GREEN_CLAIM and all violated principles for that claim.
    """

    statement: str = Field(
        ...,
        description="Exact sentence or paragraph from the input text that constitutes the green claim",
    )

    principles_violated: list[PrincipleViolation] = Field(
        ...,
        description="List of violated principles in ascending numerical order; empty if compliant",
    )


class GreenClaimResponse(BaseModel):
    """Response model for green claim analysis."""

    green_claim_report: list[GreenClaimReportItem] = Field(
        ..., description="List of analyzed claims with their assessments"
    )
    legal_statement: str = Field(..., description="Legal guidance statement")
    guidelines: str = Field(..., description="URL to the ICC guidelines document")
    problematic_words: ProblematicWordResult = Field(
        ...,
        description="Problematic words found in the text that may indicate greenwashing",
    )
    formatted_report: str | None = Field(
        default=None,
        description="Human-readable formatted report (only included if include_formatted_report=true)",
    )


class GreenClaimLLMResponse(BaseModel):
    """
    Deterministic LLM response model for green claim compliance analysis.
    Matches the revised system prompt JSON schema exactly.
    """

    green_claim_report: list[GreenClaimReportItem] = Field(
        ...,
        description="Ordered list of analyzed green claims and their principle violations",
    )

    legal_statement: str = Field(
        ...,
        description="Fixed legal guidance statement",
    )

    guidelines: str = Field(
        ...,
        description="URL to the ICC Responsible Environmental Marketing Communications Checklist",
    )

"""
Quick test to analyze a text that should violate most principles.
"""

import asyncio
import os

from dotenv import load_dotenv

from src.base.infrastructure.ai.azure_openai_client import AzureOpenAIClient
from src.base.repositories.openai_repository import OpenAIRepository
from src.domains.green_claim.services.green_claim_service import GreenClaimService

load_dotenv()


async def main():
    # Test text designed to violate most principles
    test_text = """Product X is the most sustainable option, made from 100% recycled materials with zero environmental impact.

Thanks to our voluntary commitment to environmental stewardship, we proudly meet EU Directive 2012/19/EU on electronic waste recycling - going above and beyond what's required! Our life-cycle assessment shows environmental benefits. Detailed methodology and complete LCA results are documented in our confidential internal sustainability report (available upon written request to our legal department within 90 days).

By 2030, we will become the world's greenest manufacturer. Product X reduces CO2 emissions by 50% compared to older technologies. This product is certified with the Green Excellence Seal and meets EcoLeaf standards. The packaging material can be recycled.

We use 40% less water than required by industry standards. Each purchase helps save the planet. Our manufacturing process features natural materials, reflecting our commitment to Mother Earth and future generations.

Product X contains components that can be reused. Our production facility is painted with earth-tone colors to blend with the natural environment."""

    # Get environment variables
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    chat_deployment = os.getenv("AZURE_CHAT_DEPLOYMENTS", "gpt-4o-mini-stig")

    if not api_key or not endpoint:
        print("❌ Environment variables not set. Please load .env file.")
        return

    # Initialize service
    azure_client = AzureOpenAIClient(
        endpoint=endpoint,
        api_key=api_key,
        chat_deployment=chat_deployment,
        completion_deployment=chat_deployment,
        whispering_deployment="whisper-1",
        embedding_deployment="text-embedding-ada-002-stig",
    )
    openai_repo = OpenAIRepository(openai_client=azure_client)
    service = GreenClaimService(openai_repository=openai_repo)

    print("=" * 80)
    print("CONSISTENCY TEST: RUNNING ANALYSIS 10 TIMES")
    print("=" * 80)
    print(f"\nText:\n{test_text}\n")
    print("=" * 80)
    print("\nRunning 10 analyses in PARALLEL with real OpenAI API...")
    print("This will take a moment...\n")

    # Run analysis 10 times IN PARALLEL
    async def run_single_analysis(run_number):
        """Run a single analysis and return the violated principles."""
        result = await service.run_green_claim_analysis(test_text, use_second_opinion=False)

        # Extract violated principles for this run
        violated_principles = set()
        for item in result.green_claim_report:
            # Each item has a list of principles_violated
            for violation in item.principles_violated:
                violated_principles.add(violation.principle)

        print(f"  Run {run_number}/10 completed ✓ ({len(violated_principles)} principles)")
        return sorted(violated_principles)

    # Execute all 10 analyses concurrently
    tasks = [run_single_analysis(i + 1) for i in range(10)]
    all_runs = await asyncio.gather(*tasks)

    print(f"\n{'=' * 80}")
    print("CONSISTENCY ANALYSIS")
    print(f"{'=' * 80}\n")

    # Check if all runs are identical
    first_run = all_runs[0]
    all_identical = all(run == first_run for run in all_runs)

    if all_identical:
        print("✅ PERFECT CONSISTENCY: All 10 runs produced identical results!\n")
    else:
        print("⚠️  INCONSISTENCY DETECTED: Results varied across runs\n")

    # Count frequency of each principle across all runs
    from collections import Counter

    all_principles_flat = [principle for run in all_runs for principle in run]
    principle_counts = Counter(all_principles_flat)

    # List all 13 principles and their consistency
    all_principles = [
        "Clarity and Specificity",
        "Scientific Evidence and Substantiation",
        "Life-Cycle Perspective",
        "Legal Requirements",
        "Comparative Claims",
        "Future Performance and Commitments",
        "Accessibility of Substantiation",
        "Truthfulness and Accuracy",
        "Compliance with Regulations",
        "Relevance",
        "Use of Symbols and Logos",
        "Consumer Perception",
        "Recycling and Reuse Claims",
    ]

    print("Principle Detection Frequency:")
    print("-" * 80)
    for i, principle in enumerate(all_principles, 1):
        count = principle_counts.get(principle, 0)
        percentage = (count / 10) * 100

        if count == 10:
            status = "✅ ALWAYS"
        elif count == 0:
            status = "❌ NEVER"
        else:
            status = f"⚠️  {count}/10 runs"

        print(f"{i:2d}. {principle:<45} {status:>15} ({percentage:>5.1f}%)")

    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    print("Total runs: 10")
    print(f"Identical results: {'YES ✅' if all_identical else 'NO ⚠️'}")
    print(f"Consistency score: {(sum(1 for run in all_runs if run == first_run) / 10) * 100:.1f}%")

    if all_identical:
        print(f"\nPrinciples consistently detected: {len(first_run)}/13")
        print(f"Detected: {', '.join(first_run) if first_run else 'None'}")
    else:
        print("\nVariance detected in principle assignment!")
        print("This indicates non-deterministic behavior.")

    print(f"\n{'=' * 80}\n")


if __name__ == "__main__":
    asyncio.run(main())

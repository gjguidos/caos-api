from src.domains.agentverse.tools.workflow_tool.models import RetryPolicy, WorkflowStep


def retry_delay(retry: RetryPolicy, attempt: int) -> float:
    if retry.backoff == "exponential":
        return retry.delay_seconds * (2 ** max(0, attempt - 1))

    return retry.delay_seconds


def retry_policy(step: WorkflowStep) -> RetryPolicy:
    if not step.retry:
        return RetryPolicy()

    if isinstance(step.retry, RetryPolicy):
        return step.retry

    return RetryPolicy.model_validate(step.retry)

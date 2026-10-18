from .aggregators import AdzunaSource, JoobleSource, JSearchSource, RemoteOKSource, RemotiveSource, TheMuseSource, USAJobsSource
from .ats import AshbySource, CareersSource, GreenhouseSource, LeverSource, SmartRecruitersSource
from .base import BaseSource, Field, JobPosting, SourceContext, SourceError
from .email_alerts import EmailAlertsSource

REGISTRY: dict[str, BaseSource] = {
    source.kind: source
    for source in (
        EmailAlertsSource(),
        CareersSource(),
        JSearchSource(),
        AdzunaSource(),
        JoobleSource(),
        USAJobsSource(),
        GreenhouseSource(),
        LeverSource(),
        AshbySource(),
        SmartRecruitersSource(),
        TheMuseSource(),
        RemotiveSource(),
        RemoteOKSource(),
    )
}


def get_source(kind: str) -> BaseSource:
    try:
        return REGISTRY[kind]
    except KeyError as exc:
        raise SourceError(f"Unknown source type: {kind}") from exc


__all__ = ["REGISTRY", "BaseSource", "Field", "JobPosting", "SourceContext", "SourceError", "get_source"]

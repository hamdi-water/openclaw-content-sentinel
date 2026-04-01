from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .config import AppConfig
from .workflow import RunStore, approve_run, daily_run, prepare_publish_all, record_post_result

DEFAULT_SIMULATION_URLS = [
    "https://www.rnz.co.nz/news/national/590645/health-nz-staff-told-to-stop-using-chatgpt-to-write-clinical-notes",
]


def simulate_runs(
    config: AppConfig,
    days: int = 14,
    prompt: str = "",
    competitor_urls: list[str] | None = None,
    auto_approve: bool = True,
    simulate_publish: bool = True,
) -> dict[str, Any]:
    payloads = []
    urls = competitor_urls or DEFAULT_SIMULATION_URLS
    base_prompt = (
        prompt.strip() or "Write an original response article about reliable AI operations."
    )
    start = datetime.now(UTC) - timedelta(days=max(days - 1, 0))

    for offset in range(days):
        current = start + timedelta(days=offset)
        try:
            run = daily_run(
                config,
                prompt=f"{base_prompt} Simulation day {offset + 1}.",
                competitor_url=urls[offset % len(urls)],
                ignore_pause=True,
                created_at_override=current.replace(
                    hour=8, minute=0, second=0, microsecond=0
                ).isoformat(),
                simulation={"enabled": True, "day_index": offset + 1, "total_days": days},
            )
            if auto_approve:
                # approve_run now handles LangGraph resumption and publishing internally
                run = approve_run(config, run["run_id"], note="simulation auto-approval")

            if simulate_publish:
                # Re-load run to get updated status after LangGraph completion
                store = RunStore(config)
                run = store.load_run(run["run_id"])

                # Only call prepare/record if LangGraph hasn't done it yet (e.g. simulation mode)
                if run.get("status") != "posted" and run.get("status") != "finished":
                    prepare_publish_all(config, run["run_id"])
                    for platform in run["platform_targets"]:
                        record_post_result(
                            config,
                            run["run_id"],
                            platform=platform,
                            status="posted",
                            url=f"simulated://{platform}/{run['run_id']}",
                            note="simulation publish",
                            screenshots=[
                                f"simulation/{platform}-before.png",
                                f"simulation/{platform}-after.png",
                            ],
                        )
            payloads.append({"run_id": run["run_id"], "simulation_day": offset + 1, "status": "ok"})
        except Exception as exc:
            payloads.append({"simulation_day": offset + 1, "status": "failed", "error": str(exc)})

    return {
        "days": days,
        "auto_approve": auto_approve,
        "simulate_publish": simulate_publish,
        "runs": payloads,
    }

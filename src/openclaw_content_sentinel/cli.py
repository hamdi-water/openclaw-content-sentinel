from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import FrameType
from typing import Any

from .adapter_ops import unfreeze_adapter, write_adapter_manifest
from .browser_automation import (
    check_all_browser_profiles,
    check_browser_profile_health,
    publish_all_via_browser,
    publish_via_browser,
)
from .compliance import build_zero_cost_report, write_compliance_report
from .config import AppConfig
from .dashboard import write_dashboard_summary
from .gap_tracker import write_v61_gap_tracker
from .graph_store import backend_status as graph_backend_status
from .graph_store import reindex_graph, search_graph
from .ledger import fetch_run_history, ledger_summary, rebuild_ledger
from .logging_utils import setup_logging
from .memory import backend_status, reindex_memory, search_memory
from .operations import (
    build_operator_action_queue,
    build_scheduler_health_report,
    runtime_status,
    sweep_operator_backlog,
    sync_runtime_config,
)
from .promptops import write_promptops_report
from .ragops import write_ragops_report
from .release_ops import write_release_readiness_report
from .security import security_audit
from .simulation import simulate_runs
from .source_registry import build_source_registry_report
from .storage import RunStore
from .utils import read_text
from .workflow import (
    approve_run,
    bootstrap_workspace,
    create_run,
    daily_run,
    doctor_report,
    fail_run,
    generate_drafts,
    generate_image_asset,
    ingest_competitor,
    load_daily_brief_queue,
    load_daily_input,
    mark_preview_sent,
    maybe_send_telegram_preview,
    package_run,
    pause_automation,
    prepare_publish,
    prepare_publish_all,
    promote_daily_brief,
    record_post_result,
    refresh_run_state,
    reject_run,
    render_logs,
    render_status,
    replay_run,
    research_trends,
    resume_automation,
    review_run,
    save_daily_brief,
    save_daily_input,
    scheduled_run,
    sync_graph,
    sync_memory,
    synthesize_hybrid_context,
)


def _config() -> AppConfig:
    return AppConfig.from_env(Path.cwd())


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocs", description="OpenClaw Content Sentinel helper CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Create workspace data directories")

    create = sub.add_parser("create-run", help="Create a new run record")
    create.add_argument("--prompt")
    create.add_argument("--prompt-file")
    create.add_argument("--competitor-url", required=True)
    create.add_argument("--keywords", nargs="*")
    create.add_argument("--targets", nargs="*")
    create.add_argument("--target-language", default="fr")

    ingest = sub.add_parser("ingest", help="Fetch and extract the competitor article")
    ingest.add_argument("--run-id", required=True)

    research = sub.add_parser("research", help="Collect trend signals")
    research.add_argument("--run-id", required=True)

    package = sub.add_parser("package", help="Build prompt and draft artifacts")
    package.add_argument("--run-id", required=True)

    draft = sub.add_parser("generate-drafts", help="Generate article and social drafts for a run")
    draft.add_argument("--run-id", required=True)

    review = sub.add_parser("review-run", help="Run the quality gate for one run")
    review.add_argument("--run-id", required=True)

    daily = sub.add_parser("daily-run", help="Create, ingest, research, and package in one command")
    daily.add_argument("--prompt")
    daily.add_argument("--prompt-file")
    daily.add_argument("--competitor-url", required=True)
    daily.add_argument("--keywords", nargs="*")
    daily.add_argument("--targets", nargs="*")
    daily.add_argument(
        "--target-language",
        default="fr",
        help="Â§7: Target language (fr, en, es, de)",
    )
    daily.add_argument("--ignore-pause", action="store_true")
    daily.add_argument("--simulation", action="store_true")

    scheduled = sub.add_parser(
        "scheduled-run", help="Run the workflow from the configured daily input file"
    )
    scheduled.add_argument("--ignore-pause", action="store_true")

    set_daily_input = sub.add_parser(
        "set-daily-input", help="Write the daily prompt and competitor URL file"
    )
    set_daily_input.add_argument("--prompt")
    set_daily_input.add_argument("--prompt-file")
    set_daily_input.add_argument("--competitor-url", required=True)
    set_daily_input.add_argument("--keywords", nargs="*")
    set_daily_input.add_argument("--targets", nargs="*")

    sub.add_parser("show-daily-input", help="Show the resolved daily prompt and competitor URL")
    sub.add_parser("show-daily-brief-queue", help="Show the resolved daily brief queue")
    add_daily_brief = sub.add_parser(
        "add-daily-brief", help="Append a structured brief to the daily brief queue"
    )
    add_daily_brief.add_argument("--competitor-url", required=True)
    add_daily_brief.add_argument("--topic", default="")
    add_daily_brief.add_argument("--business-angle", default="")
    add_daily_brief.add_argument("--target-audience", default="")
    add_daily_brief.add_argument("--key-call-to-action", default="")
    add_daily_brief.add_argument("--due-at", default="")
    add_daily_brief.add_argument("--prompt", default="")
    add_daily_brief.add_argument("--keywords", nargs="*")
    add_daily_brief.add_argument("--targets", nargs="*")
    add_daily_brief.add_argument("--do-not-say", nargs="*")
    add_daily_brief.add_argument("--mandatory-references", nargs="*")
    add_daily_brief.add_argument("--label", default="")
    add_daily_brief.add_argument("--priority", type=int, default=0)
    promote_brief = sub.add_parser(
        "promote-daily-brief", help="Promote one queued brief into daily_input.json"
    )
    promote_brief.add_argument("--brief-id", required=True)
    sub.add_parser("doctor", help="Check workspace readiness for a supervised run")

    approve = sub.add_parser("approve", help="Approve a run")
    approve.add_argument("--run-id", required=True)
    approve.add_argument("--note", default="")

    approve_and_publish = sub.add_parser(
        "approve-and-publish",
        help=(
            "Approve a run, then immediately publish all configured platforms "
            "through OpenClaw browser automation"
        ),
    )
    approve_and_publish.add_argument("--run-id", required=True)
    approve_and_publish.add_argument("--note", default="")
    approve_and_publish.add_argument("--no-submit", action="store_true")
    approve_and_publish.add_argument("--stop-on-error", action="store_true")
    approve_and_publish.add_argument("--retries", type=int)

    reject = sub.add_parser("reject", help="Reject a run")
    reject.add_argument("--run-id", required=True)
    reject.add_argument("--note", default="")

    replay = sub.add_parser("replay", help="Replay a node in the graph")
    replay.add_argument("--run-id", required=True)
    replay.add_argument("--node", required=True)

    status = sub.add_parser("status", help="Show recent runs")
    status.add_argument("--json", action="store_true")
    status.add_argument("--run-id")

    logs = sub.add_parser("logs", help="Show one run with artifacts and post logs")
    logs.add_argument("--run-id", required=True)

    refresh = sub.add_parser(
        "refresh-run", help="Recompute run state, pipeline, and operator artifacts"
    )
    refresh.add_argument("--run-id", required=True)

    sync_mem = sub.add_parser("sync-memory", help="Index a run into the local vector memory")
    sync_mem.add_argument("--run-id", required=True)

    sync_graph_parser = sub.add_parser("sync-graph", help="Index a run into the knowledge graph")
    sync_graph_parser.add_argument("--run-id", required=True)
    hybrid = sub.add_parser(
        "hybrid-context", help="Synthesize hybrid memory + graph context for a run"
    )
    hybrid.add_argument("--run-id", required=True)
    approval_packet = sub.add_parser("approval-packet", help="Show the approval packet for a run")
    approval_packet.add_argument("--run-id", required=True)
    execution_policy = sub.add_parser(
        "execution-policy", help="Show the execution policy for a run"
    )
    execution_policy.add_argument("--run-id", required=True)

    query_mem = sub.add_parser("search-memory", help="Query the local vector memory")
    query_mem.add_argument("--query", required=True)
    query_mem.add_argument("--top-k", type=int, default=5)

    query_graph = sub.add_parser("search-graph", help="Query the knowledge graph")
    query_graph.add_argument("--query", required=True)
    query_graph.add_argument("--top-k", type=int, default=8)

    sub.add_parser("memory-status", help="Show the active vector memory backend and readiness")
    sub.add_parser("graph-status", help="Show the active graph backend and readiness")
    reindex_mem = sub.add_parser(
        "reindex-memory", help="Reindex one or more runs into the vector store"
    )
    reindex_mem.add_argument("--run-id", nargs="*")
    reindex_graph_parser = sub.add_parser(
        "reindex-graph", help="Reindex one or more runs into the knowledge graph"
    )
    reindex_graph_parser.add_argument("--run-id", nargs="*")
    sub.add_parser("security-audit", help="Audit local security and operational hardening")
    sub.add_parser("compliance-report", help="Build a cahier des charges compliance report")
    sub.add_parser("v61-gap-report", help="Build the v6.1 weighted gap tracker on 100")
    sub.add_parser(
        "source-registry-report", help="Show the configured zero-cost source registry and adapters"
    )
    sub.add_parser(
        "promptops-report", help="Build the PromptOps registry and quality taxonomy report"
    )
    sub.add_parser("ragops-report", help="Build the RAGOps manifest and shadow-eval report")
    sub.add_parser("adapter-manifest", help="Build the browser adapter engineering manifest")
    adapter_unfreeze = sub.add_parser(
        "adapter-unfreeze", help="Clear a frozen browser adapter after operator review"
    )
    adapter_unfreeze.add_argument(
        "--platform", required=True, choices=["linkedin", "facebook", "x"]
    )
    adapter_unfreeze.add_argument("--actor", default="operator")
    sub.add_parser("release-readiness", help="Build the release-train readiness report")
    sub.add_parser("runtime-status", help="Show active OpenClaw runtime integration status")
    sub.add_parser("ledger-summary", help="Show SQLite run ledger summary")
    ledger_tail = sub.add_parser("ledger-tail", help="Show recent ledger history for one run")
    ledger_tail.add_argument("--run-id", required=True)
    ledger_tail.add_argument("--limit", type=int, default=10)
    rebuild_ledger_parser = sub.add_parser(
        "rebuild-ledger", help="Rebuild the SQLite ledger from run.json snapshots"
    )
    rebuild_ledger_parser.add_argument("--no-reset", action="store_true")

    purge = sub.add_parser("purge", help="Garbage collect old runs and artifacts")
    purge.add_argument("--days", type=int, default=90)
    purge.add_argument("--dry-run", action="store_true")

    runtime_sync = sub.add_parser(
        "runtime-sync", help="Sync workspace runtime settings into OpenClaw"
    )
    runtime_sync.add_argument("--restart-gateway", action="store_true")

    sub.add_parser("scheduler-health", help="Audit scheduler, previews, and stale runs")
    sub.add_parser(
        "ops-queue",
        help="Build the operator action queue for approvals, failures, and runtime gaps",
    )
    sweep_backlog = sub.add_parser(
        "sweep-operator-backlog",
        help=(
            "Resolve stale approvals, send missing previews, and "
            "quiet historical operator backlog items"
        ),
    )
    sweep_backlog.add_argument("--stale-hours", type=int, default=12)
    sweep_backlog.add_argument("--resolve-failed-hours", type=int, default=12)
    sweep_backlog.add_argument("--no-send-previews", action="store_true")

    zero_cost = sub.add_parser(
        "zero-cost-report", help="Aggregate zero-cost proof across recent runs"
    )
    zero_cost.add_argument("--days", type=int, default=30)

    dashboard = sub.add_parser("build-dashboard", help="Build monitoring dashboard summary data")
    dashboard.add_argument("--json", action="store_true")

    simulate = sub.add_parser("simulate", help="Create a multi-day simulation run set")
    simulate.add_argument("--days", type=int, default=14)
    simulate.add_argument("--prompt", default="")
    simulate.add_argument("--prompt-file")
    simulate.add_argument("--competitor-urls", nargs="*")
    simulate.add_argument("--no-approve", action="store_true")
    simulate.add_argument("--no-publish", action="store_true")

    record = sub.add_parser("record-post", help="Record a posting result")
    record.add_argument("--run-id", required=True)
    record.add_argument("--platform", required=True, choices=["linkedin", "facebook", "x"])
    record.add_argument("--status", required=True, choices=["posted", "failed", "skipped"])
    record.add_argument("--url", default="")
    record.add_argument("--note", default="")
    record.add_argument("--failure-reason", default="")
    record.add_argument("--screenshots", nargs="*")

    publish = sub.add_parser("publish", help="Prepare one platform publish job")
    publish.add_argument("--run-id", required=True)
    publish.add_argument("--platform", required=True, choices=["linkedin", "facebook", "x"])

    publish_all = sub.add_parser("publish-all", help="Prepare all platform publish jobs")
    publish_all.add_argument("--run-id", required=True)

    browser_publish = sub.add_parser(
        "browser-publish", help="Publish one platform through OpenClaw browser automation"
    )
    browser_publish.add_argument("--run-id", required=True)
    browser_publish.add_argument("--platform", required=True, choices=["linkedin", "facebook", "x"])
    browser_publish.add_argument("--no-submit", action="store_true")
    browser_publish.add_argument("--retries", type=int)

    browser_publish_all = sub.add_parser(
        "browser-publish-all",
        help="Publish all configured platforms sequentially through OpenClaw browser automation",
    )
    browser_publish_all.add_argument("--run-id", required=True)
    browser_publish_all.add_argument("--no-submit", action="store_true")
    browser_publish_all.add_argument("--stop-on-error", action="store_true")
    browser_publish_all.add_argument("--retries", type=int)

    browser_health = sub.add_parser(
        "browser-health", help="Check login and composer readiness of browser profiles"
    )
    browser_health.add_argument("--platform", choices=["linkedin", "facebook", "x"])
    browser_health.add_argument("--refresh", action="store_true")

    image = sub.add_parser("generate-image", help="Generate the local social image asset for a run")
    image.add_argument("--run-id", required=True)

    preview = sub.add_parser("mark-preview-sent", help="Mark that Telegram preview was sent")
    preview.add_argument("--run-id", required=True)
    preview.add_argument("--telegram-route", default="")

    send_preview = sub.add_parser(
        "send-telegram-preview", help="Send the Telegram preview for a run"
    )
    send_preview.add_argument("--run-id", required=True)
    send_preview.add_argument("--force", action="store_true")

    pause = sub.add_parser("pause", help="Pause scheduled automation")
    pause.add_argument("--reason", default="")
    pause.add_argument("--updated-by", default="operator")

    resume = sub.add_parser("resume", help="Resume scheduled automation")
    resume.add_argument("--reason", default="")
    resume.add_argument("--updated-by", default="operator")

    fail = sub.add_parser("fail-run", help="Mark a run as failed")
    fail.add_argument("--run-id", required=True)
    fail.add_argument("--reason", required=True)

    return parser


def _resolve_prompt(args: argparse.Namespace) -> str:
    if getattr(args, "prompt", None):
        return args.prompt
    if getattr(args, "prompt_file", None):
        return read_text(Path(args.prompt_file))
    raise SystemExit("A prompt or prompt file is required.")



def setup_graceful_shutdown() -> None:
    """Ref Â§34.8: Handle SIGTERM for graceful shutdown."""
    import logging
    import signal
    import sys

    def handler(signum: int, frame: FrameType | None) -> None:
        del frame
        logging.info(f"Received signal {signum}. Initiating graceful shutdown...")
        # Add cleanup logic here if needed (e.g. closing DB connections)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def main(argv: list[str] | None = None) -> None:
    setup_graceful_shutdown()
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _config()
    setup_logging(config)
    _dispatch_command(args, config)


def _dispatch_command(args: argparse.Namespace, config: AppConfig) -> None:
    cmd = args.command

    if cmd == "bootstrap":
        bootstrap_workspace(config)
        _print({"status": "ok", "data_dir": str(config.data_dir)})
        return

    if cmd == "create-run":
        payload = create_run(
            config,
            prompt=_resolve_prompt(args),
            competitor_url=args.competitor_url,
            keywords=args.keywords,
            targets=args.targets,
            target_language=args.target_language,
        )
        _print(payload)
        return

    _handle_workflow_commands(args, config)


def _handle_workflow_commands(args: argparse.Namespace, config: AppConfig) -> None:
    cmd = args.command
    if cmd == "ingest":
        _print(ingest_competitor(config, args.run_id))
    elif cmd == "research":
        _print(research_trends(config, args.run_id))
    elif cmd == "package":
        _print(package_run(config, args.run_id))
    elif cmd == "generate-drafts":
        _print(generate_drafts(config, args.run_id))
    elif cmd == "review-run":
        _print(review_run(config, args.run_id))
    elif cmd == "daily-run":
        _handle_daily_run(args, config)
    else:
        _handle_state_and_ops_commands(args, config)


def _handle_daily_run(args: argparse.Namespace, config: AppConfig) -> None:
    if args.simulation:
        config.simulation_mode = True
    payload = daily_run(
        config,
        prompt=_resolve_prompt(args),
        competitor_url=args.competitor_url,
        keywords=args.keywords,
        targets=args.targets,
        ignore_pause=args.ignore_pause,
        target_language=args.target_language,
    )
    _print(payload)


def _handle_state_and_ops_commands(args: argparse.Namespace, config: AppConfig) -> None:
    cmd = args.command
    if cmd in (
        "scheduled-run",
        "set-daily-input",
        "show-daily-input",
        "show-daily-brief-queue",
        "add-daily-brief",
        "promote-daily-brief",
    ):
        _handle_input_and_brief_commands(args, config)
    elif cmd in (
        "sync-memory",
        "sync-graph",
        "search-memory",
        "search-graph",
        "memory-status",
        "graph-status",
        "reindex-memory",
        "reindex-graph",
        "hybrid-context",
    ):
        _handle_memory_and_graph_commands(args, config)
    elif cmd in (
        "doctor",
        "security-audit",
        "compliance-report",
        "v61-gap-report",
        "source-registry-report",
        "promptops-report",
        "ragops-report",
        "adapter-manifest",
        "adapter-unfreeze",
        "release-readiness",
        "runtime-status",
    ):
        _handle_reporting_and_audit_commands(args, config)
    elif cmd in (
        "ledger-summary",
        "ledger-tail",
        "rebuild-ledger",
        "runtime-sync",
        "scheduler-health",
        "ops-queue",
        "sweep-operator-backlog",
        "zero-cost-report",
        "build-dashboard",
    ):
        _handle_ledger_and_runtime_commands(args, config)
    elif cmd == "purge":
        from .storage import PurgeService
        service = PurgeService(config)
        _print(service.purge_old_runs(retention_days=args.days))
    elif cmd in (
        "approve",
        "approve-and-publish",
        "reject",
        "publish",
        "publish-all",
        "browser-publish",
        "browser-publish-all",
        "browser-health",
        "record-post",
    ):
        _handle_publish_and_browser_commands(args, config)
    else:
        _handle_utility_commands(args, config)


def _handle_input_and_brief_commands(args: argparse.Namespace, config: AppConfig) -> None:
    cmd = args.command
    if cmd == "scheduled-run":
        _print(scheduled_run(config, ignore_pause=args.ignore_pause))
    elif cmd == "set-daily-input":
        payload = save_daily_input(
            config,
            prompt=_resolve_prompt(args),
            competitor_url=args.competitor_url,
            keywords=args.keywords,
            targets=args.targets,
            target_language=args.target_language,
        )
        _print(payload)
    elif cmd == "show-daily-input":
        _print(load_daily_input(config))
    elif cmd == "show-daily-brief-queue":
        _print(load_daily_brief_queue(config))
    elif cmd == "add-daily-brief":
        _print(
            save_daily_brief(
                config,
                competitor_url=args.competitor_url,
                topic=args.topic,
                business_angle=args.business_angle,
                target_audience=args.target_audience,
                key_call_to_action=args.key_call_to_action,
                due_at=args.due_at,
                prompt=args.prompt,
                keywords=args.keywords,
                targets=args.targets,
                do_not_say=args.do_not_say,
                mandatory_references=args.mandatory_references,
                label=args.label,
                priority=args.priority,
            )
        )
    elif cmd == "promote-daily-brief":
        _print(promote_daily_brief(config, args.brief_id))


def _handle_memory_and_graph_commands(args: argparse.Namespace, config: AppConfig) -> None:
    cmd = args.command
    if cmd == "sync-memory":
        _print(sync_memory(config, args.run_id))
    elif cmd == "sync-graph":
        _print(sync_graph(config, args.run_id))
    elif cmd == "hybrid-context":
        _print(synthesize_hybrid_context(config, args.run_id))
    elif cmd == "search-memory":
        results = search_memory(config, args.query, top_k=args.top_k)
        _print({"query": args.query, "results": results})
    elif cmd == "search-graph":
        results = search_graph(config, args.query, top_k=args.top_k)
        _print({"query": args.query, "results": results})
    elif cmd == "memory-status":
        _print(backend_status(config))
    elif cmd == "graph-status":
        _print(graph_backend_status(config))
    elif cmd == "reindex-memory":
        _print(reindex_memory(config, run_ids=args.run_id))
    elif cmd == "reindex-graph":
        _print(reindex_graph(config, run_ids=args.run_id))


def _handle_reporting_and_audit_commands(args: argparse.Namespace, config: AppConfig) -> None:
    cmd = args.command
    if cmd == "doctor":
        _print(doctor_report(config))
    elif cmd == "security-audit":
        _print(security_audit(config))
    elif cmd == "compliance-report":
        _print(write_compliance_report(config))
    elif cmd == "v61-gap-report":
        _print(write_v61_gap_tracker(config))
    elif cmd == "source-registry-report":
        _print(build_source_registry_report(config))
    elif cmd == "promptops-report":
        _print(write_promptops_report(config))
    elif cmd == "ragops-report":
        _print(write_ragops_report(config))
    elif cmd == "adapter-manifest":
        _print(write_adapter_manifest(config))
    elif cmd == "adapter-unfreeze":
        _print(unfreeze_adapter(config, args.platform, actor=args.actor))
    elif cmd == "release-readiness":
        _print(
            write_release_readiness_report(
                config, gap_report=write_v61_gap_tracker(config)["report"]
            )
        )
    elif cmd == "runtime-status":
        _print(runtime_status(config))


def _handle_ledger_and_runtime_commands(args: argparse.Namespace, config: AppConfig) -> None:
    cmd = args.command
    if cmd == "ledger-summary":
        _print(ledger_summary(config.ledger_db_path))
    elif cmd == "ledger-tail":
        _print(
            {
                "db_path": str(config.ledger_db_path),
                "run_id": args.run_id,
                "items": fetch_run_history(config.ledger_db_path, args.run_id, limit=args.limit),
            }
        )
    elif cmd == "rebuild-ledger":
        store = RunStore(config)
        _print(rebuild_ledger(config.ledger_db_path, store.list_runs(), reset=not args.no_reset))
    elif cmd == "runtime-sync":
        _print(sync_runtime_config(config, restart_gateway=args.restart_gateway))
    elif cmd == "scheduler-health":
        _print(build_scheduler_health_report(config))
    elif cmd == "ops-queue":
        _print(build_operator_action_queue(config))
    elif cmd == "sweep-operator-backlog":
        _print(
            sweep_operator_backlog(
                config,
                stale_hours=args.stale_hours,
                resolve_failed_hours=args.resolve_failed_hours,
                send_previews=not args.no_send_previews,
            )
        )
    elif cmd == "zero-cost-report":
        _print(build_zero_cost_report(config, days=args.days))
    elif cmd == "build-dashboard":
        payload = write_dashboard_summary(config)
        if args.json:
            _print(payload)
        else:
            print(payload["path"])


def _handle_publish_and_browser_commands(args: argparse.Namespace, config: AppConfig) -> None:
    cmd = args.command
    if cmd == "approve":
        _print(approve_run(config, args.run_id, note=args.note))
    elif cmd == "approve-and-publish":
        approved = approve_run(config, args.run_id, note=args.note)
        publish_result = publish_all_via_browser(
            config,
            args.run_id,
            submit=not args.no_submit,
            continue_on_error=not args.stop_on_error,
            retries=args.retries,
        )
        _print(
            {
                "run_id": args.run_id,
                "approval_state": approved.get("approval_state"),
                "status": publish_result.get("status", approved.get("status")),
                "approved": approved,
                "publish_result": publish_result,
            }
        )
    elif cmd == "reject":
        _print(reject_run(config, args.run_id, note=args.note))
    elif cmd == "replay":
        _print(replay_run(config, args.run_id, args.node))
    elif cmd == "publish":
        _print(prepare_publish(config, args.run_id, args.platform))
    elif cmd == "publish-all":
        _print(prepare_publish_all(config, args.run_id))
    elif cmd == "browser-publish":
        _print(
            publish_via_browser(
                config,
                args.run_id,
                args.platform,
                submit=not args.no_submit,
                retries=args.retries,
            )
        )
    elif cmd == "browser-publish-all":
        _print(
            publish_all_via_browser(
                config,
                args.run_id,
                submit=not args.no_submit,
                continue_on_error=not args.stop_on_error,
                retries=args.retries,
            )
        )
    elif cmd == "browser-health":
        if args.platform:
            _print(check_browser_profile_health(config, args.platform, force_refresh=args.refresh))
        else:
            _print(check_all_browser_profiles(config, force_refresh=args.refresh))
    elif cmd == "record-post":
        _print(
            record_post_result(
                config,
                args.run_id,
                platform=args.platform,
                status=args.status,
                url=args.url,
                note=args.note,
                screenshots=args.screenshots,
                failure_reason=args.failure_reason,
            )
        )


def _handle_utility_commands(args: argparse.Namespace, config: AppConfig) -> None:
    cmd = args.command
    store = RunStore(config)
    if cmd == "generate-image":
        _print(generate_image_asset(config, args.run_id))
    elif cmd == "mark-preview-sent":
        _print(mark_preview_sent(config, args.run_id, telegram_route=args.telegram_route))
    elif cmd == "send-telegram-preview":
        _print(maybe_send_telegram_preview(config, args.run_id, force=args.force))
    elif cmd == "pause":
        _print(pause_automation(config, reason=args.reason, updated_by=args.updated_by))
    elif cmd == "resume":
        _print(resume_automation(config, reason=args.reason, updated_by=args.updated_by))
    elif cmd == "fail-run":
        _print(fail_run(config, args.run_id, reason=args.reason))
    elif cmd == "logs":
        run = store.load_run(args.run_id)
        _print(render_logs(run, store.list_artifacts(args.run_id)))
    elif cmd == "refresh-run":
        _print(refresh_run_state(config, args.run_id))
    elif cmd == "status":
        runs = store.list_runs()
        payload = render_status(runs, store.load_control(), run_id=args.run_id or "")
        if args.json:
            _print(payload)
        else:
            _print_status_text(payload, args.run_id)
    elif cmd == "approval-packet":
        run = store.load_run(args.run_id)
        _print((run.get("analysis") or {}).get("approval_packet") or {})
    elif cmd == "execution-policy":
        run = store.load_run(args.run_id)
        _print((run.get("analysis") or {}).get("execution_policy") or {})
    elif cmd == "simulate":
        prompt = args.prompt
        if not prompt and args.prompt_file:
            prompt = read_text(Path(args.prompt_file))
        _print(
            simulate_runs(
                config,
                days=args.days,
                prompt=prompt,
                competitor_urls=args.competitor_urls,
                auto_approve=not args.no_approve,
                simulate_publish=not args.no_publish,
            )
        )


def _print_status_text(payload: dict[str, Any], run_id: str | None) -> None:
    if run_id and payload.get("run"):
        run = payload["run"]
        print(
            f"{run['run_id']} | {run['status']} | approval={run.get('approval_state')} "
            f"| confidence={run['confidence']} | updated={run['updated_at']}"
        )
    else:
        for run in payload["latest"]:
            print(
                f"{run['run_id']} | {run['status']} | approval={run.get('approval_state')} "
                f"| confidence={run['confidence']} | updated={run['updated_at']}"
            )


if __name__ == "__main__":
    main()



const { spawnSync } = require("node:child_process");

function parseArgs(rawArgs) {
  return (rawArgs || "").trim();
}

function runCli(pluginConfig, cliArgs) {
  if (!pluginConfig.workspaceDir) {
    return {
      ok: false,
      text: "Plugin config is missing workspaceDir.",
    };
  }
  const pythonPath = pluginConfig.pythonPath || "python";
  const cliModule = pluginConfig.cliModule || "openclaw_content_sentinel.cli";
  const result = spawnSync(
    pythonPath,
    ["-m", cliModule, ...cliArgs],
    {
      cwd: pluginConfig.workspaceDir,
      encoding: "utf8",
    },
  );

  if (result.error) {
    return {
      ok: false,
      text: `Sentinel CLI failed to start: ${result.error.message}`,
    };
  }

  if (result.status !== 0) {
    const stderr = (result.stderr || "").trim();
    const stdout = (result.stdout || "").trim();
    return {
      ok: false,
      text: stderr || stdout || `Sentinel CLI exited with code ${result.status}.`,
    };
  }

  const stdout = (result.stdout || "").trim();
  if (!stdout) {
    return { ok: true, payload: {}, text: "Command completed." };
  }

  try {
    return {
      ok: true,
      payload: JSON.parse(stdout),
      text: stdout,
    };
  } catch {
    return {
      ok: true,
      payload: {},
      text: stdout,
    };
  }
}

function summarizeStatus(payload) {
  if (payload.run) {
    const run = payload.run;
    const pipeline = payload.pipeline || {};
    return [
      `Run ${run.run_id}`,
      `Status: ${run.status}`,
      `Approval: ${run.approval_state || "pending"}`,
      `Confidence: ${run.confidence ?? "n/a"}`,
      `Updated: ${run.updated_at || "n/a"}`,
      `Current step: ${pipeline.current_step || "n/a"}/6`,
      `Next action: ${pipeline.next_action || "n/a"}`,
    ].join("\n");
  }
  const lines = [
    `Automation paused: ${payload.automation_paused ? "yes" : "no"}`,
    `Pause reason: ${payload.pause_reason || "none"}`,
  ];
  for (const run of payload.latest || []) {
    lines.push(
      `- ${run.run_id} | ${run.status} | approval=${run.approval_state || "pending"} | confidence=${run.confidence ?? "n/a"}`,
    );
  }
  return lines.join("\n");
}

function summarizeLogs(payload) {
  const pipeline = payload.analysis?.delivery_pipeline || {};
  const lines = [
    `Run ${payload.run_id}`,
    `Status: ${payload.status}`,
    `Approval: ${payload.approval_state || "pending"}`,
    `Confidence: ${payload.confidence ?? "n/a"}`,
    `Telegram route: ${payload.telegram_route || "not set"}`,
    `Preview sent: ${payload.telegram_preview_sent_at || "not sent"}`,
    `Current step: ${pipeline.current_step || "n/a"}/6`,
    `Next action: ${pipeline.next_action || "n/a"}`,
    "",
    "Artifacts:",
  ];
  for (const artifact of payload.artifacts || []) {
    lines.push(`- ${artifact}`);
  }
  lines.push("", "Post results:");
  for (const [platform, result] of Object.entries(payload.post_results || {})) {
    lines.push(
      `- ${platform}: ${result.status} | attempts=${result.attempts} | failure=${result.failure_reason || "none"} | url=${result.url || "n/a"}`,
    );
  }
  return lines.join("\n");
}

function summarizePublish(payload) {
  if (payload.targets) {
    const lines = [`Run ${payload.run_id}`, `Status: ${payload.status}`, "Publish targets:"];
    for (const item of payload.targets) {
      lines.push(
        `- ${item.platform}: profile=${item.browser_profile} | draft=${item.draft_path} | image=${item.image_path || "none"}`,
      );
    }
    return lines.join("\n");
  }
  return [
    `Run ${payload.run_id}`,
    `Platform: ${payload.platform}`,
    `Attempt: ${payload.attempt}`,
    `Profile: ${payload.browser_profile}`,
    `Draft: ${payload.draft_path}`,
    `Image: ${payload.image_path || "none"}`,
  ].join("\n");
}

function summarizeBrowserPublish(payload) {
  if (payload.results) {
    const lines = [`Run ${payload.run_id}`, `Status: ${payload.status}`, `Submit mode: ${payload.submit ? "live" : "no-submit"}`];
    for (const item of payload.results) {
      lines.push(
        `- ${item.platform}: ${item.status} | attempts=${item.attempts} | failure=${item.failure_reason || "none"} | url=${item.url || "n/a"}`,
      );
    }
    return lines.join("\n");
  }
  const platform = payload.platform || "platform";
  const result = payload.post_result || {};
  return [
    `Run ${payload.run_id}`,
    `Platform: ${platform}`,
    `Status: ${result.status || payload.status}`,
    `Attempts: ${result.attempts ?? "n/a"}`,
    `Failure: ${result.failure_reason || "none"}`,
    `URL: ${result.url || "n/a"}`,
  ].join("\n");
}

function summarizeApproveAndPublish(payload) {
  const publish = payload.publish_result || {};
  const lines = [
    `Run ${payload.run_id}`,
    `Approval: ${payload.approval_state || "approved"}`,
    `Status: ${payload.status || "unknown"}`,
    "",
    "Auto publish result:",
  ];
  for (const item of publish.results || []) {
    lines.push(
      `- ${item.platform}: ${item.status} | attempts=${item.attempts} | failure=${item.failure_reason || "none"} | url=${item.url || "n/a"}`
    );
  }
  return lines.join("\n");
}

function summarizeDoctor(payload) {
  const checks = Object.entries(payload.checks || {}).map(
    ([name, ok]) => `- ${name}: ${ok ? "ok" : "missing"}`,
  );
  return [
    `Workspace status: ${payload.status}`,
    `Ready for supervised run: ${payload.ready_for_supervised_run ? "yes" : "no"}`,
    `Preferred model: ${payload.preferred_model || "set-in-openclaw"}`,
    `Image backend: ${payload.image_backend}`,
    `Research backends: rss=${payload.research_backends?.rss ? "on" : "off"} | searxng=${payload.research_backends?.searxng ? "on" : "off"} | pytrends=${payload.research_backends?.pytrends ? "on" : "off"}`,
    `Telegram target: ${payload.telegram?.target || "not set"}`,
    "",
    "Checks:",
    ...checks,
  ].join("\n");
}

function summarizeDashboard(payload) {
  const summary = payload.summary || {};
  const platformLines = Object.entries(summary.platform_success || {}).map(
    ([name, item]) => `- ${name}: ${item.success_rate}% (${item.posted_runs}/${item.attempted_runs})`,
  );
  return [
    `Generated: ${summary.generated_at || "n/a"}`,
    `Runs total: ${summary.totals?.runs ?? 0}`,
    `Success rate 14d: ${summary.success_rate_14d ?? 0}%`,
    `Awaiting approval: ${summary.totals?.awaiting_approval ?? 0}`,
    `Posting failed: ${(summary.totals?.posting_failed ?? 0) + (summary.totals?.failed ?? 0)}`,
    "",
    "Platforms:",
    ...platformLines,
  ].join("\n");
}

function summarizeSecurity(payload) {
  const checks = Object.entries(payload.checks || {}).map(
    ([name, ok]) => `- ${name}: ${ok ? "ok" : "attention"}`,
  );
  return [
    `Security status: ${payload.status}`,
    `Telegram enabled: ${payload.openclaw_config?.telegram_enabled ? "yes" : "no"}`,
    `Telegram allowlist count: ${payload.openclaw_config?.telegram_allow_from_count ?? 0}`,
    "",
    "Checks:",
    ...checks,
    "",
    "Warnings:",
    ...((payload.warnings || []).length ? payload.warnings.map((item) => `- ${item}`) : ["- none"]),
  ].join("\n");
}

function summarizeCompliance(payload) {
  const report = payload.report || {};
  const blocked = report.blockers || [];
  return [
    `Compliance status: ${payload.status || report.overall_status || "unknown"}`,
    `14-day success: ${report.dashboard?.success_rate_14d ?? "n/a"}%`,
    `Runs in last 14d: ${report.dashboard?.runs_in_last_14d ?? "n/a"}`,
    `30-day zero-cost runs: ${report.zero_cost_report?.runs_considered ?? "n/a"}`,
    "",
    "Top blockers:",
    ...(blocked.length ? blocked.slice(0, 5).map((item) => `- ${item}`) : ["- none"]),
  ].join("\n");
}

function summarizeRuntime(payload) {
  return [
    `Primary model: ${payload.primary_model || "not set"}`,
    `Telegram enabled: ${payload.telegram_enabled ? "yes" : "no"}`,
    `Telegram allowlist count: ${payload.telegram_allowlist_count ?? 0}`,
    `Sentinel plugin: ${payload.sentinel_plugin_enabled ? "enabled" : "disabled"}`,
    `Workspace: ${payload.sentinel_workspace_dir || "not set"}`,
    `Memory search: ${payload.memory_search_enabled ? "enabled" : "disabled"}`,
  ].join("\n");
}

function summarizeScheduler(payload) {
  return [
    `Scheduler status: ${payload.status}`,
    `Today runs: ${(payload.today_runs || []).length}`,
    `Stale approvals: ${(payload.stale_approvals || []).length}`,
    `Previews missing: ${(payload.preview_missing || []).length}`,
    `Posting failed: ${(payload.posting_failed || []).length}`,
    "",
    "Actions:",
    ...((payload.action_items || []).length ? payload.action_items.map((item) => `- ${item}`) : ["- none"]),
  ].join("\n");
}

function summarizeQueue(payload) {
  const items = payload.items || [];
  return [
    `Operator queue: ${payload.status}`,
    `Total: ${payload.counts?.total ?? 0}`,
    `High: ${payload.counts?.high ?? 0}`,
    `Medium: ${payload.counts?.medium ?? 0}`,
    `Low: ${payload.counts?.low ?? 0}`,
    "",
    "Top actions:",
    ...(items.length ? items.slice(0, 8).map((item) => `- [${item.severity}] ${item.category}${item.run_id ? ` | ${item.run_id}` : ""} | ${item.summary}`) : ["- none"]),
  ].join("\n");
}

function summarizeBriefQueue(payload) {
  const items = payload.briefs || [];
  return [
    `Daily brief queue: ${items.length} item(s)`,
    `Path: ${payload.path || "n/a"}`,
    "",
    "Top briefs:",
    ...(items.length
      ? items.slice(0, 8).map((item) => `- ${item.brief_id} | ${item.status} | priority=${item.priority ?? 0} | ${item.label || item.topic || "brief"}`)
      : ["- none"]),
  ].join("\n");
}

function summarizeBrowserHealth(payload) {
  if (payload.results) {
    const lines = [`Browser health: ${payload.status}`, `Ready profiles: ${payload.ready_profiles}/${payload.total_profiles}`];
    for (const item of payload.results) {
      lines.push(`- ${item.platform}: ${item.status} | reason=${item.reason || "none"} | composer=${item.composer_detected ? "yes" : "no"}`);
    }
    return lines.join("\n");
  }
  return [
    `Platform: ${payload.platform}`,
    `Status: ${payload.status}`,
    `Reason: ${payload.reason || "none"}`,
    `Composer detected: ${payload.composer_detected ? "yes" : "no"}`,
    `Profile: ${payload.profile || "n/a"}`,
  ].join("\n");
}

function summarizeApprovalPacket(payload) {
  return [
    `Recommendation: ${payload.recommendation || "unknown"}`,
    `Risk level: ${payload.risk_level || "unknown"}`,
    `Confidence: ${payload.confidence ?? "n/a"}`,
    `Publish ready: ${payload.publish_ready ? "yes" : "no"}`,
    `Approval: ${payload.approval_state || "pending"}`,
    "",
    "Blockers:",
    ...((payload.blockers || []).length ? payload.blockers.map((item) => `- ${item}`) : ["- none"]),
  ].join("\n");
}

function summarizeExecutionPolicy(payload) {
  const lines = [
    `Decision: ${payload.decision || "unknown"}`,
    `Reason: ${payload.reason || "unknown"}`,
    `Confidence: ${payload.confidence ?? "n/a"} / threshold=${payload.threshold ?? "n/a"}`,
    `Approval: ${payload.approval_state || "pending"}`,
    `Auto publish: ${payload.auto_publish_enabled ? "yes" : "no"}`,
    "",
    "Platforms:",
  ];
  for (const [platform, item] of Object.entries(payload.platforms || {})) {
    lines.push(
      `- ${platform}: now=${item.can_publish_now ? "yes" : "no"} | wait=${item.should_wait_for_window ? "yes" : "no"} | browser=${item.browser_status || "unknown"} | timing=${item.timing_status || "unknown"}`
    );
  }
  return lines.join("\n");
}

module.exports = function register(api) {
  function cfg(ctx) {
    return ctx.config?.plugins?.entries?.["sentinel-ops"]?.config || {};
  }

  function registerSentinelCommand(definition) {
    const { aliases = [], ...base } = definition;
    const names = [base.name, ...aliases].filter(Boolean);
    for (const name of names) {
      api.registerCommand({
        ...base,
        name,
      });
    }
  }

  registerSentinelCommand({
    name: "ocs-doctor",
    aliases: ["doctor"],
    description: "Check workspace readiness and operator configuration",
    acceptsArgs: false,
    requireAuth: true,
    handler: async (ctx) => {
      const result = runCli(cfg(ctx), ["doctor"]);
      return { text: result.ok ? summarizeDoctor(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-runtime",
    aliases: ["runtime"],
    description: "Show active OpenClaw runtime configuration relevant to Sentinel",
    acceptsArgs: false,
    requireAuth: true,
    handler: async (ctx) => {
      const result = runCli(cfg(ctx), ["runtime-status"]);
      return { text: result.ok ? summarizeRuntime(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-dashboard",
    aliases: ["dashboard"],
    description: "Show monitoring summary for recent runs",
    acceptsArgs: false,
    requireAuth: true,
    handler: async (ctx) => {
      const result = runCli(cfg(ctx), ["build-dashboard", "--json"]);
      return { text: result.ok ? summarizeDashboard(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-scheduler",
    aliases: ["scheduler"],
    description: "Audit scheduler health, stale approvals, and missing previews",
    acceptsArgs: false,
    requireAuth: true,
    handler: async (ctx) => {
      const result = runCli(cfg(ctx), ["scheduler-health"]);
      return { text: result.ok ? summarizeScheduler(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-security",
    aliases: ["security"],
    description: "Audit local security and operational hardening",
    acceptsArgs: false,
    requireAuth: true,
    handler: async (ctx) => {
      const result = runCli(cfg(ctx), ["security-audit"]);
      return { text: result.ok ? summarizeSecurity(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-compliance",
    aliases: ["compliance"],
    description: "Build a cahier des charges compliance report",
    acceptsArgs: false,
    requireAuth: true,
    handler: async (ctx) => {
      const result = runCli(cfg(ctx), ["compliance-report"]);
      return { text: result.ok ? summarizeCompliance(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-queue",
    aliases: ["queue"],
    description: "Show the operator action queue for stale approvals, failed posts, and config gaps",
    acceptsArgs: false,
    requireAuth: true,
    handler: async (ctx) => {
      const result = runCli(cfg(ctx), ["ops-queue"]);
      return { text: result.ok ? summarizeQueue(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-briefs",
    aliases: ["briefs"],
    description: "Show the daily brief queue used by scheduled runs",
    acceptsArgs: false,
    requireAuth: true,
    handler: async (ctx) => {
      const result = runCli(cfg(ctx), ["show-daily-brief-queue"]);
      return { text: result.ok ? summarizeBriefQueue(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-promote-brief",
    aliases: ["promote-brief"],
    description: "Promote one queued brief into the active daily input file",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const briefId = parseArgs(ctx.args);
      if (!briefId) {
        return { text: "Usage: /promote-brief <brief_id>" };
      }
      const result = runCli(cfg(ctx), ["promote-daily-brief", "--brief-id", briefId]);
      if (!result.ok) {
        return { text: result.text };
      }
      return { text: `Brief promoted: ${result.payload.brief_id}\nDaily input: ${result.payload.daily_input_path}` };
    },
  });

  registerSentinelCommand({
    name: "ocs-browser-health",
    aliases: ["browser-health"],
    description: "Check whether browser profiles are logged in and composer-ready",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const platform = parseArgs(ctx.args);
      const result = platform
        ? runCli(cfg(ctx), ["browser-health", "--platform", platform])
        : runCli(cfg(ctx), ["browser-health"]);
      return { text: result.ok ? summarizeBrowserHealth(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-approval-packet",
    aliases: ["approval-packet"],
    description: "Show the approval recommendation packet for one run",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const runId = parseArgs(ctx.args);
      if (!runId) {
        return { text: "Usage: /approval-packet <run_id>" };
      }
      const result = runCli(cfg(ctx), ["approval-packet", "--run-id", runId]);
      return { text: result.ok ? summarizeApprovalPacket(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-execution-policy",
    aliases: ["execution-policy"],
    description: "Show the execution decision engine for one run",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const runId = parseArgs(ctx.args);
      if (!runId) {
        return { text: "Usage: /execution-policy <run_id>" };
      }
      const result = runCli(cfg(ctx), ["execution-policy", "--run-id", runId]);
      return { text: result.ok ? summarizeExecutionPolicy(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-status",
    aliases: ["status"],
    description: "Show OpenClaw Content Sentinel run status",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const args = parseArgs(ctx.args);
      const cliArgs = ["status", "--json"];
      if (args) {
        cliArgs.push("--run-id", args);
      }
      const result = runCli(cfg(ctx), cliArgs);
      return { text: result.ok ? summarizeStatus(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-logs",
    aliases: ["logs"],
    description: "Show artifacts and publish logs for a run",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const runId = parseArgs(ctx.args);
      if (!runId) {
        return { text: "Usage: /logs <run_id>" };
      }
      const result = runCli(cfg(ctx), ["logs", "--run-id", runId]);
      return { text: result.ok ? summarizeLogs(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-run-now",
    aliases: ["run-now"],
    description: "Create today's run from the configured prompt file and a competitor URL",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const competitorUrl = parseArgs(ctx.args);
      const pluginConfig = cfg(ctx);
      if (!competitorUrl) {
        const scheduled = runCli(pluginConfig, ["scheduled-run", "--ignore-pause"]);
        if (!scheduled.ok) {
          return { text: scheduled.text };
        }
        return {
          text: [
            `Run created: ${scheduled.payload.run_id}`,
            `Status: ${scheduled.payload.status}`,
            `Confidence: ${scheduled.payload.confidence ?? "n/a"}`,
            "Flow: OpenClaw executes steps 1-4 automatically, sends the Telegram preview at step 5, then launches step 6 after /approve.",
          ].join("\n"),
        };
      }
      if (!pluginConfig.promptFile) {
        return { text: "Plugin config is missing promptFile." };
      }
      const cliArgs = [
        "daily-run",
        "--prompt-file",
        pluginConfig.promptFile,
        "--ignore-pause",
        "--competitor-url",
        competitorUrl,
      ];
      if ((pluginConfig.defaultTargets || []).length) {
        cliArgs.push("--targets", ...(pluginConfig.defaultTargets || []));
      }
      const result = runCli(pluginConfig, cliArgs);
      if (!result.ok) {
        return { text: result.text };
      }
      return {
        text: [
          `Run created: ${result.payload.run_id}`,
          `Status: ${result.payload.status}`,
          `Confidence: ${result.payload.confidence ?? "n/a"}`,
          "Flow: OpenClaw executes steps 1-4 automatically, sends the Telegram preview at step 5, then launches step 6 after /approve.",
        ].join("\n"),
      };
    },
  });

  registerSentinelCommand({
    name: "ocs-approve",
    aliases: ["approve"],
    description: "Approve a run for publishing",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const args = parseArgs(ctx.args);
      if (!args) {
        return { text: "Usage: /approve <run_id> [note]" };
      }
      const [runId, ...rest] = args.split(/\s+/);
      const cliArgs = ["approve-and-publish", "--run-id", runId];
      if (rest.length) {
        cliArgs.push("--note", rest.join(" "));
      }
      const result = runCli(cfg(ctx), cliArgs);
      if (!result.ok) {
        return { text: result.text };
      }
      return {
        text: summarizeApproveAndPublish(result.payload),
      };
    },
  });

  registerSentinelCommand({
    name: "ocs-reject",
    aliases: ["reject"],
    description: "Reject a run and require edits",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const args = parseArgs(ctx.args);
      if (!args) {
        return { text: "Usage: /reject <run_id> [note]" };
      }
      const [runId, ...rest] = args.split(/\s+/);
      const cliArgs = ["reject", "--run-id", runId];
      if (rest.length) {
        cliArgs.push("--note", rest.join(" "));
      }
      const result = runCli(cfg(ctx), cliArgs);
      return { text: result.ok ? `Rejected ${runId}.` : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-pause",
    aliases: ["pause"],
    description: "Pause scheduled automation",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const reason = parseArgs(ctx.args);
      const result = runCli(cfg(ctx), ["pause", "--reason", reason, "--updated-by", ctx.senderId || "operator"]);
      return { text: result.ok ? "Scheduled automation paused." : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-resume",
    aliases: ["resume"],
    description: "Resume scheduled automation",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const reason = parseArgs(ctx.args);
      const result = runCli(cfg(ctx), ["resume", "--reason", reason, "--updated-by", ctx.senderId || "operator"]);
      return { text: result.ok ? "Scheduled automation resumed." : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-stage-publish",
    aliases: ["stage-publish"],
    description: "Prepare publish metadata for one run or a single platform",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const args = parseArgs(ctx.args);
      if (!args) {
        return { text: "Usage: /stage-publish <run_id> [platform]" };
      }
      const [runId, platform] = args.split(/\s+/);
      const result = platform
        ? runCli(cfg(ctx), ["publish", "--run-id", runId, "--platform", platform])
        : runCli(cfg(ctx), ["publish-all", "--run-id", runId]);
      return { text: result.ok ? summarizePublish(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-publish",
    aliases: ["publish"],
    description: "Publish one run or one platform through OpenClaw browser automation",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const args = parseArgs(ctx.args);
      if (!args) {
        return { text: "Usage: /publish <run_id> [platform]" };
      }
      const [runId, platform] = args.split(/\s+/);
      const result = platform
        ? runCli(cfg(ctx), ["browser-publish", "--run-id", runId, "--platform", platform])
        : runCli(cfg(ctx), ["browser-publish-all", "--run-id", runId]);
      return { text: result.ok ? summarizeBrowserPublish(result.payload) : result.text };
    },
  });

  registerSentinelCommand({
    name: "ocs-retry",
    aliases: ["retry"],
    description: "Retry live browser publishing for a failed platform",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx) => {
      const args = parseArgs(ctx.args);
      const [runId, platform] = args.split(/\s+/);
      if (!runId || !platform) {
        return { text: "Usage: /retry <run_id> <platform>" };
      }
      const result = runCli(cfg(ctx), ["browser-publish", "--run-id", runId, "--platform", platform]);
      return { text: result.ok ? summarizeBrowserPublish(result.payload) : result.text };
    },
  });
};

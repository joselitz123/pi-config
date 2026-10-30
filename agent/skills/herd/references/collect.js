// Evaluate this trusted bundled file inside native codemode; it returns collectHerd.
// Recommended OUTER first line: // @options: {"max_output_tokens": 30000}
(async function collectHerd(input) {
  const quote = value => "'" + String(value).replace(/'/g, "'\\''") + "'";
  if (!input || typeof input.helper !== "string") throw new Error("Supply the absolute collection helper path");
  const prefix = "node " + quote(input.helper);
  const key = rundir => "herd-collection:" + rundir;
  if ([input.collect, input.next, input.run].filter(value => value !== undefined).length !== 1) {
    throw new Error("Choose run:<fanout arguments>, collect:<rundir>, or next:<rundir>");
  }
  if (input.run && (!Array.isArray(input.run) || input.run.some(arg => typeof arg !== "string") ||
      !["--plan", "--wait", "--retry-leaf"].includes(input.run[0]) || input.run.includes("--detach"))) {
    throw new Error("Use blocking --plan, --wait, or --retry-leaf arguments");
  }
  const knownRun = input.collect || input.next || (input.run?.[0] === "--wait" ? input.run[1] :
    input.run?.[0] === "--retry-leaf" ? input.run[input.run.indexOf("--rundir") + 1] : undefined);
  const previous = knownRun ? load(key(knownRun)) : undefined;
  if (previous?.state === "complete" && previous.kind === "results") {
    throw new Error("Leaf results were already collected for this run");
  }
  if (input.next && (previous?.state !== "more" || !previous.next_cursor)) throw new Error("No saved collection cursor");
  if (!input.next && previous?.state === "more") throw new Error("Continue the saved cursor with next:<rundir>");

  async function command(args, stdin) {
    let shell = prefix + " " + args.map(quote).join(" ");
    if (stdin !== undefined) {
      if (typeof stdin !== "string") throw new Error("input must be a string");
      let delimiter = "HERD_COLLECTION_INPUT";
      while (stdin.split("\n").includes(delimiter)) delimiter += "_";
      shell += ` <<'${delimiter}'\n${stdin}\n${delimiter}`;
    }
    const result = await tools.bash({ command: shell });
    if (!result || typeof result.output !== "string" || result.truncated !== false) {
      throw new Error("Incomplete nested Bash capture: native structured output with truncated:false is required");
    }
    let value;
    try { value = JSON.parse(result.output); }
    catch { throw new Error("Collection helper did not return intact JSON; no findings were accepted"); }
    if (value.version !== 1) throw new Error("Unsupported collection protocol");
    if (value.state === "error") throw new Error(`${value.reason}: ${value.text}`);
    if (![0, 1].includes(result.exit_code) && value.state !== "limited") {
      throw new Error(`Collection helper failed with exit ${result.exit_code}`);
    }
    return value;
  }

  const context = await command(["--context"]);
  if (input.next && context.compaction_id !== previous.compaction_id) {
    throw new Error("Session compacted between collection pages; earlier findings are no longer guaranteed intact. Stop before synthesis.");
  }
  if (!Number.isSafeInteger(context.codemode_output_tokens) || context.codemode_output_tokens < 1) {
    throw new Error("Cannot verify the active codemode output budget from this session");
  }
  const outputChars = Math.min(120000, context.codemode_output_tokens * 4);
  const pageBytes = Math.min(96 * 1024, outputChars - 2048);
  if (pageBytes < 256) throw new Error("Codemode output budget is too small; use max_output_tokens:30000");
  if (typeof models === "undefined") throw new Error("Native codemode model metadata is unavailable");
  const model = await models.getModelOfType("chat", context.provider, context.id);
  if (!model || !Number.isSafeInteger(model.contextWindow) || model.contextWindow <= 0) {
    throw new Error("The active model's context window is unknown; collection stopped before spawning");
  }
  const limits = JSON.stringify({ provider: context.provider, id: context.id, contextWindow: model.contextWindow });
  const args = input.run ? ["--run", JSON.stringify(input.run)] :
    input.next ? ["--page", previous.next_cursor] : ["--collect", input.collect];
  if (input.final) {
    if (!input.collect) throw new Error("final:true is only for collection after retry decisions");
    args.push("--final");
  }
  const page = await command([...args, "--limits", limits, "--page-bytes", String(pageBytes)], input.input);
  if (knownRun && page.rundir !== knownRun) throw new Error("Use the exact returned rundir, without path aliases");
  if (page.state === "limited") {
    const notice = `Collection limited (${page.reason}).\n\n${page.text}`;
    if (notice.length > outputChars - 512) throw new Error("Collection limited; even its run report exceeds this output budget");
    return notice;
  }
  if (!["complete", "report_only", "more"].includes(page.state) || typeof page.text !== "string") {
    throw new Error("Invalid collection page");
  }
  let bytes = 0;
  for (let i = 0; i < page.text.length; i++) {
    const ch = page.text.charCodeAt(i);
    if (ch < 0x80) bytes++;
    else if (ch < 0x800) bytes += 2;
    else if (ch >= 0xd800 && ch <= 0xdbff) { bytes += 4; i++; }
    else bytes += 3;
  }
  if (page.end - page.offset !== bytes || !/^[a-f0-9]{64}$/.test(page.sha256) ||
      (input.next && (page.offset !== previous.end || page.sha256 !== previous.sha256 ||
        page.total_bytes !== previous.total_bytes || page.kind !== previous.kind)) ||
      (!input.next && page.offset !== 0)) {
    throw new Error("Incomplete collection page; no findings were accepted");
  }
  const output = `Collection ${page.state} (${page.kind}); bytes ${page.offset}..${page.end}/${page.total_bytes}; ` +
    `sha256 ${page.sha256}.\n\n${page.text}`;
  if (output.length > outputChars - 512) throw new Error("Collection exceeds the active codemode output budget");
  store(key(page.rundir), { state: page.state, kind: page.kind, sha256: page.sha256,
    end: page.end, total_bytes: page.total_bytes, next_cursor: page.next_cursor, compaction_id: context.compaction_id });
  return output;
})

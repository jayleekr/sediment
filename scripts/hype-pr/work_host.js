/* Work host adapter. Run in functions.exec with the connected tools object.
 * It never receives a GitHub credential. It does not execute repository code.
 * The caller authorizes one exact target repository/branch, not arbitrary writes.
 */
async function serveWorkRequest(tools, request, options) {
  const repositories = options.repositories;
  const unwrap = (result) => {
    if (!result || result.isError) throw new Error("GitHub connector operation failed");
    const value = result.structuredContent?.result ?? result.structuredContent;
    if (!value || value.error) throw new Error("GitHub connector returned no usable data");
    return value;
  };
  const get = async (path) => {
    const match = /^repos\/([^/]+\/[^/]+)\/(commits|contents|compare|issues)\/(.+)$/.exec(path);
    if (!match || !repositories.includes(match[1]) || /[\s#\\]/.test(path)
        || path.includes("..") && match[2] !== "compare"
        || match[2] === "issues" && !/^[1-9][0-9]*$/.test(match[3])) {
      throw new Error("Read outside Work preparation scope");
    }
    // Cache only immutable content. Mutable refs are fetched on every verification.
    const immutable = match[2] === "contents" && /[?&]ref=[a-f0-9]{40}$/.test(path);
    if (immutable && options.cache?.has(path)) return options.cache.get(path);
    let data;
    if (match[2] === "contents") {
      const source = /^(.+)\?ref=([a-f0-9]{40})$/.exec(match[3]);
      if (!source) throw new Error("Work source content requires an immutable ref");
      // The generic fetch tool returns raw file text for contents URLs rather
      // than GitHub's base64 envelope. Use the typed file tool explicitly.
      const value = unwrap(await tools.mcp__codex_apps__github_fetch_file({
        repository_full_name: match[1], path: source[1], ref: source[2], encoding: "base64",
      }));
      if (value.encoding !== "base64" || typeof value.content !== "string") {
        throw new Error("Connector file response incomplete");
      }
      data = {encoding: value.encoding, content: value.content};
    } else {
      const value = unwrap(await tools.mcp__codex_apps__github_fetch({url: "https://api.github.com/" + path}));
      data = JSON.parse(value.content);
    }
    if (immutable) options.cache?.set(path, data);
    return data;
  };
  if (request.version !== 1 || !/^[a-f0-9]{32}$/.test(request.id)) {
    throw new Error("Invalid Work request envelope");
  }
  const p = request.payload;
  if (request.operation === "read") return get(p.path);
  if (!options.allowCreate || p.repo !== options.repo || !repositories.includes(p.repo)) {
    throw new Error("Mutation outside authorized Work target");
  }
  if (request.operation === "reviewer") {
    if (!options.createdPR || p.pr !== options.createdPR.url
        || !options.reviewers.includes(p.reviewer) || p.reviewer === options.author) {
      throw new Error("Reviewer request outside created PR scope");
    }
    return unwrap(await tools.mcp__codex_apps__github_request_pull_request_reviewers({
      repository_full_name: p.repo, pr_number: options.createdPR.number, reviewers: [p.reviewer],
    }));
  }
  if (request.operation === "unreviewer") {
    if (!options.createdPR || p.pr !== options.createdPR.url
        || !options.reviewers.includes(p.reviewer) || p.reviewer === options.author) {
      throw new Error("Reviewer cleanup outside created PR scope");
    }
    return unwrap(await tools.mcp__codex_apps__github_remove_pull_request_reviewers({
      repository_full_name: p.repo, pr_number: options.createdPR.number, reviewers: [p.reviewer],
    }));
  }
  if (request.operation !== "create" || p.head !== options.branch || p.base !== "main"
      || p.author !== options.author || options.creationAttempted) {
    throw new Error("Unsupported or repeated Work mutation");
  }
  const actor = unwrap(await tools.mcp__codex_apps__github_get_user_login({}));
  if ((actor.login ?? actor.username) !== p.author) throw new Error("PR author differs from connected actor");
  if (!p.expected || Object.keys(p.expected.sources).sort().join() !== [...repositories].sort().join()
      || p.expected.sources[p.repo] !== p.expected.head) throw new Error("Incomplete prepared source set");
  const expectedRefs = [[p.repo, p.head, p.expected.head], [p.repo, p.base, p.expected.base],
    ...repositories.filter(r => r !== p.repo).map(r => [r, "main", p.expected.sources[r]])];
  for (const [repo, ref, sha] of expectedRefs) {
    if (!/^[a-f0-9]{40}$/.test(sha) || (await get(`repos/${repo}/commits/${ref}`)).sha !== sha) {
      throw new Error("Prepared source advanced; inspect and assess again");
    }
  }
  if (!p.body.includes("<!-- hype-pr-prepared:v1 -->")) throw new Error("Missing preparation summary");
  // No automatic retry: a network failure may follow a successful remote creation.
  options.creationAttempted = true;
  const result = unwrap(await tools.mcp__codex_apps__github_create_pull_request({
    repository_full_name: p.repo, base: p.base, head: p.head,
    title: p.title, body: p.body, draft: p.draft,
  }));
  const created = {url: result.url ?? result.html_url ?? result.display_url,
                   number: result.number ?? result.pr_number};
  if (!created.url || !created.number) throw new Error("PR creation response incomplete; reconcile remote state");
  options.createdPR = created;
  // Keep created identity even if ancillary label work fails.
  const labelErrors = [];
  for (const label of p.labels ?? []) {
    try {
      unwrap(await tools.mcp__codex_apps__github_label_pr({
        repository_full_name: p.repo, pr_number: created.number, label,
      }));
    } catch (_) { labelErrors.push(label); }
  }
  return {...created, label_errors: labelErrors};
}

async function runWorkCommand(tools, options) {
  const quote = s => "'" + String(s).replace(/'/g, "'\\''") + "'";
  const scratch = await tools.exec_command({cmd: "mktemp -d /tmp/hype-pr-work-XXXXXXXX", max_output_tokens: 100});
  if (scratch.exit_code !== 0) throw new Error("Cannot create Work transport directory");
  const directory = scratch.output.trim();
  if (!/^\/tmp\/hype-pr-work-[A-Za-z0-9]+$/.test(directory)) throw new Error("Invalid scratch path");
  options.cache ??= new Map();
  const cachedSources = Object.fromEntries([...options.cache].filter(([path]) =>
    /^repos\/[^/]+\/[^/]+\/contents\/.+\?ref=[a-f0-9]{40}$/.test(path)
    && options.repositories.includes(path.split('/').slice(1, 3).join('/'))));
  await tools.apply_patch(`*** Begin Patch\n*** Add File: ${directory}/immutable-cache.json\n+${JSON.stringify(cachedSources)}\n*** End Patch`);
  const python = options.python ?? "python3";
  const cli = options.harness + "/scripts/hype-pr/pr.py";
  const poll = options.harness + "/scripts/hype-pr/work_transport.py";
  const started = await tools.exec_command({
    cmd: `HYPE_PR_WORK_DIR=${quote(directory)} ${quote(python)} ${quote(cli)} ${options.args.map(quote).join(" ")} > ${quote(directory + "/stdout")} 2> ${quote(directory + "/stderr")}\nresult=$?\nprintf '%s' "$result" > ${quote(directory + "/done")}`,
    workdir: options.checkout, yield_time_ms: 1000, max_output_tokens: 100,
  });
  const handled = new Set();
  const diagnostics = [];
  let lastProgress = Date.now();
  // Serial request/reply protocol. No failed mutation is replayed automatically.
  for (let round = 0; round < 10000; round++) {
    const result = await tools.exec_command({cmd: `${quote(python)} ${quote(poll)} ${quote(directory)}`, max_output_tokens: 16000});
    if (result.exit_code !== 0) throw new Error("Work queue poll failed");
    const queue = JSON.parse(result.output);
    if (queue.done !== null) {
      if (started.session_id) await tools.write_stdin({session_id: started.session_id, chars: "", yield_time_ms: 1000, max_output_tokens: 100});
      return {exit_code: Number(queue.done), directory, createdPR: options.createdPR, diagnostics};
    }
    for (const request of queue.requests) {
      if (!/^[a-f0-9]{32}$/.test(request.id)) throw new Error("Invalid queued request ID");
      if (handled.has(request.id)) continue;
      handled.add(request.id);
      let response;
      try {
        response = {version: 1, id: request.id, ok: true,
                    result: await serveWorkRequest(tools, request, options)};
      } catch (error) {
        diagnostics.push({operation: request.operation, error: error.message});
        response = {version: 1, id: request.id, ok: false};
      }
      const contents = JSON.stringify(response);
      await tools.apply_patch(`*** Begin Patch\n*** Add File: ${directory}/${request.id}.reply\n+${contents}\n*** End Patch`);
    }
    if (Date.now() - lastProgress > 30000) {
      options.onProgress?.({requests: handled.size, directory});
      lastProgress = Date.now();
    }
    // Tool calls naturally pace active requests; avoid busy-polling during local computation.
    if (!queue.requests.length) await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error("Work command exceeded host polling limit; reconcile remote state");
}

if (typeof module !== "undefined") module.exports = {serveWorkRequest, runWorkCommand};

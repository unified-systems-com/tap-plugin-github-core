/**
 * github_core machinery projection — ONE repository's CI system drawn as
 * nested machinery (spec-github-core-machinery-projection.md; git-serious-tap#35).
 *
 * Three ideas carry the picture:
 *
 *   1. Nesting is the legibility. github.com ⊃ account ⊃ repository ⊃
 *      workflow ⊃ job — a workflow is a box that contains its jobs the way a
 *      pipeline contains steps (req-github-core-machinery-nesting).
 *   2. The stage axis is a derived integer and direction is a sign. Inside
 *      the repository box: sources (refs + the rulesets that gate them) →
 *      pipelines (workflow boxes, rows grouped by trigger class) → outputs
 *      (environments; releases/artifacts/packages when their rows exist).
 *      Inside a workflow box: rank = longest path over `needs:`. `flow: rtl`
 *      (default) puts sources on the RIGHT (req-github-core-machinery-stages,
 *      -flow).
 *   3. An unknown never renders as a known. A job whose `needs` cannot be
 *      resolved lands in the unranked column with a warning, never at rank 0;
 *      an output kind the collector does not observe renders as a "not yet
 *      collected" placeholder, never as an empty column
 *      (req-github-core-machinery-honesty).
 *
 * Third parties (apps, the OIDC issuer, runners) run along the top of the
 * github.com box; the bottom tier is reserved for outputs to humans
 * (req-github-core-machinery-tiers).
 *
 * Nothing here names a repository, workflow or job: the repository is whatever
 * `github_repository` node the consumer's searches placed in the scene
 * (req-github-core-machinery-module-3). Typed model fields are not on the cy
 * node data (panel-graph carries spine fields only), so the module fetches
 * job keys, permissions, needs, workflow configuration and ref kinds through
 * the same client-side Gryphon endpoint the arrangement runtime uses.
 *
 * Standard tap layout module: `export async function execute(context)`
 * (spec-viz-layouts.md, req-viz-layout-module-contract).
 */

import {projectNested, ELEVATION_HIDDEN_CLASS} from "/static/tap_viz/js/runtime/nested-projection.js";
import {applyStack} from "/static/tap_viz/js/runtime/stack.js";

const GRYPHON_URL = "/api/v1/gryphon/execute";

const T = {
    platform: "github_core__github_platform",
    account: "github_core__github_account",
    repository: "github_core__github_repository",
    workflow: "github_core__github_workflow",
    job: "github_core__workflow_job",
    ref: "github_core__git_ref",
    ruleset: "github_core__github_ruleset",
    environment: "github_core__github_environment",
    app: "github_core__github_app",
    runner: "github_core__github_runner",
    issuer: "identity_core__oidc_issuer",
    placeholder: "_machinery_placeholder",
};

const E = {
    hostsAccount: "HOSTS_ACCOUNT__github_core",
    ownsRepo: "OWNS_REPO__github_core",
    definesWorkflow: "DEFINES_WORKFLOW__github_core",
    definesJob: "DEFINES_JOB__github_core",
    dependsOnJob: "DEPENDS_ON_JOB__github_core",
    hasRef: "HAS_REF__github_core",
    protects: "PROTECTS__github_core",
    hasEnvironment: "HAS_ENVIRONMENT__github_core",
    enabledOn: "ENABLED_ON__github_core",
};

// Scene-local synthetic edges so nesting can be declared for things the
// grid has no containment edge for. Never written to the grid.
const SYN = {
    hostsThirdParty: "_MACHINERY_HOSTS_THIRD_PARTY",
    hasPlaceholder: "_MACHINERY_HAS_PLACEHOLDER",
};

// Stages inside the repository box (stage 0 is the source end).
const STAGE = {sources: 0, pipelines: 1, outputs: 2};

// Row order of pipelines within the pipelines column, by trigger class.
// A workflow with several triggers takes the first it matches; one with
// none goes to the trailing "untriggered" row (req-github-core-machinery-stages).
const TRIGGER_ORDER = ["pull_request", "merge_group", "push", "workflow_run", "schedule", "workflow_dispatch", "workflow_call"];
const UNTRIGGERED_ORDER = TRIGGER_ORDER.length;

// Effective-permission scopes whose `write` marks a job as a producer —
// the interim proxy for outputs while no release/artifact/package row exists.
const PRODUCER_SCOPES = ["packages", "contents", "id-token"];

// Output kinds the collector does not observe yet; each gets a placeholder
// per repository until that kind's nodes appear (spec: req-...-honesty-1).
const UNCOLLECTED_OUTPUT_KINDS = ["releases", "artifacts", "packages"];

// Leaf card sizes hold a name, not just an icon; containers take these as
// floors and grow to their children (spec-viz-nested-projection).
const BASE_SIZES = {
    [T.platform]: {width: 480, height: 200},
    [T.account]: {width: 320, height: 120},
    [T.repository]: {width: 320, height: 90},
    [T.workflow]: {width: 190, height: 44},
    [T.job]: {width: 150, height: 34},
    [T.ref]: {width: 150, height: 34},
    [T.ruleset]: {width: 170, height: 34},
    [T.environment]: {width: 150, height: 34},
    [T.app]: {width: 180, height: 40},
    [T.runner]: {width: 180, height: 40},
    [T.issuer]: {width: 200, height: 40},
    [T.placeholder]: {width: 190, height: 30},
};

const DEFAULTS = {flow: "rtl", column_gap: 48, row_gap: 12, stack_refs_over: 3};

// Grid edges reach the client with their type in `label` (panel-graph.js
// copies edge_type onto label only); the scene-local synthetic edges carry
// both. Match either, as nesting.js does.
const edgeSel = (type) => `edge[edge_type = "${type}"], edge[label = "${type}"]`;
const KNOWN_KEYS = new Set(Object.keys(DEFAULTS));

export async function execute(context) {
    const {cy, projection} = context;
    const warnings = [];
    const warn = (category, message) => {
        warnings.push({category, message});
        console.warn(`[machinery] ${category}: ${message}`);
    };

    const cfg = _readConfig(projection, warn);
    const direction = cfg.flow === "ltr" ? "ltr" : "rtl";

    const repos = cy.nodes(`[entity_type = "${T.repository}"]`);
    if (repos.empty()) {
        warn("machinery_no_repository", "the scene holds no github_repository node; nothing to lay out");
        return {warnings};
    }

    // Repository labels: the box sits inside its owner's box, so "owner/"
    // is redundant and pushes long names past the box. Display only; the
    // entity name (full_name) is kept for the fact queries below.
    const fullNameOf = new Map();
    repos.forEach((repo) => {
        const label = repo.data("label") || "";
        fullNameOf.set(repo.id(), label);
        const slash = label.indexOf("/");
        if (slash > 0) repo.data("label", label.slice(slash + 1));
    });

    // ---- Facts the cy data does not carry -------------------------------
    const facts = await _fetchFacts([...fullNameOf.values()], warn);

    // ---- Jobs: labels, producer state, rank over `needs` -----------------
    _stampJobs(cy, facts, warn);

    // ---- Workflows: stage + trigger-class order ------------------------
    cy.nodes(`[entity_type = "${T.workflow}"]`).forEach((wf) => {
        wf.data("_stage", STAGE.pipelines);
        const conf = (facts.workflows.get(wf.id()) || {}).configuration || {};
        const triggers = Array.isArray(conf.triggers) ? conf.triggers : [];
        const idx = triggers
            .map((t) => TRIGGER_ORDER.indexOf(t))
            .filter((i) => i >= 0)
            .reduce((m, i) => Math.min(m, i), UNTRIGGERED_ORDER);
        wf.data("_order", idx);
        wf.data("_trigger_class", idx < UNTRIGGERED_ORDER ? TRIGGER_ORDER[idx] : "untriggered");
    });

    // ---- Sources: refs (default first, tags, then the branch deck) + gates
    const refPlan = _planRefs(cy, facts, cfg.stack_refs_over);
    cy.nodes(`[entity_type = "${T.ruleset}"]`).forEach((rs) => {
        rs.data("_stage", STAGE.sources);
        rs.data("_order", 3);
    });

    // ---- Outputs: environments now; placeholders for what is not collected
    cy.nodes(`[entity_type = "${T.environment}"]`).forEach((env) => {
        env.data("_stage", STAGE.outputs);
        env.data("_order", 0);
    });
    _addPlaceholders(cy, repos);

    // ---- Third parties nest under github.com (no grid edge exists) -------
    _hostThirdParties(cy);

    // ---- Anything repo-scoped that no present repository owns is not part
    // of this picture: hide it rather than let it float as a root.
    _hideOrphans(cy, repos, warn);

    // ---- Chrome ---------------------------------------------------------
    cy.style()
        .selector("edge")
        .style({label: "", "z-compound-depth": "top", "z-index": 1})
        .selector("node")
        .style({"font-size": "13px"})
        .selector(".tap-viewport-parent")
        .style({"font-size": "15px", "font-weight": "600", "text-margin-y": 8})
        .selector(`node[entity_type = "${T.job}"], node[entity_type = "${T.ref}"], node[entity_type = "${T.ruleset}"], node[entity_type = "${T.environment}"]`)
        .style({"font-size": "12px", "text-wrap": "ellipsis", "text-max-width": "140px", "text-valign": "center", "text-halign": "center"})
        .selector(`node[entity_type = "${T.app}"], node[entity_type = "${T.runner}"], node[entity_type = "${T.issuer}"]`)
        .style({"text-wrap": "ellipsis", "text-max-width": "170px", "text-valign": "center", "text-halign": "center"})
        .selector(`node[entity_type = "${T.placeholder}"]`)
        .style({
            "shape": "round-rectangle", "background-color": "#f8fafc", "border-style": "dashed",
            "border-color": "#94a3b8", "border-width": 1.5, "color": "#64748b", "font-size": "11px",
            "font-style": "italic", "text-valign": "center", "text-halign": "center", "text-wrap": "ellipsis", "text-max-width": "180px",
        })
        .selector(".machinery-producer")
        .style({"border-color": "#b45309", "border-width": 2.5})
        .selector(".machinery-producer-not-observable")
        .style({"border-color": "#b45309", "border-width": 2, "border-style": "dotted"})
        .selector(".machinery-unresolved")
        .style({"border-color": "#dc2626", "border-style": "dashed", "border-width": 2})
        .selector(edgeSel(E.dependsOnJob))
        .style({"line-color": "#94a3b8", "target-arrow-color": "#94a3b8", "target-arrow-shape": "triangle", "width": 1.5, "curve-style": "bezier"})
        .selector(edgeSel(E.protects))
        .style({"line-color": "#b45309", "line-style": "dashed", "width": 1.5})
        .update();

    const ranked = (sort) => ({name: "ranked", direction, columnGap: cfg.column_gap, rowGap: cfg.row_gap, sort});

    const result = await projectNested(cy, {
        relationships: [
            {name: "platform-hosts-account", gryphon: `(parent:${T.platform})-[:${E.hostsAccount}]->(child:${T.account})`},
            {name: "platform-hosts-third-party", gryphon: `(parent:${T.platform})-[:${SYN.hostsThirdParty}]->(child)`},
            {name: "account-owns-repository", gryphon: `(parent:${T.account})-[:${E.ownsRepo}]->(child:${T.repository})`},
            {name: "repository-defines-workflow", gryphon: `(parent:${T.repository})-[:${E.definesWorkflow}]->(child:${T.workflow})`},
            {name: "repository-has-ref", gryphon: `(parent:${T.repository})-[:${E.hasRef}]->(child:${T.ref})`},
            {name: "repository-has-environment", gryphon: `(parent:${T.repository})-[:${E.hasEnvironment}]->(child:${T.environment})`},
            {name: "ruleset-protects-repository", gryphon: `(parent:${T.repository})<-[:${E.protects}]-(child:${T.ruleset})`},
            {name: "repository-has-placeholder", gryphon: `(parent:${T.repository})-[:${SYN.hasPlaceholder}]->(child:${T.placeholder})`},
            {name: "workflow-defines-job", gryphon: `(parent:${T.workflow})-[:${E.definesJob}]->(child:${T.job})`},
        ],
        baseSizes: BASE_SIZES,
        padding: 14,
        paddings: {[T.platform]: 40, [T.account]: 34, [T.repository]: 28, [T.workflow]: 18},
        // github.com is the one root. Third parties on top, the account below;
        // the bottom tier (outputs to humans) is reserved and empty in v0.
        innerLayout: {
            name: "tiered-rows",
            rowGap: 48,
            itemGap: 18,
            tiers: [
                {name: "third-parties", entityTypes: [T.app, T.issuer, T.runner]},
                {name: "account", entityTypes: [T.account]},
            ],
        },
        innerLayouts: {
            [T.account]: {name: "flow", aspect: 2.0, gap: 24, sort: "area-desc"},
            // The pipelines stage holds every workflow; flowed columns keep it a
            // block (rows in trigger-class order) instead of a seventeen-box tower.
            [T.repository]: {...ranked("order"), columnLayout: "flow", flowAspect: 1.1},
            [T.workflow]: ranked("label"),
        },
    });
    warnings.push(...(result.warnings || []));

    // The branch deck: the collapsed refs were hidden before measuring so the
    // source column stays one card tall; the deck token draws over the
    // representative now that it has a position.
    if (refPlan.deck) {
        applyStack(cy, {
            members: refPlan.deck.members,
            representative: refPlan.deck.representative,
            label: `${refPlan.deck.members.length} other branches`,
            stackId: `machinery-branches:${refPlan.deck.repoId}`,
            direction: direction === "rtl" ? "down-right" : "down-left",
        });
    }

    return {warnings};
}

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

function _readConfig(projection, warn) {
    const raw = (projection && projection.definition && projection.definition.machinery)
        || (projection && projection.machinery) || {};
    const cfg = {...DEFAULTS};
    Object.keys(raw).forEach((k) => {
        if (KNOWN_KEYS.has(k)) cfg[k] = raw[k];
        else warn("machinery_unknown_config_key", `ignoring machinery.${k}`);
    });
    if (cfg.flow !== "rtl" && cfg.flow !== "ltr") {
        warn("machinery_bad_flow", `machinery.flow must be "rtl" or "ltr", got ${JSON.stringify(cfg.flow)}; using rtl`);
        cfg.flow = "rtl";
    }
    return cfg;
}

// ---------------------------------------------------------------------------
// Facts: typed fields fetched per repository through client-side Gryphon
// ---------------------------------------------------------------------------

async function _fetchFacts(fullNames, warn) {
    const facts = {jobs: new Map(), workflows: new Map(), refs: new Map()};
    const queries = {
        jobs: [
            `MATCH (j:${T.job})`,
            "WHERE j.data.full_name = $repo",
            "RETURN j.entity_id AS entity_id, j.data.job_key AS job_key, j.data.name AS name, j.data.needs AS needs, j.data.permissions AS permissions, j.data.environment AS environment, j.data.workflow_id AS workflow_id",
        ],
        workflows: [
            `MATCH (w:${T.workflow})`,
            "WHERE w.data.full_name = $repo",
            "RETURN w.entity_id AS entity_id, w.data.workflow_id AS workflow_id, w.data.configuration AS configuration",
        ],
        refs: [
            `MATCH (r:${T.ref})`,
            "WHERE r.data.full_name = $repo",
            "RETURN r.entity_id AS entity_id, r.data.ref_type AS ref_type, r.data.is_default AS is_default, r.data.name AS name",
        ],
    };
    for (const repo of fullNames) {
        for (const [kind, query] of Object.entries(queries)) {
            try {
                const rows = await _gryphonRows(query, {repo});
                rows.forEach((row) => {
                    if (row && row.entity_id) facts[kind].set(String(row.entity_id), row);
                });
            } catch (err) {
                warn(`machinery_facts_${kind}`, `${repo}: ${err.message}`);
            }
        }
    }
    return facts;
}

async function _gryphonRows(queryLines, inputs) {
    const headers = {"Content-Type": "application/json"};
    const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (csrf) headers["X-CSRFToken"] = csrf[1];
    const res = await fetch(GRYPHON_URL, {
        method: "POST",
        credentials: "same-origin",
        headers,
        body: JSON.stringify({query: queryLines.join("\n"), inputs: inputs || {}, layer: "lite"}),
    });
    if (!res.ok) throw new Error(`Gryphon ${res.status}: ${(await res.text()).slice(0, 200)}`);
    const body = await res.json();
    const rows = body.rows || (body.results && body.results.rows) || [];
    return Array.isArray(rows) ? rows : [];
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

function _stampJobs(cy, facts, warn) {
    // Group jobs by workflow (the workflow the DEFINES_JOB edge points from,
    // falling back to the fetched workflow_id) so `needs` resolves within
    // the file it was written in.
    const jobsByWorkflow = new Map();
    cy.nodes(`[entity_type = "${T.job}"]`).forEach((job) => {
        const fact = facts.jobs.get(job.id()) || {};
        const definer = job.incomers(edgeSel(E.definesJob)).sources().first();
        const wfKey = definer.nonempty() ? definer.id() : `wf:${fact.workflow_id}`;
        if (!jobsByWorkflow.has(wfKey)) jobsByWorkflow.set(wfKey, []);
        jobsByWorkflow.get(wfKey).push({node: job, fact, wfNode: definer});

        // Matrix jobs carry `${{ matrix.x }}` in `name`; the key is stable.
        if (fact.job_key) {
            job.data("_job_name", job.data("label"));
            job.data("label", fact.job_key);
        }
    });

    jobsByWorkflow.forEach((entries, wfKey) => {
        const byKey = new Map(entries.filter((e) => e.fact.job_key).map((e) => [e.fact.job_key, e]));
        const wfFact = entries[0].wfNode && entries[0].wfNode.nonempty()
            ? (facts.workflows.get(entries[0].wfNode.id()) || {}) : {};
        const wfPerms = (wfFact.configuration || {}).permissions;

        // Producer state from EFFECTIVE permissions: job block, else workflow
        // block, `write-all` grants everything; neither declared → the
        // repository default applies and is not on the grid → not observable.
        entries.forEach(({node, fact}) => {
            const state = _producerState(fact.permissions, wfPerms);
            node.data("_producer", state);
            if (state === "producer") node.addClass("machinery-producer");
            else if (state === "not_observable") node.addClass("machinery-producer-not-observable");
        });

        // Rank = longest path over `needs`, resolved by job_key within the
        // workflow. Unresolved (absent dependency, or a cycle) propagates
        // downstream and is a state, never rank 0.
        const memo = new Map();   // job_key → number | "unresolved"
        const visiting = new Set();
        const rank = (key) => {
            if (memo.has(key)) return memo.get(key);
            const entry = byKey.get(key);
            if (!entry) return "unresolved";
            if (visiting.has(key)) return "unresolved";
            visiting.add(key);
            const needs = Array.isArray(entry.fact.needs) ? entry.fact.needs : [];
            let r = 0;
            for (const dep of needs) {
                const dr = rank(String(dep));
                if (dr === "unresolved") { r = "unresolved"; break; }
                r = Math.max(r, dr + 1);
            }
            visiting.delete(key);
            memo.set(key, r);
            return r;
        };
        entries.forEach(({node, fact}) => {
            const r = fact.job_key ? rank(fact.job_key) : "unresolved";
            if (Number.isInteger(r)) {
                node.data("_stage", r);
            } else {
                node.addClass("machinery-unresolved");
                const needs = Array.isArray(fact.needs) ? fact.needs : [];
                const missing = needs.filter((d) => !byKey.has(String(d)));
                warn("machinery_unresolved_rank",
                    `${fact.job_key || node.id()} in ${wfKey}: ` + (missing.length ? `needs absent job(s) ${missing.join(", ")}` : "dependency cycle or unkeyed job"));
            }
        });
    });
}

function _producerState(jobPerms, wfPerms) {
    const effective = _nonEmpty(jobPerms) ? jobPerms : (_nonEmpty(wfPerms) ? wfPerms : null);
    if (effective == null) return "not_observable";
    if (effective === "write-all") return "producer";
    if (typeof effective === "object") {
        return PRODUCER_SCOPES.some((scope) => effective[scope] === "write") ? "producer" : "not_producer";
    }
    return "not_producer";   // "read-all" or an unrecognised string
}

function _nonEmpty(v) {
    if (v == null) return false;
    if (typeof v === "string") return v.length > 0;
    return Object.keys(v).length > 0;
}

// ---------------------------------------------------------------------------
// Sources
// ---------------------------------------------------------------------------

function _planRefs(cy, facts, stackOver) {
    const plan = {deck: null};
    const byRepo = new Map();
    cy.nodes(`[entity_type = "${T.ref}"]`).forEach((ref) => {
        ref.data("_stage", STAGE.sources);
        const fact = facts.refs.get(ref.id()) || {};
        const isDefault = fact.is_default === true;
        const isTag = fact.ref_type === "tag";
        ref.data("_order", isDefault ? 0 : (isTag ? 1 : 2));
        const owner = ref.incomers(edgeSel(E.hasRef)).sources().first();
        const repoId = owner.nonempty() ? owner.id() : "";
        if (!byRepo.has(repoId)) byRepo.set(repoId, {kept: [], branches: []});
        if (isDefault || isTag) byRepo.get(repoId).kept.push(ref);
        else byRepo.get(repoId).branches.push(ref);
    });
    byRepo.forEach(({branches}, repoId) => {
        if (branches.length <= stackOver) return;
        branches.sort((a, b) => (a.data("label") || "").localeCompare(b.data("label") || ""));
        const representative = branches[0];
        branches.slice(1).forEach((ref) => ref.addClass(ELEVATION_HIDDEN_CLASS));
        representative.data("_order", 2);
        // v0: one deck per scene (the single-repository case); a later pass
        // generalises applyStack over every repository box.
        if (!plan.deck) plan.deck = {repoId, representative, members: branches};
    });
    return plan;
}

// ---------------------------------------------------------------------------
// Outputs, third parties, orphans
// ---------------------------------------------------------------------------

function _addPlaceholders(cy, repos) {
    const additions = [];
    repos.forEach((repo) => {
        UNCOLLECTED_OUTPUT_KINDS.forEach((kind, i) => {
            const id = `${T.placeholder}:${kind}:${repo.id()}`;
            if (cy.getElementById(id).nonempty()) return;
            additions.push({
                group: "nodes",
                data: {
                    id, label: `${kind}: not yet collected`, entity_type: T.placeholder,
                    icon_url: "", shape: "round-rectangle", _synthetic: true, _stage: STAGE.outputs, _order: 1 + i,
                    dimensions: {}, tags: {},
                },
            });
            additions.push({
                group: "edges",
                data: {id: `${SYN.hasPlaceholder}:${id}`, source: repo.id(), target: id, label: SYN.hasPlaceholder, edge_type: SYN.hasPlaceholder, _synthetic: true},
            });
        });
    });
    if (additions.length) cy.add(additions);
}

function _hostThirdParties(cy) {
    const platform = cy.nodes(`[entity_type = "${T.platform}"]`).first();
    if (platform.empty()) return;
    const additions = [];
    cy.nodes(`[entity_type = "${T.app}"], [entity_type = "${T.issuer}"], [entity_type = "${T.runner}"]`).forEach((n) => {
        const id = `${SYN.hostsThirdParty}:${n.id()}`;
        if (cy.getElementById(id).empty()) {
            additions.push({group: "edges", data: {id, source: platform.id(), target: n.id(), label: SYN.hostsThirdParty, edge_type: SYN.hostsThirdParty, _synthetic: true}});
        }
    });
    if (additions.length) cy.add(additions);
}

function _hideOrphans(cy, repos, warn) {
    const repoIds = new Set(repos.map((r) => r.id()));
    const hidden = [];
    const attachedTo = (node, edgeType, dir) => {
        const edges = node.connectedEdges(edgeSel(edgeType));
        return edges.some((e) => repoIds.has(dir === "in" ? e.source().id() : e.target().id()));
    };
    cy.nodes(`[entity_type = "${T.ruleset}"]`).forEach((n) => { if (!attachedTo(n, E.protects, "out")) hidden.push(n); });
    cy.nodes(`[entity_type = "${T.app}"]`).forEach((n) => { if (!attachedTo(n, E.enabledOn, "out")) hidden.push(n); });
    cy.nodes(`[entity_type = "${T.environment}"]`).forEach((n) => { if (!attachedTo(n, E.hasEnvironment, "in")) hidden.push(n); });
    cy.nodes(`[entity_type = "${T.workflow}"], [entity_type = "${T.ref}"]`).forEach((n) => {
        const et = n.data("entity_type") === T.workflow ? E.definesWorkflow : E.hasRef;
        if (!attachedTo(n, et, "in")) hidden.push(n);
    });
    hidden.forEach((n) => n.addClass(ELEVATION_HIDDEN_CLASS));
    if (hidden.length) warn("machinery_hidden_unowned", `${hidden.length} node(s) not attached to a repository in the scene were hidden`);
}

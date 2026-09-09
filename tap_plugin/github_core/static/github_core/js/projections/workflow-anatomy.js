/**
 * github_core workflow anatomy — ONE workflow drawn as a shape
 * (spec-github-core-workflow-page-v0.md, req-github-core-workflow-anatomy).
 *
 * The machinery projection draws a repository's CI system with each workflow as a box of job
 * cards. This projection goes one level in: the workflow is the whole canvas, its jobs are
 * ranked over `needs:` (a job sits one column after everything it needs; a job whose needs
 * cannot be resolved lands in a trailing unresolved column with a warning, never at rank 0),
 * every job lists its STEPS inside it — the action each step calls with its pin, or the first
 * words of its `run:` — and the ARTIFACT KINDS a job's upload step declares hang off that job as
 * piles counted from the runs in the collected window.
 *
 * Kinds are derived once, here, from the collected workflow file: a step whose `uses` is the
 * upload-artifact action declares a kind named by its `with.name`, which may be a template
 * (`digest-${{ matrix.image }}-${{ matrix.arch }}`). Observed artifacts are matched to declared
 * kinds by turning the template into a pattern. Three states, never two
 * (req-github-core-machinery-honesty):
 *
 *   - declared and observed   → a pile on the declaring job, the count in the chip;
 *   - declared, none observed → a dashed empty pile on the job ("none in the window");
 *   - observed, undeclared    → a pile on the workflow itself, marked, because the file does not
 *                               say who made it (a step uploading through a composite action, or
 *                               an action that uploads on its own).
 *
 * Nothing here names a repository, workflow or job. The workflow is whatever `github_workflow`
 * node the page's searches placed in the scene (with its jobs); the page pins which one.
 *
 * Standard tap layout module: `export async function execute(context)`
 * (spec-viz-layouts.md, req-viz-layout-module-contract).
 */

import {projectNested} from "/static/tap_viz/js/runtime/nested-projection.js";
import {applyStandardChrome, placeParentLabels, parentLabelInset} from "/static/tap_viz/js/runtime/chrome.js";
import {applyStack, settleStacks} from "/static/tap_viz/js/runtime/stack.js";

const GRYPHON_URL = "/api/v1/gryphon/execute";

const T = {
    workflow: "github_core__github_workflow",
    job: "github_core__workflow_job",
    step: "_anatomy_step",
    pile: "_anatomy_artifact_kind",
};
const E = {
    definesJob: "DEFINES_JOB__github_core",
    dependsOnJob: "DEPENDS_ON_JOB__github_core",
};
const SYN = {hasStep: "_ANATOMY_HAS_STEP", declares: "_ANATOMY_DECLARES_KIND", leaves: "_ANATOMY_LEAVES_KIND"};

const ARTIFACT_ICON = "/static/github_core/icons/actions-artifact.svg";
const UPLOAD_ACTION_RE = /(^|\/)upload-artifact(@|$)/;

const BASE_SIZES = {
    [T.workflow]: {width: 320, height: 90},
    [T.job]: {width: 200, height: 40},
    [T.step]: {width: 190, height: 22},
    [T.pile]: {width: 190, height: 34},
};
const DEFAULTS = {flow: "ltr", column_gap: 40, row_gap: 10};
const KNOWN_KEYS = new Set(Object.keys(DEFAULTS));

export async function execute(context) {
    const {cy, projection} = context;
    const warnings = [];
    const warn = (category, message) => {
        warnings.push({category, message});
        console.warn(`[workflow-anatomy] ${category}: ${message}`);
    };
    const cfg = _readConfig(projection, warn);
    const direction = cfg.flow === "rtl" ? "rtl" : "ltr";
    const workflows = cy.nodes(`[entity_type = "${T.workflow}"]`);
    if (workflows.empty()) {
        warn("anatomy_no_workflow", "the scene holds no github_workflow node; nothing to lay out");
        return {warnings};
    }
    _clear(cy);
    for (const wf of workflows) {
        const facts = await _fetchWorkflowFacts(wf, warn);
        if (!facts) continue;
        const jobs = _jobsOf(cy, wf);
        const jobFacts = new Map((await _fetchJobFacts(facts.full_name, warn)).map((r) => [String(r.entity_id), r]));
        _rankJobs(cy, wf, jobs, facts, jobFacts, warn);
        const declared = _addSteps(cy, wf, jobs, facts);
        const observed = await _fetchArtifacts(facts.full_name, facts.path, warn);
        _addPiles(cy, wf, jobs, declared, observed, facts, warn);
    }
    const chrome = applyStandardChrome(cy, {leafTypes: [T.job, T.step, T.pile], leafMaxWidth: 190});
    const labelInset = parentLabelInset(chrome);
    const result = await projectNested(cy, {
        relationships: [
            {name: "workflow-defines-job", gryphon: `(parent:${T.workflow})-[:${E.definesJob}]->(child:${T.job})`},
            {name: "job-has-step", gryphon: `(parent:${T.job})-[:${SYN.hasStep}]->(child:${T.step})`},
            {name: "job-declares-kind", gryphon: `(parent:${T.job})-[:${SYN.declares}]->(child:${T.pile})`},
            {name: "workflow-leaves-kind", gryphon: `(parent:${T.workflow})-[:${SYN.leaves}]->(child:${T.pile})`},
        ],
        baseSizes: BASE_SIZES,
        padding: 12,
        paddings: {
            [T.workflow]: {top: 18 + labelInset, right: 28, bottom: 28, left: 28},
            [T.job]: {top: 8 + labelInset, right: 12, bottom: 10, left: 12},
        },
        innerLayout: {name: "flow", aspect: 2.0, gap: 24, sort: "area-desc"},
        innerLayouts: {
            // Jobs by rank across, then whatever the workflow itself leaves behind in a final column.
            [T.workflow]: {name: "ranked", direction, columnGap: cfg.column_gap, rowGap: cfg.row_gap, sort: "order"},
            // Inside a job: the steps in order, then the declared piles, one per row.
            [T.job]: {name: "flow", aspect: 0.1, gap: 4, sort: "input"},
        },
        fit: true,
    });
    warnings.push(...(result.warnings || []));
    placeParentLabels(cy, {anchor: "upper-left", inset: 8, parentFontSize: chrome.parentFontSize, parentFontWeight: chrome.parentFontWeight});
    _stackPiles(cy);
    settleStacks(cy);
    _style(cy);
    return {warnings};
}

function _readConfig(projection, warn) {
    const raw = (projection && projection.definition && projection.definition.anatomy) || {};
    Object.keys(raw).forEach((k) => {
        if (!KNOWN_KEYS.has(k)) warn("anatomy_unknown_config", `ignoring unknown anatomy config key "${k}"`);
    });
    return {...DEFAULTS, ...raw};
}

// ---------------------------------------------------------------------------
// Facts — the configuration and the artifacts are not on the cy node data
// ---------------------------------------------------------------------------

async function _fetchWorkflowFacts(wf, warn) {
    try {
        const rows = await _gryphonRows(
            [
                `MATCH (w:${T.workflow})`,
                "WHERE w.entity_id = $id",
                "RETURN w.entity_id AS entity_id, w.data.full_name AS full_name, w.data.path AS path, w.data.name AS name, w.data.configuration AS configuration",
            ],
            {id: wf.id()},
        );
        const row = rows.find((r) => r && String(r.entity_id) === wf.id());
        if (!row) {
            warn("anatomy_facts_missing", `${wf.data("label")}: the workflow's configuration is not on the grid`);
            return null;
        }
        return {...row, configuration: row.configuration || {}};
    } catch (err) {
        warn("anatomy_facts", `${wf.data("label")}: ${err.message}`);
        return null;
    }
}

async function _fetchJobFacts(fullName, warn) {
    try {
        return await _gryphonRows(
            [
                `MATCH (j:${T.job})`,
                "WHERE j.data.full_name = $repo",
                "RETURN j.entity_id AS entity_id, j.data.job_key AS job_key, j.data.name AS name, j.data.needs AS needs",
            ],
            {repo: fullName},
        );
    } catch (err) {
        warn("anatomy_job_facts", `${fullName}: ${err.message}`);
        return [];
    }
}

async function _fetchArtifacts(fullName, path, warn) {
    try {
        return await _gryphonRows(
            [
                `MATCH (w:${T.workflow})<-[:EXECUTES_WORKFLOW__github_core]-(r:github_core__github_actions_run)-[:UPLOADS_ARTIFACT__github_core]->(a:github_core__actions_artifact)`,
                "WHERE w.data.full_name = $repo AND w.data.path = $path",
                "RETURN a.data.name AS name, a.data.expired AS expired, a.data.size_in_bytes AS size, r.data.run_id AS run_id",
            ],
            {repo: fullName, path},
        );
    } catch (err) {
        warn("anatomy_artifacts", `${fullName}: ${err.message}`);
        return [];
    }
}

async function _gryphonRows(queryLines, inputs) {
    const headers = {"Content-Type": "application/json"};
    const csrf = document.cookie.split(";").map((c) => c.trim()).find((c) => c.startsWith("csrftoken="));
    if (csrf) headers["X-CSRFToken"] = csrf.slice("csrftoken=".length);
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
// Jobs — rank over `needs:`, longest path; unknowns to a trailing column
// ---------------------------------------------------------------------------

function _jobsOf(cy, wf) {
    const ids = new Set(
        cy.edges().filter((e) => (e.data("edge_type") === E.definesJob || e.data("label") === E.definesJob) && e.source().id() === wf.id())
            .map((e) => e.target().id()),
    );
    return cy.nodes(`[entity_type = "${T.job}"]`).filter((j) => ids.has(j.id()));
}

function _rankJobs(cy, wf, jobs, facts, jobFacts, warn) {
    const byKey = new Map();
    const conf = facts.configuration || {};
    const declared = Array.isArray(conf.jobs) ? conf.jobs : [];
    const declaredByKey = new Map(declared.map((j) => [String(j.id || ""), j]));
    jobs.forEach((j) => {
        // Jobs display job_key, never name: a matrix job's name carries `${{ matrix.* }}` text.
        const f = jobFacts.get(j.id()) || {};
        const key = String(f.job_key || j.data("_job_key") || j.data("label") || "");
        byKey.set(key, j);
        j.data("_job_key", key);
        if (f.name && f.name !== key) j.data("_name", String(f.name));
        j.data("label", key);
    });
    const rank = new Map();
    const visiting = new Set();
    const rankOf = (key) => {
        if (rank.has(key)) return rank.get(key);
        if (visiting.has(key)) return null; // a cycle: GitHub would refuse the file; treat as unresolved
        visiting.add(key);
        const d = declaredByKey.get(key);
        const needs = _needsList(d && d.needs);
        let r = 0;
        for (const n of needs) {
            if (!byKey.has(n)) { visiting.delete(key); return null; }
            const nr = rankOf(n);
            if (nr === null) { visiting.delete(key); return null; }
            r = Math.max(r, nr + 1);
        }
        visiting.delete(key);
        rank.set(key, r);
        return r;
    };
    let maxRank = 0;
    jobs.forEach((j) => {
        const r = rankOf(j.data("_job_key"));
        if (r !== null) maxRank = Math.max(maxRank, r);
    });
    jobs.forEach((j) => {
        const key = j.data("_job_key");
        const r = rank.get(key);
        if (r === undefined || r === null) {
            j.data("_stage", maxRank + 1);
            j.data("_order", 999);
            j.addClass("anatomy-unresolved");
            warn("anatomy_unresolved_needs", `${key}: a need could not be resolved; placed in the unresolved column`);
        } else {
            j.data("_stage", r);
            j.data("_order", 0);
        }
        const d = declaredByKey.get(key);
        if (d && d.if) j.data("_if", String(d.if));
        if (d && d.uses) j.data("_uses", String(d.uses));
    });
    wf.data("_max_rank", maxRank);
}

function _needsList(needs) {
    if (Array.isArray(needs)) return needs.map(String);
    if (typeof needs === "string" && needs) return [needs];
    return [];
}

// ---------------------------------------------------------------------------
// Steps — synthesised from the collected configuration; the upload steps declare kinds
// ---------------------------------------------------------------------------

function _stepLabel(step) {
    if (step.uses) {
        const [ref, pin] = String(step.uses).split("@");
        const short = ref.replace(/^actions\//, "").replace(/^\.\//, "");
        return pin ? `${short}@${pin.slice(0, 7)}` : short;
    }
    if (step.run) return `run: ${String(step.run).trim().split("\n")[0].slice(0, 40)}`;
    return step.name ? String(step.name).slice(0, 40) : "step";
}

function _addSteps(cy, wf, jobs, facts) {
    const declared = []; // [{jobId, kind, template, stepIndex}]
    const conf = facts.configuration || {};
    const byKey = new Map(jobs.map((j) => [j.data("_job_key"), j]));
    (Array.isArray(conf.jobs) ? conf.jobs : []).forEach((d) => {
        const job = byKey.get(String(d.id || ""));
        if (!job) return;
        const steps = Array.isArray(d.steps) ? d.steps : [];
        if (d.uses) {
            // A job that IS a call to a reusable workflow has no steps of its own.
            const id = `${T.step}:${job.id()}:call`;
            cy.add({group: "nodes", data: {id, entity_type: T.step, label: `uses ${String(d.uses).replace(/^\.\/\.github\/workflows\//, "")}`, shape: "round-rectangle",
                                          fill_color: "#eef2ff", border_color: "#4c6ef5", label_color: "#1e2a78", _order: 0}, classes: "anatomy-step anatomy-step-call"});
            cy.add({group: "edges", data: {id: `${SYN.hasStep}:${id}`, source: job.id(), target: id, edge_type: SYN.hasStep}, classes: "anatomy-containment"});
        }
        steps.forEach((step, i) => {
            const id = `${T.step}:${job.id()}:${i}`;
            const uses = String(step.uses || "");
            const isUpload = UPLOAD_ACTION_RE.test(uses.split("@")[0]);
            cy.add({
                group: "nodes",
                data: {id, entity_type: T.step, label: _stepLabel(step), shape: "round-rectangle", _order: i, _index: i,
                       fill_color: isUpload ? "#fff4d6" : "#ffffff", border_color: isUpload ? "#a15c00" : "#c9cdd3", label_color: "#1b1d22",
                       _uses: uses, _run: step.run ? String(step.run).slice(0, 400) : "", _with: step.with ? JSON.stringify(step.with).slice(0, 400) : ""},
                classes: isUpload ? "anatomy-step anatomy-step-upload" : "anatomy-step",
            });
            cy.add({group: "edges", data: {id: `${SYN.hasStep}:${id}`, source: job.id(), target: id, edge_type: SYN.hasStep}, classes: "anatomy-containment"});
            if (isUpload) {
                const template = step.with && step.with.name ? String(step.with.name) : "artifact";
                declared.push({jobId: job.id(), template, stepIndex: i});
            }
        });
    });
    return declared;
}

// ---------------------------------------------------------------------------
// Piles — declared kinds counted from observed artifacts; three states
// ---------------------------------------------------------------------------

//: A `with.name` template → a matcher: `${{ … }}` spans match anything, the rest is literal, in
//: order, anchored at both ends. Plain string scanning — no regular expression is built from data.
function _templateMatcher(template) {
    const parts = String(template).split(/\$\{\{[^}]*\}\}/);
    return (name) => {
        const text = String(name || "");
        if (parts.length === 1) return text === parts[0];
        if (!text.startsWith(parts[0])) return false;
        let at = parts[0].length;
        for (let i = 1; i < parts.length - 1; i++) {
            const idx = text.indexOf(parts[i], at);
            if (idx < 0) return false;
            at = idx + parts[i].length;
        }
        const last = parts[parts.length - 1];
        return text.length >= at + last.length && text.endsWith(last);
    };
}

//: Fold the variable parts of an observed name so undeclared kinds group sensibly.
function _foldName(name) {
    return String(name || "").replace(/[0-9a-f]{7,}/gi, "*").replace(/[A-Z0-9]{4,}/g, "*").replace(/\d+/g, "#");
}

function _addPiles(cy, wf, jobs, declared, observed, facts, warn) {
    const kinds = declared.map((d) => ({...d, matches: _templateMatcher(d.template), rows: []}));
    const undeclared = new Map(); // folded name → rows
    for (const row of observed) {
        const name = String(row.name || "");
        const hit = kinds.find((k) => k.matches(name));
        if (hit) hit.rows.push(row);
        else {
            const f = _foldName(name);
            if (!undeclared.has(f)) undeclared.set(f, []);
            undeclared.get(f).push(row);
        }
    }
    const fullName = facts.full_name || "";
    const pileNode = (id, label, rows, extra, classes) => {
        const latest = rows.reduce((m, r) => Math.max(m, Number(r.run_id) || 0), 0);
        cy.add({
            group: "nodes",
            data: {id, entity_type: T.pile, label, shape: "round-rectangle", icon_url: ARTIFACT_ICON, _count: rows.length,
                   _expired: rows.filter((r) => r.expired).length, nav_url: latest ? `https://github.com/${fullName}/actions/runs/${latest}` : "", nav_external: !!latest,
                   fill_color: "#fffbea", border_color: "#a15c00", label_color: "#5a3d00", ...extra},
            classes,
        });
        return id;
    };
    kinds.forEach((k, i) => {
        const id = `${T.pile}:${k.jobId}:${i}`;
        if (k.rows.length) {
            pileNode(id, k.template, k.rows, {_order: 1000 + i, _state: "observed"}, "anatomy-pile");
            cy.getElementById(id).data("_members", k.rows.length);
        } else {
            pileNode(id, `${k.template} — none in the window`, [], {_order: 1000 + i, _state: "declared-only", fill_color: "#ffffff"}, "anatomy-pile anatomy-pile-empty");
        }
        cy.add({group: "edges", data: {id: `${SYN.declares}:${id}`, source: k.jobId, target: id, edge_type: SYN.declares}, classes: "anatomy-containment"});
    });
    let n = 0;
    for (const [folded, rows] of undeclared) {
        const id = `${T.pile}:${wf.id()}:undeclared:${n++}`;
        pileNode(id, `${folded} — no upload step declares it`, rows, {_stage: (Number(wf.data("_max_rank")) || 0) + 1, _order: 500 + n, _state: "undeclared"}, "anatomy-pile anatomy-pile-undeclared");
        cy.getElementById(id).data("_members", rows.length);
        cy.add({group: "edges", data: {id: `${SYN.leaves}:${id}`, source: wf.id(), target: id, edge_type: SYN.leaves}, classes: "anatomy-containment"});
        warn("anatomy_undeclared_artifact", `${folded}: ${rows.length} artifact(s) with no declaring upload step in the file`);
    }
}

function _stackPiles(cy) {
    // One pile per kind: the laid-out node is the face; the rest of the kind's artifacts are cards
    // added after the nesting pass (so the job box did not grow for them). The chip is the count.
    cy.nodes(".anatomy-pile[_members]").forEach((face) => {
        const count = Number(face.data("_members")) || 1;
        const parent = face.data("_viewport_parent");
        const members = [face];
        for (let i = 1; i < count; i++) {
            members.push(cy.add({
                group: "nodes",
                data: {id: `${face.id()}:card:${i}`, entity_type: T.pile, label: face.data("label"), shape: "round-rectangle", icon_url: ARTIFACT_ICON,
                       fill_color: "#fffbea", border_color: "#a15c00", label_color: "#5a3d00", _viewport_parent: parent, _card: true},
                position: {x: face.position("x"), y: face.position("y")},
                classes: "anatomy-pile anatomy-pile-card",
            }));
        }
        if (members.length >= 2) {
            applyStack(cy, {members: cy.collection(members), representative: face, label: face.data("label"), stackId: `stack:anatomy:${face.id()}`, direction: "down"});
        }
    });
}

// ---------------------------------------------------------------------------
// Style and re-entry
// ---------------------------------------------------------------------------

function _style(cy) {
    const labelled = {"label": "data(label)", "text-opacity": 1, "text-valign": "center", "text-halign": "center", "text-wrap": "ellipsis"};
    cy.style()
        .selector(".anatomy-step")
        .style({...labelled, "text-max-width": "180px", "font-size": "9px", "font-family": "ui-monospace, Menlo, monospace", "background-opacity": 1, "border-width": 1})
        .selector(".anatomy-step-upload")
        .style({"border-width": 1.5})
        .selector(".anatomy-step-call")
        .style({"font-style": "italic"})
        .selector(".anatomy-pile")
        .style({...labelled, "text-max-width": "170px", "font-size": "10px", "background-opacity": 1, "border-width": 1})
        .selector(".anatomy-pile-empty")
        .style({"border-style": "dashed", "color": "#8c959f", "font-style": "italic"})
        .selector(".anatomy-pile-undeclared")
        .style({"border-style": "dotted", "border-color": "#6f42c1", "color": "#4a2a9a"})
        .selector(".anatomy-unresolved")
        .style({"border-color": "#dc2626", "border-style": "dashed", "border-width": 2})
        .selector(".anatomy-containment")
        .style({"display": "none"})
        .selector(`edge[edge_type = "${E.dependsOnJob}"], edge[label = "${E.dependsOnJob}"]`)
        .style({"line-color": "#94a3b8", "target-arrow-color": "#94a3b8", "target-arrow-shape": "triangle", "width": 1.5, "curve-style": "bezier", "label": ""})
        .update();
}

function _clear(cy) {
    cy.remove(cy.edges(".anatomy-containment"));
    cy.remove(cy.nodes(".anatomy-step"));
    cy.remove(cy.nodes(".anatomy-pile"));
    cy.nodes(".anatomy-unresolved").removeClass("anatomy-unresolved");
}

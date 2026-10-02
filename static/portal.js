/* OpsPilot portal: role-based sidebar views (employee + admin).
   Talks to the API in src/api/routes/portal.py. The voice/chat code in index.html is untouched; it only calls
   Portal.init() after login and Portal.afterAgent() after the assistant answers. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (s) => {
    if (!s) return "–";
    const d = new Date(String(s).replace(" ", "T") + "Z");
    return isNaN(d) ? esc(s) : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  };
  const S = { token: "", role: "employee", uid: "", view: "assistant", arg: null, emps: null, pending: null, csvData: {}, tf: {}, lf: { days: "30" } };
  const STAGES = [["planning", "Planning"], ["in_progress", "In Progress"], ["review", "Review"], ["done", "Done"]];
  const NAV = {
    employee: [["assistant", "Assistant"], ["projects", "My Projects"], ["tickets", "My Tickets"], ["account", "My Account"]],
    admin: [["assistant", "Assistant"], ["overview", "Overview"], ["analytics", "Analytics"], ["employees", "Employees"], ["projects", "Projects"],
      ["tickets", "Tickets"], ["logs", "Activity Logs"], ["security", "Security"], ["account", "My Account"]],
  };
  const TITLES = { assistant: "Assistant", overview: "Admin Overview", analytics: "Analytics", employees: "Employees", employee: "Employee 360°",
    project: "Project Flow", logs: "Activity Logs", security: "Security & Accounts", account: "My Account" };
  const title = (v) => TITLES[v] || (v === "projects" ? (S.role === "admin" ? "Projects" : "My Projects") : S.role === "admin" ? "Tickets" : "My Tickets");

  // ------------------------------------------------------------------ helpers
  async function api(path, method = "GET", body) {
    const r = await fetch(path, {
      method, body: body ? JSON.stringify(body) : undefined,
      headers: { Authorization: "Bearer " + S.token, ...(body ? { "Content-Type": "application/json" } : {}) },
    });
    let data = null;
    try { data = await r.json(); } catch (e) { /* empty body */ }
    if (!r.ok) throw new Error((data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || r.statusText);
    return data;
  }
  const badge = (t, kind) => `<span class="b b-${kind || String(t).toLowerCase().replace(/[^a-z]+/g, "_")}">${esc(String(t).replace(/_/g, " "))}</span>`;
  const card = (t, body, attrs = "") => `<section class="card" ${attrs}><h3>${t}</h3>${body}</section>`;
  const kpi = (l, v, tone = "", go = "") => `<div class="kpi ${tone}"${go ? ` data-act="go" data-v="${go}"` : ""}><b>${esc(v)}</b><span>${esc(l)}</span></div>`;
  const prog = (n) => `<div class="pg"><i style="width:${n || 0}%"></i><b>${n || 0}%</b></div>`;
  const link = (v, arg, text) => `<a href="#" data-act="go" data-v="${v}" data-arg="${esc(arg)}">${text}</a>`;
  const table = (cols, data, empty = "Nothing here yet.") => data && data.length
    ? `<div class="tw"><table><thead><tr>${cols.map((c) => `<th>${esc(c[0])}</th>`).join("")}</tr></thead><tbody>${data.map((r) => `<tr>${cols.map((c) => `<td>${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
    : `<p class="muted">${empty}</p>`;
  const bars = (items, val = (i) => i.count, label = (i) => i.name) => {
    const max = Math.max(1, ...items.map(val));
    return items.length ? `<div class="bars">${items.map((i) => `<div class="br"><span>${esc(label(i))}</span><div><i style="width:${(100 * val(i)) / max}%"></i></div><b>${val(i)}</b></div>`).join("")}</div>` : '<p class="muted">No data.</p>';
  };
  const spark = (data, a, b) => {
    const max = Math.max(1, ...data.flatMap((d) => [d[a], d[b]]));
    return `<div class="spark">${data.map((d) => `<div title="${esc(d.day)}: ${d[a]} ${a}, ${d[b]} ${b}"><i class="a" style="height:${(80 * d[a]) / max}px"></i><i class="${b === "failed" ? "r" : "g"}" style="height:${(80 * d[b]) / max}px"></i><small>${d.day.slice(8)}</small></div>`).join("")}</div><p class="muted">Blue = ${a}, ${b === "failed" ? "red" : "green"} = ${b}</p>`;
  };
  const pipeline = (stage) => {
    const i = STAGES.findIndex((s) => s[0] === stage);
    return `<div class="pipe">${STAGES.map((s, k) => `<div class="st ${stage === "done" || k < i ? "past" : k === i ? "now" : ""}"><i>${stage === "done" || k < i ? "✓" : k + 1}</i>${s[1]}</div>${k < 3 ? "<em></em>" : ""}`).join("")}</div>`;
  };
  const selectHtml = (id, opts, cur) => `<select id="${id}">${opts.map((o) => `<option value="${esc(o[0])}"${o[0] === (cur || "") ? " selected" : ""}>${esc(o[1])}</option>`).join("")}</select>`;
  const empOpts = (list, sel = "", blank = "") => (blank ? `<option value="">${blank}</option>` : "") + list.map((e) => `<option value="${esc(e.employee_id)}"${e.employee_id === sel ? " selected" : ""}>${esc(e.name)} (${esc(e.employee_id)})</option>`).join("");
  const emps = async () => S.emps || (S.emps = await api("/admin/employees"));
  const qs = (o) => { const q = new URLSearchParams(Object.entries(o).filter(([, v]) => v)).toString(); return q ? "?" + q : ""; };

  function toast(msg, bad) {
    let t = $("#toast");
    if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
    t.textContent = msg; t.className = bad ? "bad show" : "show";
    clearTimeout(t._h); t._h = setTimeout(() => (t.className = ""), 4500);
  }
  function modal(html) {
    let m = $("#modal");
    if (!m) { m = document.createElement("div"); m.id = "modal"; document.body.appendChild(m); m.addEventListener("click", (e) => { if (e.target === m) m.style.display = "none"; }); }
    m.innerHTML = `<div class="mbox">${html}<p><button class="btn alt" data-act="close">Close</button></p></div>`; m.style.display = "flex";
  }

  // ------------------------------------------------------------------ shared tables
  const ticketCols = [
    ["ID", (t) => esc(t.ticket_id)], ["Title", (t) => esc(t.title)], ["Employee", (t) => link("employee", t.employee_id, esc(t.employee_name || t.employee_id))],
    ["Category", (t) => esc(t.category || "–")], ["Priority", (t) => badge(t.priority || "n/a")], ["Status", (t) => badge(t.status)],
    ["Assigned to", (t) => esc(t.assigned_name || "Unassigned")], ["Created", (t) => fmt(t.created_at)],
  ];
  const myTicketCols = ticketCols.filter((c) => c[0] !== "Employee");
  const auditCols = [
    ["When", (r) => fmt(r.created_at)],
    ["Who did it", (r) => `${esc(r.actor_name)} <span class="muted">${esc(r.actor_role || "")}</span>`],
    ["Action", (r) => `<b>${esc(r.action)}</b>`],
    ["To whom", (r) => esc(r.target_name ? `${r.target_name} (${r.employee_id})` : r.employee_id)],
    ["Why", (r) => esc(r.reason || "–")], ["Detail", (r) => esc(r.detail || "")],
    ["Result", (r) => badge(r.status)], ["Via", (r) => esc(r.source)],
  ];
  const projectCols = [
    ["ID", (p) => esc(p.project_id)], ["Project", (p) => link("project", p.project_id, `<b>${esc(p.name)}</b>`)],
    ["Priority", (p) => badge(p.priority)], ["Stage", (p) => badge(p.stage)], ["Progress", (p) => prog(p.progress_pct)],
    ["Team", (p) => p.team.map((m) => `<span class="chip s-${m.state}" title="${esc(m.role_in_project)} · ${m.pct}%">${esc((m.name || m.employee_id).split(" ")[0])}</span>`).join("") || "–"],
    ["Due", (p) => esc(p.due_date || "–")], ["", (p) => (p.blocked_count ? badge("blocked " + p.blocked_count, "blocked") : "")],
  ];

  // ------------------------------------------------------------------ project flow (shared)
  function flowHTML(d, admin) {
    const team = d.members.map((m) => `<div class="mc s-${m.state}"><div class="row"><b>${esc(m.name || m.employee_id)}</b><span class="sp"></span>${badge(m.state)}</div>
      <div class="muted">${esc(m.role_in_project || "Member")} · ${esc(m.job_title || "")}</div>${prog(m.pct)}
      <p class="small">${esc(m.last_message || "No update yet")}<br><span class="muted">${fmt(m.last_at)}</span></p>
      ${admin ? `<button class="btn alt sm" data-act="unassign" data-id="${esc(m.employee_id)}">Remove</button>` : ""}</div>`).join("");
    const tl = d.timeline.map((t) => `<li><b>${esc(t.employee_name || "system")}</b> ${badge(t.update_type)} ${esc(t.message || "")}<time>${fmt(t.created_at)}</time></li>`).join("");
    return `${pipeline(d.stage)}${prog(d.progress_pct)}<h4>Team: who is doing what</h4><div class="mcs">${team || '<p class="muted">Nobody assigned yet.</p>'}</div>
      <h4>Activity timeline</h4><ul class="tl">${tl || '<li class="muted">No activity yet.</li>'}</ul>
      <h4>Text flow chart</h4><pre class="flowpre">${esc(d.flowchart)}</pre>`;
  }
  const updateForm = () => `<div class="form"><select name="t"><option value="progress">I'm working on it</option><option value="blocked">I'm blocked</option><option value="done">I'm done with my part</option><option value="note">Add a note</option></select>
    <input name="m" placeholder="What happened? (needed for blocked / note)"><input name="p" type="number" min="0" max="100" placeholder="% done"><button class="btn" data-act="postUpdate">Send update</button></div>`;

  // ------------------------------------------------------------------ views
  const V = {};
  V.overview = async (el) => {
    const [o, pr, tk] = await Promise.all([api("/admin/overview"), api("/admin/projects?status=active"), api("/admin/tickets?status=open&priority=Critical")]);
    el.innerHTML = `<div class="grid">${kpi("Employees", o.employees, "", "employees")}${kpi("Open tickets", o.open_tickets, "", "tickets")}${kpi("Critical open", o.critical_open, o.critical_open ? "bad" : "")}
      ${kpi("Unassigned open", o.unassigned_open, o.unassigned_open ? "warn" : "")}${kpi("Active projects", o.active_projects, "", "projects")}${kpi("Overdue projects", o.overdue_projects, o.overdue_projects ? "bad" : "")}
      ${kpi("People blocked", o.blocked_people, o.blocked_people ? "warn" : "")}${kpi("Accounts locked", o.accounts_locked, o.accounts_locked ? "warn" : "", "security")}
      ${kpi("Failed logins (24h)", o.failed_logins_24h)}${kpi("Ops actions (24h)", o.ops_actions_24h, "", "logs")}</div>
      <div class="cols">${card("Projects by stage", bars(o.projects_by_stage))}${card("Projects needing attention", table(projectCols, pr.filter((p) => p.blocked_count), "No blocked work right now."))}</div>
      ${card("Critical open tickets", table(ticketCols, tk, "No critical tickets open."))}`;
  };

  V.analytics = async (el) => {
    const a = await api("/admin/analytics"), t = a.tickets, p = a.projects, s = a.security;
    el.innerHTML = `<div class="grid">${kpi("Tickets (all time)", t.total)}${kpi("Avg resolution (hours)", t.avg_resolution_hours ?? "–")}${kpi("Avg project progress", p.avg_progress + "%")}${kpi("Overdue projects", p.overdue.length, p.overdue.length ? "bad" : "")}</div>
      <div class="cols">${card("Tickets by status", bars(t.by_status))}${card("Tickets by priority", bars(t.by_priority))}${card("Tickets by category", bars(t.by_category))}${card("Top requesters", bars(t.top_requesters))}</div>
      ${card("Tickets created vs resolved (14 days)", spark(t.per_day, "created", "resolved"))}
      <div class="cols">${card("Tickets by department", table([["Department", (r) => esc(r.name)], ["Tickets", (r) => r.tickets], ["Open", (r) => r.open]], t.by_department))}
      ${card("Avg resolution time by category", table([["Category", (r) => esc(r.name)], ["Avg hours", (r) => r.avg_hours], ["Resolved", (r) => r.resolved]], t.resolution_by_category))}</div>
      ${card("Oldest open tickets", table([["ID", (r) => esc(r.ticket_id)], ["Title", (r) => esc(r.title)], ["Requester", (r) => esc(r.requester || r.employee_id)], ["Priority", (r) => badge(r.priority)], ["Opened", (r) => fmt(r.created_at)]], t.oldest_open))}
      <div class="cols">${card("Projects by stage", bars(p.by_stage))}${card("Projects by priority", bars(p.by_priority))}</div>
      ${card("Overdue projects", table([["Project", (r) => link("project", r.project_id, esc(r.name))], ["Due", (r) => esc(r.due_date)], ["Progress", (r) => prog(r.progress_pct)]], p.overdue, "Nothing is overdue."))}
      ${card("Workload per employee", table([["Employee", (r) => link("employee", r.employee_id, esc(r.name))], ["Department", (r) => esc(r.department || "–")], ["Projects", (r) => r.projects], ["Open tickets raised", (r) => r.open_tickets], ["Tickets assigned", (r) => r.assigned_tickets]], a.workload))}
      ${card("Logins per day (14 days)", spark(s.logins_per_day, "success", "failed"))}
      <div class="cols">${card("Top failed-login usernames", bars(s.top_failed))}${card("Lock events (30 days)", bars(s.lock_events_30d))}${card("What Ops did (30 days)", bars(a.ops_actions.by_action))}${card("Where requests came from", bars(a.ops_actions.by_source))}</div>`;
  };

  V.employees = async (el) => {
    const list = (S.emps = await api("/admin/employees")); S.csvData.employees = list;
    const draw = (q) => table([
      ["ID", (e) => esc(e.employee_id)], ["Name", (e) => link("employee", e.employee_id, `<b>${esc(e.name)}</b>`)], ["Department", (e) => esc(e.department)],
      ["Title", (e) => esc(e.job_title || "–")], ["Open tickets", (e) => e.open_tickets], ["Projects", (e) => e.projects], ["Account", (e) => badge(e.account_state)],
      ["Last login", (e) => fmt(e.last_login_at)], ["Last activity", (e) => fmt(e.last_activity)], ["", (e) => link("employee", e.employee_id, "Open 360° →")],
    ], list.filter((e) => !q || `${e.name} ${e.department} ${e.employee_id} ${e.job_title || ""}`.toLowerCase().includes(q)));
    el.innerHTML = card("Employees", `<div class="row"><input id="emp-q" placeholder="Search name, department, ID…"><button class="btn alt" data-act="csv" data-k="employees">⬇ CSV</button></div><div id="emp-t">${draw("")}</div>`);
    $("#emp-q").oninput = (e) => ($("#emp-t").innerHTML = draw(e.target.value.toLowerCase()));
  };

  V.employee = async (el, id) => {
    const d = await api("/admin/employees/" + encodeURIComponent(id)), p = d.profile, a = d.account || {}, st = d.stats;
    const btn = !a.employee_id ? "" : a.state !== "active" ? `<button class="btn" data-act="unlock" data-id="${esc(id)}">Unlock account</button>` : `<button class="btn danger" data-act="lock" data-id="${esc(id)}">Lock account</button>`;
    const kv = (k, v) => `<div><span>${k}</span>${v}</div>`;
    el.innerHTML = `<p><button class="btn alt" data-act="go" data-v="employees">← All employees</button></p>
      ${card(`${esc(p.name)} <small class="muted">${esc(p.employee_id)}</small>`, `<div class="kv">${kv("Title", esc(p.job_title || "–"))}${kv("Department", esc(p.department))}${kv("Manager", esc(p.manager_name || "–"))}${kv("Email", esc(p.email))}${kv("Location", esc(p.location || "–"))}${kv("Hired", esc(p.hire_date || "–"))}${kv("Login account", a.state ? badge(a.state) : "none")}${kv("Last login", fmt(a.last_login_at))}</div><div class="row">${btn}</div>`)}
      <div class="grid">${kpi("Open tickets", st.open_tickets)}${kpi("Total tickets", st.total_tickets)}${kpi("Assigned to them (open)", st.assigned_open)}${kpi("Failed logins (14d)", st.failed_logins_14d, st.failed_logins_14d > 2 ? "warn" : "")}${kpi("Projects", d.projects.length)}</div>
      ${card("Assigned projects", table([["Project", (x) => link("project", x.project_id, `<b>${esc(x.name)}</b>`)], ["Role", (x) => esc(x.my_role || "–")], ["Stage", (x) => badge(x.stage)], ["Their status", (x) => badge(x.my_state || "not_started")], ["Their progress", (x) => prog(x.my_pct)], ["Due", (x) => esc(x.due_date || "–")]], d.projects, "Not assigned to any project."))}
      ${card("Tickets they raised", table(myTicketCols, d.tickets_raised, "No tickets."))}${card("Tickets assigned to them to fix", table(myTicketCols, d.tickets_assigned, "None."))}
      ${card("What OpsPilot did to / for this employee, and why", table(auditCols, d.ops_actions_on_employee, "Nothing logged yet."))}
      ${card("What this employee asked OpsPilot to do", table(auditCols, d.actions_requested_by_employee, "Nothing logged yet."))}
      ${card("Project updates they posted", table([["When", (x) => fmt(x.created_at)], ["Project", (x) => esc(x.project_id)], ["Type", (x) => badge(x.update_type)], ["Message", (x) => esc(x.message || "")], ["%", (x) => x.progress_pct ?? "–"]], d.project_updates, "None yet."))}
      <div class="cols">${card("Login history", table([["When", (x) => fmt(x.created_at)], ["Result", (x) => badge(x.success ? "success" : "failed")], ["Reason", (x) => esc(x.reason)], ["IP", (x) => esc(x.ip || "–")]], d.logins, "No logins."))}
      ${card("Lock history", table([["When", (x) => fmt(x.created_at)], ["Action", (x) => badge(x.action)], ["Type", (x) => esc(x.lock_type || "")], ["By", (x) => esc(x.performed_by || "")], ["Reason", (x) => esc(x.reason || "")]], d.lock_events, "Never locked."))}</div>`;
  };

  async function adminProjects(el) {
    const [list, E] = await Promise.all([api("/admin/projects"), emps()]);
    el.innerHTML = card("New project", `<div class="form"><input id="np-name" placeholder="Project name"><input id="np-desc" placeholder="Description">
      ${selectHtml("np-prio", [["low", "Low"], ["medium", "Medium"], ["high", "High"], ["critical", "Critical"]], "medium")}<input id="np-due" type="date">
      <select id="np-team" multiple size="4" title="Hold Ctrl to pick several">${empOpts(E)}</select><button class="btn" data-act="createProject">Create &amp; assign</button></div>`)
      + card(`All projects (${list.length})`, table(projectCols, list));
  }
  async function myProjects(el) {
    const list = await api("/me/projects");
    el.innerHTML = list.length ? list.map((p) => card(`${esc(p.name)} ${badge(p.priority)} ${badge(p.stage)}`,
      `<p class="muted">${esc(p.description || "")} · Due ${esc(p.due_date || "not set")}</p>${pipeline(p.stage)}${prog(p.progress_pct)}
       <p>My role: <b>${esc(p.my_role || "–")}</b> · My status: ${badge(p.my_state || "not_started")} (${p.my_pct ?? 0}%)</p>${updateForm()}
       <p><button class="btn alt" data-act="go" data-v="project" data-arg="${esc(p.project_id)}">Open flow &amp; team</button></p>`, `data-pid="${esc(p.project_id)}"`)).join("")
      : card("My projects", '<p class="muted">You are not assigned to any project yet. Your admin will assign you one.</p>');
  }
  V.projects = (el) => (S.role === "admin" ? adminProjects(el) : myProjects(el));

  V.project = async (el, id) => {
    const admin = S.role === "admin", d = await api("/projects/" + encodeURIComponent(id));
    let ctl = "";
    if (admin) {
      const E = await emps(), on = new Set(d.members.map((m) => m.employee_id));
      ctl = `<h4>Admin controls</h4><div class="row">Stage: ${STAGES.map((s) => `<button class="btn ${d.stage === s[0] ? "" : "alt"} sm" data-act="stage" data-s="${s[0]}">${s[1]}</button>`).join("")}</div>
        <div class="form"><select id="as-emp">${empOpts(E.filter((e) => !on.has(e.employee_id) && !e.account_locked), "", "Assign someone…")}</select><input id="as-role" placeholder="Role (e.g. QA)"><button class="btn" data-act="assign">Assign to project</button></div>`;
    }
    const mine = d.members.some((m) => m.employee_id === S.uid);
    el.innerHTML = `<p><button class="btn alt" data-act="go" data-v="projects">← ${admin ? "All projects" : "My projects"}</button></p>
      ${card(`${esc(d.name)} <small class="muted">${esc(d.project_id)}</small> ${badge(d.priority)} ${badge(d.status)}`,
        `<p class="muted">${esc(d.description || "")}<br>Due ${esc(d.due_date || "not set")} · created by ${esc(d.created_by_name || d.created_by || "–")}</p>${flowHTML(d, admin)}${ctl}`, `data-pid="${esc(d.project_id)}"`)}
      ${mine ? card("Tell your admin what happened", updateForm(), `data-pid="${esc(d.project_id)}"`) : ""}`;
  };

  async function adminTickets(el) {
    const f = S.tf, [list, E] = await Promise.all([api("/admin/tickets" + qs(f)), emps()]); S.csvData.tickets = list;
    const sel = (cls, opts, cur) => `<select class="${cls}">${opts.map((o) => `<option${o === cur ? " selected" : ""}>${o}</option>`).join("")}</select>`;
    el.innerHTML = card("Filters", `<div class="row">${selectHtml("tf-status", [["", "Any status"], ["open", "Open"], ["in_progress", "In progress"], ["resolved", "Resolved"], ["closed", "Closed"]], f.status)}
      ${selectHtml("tf-priority", [["", "Any priority"], ["Critical", "Critical"], ["High", "High"], ["Medium", "Medium"], ["Low", "Low"]], f.priority)}
      ${selectHtml("tf-category", [["", "Any category"], ...["Access", "Network", "Hardware", "Software", "Account", "Application", "Email"].map((c) => [c, c])], f.category)}
      <select id="tf-employee">${empOpts(E, f.employee_id, "Any employee")}</select><button class="btn" data-act="tfApply">Apply</button><button class="btn alt" data-act="csv" data-k="tickets">⬇ CSV</button><span class="sp"></span><span class="muted">${list.length} ticket(s)</span></div>`)
      + card("Tickets", table([...ticketCols.slice(0, 5), ["Status", (t) => sel("t-status", ["open", "in_progress", "resolved", "closed"], t.status)],
        ["Assign", (t) => `<select class="t-assign">${empOpts(E, t.assigned_to, "Unassigned")}</select>`],
        ["Priority", (t) => sel("t-prio", ["Low", "Medium", "High", "Critical"], t.priority)],
        ["Note", () => '<input class="t-note" placeholder="optional note">'],
        ["", (t) => `<button class="btn sm" data-act="tSave" data-id="${esc(t.ticket_id)}">Save</button> <button class="btn alt sm" data-act="tHist" data-id="${esc(t.ticket_id)}">History</button>`]], list));
  }
  V.tickets = async (el) => {
    if (S.role === "admin") return adminTickets(el);
    const list = await api("/me/tickets");
    el.innerHTML = card(`My tickets (${list.length})`, table([...myTicketCols, ["Updated", (t) => fmt(t.updated_at)]], list, "You have no tickets. Ask OpsPilot to create one."));
  };

  V.logs = async (el) => {
    const f = S.lf, [E, acts] = await Promise.all([emps(), api("/admin/logs/actions")]), list = await api("/admin/logs" + qs(f)); S.csvData.logs = list;
    el.innerHTML = card("Filters", `<div class="row"><select id="lf-emp">${empOpts(E, f.employee_id, "Any employee")}</select>
      ${selectHtml("lf-act", [["", "Any action"], ...acts.map((a) => [a, a])], f.action)}${selectHtml("lf-st", [["", "Any result"], ["success", "Success"], ["denied", "Denied"]], f.status)}
      ${selectHtml("lf-src", [["", "Any source"], ["agent", "Assistant"], ["portal", "Portal"], ["login", "Login"]], f.source)}
      ${selectHtml("lf-days", [["1", "Last 24h"], ["7", "Last 7 days"], ["30", "Last 30 days"], ["", "All time"]], f.days)}
      <button class="btn" data-act="lApply">Apply</button><button class="btn alt" data-act="csv" data-k="logs">⬇ CSV</button><span class="sp"></span><span class="muted">${list.length} entries (latest 200)</span></div>`)
      + card("What Ops did, to whom, and why", table(auditCols, list, "No activity matches these filters."));
  };

  V.security = async (el) => {
    const [acc, ev] = await Promise.all([api("/admin/accounts"), api("/admin/login-events?only_failed=true&limit=60")]);
    el.innerHTML = card("Login accounts", table([
      ["Employee", (a) => link("employee", a.employee_id, `<b>${esc(a.name || a.username)}</b> <span class="muted">${esc(a.employee_id)}</span>`)], ["Username", (a) => esc(a.username)], ["Role", (a) => esc(a.role)],
      ["State", (a) => badge(a.state)], ["Failed", (a) => a.failed_attempts], ["Lockouts", (a) => a.lock_count], ["Locked until", (a) => (a.temp_locked ? fmt(a.locked_until) : "–")],
      ["Reason", (a) => esc(a.lock_reason || "")], ["Last login", (a) => fmt(a.last_login_at)], ["Password changed", (a) => fmt(a.password_changed_at)],
      ["", (a) => (a.state === "active" ? `<button class="btn danger sm" data-act="lock" data-id="${esc(a.employee_id)}">Lock</button>` : `<button class="btn sm" data-act="unlock" data-id="${esc(a.employee_id)}">Unlock</button>`)],
    ], acc)) + card("Recent failed logins", table([["When", (e) => fmt(e.created_at)], ["Username", (e) => esc(e.username)], ["Employee", (e) => esc(e.name || "–")], ["Reason", (e) => esc(e.reason)], ["IP", (e) => esc(e.ip || "–")]], ev, "No failed logins."));
  };

  V.account = async (el) => {
    const o = await api("/me/overview"), p = o.profile; S.pending = o.pending_password_reset; banner();
    el.innerHTML = card("My profile", `<div class="kv"><div><span>Name</span>${esc(p.name)}</div><div><span>Employee ID</span>${esc(p.employee_id)}</div><div><span>Department</span>${esc(p.department || "–")}</div><div><span>Title</span>${esc(p.job_title || "–")}</div><div><span>Email</span>${esc(p.email || "–")}</div></div>`)
      + card("Set new password", S.pending
        ? `<p>✅ Your password reset was approved. Choose a new password (valid until ${fmt(S.pending.expires_at)}).</p><div class="form pw"><input id="pw1" type="password" placeholder="New password" autocomplete="new-password"><input id="pw2" type="password" placeholder="Confirm new password" autocomplete="new-password"><button class="btn" data-act="setPw">Save password</button></div><p class="muted small">At least 8 characters, with a letter and a number. Your password is stored encrypted (hashed), and it never goes through chat or voice.</p>`
        : '<p class="muted">There is no approved reset waiting. To change your password, tell OpsPilot "reset my password" by voice or chat and approve the request. A box to type the new password will then appear here and in a banner at the top.</p>');
  };

  // ------------------------------------------------------------------ actions (event delegation)
  const ACT = {
    go: (t) => show(t.dataset.v, t.dataset.arg),
    close: () => ($("#modal").style.display = "none"),
    logout: () => location.reload(),
    csv: (t) => {
      const rows = S.csvData[t.dataset.k] || []; if (!rows.length) return toast("Nothing to export.", true);
      const keys = Object.keys(rows[0]).filter((k) => typeof rows[0][k] !== "object" || rows[0][k] === null), q = (v) => '"' + String(v ?? "").replace(/"/g, '""') + '"';
      const blob = new Blob([[keys.map(q).join(","), ...rows.map((r) => keys.map((k) => q(r[k])).join(","))].join("\n")], { type: "text/csv" });
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `opspilot-${t.dataset.k}.csv` }); a.click();
    },
    createProject: async () => {
      const body = { name: $("#np-name").value, description: $("#np-desc").value, priority: $("#np-prio").value, due_date: $("#np-due").value || null, assignees: $$("#np-team option:checked").map((o) => o.value) };
      try { const p = await api("/admin/projects", "POST", body); toast(`Project ${p.project_id} created and assigned.`); badges(); show("project", p.project_id); } catch (e) { toast(e.message, true); }
    },
    assign: async () => {
      const id = $("#as-emp").value; if (!id) return toast("Pick an employee first.", true);
      try { await api(`/admin/projects/${S.arg}/assign`, "POST", { employee_id: id, role_in_project: $("#as-role").value || "Member" }); toast("Assigned. It now shows under that employee."); show("project", S.arg); } catch (e) { toast(e.message, true); }
    },
    unassign: async (t) => { if (!confirm("Remove this person from the project?")) return; try { await api(`/admin/projects/${S.arg}/unassign`, "POST", { employee_id: t.dataset.id }); show("project", S.arg); } catch (e) { toast(e.message, true); } },
    stage: async (t) => { try { await api(`/admin/projects/${S.arg}/stage`, "POST", { stage: t.dataset.s }); show("project", S.arg); } catch (e) { toast(e.message, true); } },
    postUpdate: async (t) => {
      const c = t.closest("[data-pid]"), body = { update_type: $("[name=t]", c).value, message: $("[name=m]", c).value }, pct = $("[name=p]", c).value;
      if (pct !== "") body.progress_pct = +pct;
      try { await api(`/projects/${c.dataset.pid}/updates`, "POST", body); toast("Update sent. Your admin's flow chart is updated."); badges(); show(S.view, S.arg); } catch (e) { toast(e.message, true); }
    },
    tfApply: () => { S.tf = { status: $("#tf-status").value, priority: $("#tf-priority").value, category: $("#tf-category").value, employee_id: $("#tf-employee").value }; show("tickets"); },
    lApply: () => { S.lf = { employee_id: $("#lf-emp").value, action: $("#lf-act").value, status: $("#lf-st").value, source: $("#lf-src").value, days: $("#lf-days").value }; show("logs"); },
    tSave: async (t) => {
      const r = t.closest("tr"), body = { status: $(".t-status", r).value, priority: $(".t-prio", r).value, note: $(".t-note", r).value };
      if ($(".t-assign", r).value) body.assigned_to = $(".t-assign", r).value;
      try { await api("/admin/tickets/" + t.dataset.id, "PATCH", body); toast(`Ticket ${t.dataset.id} updated and logged.`); badges(); show("tickets", null, true); } catch (e) { toast(e.message, true); }
    },
    tHist: async (t) => {
      try {
        const ev = await api(`/admin/tickets/${t.dataset.id}/history`);
        modal(`<h3>Ticket ${esc(t.dataset.id)} history</h3>${table([["When", (e) => fmt(e.created_at)], ["Event", (e) => badge(e.event_type)], ["From", (e) => esc(e.from_value || "–")], ["To", (e) => esc(e.to_value || "–")], ["By", (e) => esc(e.performed_by_name || e.performed_by || "–")], ["Note", (e) => esc(e.note || "")]], ev)}`);
      } catch (e) { toast(e.message, true); }
    },
    lock: async (t) => {
      const reason = prompt("Why are you locking this account? (required, it is logged)"); if (!reason) return;
      const m = prompt("Lock for how many minutes? Leave empty to lock until you unlock it.");
      try { await api(`/admin/accounts/${t.dataset.id}/lock`, "POST", { reason, minutes: m ? +m : null }); toast("Account locked."); badges(); show(S.view, S.arg); } catch (e) { toast(e.message, true); }
    },
    unlock: async (t) => {
      const reason = prompt("Reason for unlocking (optional, it is logged)"); if (reason === null) return;
      try { await api(`/admin/accounts/${t.dataset.id}/unlock`, "POST", { reason }); toast("Account unlocked."); badges(); show(S.view, S.arg); } catch (e) { toast(e.message, true); }
    },
    setPw: async () => {
      const a = $("#pw1").value, b = $("#pw2").value;
      if (a !== b) return toast("The two passwords do not match.", true);
      try { await api("/me/password/set", "POST", { new_password: a }); toast("Password updated. Use it next time you log in."); S.pending = null; banner(); badges(); show("account"); } catch (e) { toast(e.message, true); }
    },
  };
  document.addEventListener("click", (e) => { const t = e.target.closest("[data-act]"); if (t && ACT[t.dataset.act]) { e.preventDefault(); ACT[t.dataset.act](t, e); } });

  // ------------------------------------------------------------------ shell: nav, badges, banner
  async function show(view, arg, quiet) {
    S.view = view; S.arg = arg;
    const navOf = { employee: "employees", project: "projects" }[view] || view;
    $$(".nav a").forEach((a) => a.classList.toggle("on", a.dataset.view === navOf));
    $(".main").classList.toggle("portal-mode", view !== "assistant");
    $(".topbar h1").textContent = title(view);
    if (view === "assistant") return;
    const el = $("#portal-view");
    if (!quiet) el.innerHTML = '<p class="muted">Loading…</p>';
    try { await V[view](el, arg); } catch (e) { el.innerHTML = card("Could not load this page", `<p class="muted">${esc(e.message)}</p>`); }
  }
  const setNb = (v, n, warn) => { const e = $("#nb-" + v); if (e) { e.textContent = n || ""; e.className = "nb" + (warn ? " warn" : ""); } };
  function banner() {
    let b = $("#pw-banner");
    if (!b) { b = document.createElement("div"); b.id = "pw-banner"; b.innerHTML = '🔑 Your password reset was approved. <button class="btn sm" data-act="go" data-v="account">Set new password</button>'; $(".topbar").after(b); }
    b.style.display = S.pending && S.view !== "account" ? "flex" : "none"; setNb("account", S.pending ? "!" : "", true);
  }
  async function pw() { try { const s = await api("/me/password/status"); S.pending = s.pending ? s : null; banner(); } catch (e) { /* not logged in yet */ } }
  async function badges() {
    try {
      if (S.role === "admin") { const o = await api("/admin/overview"); setNb("tickets", o.open_tickets); setNb("projects", o.blocked_people, true); setNb("security", o.accounts_locked, true); }
      else { const o = await api("/me/overview"); setNb("projects", o.stats.projects); setNb("tickets", o.stats.open_tickets); }
    } catch (e) { /* badges are optional */ }
  }
  function afterAgent() {
    pw(); badges();
    const f = document.activeElement;
    if (S.view !== "assistant" && !($("#portal-view").contains(f) && /INPUT|SELECT|TEXTAREA/.test(f.tagName))) show(S.view, S.arg, true);
  }
  async function init(token, role, uid) {
    Object.assign(S, { token, role, uid, emps: null });
    const nav = $(".nav"); nav.innerHTML = NAV[role === "admin" ? "admin" : "employee"].map(([v, l]) => `<a href="#" data-view="${v}"><i></i>${l}<span class="nb" id="nb-${v}"></span></a>`).join("");
    nav.onclick = (e) => { const a = e.target.closest("a[data-view]"); if (a) { e.preventDefault(); show(a.dataset.view); } };
    const av = $(".topbar .avatar"); if (av) av.outerHTML = `<div class="userchip"><span id="uname">${esc(uid)}</span>${badge(role)}<button class="btn alt sm" data-act="logout">Log out</button></div>`;
    api("/me/overview").then((o) => { const u = $("#uname"); if (u && o.profile && o.profile.name) u.textContent = o.profile.name; }).catch(() => {});
    show("assistant"); badges(); pw(); setInterval(pw, 20000);
  }
  window.Portal = { init, afterAgent, show };
})();

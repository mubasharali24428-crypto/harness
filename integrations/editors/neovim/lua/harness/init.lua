-- Thin Harness client for Neovim >= 0.10 (uses vim.system). Talks only to the configured
-- services; never merges, never executes tools, never writes the token to disk or logs.
local core = require("harness.core")
local M = { _token = nil, config = {
  identity_url = "http://localhost:8001",
  workflow_url = "http://localhost:8014",
  orchestrator_url = "http://localhost:8004",
}, _project = nil }

function M.setup(opts) M.config = vim.tbl_extend("force", M.config, opts or {}) end

local function notify(msg, level) vim.notify("Harness: " .. core.redact(msg, M._token), level or vim.log.levels.INFO) end

local function call(svc_url, method, path, body, cb, token_override)
  local token = token_override or M._token
  local base, err = core.safe_base(svc_url)
  if not base then return notify(err, vim.log.levels.ERROR) end
  if not token then return notify("Sign in first (:HarnessSignIn)", vim.log.levels.ERROR) end
  local cfg = core.curl_config(method, base .. path, token, body and vim.json.encode(body) or nil)
  vim.system({ "curl", "--config", "-" }, { stdin = cfg, text = true }, function(res)
    vim.schedule(function()
      local text, status = core.split_response(res.stdout or "")
      if not text then
        return notify("could not reach the service: " .. (res.stderr or ""), vim.log.levels.ERROR)
      end
      local ok, decoded = pcall(vim.json.decode, text)
      if status >= 400 then
        local e = ok and type(decoded) == "table" and (decoded.error or (decoded.detail and decoded.detail.error)) or {}
        return notify(core.friendly(status, e.code or "UNKNOWN", e.message or ("HTTP " .. status)), vim.log.levels.ERROR)
      end
      cb(ok and decoded or {})
    end)
  end)
end

function M.sign_in(token)
  token = token or vim.env.HARNESS_TOKEN or vim.fn.inputsecret("Harness session token: ")
  if not token or token == "" then return end
  -- Verify BEFORE keeping it: a rejected token is never retained (memory only, never disk).
  call(M.config.identity_url, "GET", "/api/v1/me", nil, function(me)
    M._token = token
    notify(("signed in as %s (%s) on team %s"):format(me.userId, me.role, me.teamId))
  end, token)
end

function M.sign_out() M._token, M._project = nil, nil; notify("signed out") end

function M.select_project()
  call(M.config.identity_url, "GET", "/api/v1/projects?limit=100", nil, function(r)
    local items = r.items or {}
    if #items == 0 then return notify("no projects visible to your team", vim.log.levels.WARN) end
    vim.ui.select(items, { prompt = "Harness project", format_item = function(p) return p.name or p.id end },
      function(p) if p then M._project = p.id end end)
  end)
end

function M.change_sets()
  if not M._project then return notify("select a project first (:HarnessProject)", vim.log.levels.ERROR) end
  call(M.config.workflow_url, "GET",
    "/api/v1/developer-workflow/code-sessions?projectId=" .. M._project, nil, function(r)
      local qf = {}
      for _, s in ipairs(r.items or {}) do
        qf[#qf + 1] = { text = ("session %s on %s"):format(s.sessionId, s.branch) }
      end
      vim.fn.setqflist(qf, "r"); vim.cmd("copen")
    end)
end

function M.approve_plan(run_id, plan_id)
  if not run_id or not plan_id then return notify("usage: :HarnessApprove <runId> <planId>", vim.log.levels.ERROR) end
  -- explicit confirmation: approval lets the agent execute the plan
  if vim.fn.confirm(("Approve plan %s for run %s?"):format(plan_id, run_id), "&Approve\n&Cancel", 2) ~= 1 then return end
  call(M.config.orchestrator_url, "POST", "/api/v1/orchestrator/runs/" .. run_id .. "/plan/approve",
    { planId = plan_id }, function() notify("plan approved") end)
end

return M

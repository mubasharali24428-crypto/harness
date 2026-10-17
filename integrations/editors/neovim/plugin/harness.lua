if vim.g.loaded_harness then return end
vim.g.loaded_harness = 1
local h = function() return require("harness") end
vim.api.nvim_create_user_command("HarnessSignIn", function(o) h().sign_in(o.args ~= "" and o.args or nil) end, { nargs = "?" })
vim.api.nvim_create_user_command("HarnessSignOut", function() h().sign_out() end, {})
vim.api.nvim_create_user_command("HarnessProject", function() h().select_project() end, {})
vim.api.nvim_create_user_command("HarnessChangeSets", function() h().change_sets() end, {})
vim.api.nvim_create_user_command("HarnessApprove", function(o)
  local run, plan = o.fargs[1], o.fargs[2]
  h().approve_plan(run, plan)
end, { nargs = "*" })

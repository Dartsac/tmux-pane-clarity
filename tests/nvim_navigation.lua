-- integrations/nvim, run by setup_test.py:
--   nvim --headless -u NONE -i NONE -l tests/nvim_navigation.lua
vim.opt.runtimepath:prepend(vim.fn.getcwd() .. "/integrations/nvim")
local adapter = vim.fn.getcwd() .. "/bin/tmux"

local calls = {}
vim.system = function(args)
	table.insert(calls, args)
	return {}
end
vim.env.TMUX = "/tmp/fake-tmux,1,0"
vim.env.TMUX_PANE = "%5"
vim.env.PANE_CLARITY_TMUX = adapter

local navigation = require("pane_clarity")
navigation.setup()
assert(vim.fn.maparg("<C-l>", "n") ~= "", "setup maps the keys")

vim.cmd("vsplit")
vim.cmd("wincmd h")
local left = vim.api.nvim_get_current_win()
navigation.navigate("l") -- between Neovim windows: no tmux
assert(vim.api.nvim_get_current_win() ~= left and #calls == 0, vim.inspect(calls))
navigation.navigate("l") -- right edge: tmux, through the adapter, without a shell
assert(vim.deep_equal(calls[1], { adapter, "select-pane", "-t", "%5", "-R" }), vim.inspect(calls))
navigation.navigate("p") -- back to tmux's last pane
assert(vim.deep_equal(calls[2], { adapter, "select-pane", "-t", "%5", "-l" }), vim.inspect(calls))

-- Moving any other way makes "previous" mean Neovim's previous window again.
vim.cmd("wincmd h")
navigation.navigate("p")
assert(#calls == 2 and vim.api.nvim_get_current_win() ~= left, "previous stays in Neovim")

-- Without the plugin's adapter, plain tmux; outside tmux, nothing at the edge.
vim.env.PANE_CLARITY_TMUX = nil
navigation.navigate("l") -- still in the right window: its edge
assert(calls[3][1] == "tmux", vim.inspect(calls[3]))
vim.env.TMUX = nil
navigation.navigate("l")
assert(#calls == 3)
print("nvim navigation ok")

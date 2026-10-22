-- Move between Neovim windows and tmux panes with the same keys, in place of
-- vim-tmux-navigator. At a window edge the move goes to tmux through
-- pane-clarity's adapter (so always-framed windows navigate by their tiled
-- layout), started directly rather than through 'shell' and not waited for.
--
--   require("pane_clarity").setup()            -- Ctrl-h/j/k/l, Ctrl-\ in normal mode
--   require("pane_clarity").setup({ modes = { "n", "i" } })
--
-- tmux must pass these keys to Neovim: see the README's Neovim section.
local M = {}

local FLAG = { h = "-L", j = "-D", k = "-U", l = "-R", p = "-l" }
local last_was_tmux = false
local moving = false

local function tmux()
	local adapter = vim.env.PANE_CLARITY_TMUX
	if adapter and adapter ~= "" and vim.fn.executable(adapter) == 1 then
		return adapter
	end
	return "tmux"
end

local function report(message)
	vim.schedule(function()
		vim.notify(message, vim.log.levels.ERROR)
	end)
end

--- Move in `direction` (h/j/k/l, or p for previous) and return the tmux
--- process started at an edge, if any.
function M.navigate(direction)
	local pane = vim.env.TMUX and vim.env.TMUX ~= "" and vim.env.TMUX_PANE
	-- "Previous" returns to tmux's last pane if the previous move left
	-- Neovim, otherwise to Neovim's previous window.
	local back_to_tmux = direction == "p" and last_was_tmux
	local window = vim.api.nvim_get_current_win()
	if not back_to_tmux then
		moving = true
		pcall(vim.cmd.wincmd, direction)
		moving = false
	end
	if not pane or not (back_to_tmux or vim.api.nvim_get_current_win() == window) then
		last_was_tmux = false
		return nil
	end
	last_was_tmux = true
	local ok, process = pcall(vim.system, { tmux(), "select-pane", "-t", pane, FLAG[direction] }, { text = true },
		function(result)
			if result.code ~= 0 then
				report(vim.trim(result.stderr or "") ~= "" and result.stderr or "tmux select-pane failed")
			end
		end)
	if not ok then
		report(tostring(process))
		return nil
	end
	return process
end

function M.setup(opts)
	opts = opts or {}
	local keys = opts.keys or { ["<C-h>"] = "h", ["<C-j>"] = "j", ["<C-k>"] = "k", ["<C-l>"] = "l", ["<C-\\>"] = "p" }
	for key, direction in pairs(keys) do
		vim.keymap.set(opts.modes or { "n" }, key, function()
			M.navigate(direction)
		end, { silent = true, desc = "Window or tmux pane " .. direction })
	end
	-- Moving between windows any other way (mouse, <C-w>w) makes "previous"
	-- mean Neovim's previous window again.
	vim.api.nvim_create_autocmd("WinEnter", {
		group = vim.api.nvim_create_augroup("pane_clarity_navigation", { clear = true }),
		callback = function()
			if not moving then
				last_was_tmux = false
			end
		end,
	})
end

return M
